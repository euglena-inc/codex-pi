// Drives runtime/pi_worker.ts with a fake ExtensionAPI (no Pi, no model).
//
//   node extension_harness.mjs <extension.ts> <scenario.json>
//
// The scenario is {"steps": [...]}; each step yields one entry of the printed JSON array:
//   {"op": "tool_call", "toolName": "bash", "input": {...}}      -> {blocked, reason, input}
//   {"op": "before_agent_start"}                                  -> {sections}
//   {"op": "settle", "outcome": "completed"}                      -> {result}
//   {"op": "tool", "name": "check", "params": {...}}              -> execute() result
//   {"op": "registered"}                                          -> {tools: [names], events: [names]}
// Environment (CODEX_PI_WORKER_CONFIG) is inherited from the caller. The extension is
// imported only after the scenario is read, so its load-time behavior is observable.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [, , extension, scenarioFile] = process.argv;
const scenario = JSON.parse(fs.readFileSync(scenarioFile, "utf8"));

const handlers = new Map();
const tools = new Map();
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
module.default(pi);

const ctx = { cwd: process.cwd(), hasUI: false, ui: { notify() {} } };
const out = [];
let counter = 0;
for (const step of scenario.steps) {
	try {
		if (step.op === "tool_call") {
			const event = { type: "tool_call", toolCallId: `call-${++counter}`, toolName: step.toolName, input: structuredClone(step.input ?? {}) };
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
				out.push(await tool.execute(`call-${++counter}`, structuredClone(step.params ?? {}), undefined, undefined, ctx));
			}
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
