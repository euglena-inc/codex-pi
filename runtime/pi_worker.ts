/**
 * Codex-Pi worker extension (Pi >= 1.0.0).
 *
 * Loaded explicitly with `pi --no-extensions -e <tools>/pi_worker.ts`. It imports Node
 * built-ins and the host package that loads it; the host import registers Pi's public
 * codemode extension at load time.
 *
 * - Proves it loaded by writing `worker.ready` next to `worker.json` only after the
 *   native codemode registration returned.
 * - Guards every tool call: main checkout and other worktrees are forbidden,
 *   writes stay inside the allowed roots, bash gets a default timeout that is
 *   clamped to a ceiling. Anything it cannot decide is blocked.
 * - Normalizes a codemode script's first-line options to a bounded deadline and
 *   output budget; nested `tools.*` calls pass the same guard.
 * - Registers the native tools `check`, `progress` and `readiness`, which call
 *   the task's frozen Python helpers so receipts stay byte-compatible.
 * - Injects the worker contract as a system prompt section on every run.
 * - Continues once at settle time when only evidence is missing (one persisted
 *   quota per phase).
 *
 * Configuration: the JSON file named by CODEX_PI_WORKER_CONFIG.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

interface ContractItem {
	id: string;
	checkId?: string;
	command: string;
	targetedCommand?: string;
	estimatedSeconds?: number;
}

interface WorkerConfig {
	task: string;
	round: number;
	repo: string;
	worktree: string;
	forbiddenRoots: string[];
	allowedRoots: string[];
	bashDefaultTimeoutSeconds: number;
	bashCeilingSeconds: number;
	checkTimeoutSeconds: number;
	checksDir: string;
	toolsDir: string;
	python: string;
	phase: boolean;
	settleQuotaPath: string | null;
	roundDir: string;
	contract: string;
	/** When present, the check tool reads the real deadline from this round state file. */
	deadlinePath?: string;
	/** Acceptance item metadata for targeted repair and full-check admission. */
	acceptanceItems?: ContractItem[];
}

interface Verdict {
	block: boolean;
	rule?: string;
	path?: string;
	reason?: string;
}

