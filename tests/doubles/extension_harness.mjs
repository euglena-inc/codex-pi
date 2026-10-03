// Drives runtime/pi_worker.ts with a fake ExtensionAPI (no Pi, no model).
//
//   node extension_harness.mjs <extension.ts> <scenario.json>
//
// The scenario is {"steps": [...]}; each step yields one entry of the printed JSON array:
//   {"op": "tool_call", "toolName": "bash", "input": {...}}      -> {blocked, reason, input}
//   {"op": "before_agent_start"}                                  -> {sections}
//   {"op": "settle", "outcome": "completed"}                      -> {result}
//   {"op": "tool", "name": "check", "params": {...}}              -> execute() result
//   {"op": "tool_start", "slot": s, "name": "check", ...}         -> {started}; runs execute() without awaiting
//   {"op": "tool_await", "slot": s}                               -> the awaited execute() result
//   {"op": "tool_abort", "slot": s}                               -> {aborted}; aborts the signal passed to execute()
//   {"op": "sleep", "ms": n}                                      -> {slept}; lets in-flight tools make progress
//   {"op": "registered"}                                          -> {tools: [names], events: [names]}
// tool_start/tool_await/tool_abort share one extension instance and its in-process pool;
// each started tool gets its own AbortSignal unless abortable is false.
// Environment (CODEX_PI_WORKER_CONFIG) is inherited from the caller. The extension is
// imported only after the scenario is read, so its load-time behavior is observable.
import fs from "node:fs";
import { register } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

// The extension imports the host package by name; map it to the offline stub before loading.
register("./host_resolver.mjs", import.meta.url);

const [, , extension, scenarioFile] = process.argv;
const scenario = JSON.parse(fs.readFileSync(scenarioFile, "utf8"));

const handlers = new Map();
const tools = new Map();
const pendingTools = new Map();
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const pi = {
	on(event, handler) {
		if (!handlers.has(event)) handlers.set(event, []);
		handlers.get(event).push(handler);
		return () => {};
	},
	registerTool(tool) {
		tools.set(tool.name, tool);
	},
};

const module = await import(pathToFileURL(path.resolve(extension)).href);
await module.default(pi);

const ctx = { cwd: process.cwd(), hasUI: false, ui: { notify() {} } };
const out = [];
let counter = 0;
for (const step of scenario.steps) {
	try {
		if (step.op === "tool_call") {
			const event = { type: "tool_call", toolCallId: `call-${++counter}`, toolName: step.toolName, input: structuredClone(step.input ?? {}) };
			if (step.parentToolCallId) event.parentToolCallId = step.parentToolCallId;
			let result;
			for (const handler of handlers.get("tool_call") ?? []) {
				result = await handler(event, ctx);
				if (result?.block) break;
			}
			out.push({ blocked: Boolean(result?.block), reason: result?.reason ?? null, input: event.input });
		} else if (step.op === "before_agent_start") {
			const event = { type: "before_agent_start", prompt: "", systemPrompt: "", systemPromptOptions: { sections: {} } };
			for (const handler of handlers.get("before_agent_start") ?? []) await handler(event, ctx);
			out.push({ sections: event.systemPromptOptions.sections });
		} else if (step.op === "settle") {
			const event = { type: "agent_before_settle", entries: [], continue: false, outcome: step.outcome ?? "completed", context: {} };
			let result;
			for (const handler of handlers.get("agent_before_settle") ?? []) result = (await handler(event, ctx)) ?? result;
			out.push({ result: result ?? null });
		} else if (step.op === "tool") {
			const tool = tools.get(step.name);
			if (!tool) {
				out.push({ error: `no tool ${step.name}` });
			} else {
				const callId = typeof step.toolCallId === "string" ? step.toolCallId : `call-${++counter}`;
				out.push(await tool.execute(callId, structuredClone(step.params ?? {}), undefined, undefined, ctx));
			}
		} else if (step.op === "tool_start") {
			const tool = tools.get(step.name);
			if (!tool) {
				out.push({ error: `no tool ${step.name}` });
			} else {
				const slot = step.slot ?? `slot-${++counter}`;
				const controller = step.abortable === false ? null : new AbortController();
				const callId = typeof step.toolCallId === "string" ? step.toolCallId : `call-${++counter}`;
				pendingTools.set(slot, {
					promise: tool.execute(callId, structuredClone(step.params ?? {}), controller?.signal, undefined, ctx),
					controller,
				});
				out.push({ started: slot, callId });
			}
		} else if (step.op === "tool_await") {
			const entry = pendingTools.get(step.slot);
			if (!entry) {
				out.push({ error: `no pending tool ${step.slot}` });
			} else {
				out.push(await entry.promise);
				pendingTools.delete(step.slot);
			}
		} else if (step.op === "tool_abort") {
			const entry = pendingTools.get(step.slot);
			if (!entry) {
				out.push({ error: `no pending tool ${step.slot}` });
			} else {
				entry.controller?.abort();
				out.push({ aborted: step.slot });
			}
		} else if (step.op === "file_write") {
            fs.writeFileSync(step.path, step.text);
            out.push({written: true});
        } else if (step.op === "wait_file") {
            const deadline = Date.now() + 5000;
            while (!fs.existsSync(step.path) && Date.now() < deadline) await sleep(10);
            if (!fs.existsSync(step.path)) throw new Error("fixture child did not start");
            out.push({exists:true});
        } else if (step.op === "sleep") {
			const ms = Number(step.ms ?? 50);
			await sleep(ms);
			out.push({ slept: ms });
		} else if (step.op === "registered") {
			out.push({ tools: [...tools.keys()].sort(), events: [...handlers.keys()].sort() });
		} else {
			out.push({ error: `unknown op ${step.op}` });
		}
	} catch (error) {
		out.push({ thrown: String(error) });
	}
}
console.log(JSON.stringify(out));
