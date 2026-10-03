// Real Pi 1.0.0+ SDK driver for `scripts/validate_codemode.py --context-workflow`.
//
// Three previously authorized observations on the real scripted-provider SDK
// pipeline, with no network and no credentials:
//   1. a cheap precondition check that fails on missing input leaves an immutable
//      failed receipt and never starts the dependent check; after fixing the
//      synthetic input the same check passes and the dependent command runs;
//   2. equivalent direct `read` turns versus one codemode batch read: actual
//      provider request counts, serialized tool-result bytes entering later
//      requests, wall clock and identical real tool results;
//   3. idle native `session.compact()` safety: success entry and usage, prefix and
//      identity preservation, store/receipt/contract retention after reopen, plus
//      no-model/summary-failure/cancel negatives and an active-state negative control.
//
// Only observations are recorded here; every expectation lives in the Python probe.
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const sha256 = (buffer) => createHash("sha256").update(buffer).digest("hex");

function readEntries(file) {
	if (!file || !fs.existsSync(file)) return [];
	return fs.readFileSync(file, "utf8").split("\n").filter(Boolean)
		.map((line) => { try { return JSON.parse(line); } catch { return null; } })
		.filter((entry) => entry !== null);
}

function countCompactionEntries(file) {
	return readEntries(file).filter((entry) => entry.type === "compaction").length;
}

function receiptFacts(checksDir, prefix) {
	const rows = [];
	let names = [];
	try { names = fs.readdirSync(checksDir).sort(); } catch { return rows; }
	for (const name of names) {
		if (!name.endsWith(".json") || !name.startsWith(`${prefix}-`)) continue;
		const full = path.join(checksDir, name);
		const raw = fs.readFileSync(full);
		let data = null;
		try { data = JSON.parse(raw.toString("utf8")); } catch { /* recorded as null below */ }
		rows.push({ path: full, name, bytes: raw.length, sha256: sha256(raw),
			exitCode: data?.exit_code ?? null, startedAt: data?.started_at ?? null,
			head: data?.head ?? null, dirty: data?.dirty ?? null, log: data?.log ?? null });
	}
	return rows;
}

function summarizeReceipts(rows) {
	return rows.map((row) => ({ name: row.name, sha256: row.sha256, exitCode: row.exitCode,
		startedAt: row.startedAt, head: row.head, dirty: row.dirty }));
}

