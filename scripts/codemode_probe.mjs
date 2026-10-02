// Real Pi 1.0.0+ SDK driver for scripts/validate_codemode.py.
//
// It loads the candidate worker extension and the real QuickJS codemode sandbox in
// an in-process session whose model is a local scripted provider: no network, no
// credentials and no paid provider. The driver only records observations; every
// expectation lives in the Python probe.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const workdir = path.resolve(process.argv[2] ?? ".");
const packageRoot = process.env.PI_PACKAGE_ROOT;
const packageEntry = process.env.PI_PACKAGE_ENTRY;
const extensionPath = process.env.PI_CODEMODE_EXTENSION;
const workerConfigPath = process.env.CODEX_PI_WORKER_CONFIG;
const reportPath = process.env.PI_PROBE_REPORT;
if (!packageRoot || !packageEntry || !extensionPath || !workerConfigPath || !reportPath) {
	throw new Error("probe environment is incomplete (PI_PACKAGE_ROOT/PI_PACKAGE_ENTRY/PI_CODEMODE_EXTENSION/CODEX_PI_WORKER_CONFIG/PI_PROBE_REPORT)");
}

const host = await import(pathToFileURL(packageEntry).href);
const { createAgentSession, DefaultResourceLoader, SessionManager, SettingsManager } = host;
const { createAssistantMessageEventStream } = await import(pathToFileURL(
	path.join(packageRoot, "node_modules/@earendil-works/pi-ai/dist/utils/event-stream.js")).href);

const paths = {
	main: path.join(workdir, "main"),
	worktree: path.join(workdir, "wt"),
	sessionDir: path.join(workdir, "session"),
	agentDir: path.join(workdir, "agent"),
};
const secret = path.join(paths.main, "secret.txt");
const allowedWrite = path.join(paths.worktree, "nested-ok.txt");
const abortPidFile = path.join(paths.worktree, "abort.pid");
const workerConfig = JSON.parse(fs.readFileSync(workerConfigPath, "utf8"));
const readyPath = path.join(workerConfig.roundDir, "worker.ready");

const report = {
	node: process.version,
	piVersion: host.VERSION ?? null,
	readyMarker: null,
	readyBeforeFirstTool: null,
	activeTools: [],
	allTools: [],
	toolsWithOutputSchema: [],
	idleResponses: 0,
	cases: {},
	blocks: [],
};
const toolEvents = [];
let firstToolStart = false;

const scripted = [];
function nextScripted() {
	if (scripted.length > 0) return scripted.shift();
	report.idleResponses += 1;
	return { text: "probe: idle" };
}

const usage = () => ({ input: 10, output: 5, cacheRead: 0, cacheWrite: 0, totalTokens: 15,
	cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } });
let callCounter = 0;

function buildStream(model, response) {
	const stream = createAssistantMessageEventStream();
	const isCall = typeof response.code === "string";
	const content = isCall
		? [{ type: "toolCall", id: `probe-${++callCounter}`, name: "codemode", arguments: { code: response.code } }]
		: [{ type: "text", text: response.text ?? "probe: text" }];
	const stopReason = isCall ? "toolUse" : "stop";
	const message = { role: "assistant", content, api: model.api, provider: model.provider,
		model: model.id, usage: usage(), stopReason, timestamp: Date.now() };
	queueMicrotask(() => {
		stream.push({ type: "start", partial: { ...message, content: [], stopReason: "pending" } });
		stream.push({ type: "done", reason: stopReason, message });
		stream.end(message);
	});
	return stream;
}

function probeProvider(api) {
	api.registerProvider("probe", {
		name: "Probe",
		apiKey: "probe-key",
		api: "probe-api",
		baseUrl: "http://127.0.0.1:0",
		models: [{ id: "scripted", name: "Scripted", api: "probe-api", input: ["text"],
			reasoning: false, contextWindow: 200000, maxTokens: 4096,
			cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } }],
		streamSimple: (model) => buildStream(model, nextScripted()),
	});
}