const PATH_TOOLS = new Set(["read", "write", "edit", "grep", "find", "ls"]);
const WRITE_TOOLS = new Set(["write", "edit"]);
const NATIVE_TOOLS = new Set(["check", "progress", "readiness"]);
const CODEMODE_TOOL = "codemode";
const CODEMODE_OPTIONS_PREFIX = "// @options:";
const CODEMODE_MAX_OUTPUT_TOKENS = 10_000;
const CODEMODE_MAX_TIMEOUT_MS = 2_147_483_647;
const SPLIT = /[\s'"`;|&()<>=,:{}\[\]$]+/;
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/;

export function loadConfig(file: string | undefined): WorkerConfig {
	if (!file) throw new Error("CODEX_PI_WORKER_CONFIG is not set");
	const data = JSON.parse(fs.readFileSync(file, "utf8"));
	for (const key of ["task", "worktree", "checksDir", "toolsDir", "python", "roundDir", "repo"]) {
		if (typeof data[key] !== "string" || !data[key]) throw new Error(`worker config field ${key} is missing`);
	}
	for (const key of ["forbiddenRoots", "allowedRoots"]) {
		if (!Array.isArray(data[key]) || data[key].some((entry: unknown) => typeof entry !== "string")) {
			throw new Error(`worker config field ${key} is invalid`);
		}
	}
	for (const key of ["bashDefaultTimeoutSeconds", "bashCeilingSeconds", "checkTimeoutSeconds"]) {
		if (typeof data[key] !== "number" || !Number.isFinite(data[key]) || data[key] <= 0) {
			throw new Error(`worker config field ${key} is invalid`);
		}
	}
	if (!Number.isInteger(data.round) || data.round < 1) throw new Error("worker config round is invalid");
	if (data.deadlinePath !== undefined
		&& (typeof data.deadlinePath !== "string" || data.deadlinePath.length === 0)) {
		throw new Error("worker config field deadlinePath is invalid");
	}
	if (data.acceptanceItems !== undefined) {
		if (!Array.isArray(data.acceptanceItems)) throw new Error("worker config field acceptanceItems is invalid");
		for (const item of data.acceptanceItems) {
			if (typeof item !== "object" || item === null || typeof item.id !== "string" || !ID_RE.test(item.id)
				|| typeof item.command !== "string" || item.command.length === 0) {
				throw new Error("worker config acceptanceItems entry is invalid");
			}
			const entry = item as ContractItem;
			if (entry.checkId !== undefined && (typeof entry.checkId !== "string" || !ID_RE.test(entry.checkId))) {
				throw new Error("worker config acceptanceItems checkId is invalid");
			}
			if (entry.targetedCommand !== undefined
				&& (typeof entry.targetedCommand !== "string" || entry.targetedCommand.length === 0)) {
				throw new Error("worker config acceptanceItems targetedCommand is invalid");
			}
			if (entry.estimatedSeconds !== undefined
				&& (typeof entry.estimatedSeconds !== "number" || !Number.isFinite(entry.estimatedSeconds)
					|| entry.estimatedSeconds <= 0)) {
				throw new Error("worker config acceptanceItems estimatedSeconds is invalid");
			}
		}
	}
	return data as WorkerConfig;
}

/** Same result as Python's os.path.realpath for a possibly non-existent path. */
export function realpath(input: string, depth = 0): string {
	if (depth > 40) throw new Error("too many symlink levels");
	const parts = path.resolve("/", input).split("/");
	let current = "/";
	for (const part of parts) {
		if (!part || part === ".") continue;
		if (part === "..") {
			current = path.dirname(current);
			continue;
		}
		const next = path.join(current, part);
		let link: string | null = null;
		try {
			if (fs.lstatSync(next).isSymbolicLink()) link = fs.readlinkSync(next);
		} catch {
			link = null;
		}
		current = link === null ? next : realpath(path.resolve(current, link), depth + 1);
	}
	return current;
}

function expand(token: string): string {
	if (token === "~") return os.homedir();
	if (token.startsWith("~/")) return path.join(os.homedir(), token.slice(2));
	return token;
}

function resolveToken(token: string, cwd: string): string {
	const expanded = expand(token);
	return realpath(path.isAbsolute(expanded) ? expanded : path.join(cwd, expanded));
}

export function within(candidate: string, root: string): boolean {
	const base = root.replace(/\/+$/, "");
	return candidate === root || candidate === base || candidate.startsWith(base + "/");
}

function realRoots(roots: string[]): string[] {
	return roots.map((root) => realpath(root));
}

/** First path in `text` that resolves into a forbidden root and outside the allowed roots. */
export function forbiddenReference(text: string, cfg: WorkerConfig): string | null {
	const forbidden = realRoots(cfg.forbiddenRoots);
	const allowed = realRoots(cfg.allowedRoots);
	for (const token of text.split(SPLIT)) {
		if (!token.includes("/") && token !== "~" && token !== ".." && token !== ".") continue;
		const resolved = resolveToken(token, cfg.worktree);
		if (allowed.some((root) => within(resolved, root))) continue;
		if (forbidden.some((root) => within(resolved, root))) return resolved;
	}
	return null;
}

function forbiddenVerdict(found: string, cfg: WorkerConfig): Verdict {
	return {
		block: true,
		rule: "forbidden-path",
		path: found,
		reason: `forbidden-path: ${found} is outside this task's worktree ${cfg.worktree}; work only inside it`,
	};
}

function isSafeInteger(value: unknown): value is number {
	return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

/**
 * Upstream-compatible `// @options:` parsing with the worker's bounds applied: the phase command
 * cap is the deadline ceiling and an omitted deadline gets the bounded bash default. The source is
 * never scanned for forbidden strings; every nested call passes the guard on its own.
 */
export function normalizeCodemode(code: string, cfg: WorkerConfig): string {
	if (code.trim() === "") throw new Error("expected non-empty JavaScript source");
	const newline = code.indexOf("\n");
	const firstLine = (newline === -1 ? code : code.slice(0, newline)).replace(/\r$/, "");
	const trimmed = firstLine.trimStart();
	let body = code;
	let requestedTimeout: number | undefined;
	let requestedOutput: number | undefined;
	if (trimmed.startsWith(CODEMODE_OPTIONS_PREFIX)) {
		if (newline === -1 || code.slice(newline).trim() === "") {
			throw new Error("the @options line must be followed by JavaScript source");
		}
		body = code.slice(newline).replace(/^\r?\n/, "");
		const raw = trimmed.slice(CODEMODE_OPTIONS_PREFIX.length).trim();
		if (!raw) throw new Error("@options must be a JSON object");
		let parsed: unknown;
		try {
			parsed = JSON.parse(raw);
		} catch (error) {
			throw new Error(`@options must be valid JSON: ${String(error)}`);
		}
		if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
			throw new Error("@options must be a JSON object");
		}
		for (const key of Object.keys(parsed)) {
			if (key !== "max_output_tokens" && key !== "timeout_ms") {
				throw new Error(`@options does not support ${key}`);
			}
		}
		const fields = parsed as { max_output_tokens?: unknown; timeout_ms?: unknown };
		if (fields.max_output_tokens !== undefined) {
			if (!isSafeInteger(fields.max_output_tokens)) {
				throw new Error("@options max_output_tokens must be a non-negative safe integer");
			}
			requestedOutput = fields.max_output_tokens;
		}
		if (fields.timeout_ms !== undefined) {
			if (!isSafeInteger(fields.timeout_ms) || fields.timeout_ms === 0 || fields.timeout_ms > CODEMODE_MAX_TIMEOUT_MS) {
				throw new Error(`@options timeout_ms must be a positive integer up to ${CODEMODE_MAX_TIMEOUT_MS}`);
			}
			requestedTimeout = fields.timeout_ms;
		}
	}
	const ceilingMs = Math.min(Math.floor(cfg.bashCeilingSeconds * 1000), CODEMODE_MAX_TIMEOUT_MS);
	const defaultMs = Math.min(Math.floor(cfg.bashDefaultTimeoutSeconds * 1000), ceilingMs);
	const timeoutMs = Math.min(requestedTimeout ?? defaultMs, ceilingMs);
	const maxOutputTokens = Math.min(requestedOutput ?? CODEMODE_MAX_OUTPUT_TOKENS, CODEMODE_MAX_OUTPUT_TOKENS);
	return `${CODEMODE_OPTIONS_PREFIX} ${JSON.stringify({ max_output_tokens: maxOutputTokens, timeout_ms: timeoutMs })}\n${body}`;
}

export function codemodeTimeoutMs(code: string): number | null {
	const first = code.split("\n", 1)[0].trimStart();
	if (!first.startsWith(CODEMODE_OPTIONS_PREFIX)) return null;
	try {
		const parsed = JSON.parse(first.slice(CODEMODE_OPTIONS_PREFIX.length).trim());
		if (parsed && typeof parsed === "object" && typeof (parsed as { timeout_ms?: unknown }).timeout_ms === "number"
			&& Number.isFinite((parsed as { timeout_ms: number }).timeout_ms)
			&& (parsed as { timeout_ms: number }).timeout_ms > 0) {
			return (parsed as { timeout_ms: number }).timeout_ms;
		}
	} catch {
		// normalizeCodemode always emits valid JSON; an unreadable value stays unknown
	}
	return null;
}

/** Decide one tool call. Throws on anything it cannot decide (the caller blocks). */
export function judge(toolName: string, input: Record<string, unknown>, cfg: WorkerConfig,
	nested = false): Verdict {
	if (toolName === CODEMODE_TOOL) {
		if (nested) return invalid(toolName, "a codemode script cannot call codemode");
		if (typeof input.code !== "string") return invalid(toolName, "codemode code is not a string");
		try {
			input.code = normalizeCodemode(input.code, cfg);
		} catch (error) {
			return invalid(toolName, `invalid codemode source: ${String(error)}`);
		}
		return { block: false };
	}
	if (toolName === "bash") {
		if (typeof input.command !== "string") return invalid(toolName, "bash command is not a string");
		const found = forbiddenReference(input.command, cfg);
		if (found) return forbiddenVerdict(found, cfg);
		const timeout = input.timeout;
		if (typeof timeout !== "number" || !Number.isFinite(timeout) || timeout <= 0) {
			input.timeout = cfg.bashDefaultTimeoutSeconds;
		} else if (timeout > cfg.bashCeilingSeconds) {
			input.timeout = cfg.bashCeilingSeconds;
		}
		return { block: false };
	}
	if (PATH_TOOLS.has(toolName)) {
		const raw = input.path;
		if (raw === undefined && (toolName === "grep" || toolName === "find" || toolName === "ls")) {
			return { block: false };
		}
		if (typeof raw !== "string" || !raw) return invalid(toolName, `${toolName} path is missing`);
		const resolved = resolveToken(raw, cfg.worktree);
		const allowed = realRoots(cfg.allowedRoots);
		const inside = allowed.some((root) => within(resolved, root));
		if (!inside && realRoots(cfg.forbiddenRoots).some((root) => within(resolved, root))) {
			return forbiddenVerdict(resolved, cfg);
		}
		if (WRITE_TOOLS.has(toolName) && !inside) {
			return {
				block: true,
				rule: "outside-worktree",
				path: resolved,
				reason: `outside-worktree: ${resolved} is not inside this task's worktree ${cfg.worktree} or its tools/checks directories`,
			};
		}
		return { block: false };
	}
	if (NATIVE_TOOLS.has(toolName)) {
		for (const key of ["command", "watchPath"]) {
			const value = input[key];
			if (typeof value === "string") {
				const found = forbiddenReference(value, cfg);
				if (found) return forbiddenVerdict(found, cfg);
			}
		}
		return { block: false };
	}
	return invalid(toolName, `tool ${toolName} is not known to the worker guard`);
}

function invalid(toolName: string, detail: string): Verdict {
	return { block: true, rule: "undecidable", path: toolName, reason: `undecidable: ${detail}; blocked` };
}

function logBlock(cfg: WorkerConfig, tool: string, verdict: Verdict): void {
	try {
		const line = JSON.stringify({ at: Date.now() / 1000, tool, rule: verdict.rule, path: verdict.path });
		fs.appendFileSync(path.join(cfg.roundDir, "worker-blocks.jsonl"), line + "\n");
	} catch {
		// The block stands even when the record cannot be written.
	}
}

/** POSIX shlex.split, so a check receipt's argv matches the declared command. */
export function shlexSplit(text: string): string[] {
	const out: string[] = [];
	let token = "";
	let inToken = false;
	let i = 0;
	while (i < text.length) {
		const ch = text[i];
		if (ch === "'") {
			const end = text.indexOf("'", i + 1);
			if (end < 0) throw new Error("unterminated single quote");
			token += text.slice(i + 1, end);
			inToken = true;
			i = end + 1;
		} else if (ch === '"') {
			i += 1;
			let closed = false;
			while (i < text.length) {
				const c = text[i];
				if (c === '"') {
					closed = true;
					i += 1;
					break;
				}
				if (c === "\\" && i + 1 < text.length && '"\\$`\n'.includes(text[i + 1])) {
					token += text[i + 1];
					i += 2;
				} else {
					token += c;
					i += 1;
				}
			}
			if (!closed) throw new Error("unterminated double quote");
			inToken = true;
		} else if (ch === "\\") {
			if (i + 1 >= text.length) throw new Error("trailing backslash");
			token += text[i + 1];
			inToken = true;
			i += 2;
		} else if (/\s/.test(ch)) {
			if (inToken) out.push(token);
			token = "";
			inToken = false;
			i += 1;
		} else {
			token += ch;
			inToken = true;
			i += 1;
		}
	}
	if (inToken) out.push(token);
	return out;
}

interface RunResult {
	code: number | null;
	stdout: string;
	stderr: string;
	timedOut: boolean;
}

const CHECK_RESERVE_SECONDS = 60;
const CODEMODE_GRACE_SECONDS = 0.5;
const MAX_ROUND_STATE_BYTES = 65_536;
const CLEAN_STATUS_TIMEOUT_MS = 30_000;

function positiveFinite(value: unknown): number | null {
	return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

function roundSeconds(value: number): number {
	return Math.round(value * 1000) / 1000;
}

/** Report an allowed window without overstating it. */
function floorSeconds(value: number): number {
	return Math.floor(value * 1000) / 1000;
}

/** Contract items whose declared command normalizes to exactly the requested argv. */
function matchingItems(cfg: WorkerConfig, argv: string[]): ContractItem[] {
	if (!Array.isArray(cfg.acceptanceItems)) return [];
	const matches: ContractItem[] = [];
	for (const item of cfg.acceptanceItems) {
		let declared: string[];
		try {
			declared = shlexSplit(item.command);
		} catch {
			continue; // an unparseable contract command cannot match a parsed argv
		}
		if (declared.length === argv.length && declared.every((token, index) => token === argv[index])) {
			matches.push(item);
		}
	}
	return matches;
}

/** The single targeted-command suggestion for one normalized argv, or a contradiction. */
function targetedAdvice(matches: ContractItem[]): { targeted: string | null; problem: string | null } {
	let targeted: string | null = null;
	for (const item of matches) {
		if (item.targetedCommand === undefined) continue;
		if (targeted !== null && targeted !== item.targetedCommand) {
			return { targeted: null, problem: "the matched items declare contradictory targetedCommand values" };
		}
		targeted = item.targetedCommand;
	}
	return { targeted, problem: null };
}

/** Largest declared estimate across the matched items; a malformed entry fails closed. */
function contractEstimate(matches: ContractItem[]): { value: number | null; problem: string | null } {
	let value: number | null = null;
	for (const item of matches) {
		if (item.estimatedSeconds === undefined) continue;
		const candidate = positiveFinite(item.estimatedSeconds);
		if (candidate === null) return { value: null, problem: `item ${item.id}` };
		if (value === null || candidate > value) value = candidate;
	}
	return { value, problem: null };
}

/** The real round deadline, re-read from round.state.json before every admitted check. */
function readBudgetState(cfg: WorkerConfig): { ok: true; remainingSeconds: number } | { ok: false; reason: string } {
	const deadlinePath = cfg.deadlinePath as string;
	let raw: string;
	try {
		const stat = fs.statSync(deadlinePath);
		if (!stat.isFile() || stat.size > MAX_ROUND_STATE_BYTES) {
			return { ok: false, reason: "budget_unknown: the round state is not a bounded regular file" };
		}
		raw = fs.readFileSync(deadlinePath, "utf8");
	} catch (error) {
		return { ok: false, reason: `budget_unknown: cannot read the round state: ${String(error)}` };
	}
	let state: Record<string, unknown>;
	try {
		state = JSON.parse(raw);
	} catch {
		return { ok: false, reason: "budget_unknown: the round state is not valid JSON" };
	}
	if (!state || typeof state !== "object") {
		return { ok: false, reason: "budget_unknown: the round state is not an object" };
	}
	if (state.round !== cfg.round) {
		return { ok: false, reason: `budget_unknown: the round state identifies round ${String(state.round)}, not ${cfg.round}` };
	}
	const taskDir = path.resolve(path.join(cfg.roundDir, "..", ".."));
	if (typeof state.taskDir !== "string" || path.resolve(state.taskDir) !== taskDir) {
		return { ok: false, reason: "budget_unknown: the round state task identity is missing or does not match" };
	}
	const deadlineAt = state.deadlineAt;
	if (typeof deadlineAt !== "number" || !Number.isFinite(deadlineAt) || deadlineAt <= 0) {
		return { ok: false, reason: "budget_unknown: the round state deadlineAt is missing or not a finite number" };
	}
	return { ok: true, remainingSeconds: Math.max(0, deadlineAt - Date.now() / 1000) };
}

async function worktreeClean(cfg: WorkerConfig, signal?: AbortSignal): Promise<{ ok: boolean; reason?: string }> {
	const result = await run("git", ["status", "--porcelain"], cfg.worktree, signal, CLEAN_STATUS_TIMEOUT_MS);
	if (result.timedOut || result.code === null || result.code !== 0) {
		return { ok: false, reason: `cannot verify the worktree is clean (${result.timedOut ? "git status timed out" : `exit ${result.code}`})` };
	}
	if (result.stdout.trim() !== "") {
		return { ok: false, reason: "the worktree has uncommitted or untracked changes; commit the final candidate first" };
	}
	return { ok: true };
}

function run(cmd: string, args: string[], cwd: string, signal?: AbortSignal, limitMs = 0): Promise<RunResult> {
	return new Promise((resolve) => {
		let stdout = "";
		let stderr = "";
		let timedOut = false;
		const child = spawn(cmd, args, { cwd, env: process.env, stdio: ["ignore", "pipe", "pipe"] });
		const timer = limitMs > 0 ? setTimeout(() => ((timedOut = true), child.kill("SIGTERM")), limitMs) : null;
		const abort = () => child.kill("SIGTERM");
		signal?.addEventListener("abort", abort, { once: true });
		child.stdout.on("data", (chunk) => (stdout += chunk));
		child.stderr.on("data", (chunk) => (stderr += chunk));
		child.on("error", (error) => {
			if (timer) clearTimeout(timer);
			resolve({ code: null, stdout, stderr: stderr + String(error), timedOut });
		});
		child.on("close", (code) => {
			if (timer) clearTimeout(timer);
			signal?.removeEventListener("abort", abort);
			resolve({ code, stdout, stderr, timedOut });
		});
	});
}

function lastJson(text: string): Record<string, any> | null {
	const lines = text.split("\n").filter((line) => line.trim());
	for (let i = lines.length - 1; i >= 0; i--) {
		try {
			const value = JSON.parse(lines[i]);
			if (value && typeof value === "object") return value;
		} catch {
			// keep looking
		}
	}
	return null;
}

function textResult(text: string, details: unknown, structured: unknown, isError = false) {
	return { content: [{ type: "text" as const, text }], details: details as never,
		structuredContent: structured as never, ...(isError ? { isError: true } : {}) };
}

function checkStructured(id: string, ok: boolean, patch: Record<string, unknown> = {}) {
	return { id, ok, exit_code: null, timed_out: false, cancelled: false, receipt: null,
		test_counts: null, log_tail: null, error: null, ...patch };
}

/** Structured refusal: no child, no receipt, no fake success evidence. */
function refuseCheck(id: string, reason: string, patch: Record<string, unknown> = {}) {
	return textResult(`check ${id}: refused ${reason}`, {}, checkStructured(id, false,
		{ receipt: null, error: reason, reason, ...patch }), true);
}

const CHECK_OUTPUT_SCHEMA = {
	type: "object",
	properties: {
		id: { type: "string" }, ok: { type: "boolean" },
		exit_code: { type: ["integer", "null"] }, timed_out: { type: "boolean" },
		cancelled: { type: "boolean" }, receipt: { type: ["string", "null"] },
		test_counts: { type: ["object", "null"] }, log_tail: { type: ["string", "null"] },
		error: { type: ["string", "null"] },
		reason: { type: ["string", "null"] },
		remainingSeconds: { type: ["number", "null"] },
		requiredSeconds: { type: ["number", "null"] },
		reserveSeconds: { type: ["number", "null"] },
		allowedSeconds: { type: ["number", "null"] },
		elapsedSeconds: { type: ["number", "null"] },
		estimateSource: { type: ["string", "null"] },
		targetedCommand: { type: ["string", "null"] },
		codemodeRemainingSeconds: { type: ["number", "null"] },
	},
	required: ["id", "ok", "exit_code", "timed_out", "cancelled"],
} as never;

const PROGRESS_OUTPUT_SCHEMA = {
	type: "object",
	properties: {
		ok: { type: "boolean" }, activity: { type: "string" }, code: { type: ["integer", "null"] },
		output: { type: ["object", "null"] }, error: { type: ["string", "null"] },
	},
	required: ["ok", "activity", "code"],
} as never;

const READINESS_OUTPUT_SCHEMA = {
	type: "object",
	properties: {
		ok: { type: "boolean" }, code: { type: ["integer", "null"] }, status: { type: ["string", "null"] },
		reason: { type: ["string", "null"] }, coverage: { type: ["object", "null"] },
		gaps: { type: ["array", "null"] }, error: { type: ["string", "null"] },
	},
	required: ["ok", "code"],
} as never;

function helperArgs(cfg: WorkerConfig, command: string): string[] {
	return [path.join(cfg.toolsDir, "pi_task.py"), command, "--repo", cfg.repo, "--task", cfg.task, "--round", String(cfg.round)];
}

function writeReady(cfg: WorkerConfig, version: string): void {
	const target = path.join(cfg.roundDir, "worker.ready");
	const temp = `${target}.${process.pid}.tmp`;
	fs.writeFileSync(temp, JSON.stringify({ piVersion: version, pid: process.pid,
		at: Date.now() / 1000, codemode: true }));
	fs.renameSync(temp, target);
}

export default async function (pi: ExtensionAPI) {
	let cfg: WorkerConfig | null = null;
	const codemodeDeadlines = new Map<string, number>();
	try {
		// The load proof is written only after the native codemode tool registered: a Pi without
		// the public export, or a failing registration, must not let the round claim it loaded.
		cfg = loadConfig(process.env.CODEX_PI_WORKER_CONFIG);
		const host = await import("@earendil-works/pi-coding-agent");
		host.createCodemodeExtension({ models: false, mode: "on" })(pi);
		writeReady(cfg, typeof host.VERSION === "string" ? host.VERSION : "unknown");
	} catch (error) {
		cfg = null; // no marker: the round fails WORKER_EXTENSION_NOT_LOADED and every call blocks
		void error;
	}

	pi.on("tool_call", async (event) => {
		const toolName = String((event as { toolName?: unknown }).toolName);
		const nested = Boolean((event as { parentToolCallId?: unknown }).parentToolCallId);
		try {
			if (!cfg) return { block: true, reason: "undecidable: worker configuration is unavailable; blocked" };
			const input = (event as { input: Record<string, unknown> }).input;
			const verdict = judge(toolName, input, cfg, nested);
			if (!verdict.block) {
				if (toolName === CODEMODE_TOOL) {
					// The codemode tool_call is the only place the extension's own outer deadline is
					// observable; remember it so a nested check is admitted inside that envelope or
					// refused as unverifiable instead of being killed by the script timeout.
					const timeoutMs = codemodeTimeoutMs(String(input.code));
					if (timeoutMs !== null) {
						codemodeDeadlines.set(String((event as { toolCallId?: unknown }).toolCallId),
							Date.now() + timeoutMs);
						if (codemodeDeadlines.size > 64) {
							const oldest = codemodeDeadlines.keys().next().value;
							if (oldest !== undefined) codemodeDeadlines.delete(oldest);
						}
					}
				}
				return undefined;
			}
			logBlock(cfg, toolName, verdict);
			return { block: true, reason: verdict.reason };
		} catch (error) {
			if (cfg) logBlock(cfg, toolName, { block: true, rule: "guard-error", path: String(error) });
			return { block: true, reason: `undecidable: ${String(error)}; blocked` };
		}
	});

	pi.on("before_agent_start", (event) => {
		if (!cfg) return undefined;
		try {
			event.systemPromptOptions.sections.codex_pi_worker = cfg.contract;
		} catch {
			// the contract is also written to rounds/N/contract.md
		}
		return undefined;
	});

	pi.on("agent_before_settle", async (event, _ctx) => {
		try {
			if (!cfg || !cfg.phase || !cfg.settleQuotaPath || event.outcome !== "completed") return undefined;
			const result = await run(cfg.python, helperArgs(cfg, "readiness"), cfg.worktree, undefined, 60000);
			const readiness = result.code === 0 ? lastJson(result.stdout) : null;
			const settle = readiness?.settle;
			if (!settle || settle.eligible !== true) return undefined;
			try {
				fs.mkdirSync(path.dirname(cfg.settleQuotaPath), { recursive: true });
				const fd = fs.openSync(cfg.settleQuotaPath, "wx");
				fs.writeSync(fd, JSON.stringify({ round: cfg.round, at: Date.now() / 1000 }));
				fs.closeSync(fd);
			} catch {
				return undefined; // quota already used (or unknowable): never continue twice
			}
			const lines = ["Delivery check: required acceptance evidence is still missing for the current candidate.",
				"Run each missing check with the `check` tool, then finish with a short report. Do not change scope."];
			for (const item of settle.missing ?? []) {
				lines.push(`- ${item.id}: ${item.command} (pass: ${item.passCondition})`);
			}
			return {
				continue: true,
				entries: [{ type: "custom_message" as const, customType: "codex-pi-settle", content: lines.join("\n"), display: false }],
			};
		} catch {
			return undefined;
		}
	});

	pi.registerTool({
		name: "check",
		label: "Check",
		description:
			"Run a verification command through the task's frozen pi_check helper and record an immutable receipt. " +
			"Use this for every acceptance check. Returns exit code, test counts and, on failure, the log tail. " +
			"Before spawning, the final effective window is computed from the requested/contract cap, the real round " +
			"deadline minus a 60s wrap-up reserve, and a verifiable codemode outer deadline; a check is refused when " +
			"the estimate (estimatedSeconds, else the declared cap) exceeds that window, and the admitted helper " +
			"timeout is the window itself with fractional seconds kept. A command that matches a contract item with " +
			"targetedCommand is a full acceptance check and requires final:true on a clean worktree. A refusal returns " +
			"structured ok:false with receipt:null and never spawns or writes evidence.",
		promptSnippet: "Run a recorded check (receipt-bound)",
		parameters: {
			type: "object",
			properties: {
				id: { type: "string", description: "Stable check id, letters, digits, '_' or '-'" },
				command: { type: "string", description: "Command line, split like a POSIX shell without running a shell" },
				timeoutSeconds: { type: "number", description: "Wrapper deadline in seconds (clamped to the task cap)" },
				estimatedSeconds: { type: "number", description: "Finite positive planning estimate; never a completion guarantee" },
				final: { type: "boolean", description: "Set true only for a full acceptance command with targetedCommand on a clean worktree" },
				watchPath: { type: "string", description: "Directory whose byte budget is guarded" },
				maxBytes: { type: "number", description: "Byte budget for watchPath (required with it)" },
			},
			required: ["id", "command"],
			additionalProperties: false,
		} as never,
		outputSchema: CHECK_OUTPUT_SCHEMA,
		async execute(toolCallId: string, params: any, signal: AbortSignal | undefined) {
			if (!cfg) {
				const error = "worker configuration is unavailable";
				return textResult(error, {}, checkStructured("", false, { error }), true);
			}
			const checkId = String(params.id);
			if (!ID_RE.test(checkId)) {
				return textResult(`invalid check id ${checkId}`, {}, checkStructured(checkId, false,
					{ error: "invalid check id" }), true);
			}
			let argv: string[];
			try {
				argv = shlexSplit(String(params.command));
			} catch (error) {
				return textResult(`cannot parse command: ${String(error)}`, {}, checkStructured(checkId, false,
					{ error: `cannot parse command: ${String(error)}` }), true);
			}
			if (argv.length === 0) {
				return textResult("empty command", {}, checkStructured(checkId, false, { error: "empty command" }), true);
			}
			let requested = cfg.checkTimeoutSeconds;
			if (typeof params.timeoutSeconds === "number" && Number.isFinite(params.timeoutSeconds) && params.timeoutSeconds > 0) {
				requested = Math.min(params.timeoutSeconds, cfg.checkTimeoutSeconds);
			}
			let callEstimate: number | null = null;
			if (params.estimatedSeconds !== undefined) {
				callEstimate = positiveFinite(params.estimatedSeconds);
				if (callEstimate === null) {
					return refuseCheck(checkId, "invalid_estimate: estimatedSeconds must be a finite positive number");
				}
			}
			if (params.final !== undefined && typeof params.final !== "boolean") {
				return refuseCheck(checkId, "invalid_final: final must be a boolean");
			}
			const matches = matchingItems(cfg, argv);
			const advice = targetedAdvice(matches);
			if (advice.problem !== null) {
				return refuseCheck(checkId, `contract_metadata: ${advice.problem}`);
			}
			if (advice.targeted !== null) {
				if (params.final !== true) {
					return refuseCheck(checkId,
						`final_required: this is a full acceptance command; run the targeted check first (${advice.targeted}) ` +
						"and pass final:true only on the clean final candidate",
						{ targetedCommand: advice.targeted });
				}
				const clean = await worktreeClean(cfg, signal);
				if (!clean.ok) {
					return refuseCheck(checkId, `dirty_final: ${clean.reason}`,
						{ targetedCommand: advice.targeted });
				}
			}
			let timeout = requested;
			let budgetPatch: Record<string, unknown> = {};
			if (typeof cfg.deadlinePath === "string") {
				const state = readBudgetState(cfg);
				if (!state.ok) return refuseCheck(checkId, state.reason);
				const remainingSeconds = state.remainingSeconds;
				const available = remainingSeconds - CHECK_RESERVE_SECONDS;
				const estimate = contractEstimate(matches);
				if (estimate.problem !== null) {
					return refuseCheck(checkId, `invalid_estimate: the contract estimate for ` +
						`${estimate.problem} is not a finite positive number`);
				}
				const values = [callEstimate, estimate.value].filter((value): value is number => value !== null);
				// Without an estimate the effective requested/contract cap is the conservative
				// requirement; phase remaining is a constraint, never an expected duration.
				const requiredSeconds = values.length > 0 ? Math.max(...values) : requested;
				const estimateSource = callEstimate !== null && estimate.value !== null ? "contract+call"
					: callEstimate !== null ? "call" : estimate.value !== null ? "contract" : "timeout";
				// One final effective execution window: the requested/contract cap, the phase
				// remaining minus the fixed wrap-up reserve, and a verifiable codemode outer
				// deadline minus its cleanup grace. The estimate must fit this window, and the
				// admitted helper timeout is the window itself: a fractional window is never
				// rounded down below the requirement.
				let windowSeconds = Math.min(requested, available);
				let outerRemaining: number | null = null;
				const separator = toolCallId.indexOf("/");
				if (separator > 0) {
					// A nested check runs inside the codemode script's outer deadline, which the
					// native context does not expose directly. This extension recorded its own
					// normalized timeout at the parent tool_call; without that record the envelope
					// is unverifiable and the check must run directly instead.
					const outerDeadline = codemodeDeadlines.get(toolCallId.slice(0, separator));
					if (outerDeadline === undefined) {
						return refuseCheck(checkId,
							"codemode_deadline_unknown: a check from codemode has no verifiable outer deadline; " +
							"run it directly with the check tool",
							{ requiredSeconds: roundSeconds(requiredSeconds), reserveSeconds: CHECK_RESERVE_SECONDS });
					}
					outerRemaining = Math.max(0, (outerDeadline - Date.now()) / 1000);
					windowSeconds = Math.min(windowSeconds, outerRemaining - CODEMODE_GRACE_SECONDS);
				}
				const allowedSeconds = Math.max(0, floorSeconds(windowSeconds));
				if (requiredSeconds > windowSeconds) {
					const patch = { remainingSeconds: roundSeconds(remainingSeconds),
						requiredSeconds: roundSeconds(requiredSeconds),
						reserveSeconds: CHECK_RESERVE_SECONDS, allowedSeconds };
					if (requested < requiredSeconds) {
						return refuseCheck(checkId,
							`timeout_below_estimate: the requested or contract window ` +
							`${roundSeconds(requested)}s is below the required ${roundSeconds(requiredSeconds)}s; ` +
							"raise timeoutSeconds only if the estimate is wrong", patch);
					}
					if (available < requiredSeconds) {
						return refuseCheck(checkId,
							`insufficient_budget: ${roundSeconds(remainingSeconds)}s remain minus a ` +
							`${CHECK_RESERVE_SECONDS}s reserve, below the required ${roundSeconds(requiredSeconds)}s`,
							patch);
					}
					return refuseCheck(checkId,
						`codemode_deadline_too_short: ${roundSeconds(outerRemaining ?? 0)}s remain in the codemode ` +
						`script but ${roundSeconds(requiredSeconds)}s are required; run it directly with the check tool`,
						{ ...patch, codemodeRemainingSeconds: roundSeconds(outerRemaining ?? 0) });
				}
				timeout = windowSeconds;
				budgetPatch = { requiredSeconds: roundSeconds(requiredSeconds), estimateSource, allowedSeconds };
			}
			const args = [path.join(cfg.toolsDir, "pi_check.py"), "--output-dir", cfg.checksDir, "--id", checkId,
				"--timeout-seconds", String(timeout)];
			if (params.watchPath !== undefined || params.maxBytes !== undefined) {
				if (typeof params.watchPath !== "string" || typeof params.maxBytes !== "number") {
					return textResult("watchPath and maxBytes must be given together", {}, checkStructured(checkId, false,
						{ error: "watchPath and maxBytes must be given together" }), true);
				}
				args.push("--watch-path", params.watchPath, "--max-bytes", String(Math.trunc(params.maxBytes)));
			}
			args.push("--", ...argv);
			const startedAt = Date.now();
			const result = await run(cfg.python, args, cfg.worktree, signal);
			const elapsedSeconds = roundSeconds((Date.now() - startedAt) / 1000);
			const summary = lastJson(result.stdout);
			if (!summary) {
				const error = `pi_check produced no receipt summary (exit ${result.code}): ${result.stderr.slice(-500)}`;
				return textResult(error, { code: result.code },
					checkStructured(checkId, false, { exit_code: result.code, error, elapsedSeconds }), true);
			}
			const failed = summary.exit_code !== 0 || summary.timed_out === true || summary.cancelled === true;
			const structured = checkStructured(checkId, !failed, {
				exit_code: typeof summary.exit_code === "number" ? summary.exit_code : null,
				timed_out: summary.timed_out === true, cancelled: summary.cancelled === true,
				receipt: typeof summary.receipt === "string" ? summary.receipt : null,
				test_counts: summary.test_counts && typeof summary.test_counts === "object" ? summary.test_counts : null,
				log_tail: typeof summary.log_tail === "string" ? summary.log_tail : null,
				error: failed ? (summary.timed_out ? "timed_out" : summary.cancelled ? "cancelled" : `exit ${summary.exit_code}`) : null,
				elapsedSeconds,
				...budgetPatch,
			});
			const counts = summary.test_counts && typeof summary.test_counts === "object"
				? ` counts=${JSON.stringify(summary.test_counts)}` : "";
			const lines = [`check ${params.id}: exit=${summary.exit_code}${summary.timed_out ? " TIMED_OUT" : ""}${summary.cancelled ? " CANCELLED" : ""}${counts} elapsed=${elapsedSeconds}s`,
				`receipt=${summary.receipt}`];
			if (typeof summary.log_tail === "string") lines.push("log_tail:", summary.log_tail);
			return textResult(lines.join("\n"), summary, structured, failed);
		},
	});

	pi.registerTool({
		name: "progress",
		label: "Progress",
		description: "Record a short structured progress report (self-report only; never acceptance, never queued).",
		promptSnippet: "Record structured progress",
		parameters: {
			type: "object",
			properties: {
				activity: { type: "string", enum: ["implementing", "checking", "repairing", "blocked"] },
				step: { type: "string" },
				next: { type: "string" },
				blocker: { type: "string" },
				completedCriteria: { type: "array", items: { type: "string" } },
				evidenceRefs: { type: "array", items: { type: "string" } },
			},
			required: ["activity"],
			additionalProperties: false,
		} as never,
		outputSchema: PROGRESS_OUTPUT_SCHEMA,
		async execute(_id: string, params: any, signal: AbortSignal | undefined) {
			const activity = String(params.activity);
			if (!cfg) {
				const error = "worker configuration is unavailable";
				return textResult(error, {}, { ok: false, activity, code: null, output: null, error }, true);
			}
			const args = [...helperArgs(cfg, "progress"), "--activity", activity];
			for (const [flag, key] of [["--step", "step"], ["--next", "next"], ["--blocker", "blocker"]] as const) {
				if (typeof params[key] === "string") args.push(flag, params[key]);
			}
			for (const item of Array.isArray(params.completedCriteria) ? params.completedCriteria : []) args.push("--completed-criteria", String(item));
			for (const item of Array.isArray(params.evidenceRefs) ? params.evidenceRefs : []) args.push("--evidence-ref", String(item));
			const result = await run(cfg.python, args, cfg.worktree, signal, 60000);
			const text = (result.code === 0 ? result.stdout : (result.stderr || result.stdout)).trim();
			const output = result.code === 0 ? lastJson(result.stdout) : null;
			const structured = { ok: result.code === 0, activity, code: result.code, output,
				error: result.code === 0 ? null : (text.slice(-500) || `exit ${result.code}`) };
			return textResult(text, { code: result.code }, structured, result.code !== 0);
		},
	});

	pi.registerTool({
		name: "readiness",
		label: "Readiness",
		description: "Mechanical delivery check of the phase contract against recorded receipts (read-only; never acceptance).",
		promptSnippet: "Check delivery readiness",
		parameters: { type: "object", properties: {}, additionalProperties: false } as never,
		outputSchema: READINESS_OUTPUT_SCHEMA,
		async execute(_id: string, _params: any, signal: AbortSignal | undefined) {
			if (!cfg) {
				const error = "worker configuration is unavailable";
				return textResult(error, {}, { ok: false, code: null, status: null, reason: null, coverage: null, gaps: null, error }, true);
			}
			const result = await run(cfg.python, helperArgs(cfg, "readiness"), cfg.worktree, signal, 60000);
			const data = result.code === 0 ? lastJson(result.stdout) : null;
			if (!data) {
				const error = (result.stderr || result.stdout).trim();
				return textResult(error, { code: result.code },
					{ ok: false, code: result.code, status: null, reason: null, coverage: null, gaps: null,
						error: error.slice(-500) || "readiness unavailable" }, true);
			}
			const gaps = (data.gaps ?? []).map((gap: any) => `${gap.id}:${gap.status}`).join(", ");
			const structured = { ok: true, code: 0, status: data.status ?? null,
				reason: data.readinessReason ?? null, coverage: data.coverage ?? null,
				gaps: data.gaps ?? null, error: null };
			return textResult(`readiness=${data.status} reason=${data.readinessReason} coverage=${JSON.stringify(data.coverage)}${gaps ? ` gaps=${gaps}` : ""}`,
				{ status: data.status, coverage: data.coverage, gaps: data.gaps }, structured);
		},
	});
}