export async function runContextWorkflow({ createSession, attach, runCase, report, workerConfig,
	workdir, paths, sleep, waitFor, scripted, SessionManager }) {
	const checksDir = workerConfig.checksDir;
	const worktree = paths.worktree;
	const markerPath = path.join(worktree, "ctx-active.marker");
	const inputPath = path.join(worktree, "ctx-input.txt");
	const dependentPath = path.join(worktree, "ctx-dependent.marker");

	// A real commit makes the check receipts bind a candidate head. Empty
	// baseline plus later untracked writes keep dirty/diff state real too.
	try {
		execFileSync("git", ["rev-parse", "--git-dir"], { cwd: worktree, stdio: "ignore" });
	} catch {
		execFileSync("git", ["init", "-q"], { cwd: worktree });
		execFileSync("git", ["config", "user.email", "probe@example.invalid"], { cwd: worktree });
		execFileSync("git", ["config", "user.name", "Probe"], { cwd: worktree });
		execFileSync("git", ["commit", "--allow-empty", "-q", "-m", "probe baseline"], { cwd: worktree });
	}

	const session = await createSession();
	attach(session);
	session.subscribe((event) => {
		if (!["compaction_start", "compaction_end", "summarization_retry_scheduled"].includes(event.type)) return;
		report.sessionCompactionEvents.push({
			type: event.type, reason: event.reason ?? null,
			aborted: event.aborted ?? null, error: event.errorMessage ?? null,
			result: event.result ? { tokensBefore: event.result.tokensBefore ?? null,
				estimatedTokensAfter: event.result.estimatedTokensAfter ?? null,
				usage: event.result.usage ?? null } : null,
		});
	});
	report.activeTools = session.getActiveToolNames();
	report.allTools = session.getAllTools().map((tool) => tool.name);
	report.toolsWithOutputSchema = ["check", "progress", "readiness"]
		.filter((name) => session.getToolDefinition(name)?.outputSchema !== undefined).sort();
	report.model = `${session.model?.provider}/${session.model?.id}`;
	report.contract = workerConfig.contract;
	report.compactToolExposed = report.allTools.includes("compact")
		|| report.activeTools.includes("compact");

	// --- enough synthetic history for the real compact preparation ---
	const historyTurns = 12;
	const userLine = "history user " + "u".repeat(4500);
	const assistantLine = "history assistant " + "a".repeat(4500);
	for (let i = 0; i < historyTurns; i++) {
		scripted.length = 0;
		scripted.push({ text: `${assistantLine} turn ${i}` });
		await session.prompt(`${userLine} turn ${i}`);
	}
	report.history = { turns: historyTurns };
	report.sessionId = session.sessionId ?? null;
	const sessionFile = session.sessionFile ?? null;
	report.sessionFile = sessionFile;

	// --- active-state negative control: activity must not compact or abort the task ---
	for (const leftover of [markerPath]) { try { fs.unlinkSync(leftover); } catch { /* absent */ } }
	const beforeActiveEvents = report.sessionCompactionEvents.length;
	await runCase(session, "active-negative", [
		{ code: `// @options: {"timeout_ms": 12000}\nawait tools.bash({ command: 'sleep 2; printf original > ${markerPath}', timeout: 10 });\nreturn 'active done';` },
		{ text: "active done" },
	], async (active) => {
		const prompt = active.prompt("probe case active-negative");
		const appeared = await waitFor(() => fs.existsSync(markerPath), 15000);
		const during = report.sessionCompactionEvents.length - beforeActiveEvents;
		await prompt;
		report.activeNegative = {
			markerAppeared: appeared,
			compactionEventsDuringTask: during,
			compactionEntriesDuringTask: sessionFile ? countCompactionEntries(sessionFile) : null,
			marker: fs.existsSync(markerPath) ? fs.readFileSync(markerPath, "utf8") : null,
			compactToolExposed: report.compactToolExposed,
		};
	});

	// --- cheap precondition: missing input must not start the dependent command ---
	try { fs.unlinkSync(inputPath); } catch { /* absent is the precondition */ }
	try { fs.unlinkSync(dependentPath); } catch { /* absent is the observation */ }
	const preconditionCode = `// @options: {"timeout_ms": 20000}
const first = await tools.check({ id: 'ctx-precondition', command: 'test -f ctx-input.txt', estimatedSeconds: 1 });
const outcome = { first: { ok: first.ok, exit: first.exit_code, receipt: typeof first.receipt } };
if (first.ok) {
	const dependent = await tools.check({ id: 'ctx-dependent', command: 'touch ctx-dependent.marker', estimatedSeconds: 1 });
	outcome.dependent = { ok: dependent.ok, exit: dependent.exit_code };
} else {
	outcome.dependentSkipped = true;
}
return JSON.stringify(outcome);`;
	await runCase(session, "precondition-missing", [
		{ code: preconditionCode },
		{ text: "precondition missing done" },
	]);
	report.precondition = {
		missing: {
			caseError: report.cases["precondition-missing"].error,
			dependentMarkerExists: fs.existsSync(dependentPath),
			preconditionReceipts: summarizeReceipts(receiptFacts(checksDir, "ctx-precondition")),
			dependentReceipts: summarizeReceipts(receiptFacts(checksDir, "ctx-dependent")),
		},
	};
	await runCase(session, "precondition-ready", [
		{ code: `// @options: {"timeout_ms": 20000}\nawait tools.write({ path: ${JSON.stringify(inputPath)}, content: 'ready' });\nreturn JSON.stringify({ written: true });` },
		{ code: preconditionCode },
		{ text: "precondition ready done" },
	]);
	report.precondition.ready = {
		caseError: report.cases["precondition-ready"].error,
		dependentMarker: fs.existsSync(dependentPath)
			? fs.readFileSync(dependentPath, "utf8").trim() : null,
		preconditionReceipts: summarizeReceipts(receiptFacts(checksDir, "ctx-precondition")),
		dependentReceipts: summarizeReceipts(receiptFacts(checksDir, "ctx-dependent")),
	};

	// --- direct read turns versus one codemode batch read/filter ---
	function syntheticFile(tag, totalLines, keepLines) {
		const lines = [];
		for (let i = 0; i < totalLines; i++) {
			lines.push(`${tag} filler line ${String(i).padStart(3, "0")} ${"x".repeat(34)}`);
		}
		for (const [index, text] of keepLines) {
			lines[index] = `${tag} ${text} KEEP-${tag.toUpperCase()}`;
		}
		return lines.join("\n") + "\n";
	}
	const batchFiles = [
		{ key: "alpha", name: "ctx-alpha.txt",
			content: syntheticFile("alpha", 120, [[3, "first marker"], [57, "middle marker"], [110, "last marker"]]) },
		{ key: "beta", name: "ctx-beta.txt",
			content: syntheticFile("beta", 120, [[8, "first marker"], [60, "middle marker"], [115, "last marker"]]) },
		{ key: "gamma", name: "ctx-gamma.txt",
			content: syntheticFile("gamma", 120, [[1, "first marker"], [70, "middle marker"], [118, "last marker"]]) },
	];
	const filePaths = {};
	for (const file of batchFiles) {
		const full = path.join(worktree, file.name);
		fs.writeFileSync(full, file.content, "utf8");
		filePaths[file.key] = full;
	}
	const missingReadPath = path.join(worktree, "ctx-missing.txt");
	const directResponses = batchFiles.map((file) => ({ toolCall: { name: "read",
		arguments: { path: filePaths[file.key] } } }));
	directResponses.push({ toolCall: { name: "read", arguments: { path: missingReadPath } } });
	directResponses.push({ text: "direct read done" });
	await runCase(session, "read-direct", directResponses);

	const batchCode = `// @options: {"timeout_ms": 20000}
const paths = ${JSON.stringify(filePaths)};
const missing = ${JSON.stringify(missingReadPath)};
const read = {};
const failures = {};
for (const [key, file] of Object.entries(paths)) {
	try { read[key] = await tools.read({ path: file }); }
	catch (error) { failures[key] = String(error && error.message ? error.message : error); }
}
try { await tools.read({ path: missing }); }
catch (error) { failures.missing = String(error && error.message ? error.message : error); }
const kept = {};
for (const [key, text] of Object.entries(read)) {
	kept[key] = text.split("\\n").filter((line) => line.includes("KEEP"));
}
return JSON.stringify({ kept, failures, read: Object.keys(read) });`;
	await runCase(session, "read-codemode", [{ code: batchCode }, { text: "codemode batch done" }]);

	const directCase = report.cases["read-direct"];
	const codemodeCase = report.cases["read-codemode"];
	const directReads = directCase.topEnds.filter((entry) => entry.tool === "read");
	const nestedReads = codemodeCase.nestedEnds.filter((entry) => entry.tool === "read");
	report.batchRead = {
		files: batchFiles.map((file) => ({ key: file.key, name: file.name,
			path: filePaths[file.key], bytes: Buffer.byteLength(file.content, "utf8") })),
		missingPath: missingReadPath,
		direct: {
			providerRequests: directCase.providerRequests.length,
			lastToolResultBytes: directCase.providerRequests.at(-1)?.toolResultBytes ?? null,
			toolResultDeltaBytes: (directCase.providerRequests.at(-1)?.toolResultBytes ?? 0)
				- (directCase.providerRequests[0]?.toolResultBytes ?? 0),
			elapsedMs: directCase.elapsedMs,
			reads: directReads.map((entry) => ({ path: entry.args?.path ?? null, isError: entry.isError,
				bytes: Buffer.byteLength(entry.text ?? "", "utf8"), text: entry.text ?? "" })),
		},
		codemode: {
			providerRequests: codemodeCase.providerRequests.length,
			lastToolResultBytes: codemodeCase.providerRequests.at(-1)?.toolResultBytes ?? null,
			toolResultDeltaBytes: (codemodeCase.providerRequests.at(-1)?.toolResultBytes ?? 0)
				- (codemodeCase.providerRequests[0]?.toolResultBytes ?? 0),
			elapsedMs: codemodeCase.elapsedMs,
			nestedReads: nestedReads.map((entry) => ({ path: entry.args?.path ?? null,
				isError: entry.isError, bytes: Buffer.byteLength(entry.text ?? "", "utf8"),
				text: entry.text ?? "" })),
		},
	};

	// --- codemode store before compaction ---
	await runCase(session, "store-before", [
		{ code: "store('ctx-workflow', 'kept'); return JSON.stringify({ stored: load('ctx-workflow') });" },
		{ text: "store before done" },
	]);

	// --- idle native compaction: failure, cancel and no-model negatives ---
	report.compactFacts = { sessionFile, before: countCompactionEntries(sessionFile) };
	await runCase(session, "compact-failure", [{ error: "synthetic deterministic summary failure" }],
		async (active) => {
			let failure = null;
			try { await active.compact("probe failure"); } catch (error) { failure = String(error); }
			report.compactFacts.failure = { error: failure,
				entries: countCompactionEntries(sessionFile) };
		});

	let releaseGate;
	const gate = new Promise((resolve) => { releaseGate = resolve; });
	await runCase(session, "compact-cancel", [{ text: "late synthetic summary", gate }],
		async (active) => {
			let failure = null;
			const compaction = active.compact("probe cancel")
				.catch((error) => { failure = String(error); });
			const requested = await waitFor(
				() => report.providerRequests.some((row) => row.case === "compact-cancel"), 5000);
			active.abortCompaction();
			releaseGate();
			await compaction;
			report.compactFacts.cancel = { providerRequested: requested, error: failure,
				entries: countCompactionEntries(sessionFile) };
		});

	await runCase(session, "compact-no-model", [], async (active) => {
		const original = active.agent.state.model;
		let failure = null;
		active.agent.state.model = undefined;
		try { await active.compact("probe no model"); } catch (error) { failure = String(error); }
		finally { active.agent.state.model = original; }
		report.compactFacts.noModel = { error: failure, entries: countCompactionEntries(sessionFile) };
	});

	// --- idle successful compaction: real entry, usage and append-only prefix ---
	const beforeBytes = fs.readFileSync(sessionFile);
	const beforeSeconds = Date.now() / 1000;
	await runCase(session, "compact-success", [
		{ text: "Synthetic history summary of the probe session.\n" + "detail ".repeat(40) },
		{ text: "Synthetic turn-prefix summary.\n" + "prefix ".repeat(40) },
	], async (active) => {
		let failure = null;
		let result = null;
		try { result = await active.compact("probe success"); } catch (error) { failure = String(error); }
		report.compactFacts.success = {
			error: failure,
			result: result ? { tokensBefore: result.tokensBefore ?? null,
				estimatedTokensAfter: result.estimatedTokensAfter ?? null,
				usage: result.usage ?? null } : null,
		};
	});
	const afterSeconds = Date.now() / 1000;
	const afterBytes = fs.readFileSync(sessionFile);
	const compactionEntries = readEntries(sessionFile).filter((entry) => entry.type === "compaction");
	const compactionEntry = compactionEntries.length > 0
		? compactionEntries[compactionEntries.length - 1] : null;
	report.compactFacts.success = Object.assign(report.compactFacts.success ?? {}, {
		prefixPreserved: afterBytes.subarray(0, beforeBytes.length).equals(beforeBytes),
		beforeBytes: beforeBytes.length, afterBytes: afterBytes.length,
		appendedBytes: afterBytes.length - beforeBytes.length,
		entriesBefore: report.compactFacts.before,
		entriesAfter: countCompactionEntries(sessionFile),
		entry: compactionEntry,
		window: [beforeSeconds - 2, afterSeconds + 2],
		sessionId: session.sessionId ?? null,
		model: `${session.model?.provider}/${session.model?.id}`,
		providerRequests: report.cases["compact-success"].providerRequests.length,
	});

	await runCase(session, "post-compact-store", [
		{ code: "return JSON.stringify({ ok: load('ctx-workflow') });" },
		{ text: "post compact store done" },
	]);
	report.receiptsBeforeDispose = {
		precondition: summarizeReceipts(receiptFacts(checksDir, "ctx-precondition")),
		dependent: summarizeReceipts(receiptFacts(checksDir, "ctx-dependent")),
	};

	// --- reopen the same session file: identity, store, contract and receipts ---
	session.dispose();
	const resumed = await createSession(SessionManager.open(sessionFile, paths.sessionDir, workdir));
	attach(resumed);
	report.resume = {
		sessionId: resumed.sessionId ?? null,
		model: `${resumed.model?.provider}/${resumed.model?.id}`,
	};
	await runCase(resumed, "resume-store", [
		{ code: "return JSON.stringify({ ok: load('ctx-workflow') });" },
		{ text: "resume store done" },
	]);
	await runCase(resumed, "resume-contract", [{ text: "contract check" }]);
	report.resume.contractInjected = report.providerRequests
		.filter((row) => row.case === "resume-contract")
		.every((row) => row.contractInjected);
	report.resume.sessionFile = resumed.sessionFile ?? null;
	report.receiptsAfterResume = {
		precondition: summarizeReceipts(receiptFacts(checksDir, "ctx-precondition")),
		dependent: summarizeReceipts(receiptFacts(checksDir, "ctx-dependent")),
	};
	resumed.dispose();

	const readyPath = path.join(workerConfig.roundDir, "worker.ready");
	report.readyMarker = fs.existsSync(readyPath)
		? JSON.parse(fs.readFileSync(readyPath, "utf8")) : null;
}