const model = { id: "scripted", name: "Scripted", api: "probe-api", provider: "probe",
	baseUrl: "http://127.0.0.1:0", reasoning: false, input: ["text"],
	cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 200000, maxTokens: 4096 };

function textOf(result) {
	return (result?.content ?? []).filter((block) => block.type === "text").map((block) => block.text).join("\n");
}

async function createSession(sessionManager) {
	const settingsManager = SettingsManager.create(workdir, paths.agentDir);
	const resourceLoader = new DefaultResourceLoader({
		cwd: workdir, agentDir: paths.agentDir, settingsManager,
		extensionFactories: [probeProvider],
		additionalExtensionPaths: [extensionPath],
		noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
	});
	await resourceLoader.reload();
	const created = await createAgentSession({
		cwd: workdir, agentDir: paths.agentDir, model, resourceLoader, settingsManager,
		sessionManager: sessionManager ?? SessionManager.create(workdir, paths.sessionDir),
		tools: ["read", "write", "edit", "bash", "check", "progress", "readiness", "codemode"],
		thinkingLevel: "off",
	});
	await created.session.bindExtensions({});
	return created.session;
}

function attach(session) {
	return session.subscribe((event) => {
		if (event.type === "tool_execution_start") {
			if (!firstToolStart) {
				firstToolStart = true;
				report.readyBeforeFirstTool = fs.existsSync(readyPath);
			}
			toolEvents.push({ phase: "start", tool: event.toolName,
				parent: event.parentToolCallId ?? null });
		} else if (event.type === "tool_execution_end") {
			toolEvents.push({ phase: "end", tool: event.toolName, parent: event.parentToolCallId ?? null,
				isError: Boolean(event.isError), text: textOf(event.result) });
		}
	});
}

function caseSlice(startIndex) {
	return toolEvents.slice(startIndex);
}

async function runCase(session, name, responses, runner) {
	scripted.length = 0;
	scripted.push(...responses);
	const startIndex = toolEvents.length;
	const started = Date.now();
	let error = null;
	try {
		await (runner ? runner(session) : session.prompt(`probe case ${name}`));
	} catch (caught) {
		error = String(caught);
	}
	const events = caseSlice(startIndex);
	report.cases[name] = {
		error,
		elapsedMs: Date.now() - started,
		leftover: scripted.length,
		codemode: events.filter((entry) => entry.phase === "end" && entry.tool === "codemode"),
		nestedStarts: events.filter((entry) => entry.phase === "start" && entry.parent !== null),
		nestedEnds: events.filter((entry) => entry.phase === "end" && entry.parent !== null),
	};
}

function sleep(ms) {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitFor(predicate, timeoutMs) {
	const deadline = Date.now() + timeoutMs;
	while (Date.now() < deadline) {
		if (predicate()) return true;
		await sleep(50);
	}
	return false;
}

function processAlive(pid) {
	try {
		process.kill(pid, 0);
		return true;
	} catch {
		return false;
	}
}

async function main() {
	const session = await createSession();
	attach(session);
	report.activeTools = session.getActiveToolNames();
	report.allTools = session.getAllTools().map((tool) => tool.name);
	report.toolsWithOutputSchema = ["check", "progress", "readiness"]
		.filter((name) => session.getToolDefinition(name)?.outputSchema !== undefined).sort();
	report.model = `${session.model?.provider ?? "?"}/${session.model?.id ?? "?"}`;

	await runCase(session, "models", [
		{ code: "return JSON.stringify({models: typeof models, tools: typeof tools});" },
		{ text: "models case done" },
	]);

	await runCase(session, "check-ok", [
		{ code: "const r = await tools.check({ id: 'probe-ok', command: 'true' }); return JSON.stringify(r);" },
		{ text: "check-ok done" },
	]);

	await runCase(session, "check-fail", [
		{ code: "const r = await tools.check({ id: 'probe-fail', command: 'false' }); return JSON.stringify({ok: r.ok, exit: r.exit_code, tail: typeof r.log_tail});" },
		{ text: "check-fail done" },
	]);

	await runCase(session, "nested-write", [
		{ code:
			"let blocked = 'none';\n" +
			`try { await tools.write({ path: ${JSON.stringify(secret)}, content: 'changed' }); blocked = 'unblocked'; }\n` +
			"catch (error) { blocked = String(error && error.message ? error.message : error); }\n" +
			`await tools.write({ path: ${JSON.stringify(allowedWrite)}, content: 'ok' });\n` +
			"return JSON.stringify({blocked});" },
		{ text: "nested-write done" },
	]);

	await runCase(session, "timeout-default", [
		{ code: "await tools.bash({ command: 'sleep 30', timeout: 120 }); return 'late';" },
		{ text: "timeout-default done" },
	]);

	await runCase(session, "timeout-excess", [
		{ code: "// @options: {\"timeout_ms\": 999999999}\nawait tools.bash({ command: 'sleep 30', timeout: 120 }); return 'late';" },
		{ text: "timeout-excess done" },
	]);

	await runCase(session, "invalid-options", [
		{ code: "// @options: {\"timeout_ms\": 0}\nreturn 'must not run';" },
		{ text: "invalid-options done" },
	]);

	await runCase(session, "store-ok", [
		{ code: "store('probe-ok', 41); return JSON.stringify({value: load('probe-ok')});" },
		{ text: "store-ok done" },
	]);

	await runCase(session, "store-fail", [
		{ code: "store('probe-bad', 7); throw new Error('probe failure');" },
		{ text: "store-fail done" },
	]);

	await runCase(session, "store-branch", [
		{ code: "return JSON.stringify({ok: load('probe-ok'), bad: typeof load('probe-bad')});" },
		{ text: "store-branch done" },
	]);

	await runCase(session, "abort", [
		{ code: `await tools.bash({ command: 'echo $$ > ${abortPidFile}; sleep 30', timeout: 120 }); return 'late';` },
		{ text: "abort done" },
	], async (active) => {
		const prompt = active.prompt("probe case abort");
		const appeared = await waitFor(() => fs.existsSync(abortPidFile), 15000);
		if (!appeared) throw new Error("abort case: the nested bash never started");
		const pid = Number.parseInt(fs.readFileSync(abortPidFile, "utf8").trim(), 10);
		report.abortPid = pid;
		await active.abort();
		try {
			await prompt;
		} catch {
			// an aborted prompt may reject; the recorded tool event is the observation
		}
		await sleep(700);
		report.abortPidAlive = Number.isFinite(pid) && processAlive(pid);
	});

	const sessionFile = session.sessionFile;
	session.dispose();
	report.sessionFile = sessionFile ?? null;

	if (sessionFile) {
		const resumed = await createSession(SessionManager.open(sessionFile, paths.sessionDir, workdir));
		attach(resumed);
		await runCase(resumed, "store-resume", [
			{ code: "return JSON.stringify({ok: load('probe-ok'), bad: typeof load('probe-bad')});" },
			{ text: "store-resume done" },
		]);
		resumed.dispose();
	}

	report.readyMarker = fs.existsSync(readyPath)
		? JSON.parse(fs.readFileSync(readyPath, "utf8")) : null;
	const blocksPath = path.join(workerConfig.roundDir, "worker-blocks.jsonl");
	report.blocks = fs.existsSync(blocksPath)
		? fs.readFileSync(blocksPath, "utf8").split("\n").filter(Boolean).map((line) => JSON.parse(line))
		: [];
}

try {
	await main();
	fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
	process.exit(0);
} catch (error) {
	fs.writeFileSync(reportPath, JSON.stringify({ ...report, fatal: String(error) }, null, 2));
	console.error(String(error?.stack ?? error));
	process.exit(1);
}
