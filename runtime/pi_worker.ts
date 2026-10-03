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

/** Optional per-item resource declaration; absent always means exclusive. */
interface CheckResources {
	parallelSafe?: boolean;
	cpuSlots?: number;
	memoryMiB?: number;
	exclusiveKeys?: string[];
}

/** Optional per-phase permit-pool declaration; absent means one check at a time. */
interface CheckExecution {
	maxConcurrent?: number;
	cpuSlots?: number;
	memoryMiB?: number;
}

interface ContractItem {
	id: string;
	checkId?: string;
	command: string;
	targetedCommand?: string;
	estimatedSeconds?: number;
	checkResources?: CheckResources;
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
	/** Optional per-worker check concurrency/resource bounds. */
	checkExecution?: CheckExecution;
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
			if (entry.checkResources !== undefined) {
				const resources = entry.checkResources;
				if (typeof resources !== "object" || resources === null || Array.isArray(resources)) {
					throw new Error("worker config acceptanceItems checkResources is invalid");
				}
				for (const key of Object.keys(resources)) {
					if (!["parallelSafe", "cpuSlots", "memoryMiB", "exclusiveKeys"].includes(key)) {
						throw new Error(`worker config acceptanceItems checkResources has unsupported key ${key}`);
					}
				}
				if (Object.keys(resources).length === 0) {
					throw new Error("worker config acceptanceItems checkResources must declare at least one bound");
				}
				if (resources.parallelSafe !== undefined && typeof resources.parallelSafe !== "boolean") {
					throw new Error("worker config acceptanceItems checkResources parallelSafe is invalid");
				}
				if (resources.cpuSlots !== undefined
					&& (!Number.isSafeInteger(resources.cpuSlots) || resources.cpuSlots <= 0)) {
					throw new Error("worker config acceptanceItems checkResources cpuSlots is invalid");
				}
				if (resources.memoryMiB !== undefined && positiveFinite(resources.memoryMiB) === null) {
					throw new Error("worker config acceptanceItems checkResources memoryMiB is invalid");
				}
				if (resources.exclusiveKeys !== undefined) {
					if (!Array.isArray(resources.exclusiveKeys) || resources.exclusiveKeys.length > 16
						|| resources.exclusiveKeys.some((key: unknown) => typeof key !== "string"
							|| key.trim() === "" || key.length > 64)) {
						throw new Error("worker config acceptanceItems checkResources exclusiveKeys is invalid");
					}
				}
			}
		}
	}
	if (data.checkExecution !== undefined) {
		const execution = data.checkExecution;
		if (typeof execution !== "object" || execution === null || Array.isArray(execution)) {
			throw new Error("worker config checkExecution is invalid");
		}
		for (const key of Object.keys(execution)) {
			if (!["maxConcurrent", "cpuSlots", "memoryMiB"].includes(key)) {
				throw new Error(`worker config checkExecution has unsupported key ${key}`);
			}
		}
		if (Object.keys(execution).length === 0) {
			throw new Error("worker config checkExecution must declare at least one bound");
		}
		if (execution.maxConcurrent !== undefined
			&& (!Number.isSafeInteger(execution.maxConcurrent) || execution.maxConcurrent <= 0
				|| execution.maxConcurrent > 4)) {
			throw new Error("worker config checkExecution maxConcurrent is invalid (1..4)");
		}
		if (execution.cpuSlots !== undefined
			&& (!Number.isSafeInteger(execution.cpuSlots) || execution.cpuSlots <= 0)) {
			throw new Error("worker config checkExecution cpuSlots is invalid");
		}
		if (execution.memoryMiB !== undefined && positiveFinite(execution.memoryMiB) === null) {
			throw new Error("worker config checkExecution memoryMiB is invalid");
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

/** Shared admission calculation, used before queueing and again after all waits. */
function checkWindow(cfg: WorkerConfig, requested: number, required: number,
    outerDeadline: number | null, estimateSource: string) {
    let remaining: number | null = null;
    let latestStart = Date.now() + requested * 1000;
    if (cfg.deadlinePath) {
        const state = readBudgetState(cfg);
        if (!state.ok) return {timeout:0, latestStart:0, patch:{}, reason:state.reason};
        remaining = state.remainingSeconds;
        latestStart = Date.now() + (remaining - CHECK_RESERVE_SECONDS - required) * 1000;
    }
    const outer = outerDeadline === null ? null : Math.max(0, (outerDeadline-Date.now())/1000);
    if (outerDeadline !== null) latestStart = Math.min(latestStart, outerDeadline - (CODEMODE_GRACE_SECONDS+required)*1000);
    const window = Math.min(requested, remaining === null ? Infinity : remaining-CHECK_RESERVE_SECONDS,
        outer === null ? Infinity : outer-CODEMODE_GRACE_SECONDS);
    const patch: Record<string, unknown> = cfg.deadlinePath ? {remainingSeconds:roundSeconds(remaining!),
        requiredSeconds:required, reserveSeconds:CHECK_RESERVE_SECONDS, allowedSeconds:Math.max(0,floorSeconds(window)), estimateSource} : {};
    if (outer !== null) patch.codemodeRemainingSeconds = roundSeconds(outer);
    const reason = required <= window ? null : requested < required ? "timeout_below_estimate: effective command cap is below estimate; adjust timeoutSeconds only within the authorized cap to cover a trusted estimate, or correct the estimate only when evidence supports it"
        : remaining !== null && remaining-CHECK_RESERVE_SECONDS < required ? "insufficient_budget: round reserve leaves too little time"
        : "codemode_deadline_too_short: run this check directly or use a sufficient script deadline";
    return {timeout:window, latestStart, patch, reason};
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
		if (signal?.aborted) { resolve({code:null, stdout:"", stderr:"cancelled", timedOut:false}); return; }
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

/** Soft in-process permit pool shared by every check of this worker process.
 *
 * It never persists, never touches the board and never coordinates across
 * worker processes. A permit is accounting for declared estimates only: CPU
 * slots are a concurrency estimate and memoryMiB is an admission estimate, not
 * an OS affinity or a hard memory cap. Unknown or undeclared checks are
 * exclusive; parallel admission requires an explicit ``parallelSafe``
 * declaration plus a free count/CPU/memory/shared-key combination. The queue
 * is strictly FIFO so a waiting exclusive check cannot be starved by later
 * parallel work, and any cancellation releases the permit it holds.
 */
interface CheckRequirement {
	parallelSafe: boolean;
	cpuSlots: number;
	memoryMiB: number;
	exclusiveKeys: string[];
	declaration: CheckResources | null;
}

interface PoolLimits {
	maxConcurrent: number;
	cpuSlots: number;
	memoryMiB: number | null;
}

interface AcquireResult {
	granted: boolean;
	cancelled: boolean;
	expired?: boolean;
	queueSeconds: number;
}

function poolLimits(cfg: WorkerConfig): PoolLimits {
	const execution = cfg.checkExecution;
	const maxConcurrent = execution?.maxConcurrent ?? 1;
	return { maxConcurrent, cpuSlots: Math.min(execution?.cpuSlots ?? os.availableParallelism(), os.availableParallelism()),
		memoryMiB: execution?.memoryMiB ?? null };
}

/** An undeclared or non-parallel-safe check holds the whole pool: one at a time. */
function exclusiveRequirement(cfg: WorkerConfig, declaration: CheckResources | null): CheckRequirement {
	const limits = poolLimits(cfg);
	return { parallelSafe: false, cpuSlots: limits.cpuSlots, memoryMiB: limits.memoryMiB ?? 0,
		exclusiveKeys: declaration?.exclusiveKeys ?? [],
		declaration };
}

/**
 * Resolve the frozen resource declaration for one normalized argv. Contract
 * validation already rejects contradictory same-argv metadata; if a run-time
 * config still carries a contradiction the check is refused instead of guessed.
 * The tool call itself cannot add or lower requirements.
 */
function resolveCheckRequirement(cfg: WorkerConfig, matches: ContractItem[]):
	{ req: CheckRequirement; problem: string | null } {
	const declarations = matches.map((item) => item.checkResources)
		.filter((entry): entry is CheckResources => entry !== undefined);
	if (declarations.length === 0) {
		return { req: exclusiveRequirement(cfg, null), problem: null };
	}
	if (declarations.length !== matches.length) {
		return { req: exclusiveRequirement(cfg, null),
			problem: "some matching acceptance items declare checkResources and others do not" };
	}
	if (new Set(declarations.map((entry) => JSON.stringify(entry))).size > 1) {
		return { req: exclusiveRequirement(cfg, null),
			problem: "matching acceptance items declare contradictory checkResources" };
	}
	const declaration = declarations[0];
	if (declaration.parallelSafe !== true || declaration.cpuSlots === undefined
		|| declaration.memoryMiB === undefined || poolLimits(cfg).memoryMiB === null) {
		return { req: exclusiveRequirement(cfg, declaration), problem: null };
	}
	const cpuSlots = declaration.cpuSlots ?? 1;
	const memoryMiB = declaration.memoryMiB ?? 0;
	const exclusiveKeys = declaration.exclusiveKeys ?? [];
	return { req: { parallelSafe: true, cpuSlots, memoryMiB, exclusiveKeys,
		declaration }, problem: null };
}

/** Immediate structured refusal when declared needs cannot ever fit the pool. */
function resourceCapacityProblem(cfg: WorkerConfig, declaration: CheckResources | null): string | null {
	if (declaration === null) return null;
	const limits = poolLimits(cfg);
	if (declaration.cpuSlots !== undefined && declaration.cpuSlots > limits.cpuSlots) {
		return `resource_exceeds_pool: declared cpuSlots ${declaration.cpuSlots} exceed the pool capacity `
			+ `${limits.cpuSlots}; this check would never be admitted`;
	}
	if (declaration.memoryMiB !== undefined && limits.memoryMiB !== null
		&& declaration.memoryMiB > limits.memoryMiB) {
		return `resource_exceeds_pool: declared memoryMiB ${declaration.memoryMiB} exceed the pool budget `
			+ `${limits.memoryMiB}; this check would never be admitted`;
	}
	return null;
}

interface PoolWaiter {
	req: CheckRequirement;
	resolve: (result: AcquireResult) => void;
	granted: boolean;
	cancelled: boolean;
	enqueuedAt: number;
	removeAbort: (() => void) | null;
	timer: ReturnType<typeof setTimeout> | null;
}

class CheckPool {
	private readonly limits: PoolLimits;
	private readonly running: CheckRequirement[] = [];
	private readonly waiters: PoolWaiter[] = [];
	private usedCpu = 0;
	private usedMemory = 0;
	private readonly keys = new Map<string, number>();

	constructor(limits: PoolLimits) {
		this.limits = limits;
	}

	snapshot(): Record<string, unknown> {
		return {
			maxConcurrent: this.limits.maxConcurrent,
			cpuSlots: this.limits.cpuSlots,
			memoryMiB: this.limits.memoryMiB,
			running: this.running.length,
			queued: this.waiters.length,
			usedCpu: this.usedCpu,
			usedMemory: this.usedMemory,
		};
	}

	acquire(req: CheckRequirement, signal: AbortSignal | undefined, latestStart: number): Promise<AcquireResult> {
		if (Date.now() >= latestStart) return Promise.resolve({granted:false, cancelled:false, expired:true, queueSeconds:0});
		if (signal?.aborted) {
			return Promise.resolve({ granted: false, cancelled: true, queueSeconds: 0 });
		}
		if (this.waiters.length === 0 && this.canGrant(req)) {
			this.startRunning(req);
			return Promise.resolve({ granted: true, cancelled: false, queueSeconds: 0 });
		}
		return new Promise<AcquireResult>((resolve) => {
			const waiter: PoolWaiter = { req, resolve, granted: false, cancelled: false,
				enqueuedAt: Date.now(), removeAbort: null, timer: null };
			if (signal) {
				const onAbort = () => {
					waiter.cancelled = true;
					if (waiter.granted) return; // already admitted; run() stops the owned child
					this.finish(waiter, { granted: false, cancelled: true,
						queueSeconds: roundSeconds((Date.now() - waiter.enqueuedAt) / 1000) });
				};
				signal.addEventListener("abort", onAbort, { once: true });
				waiter.removeAbort = () => signal.removeEventListener("abort", onAbort);
			}
			waiter.timer = setTimeout(() => this.finish(waiter, {granted:false, cancelled:false, expired:true,
				queueSeconds: roundSeconds((Date.now() - waiter.enqueuedAt)/1000)}), Math.min(latestStart-Date.now(), CODEMODE_MAX_TIMEOUT_MS));
			this.waiters.push(waiter);
			this.pump();
		});
	}

	release(req: CheckRequirement): void {
		const index = this.running.indexOf(req);
		if (index < 0) return;
		this.running.splice(index, 1);
		this.usedCpu -= req.cpuSlots;
		this.usedMemory -= req.memoryMiB;
		for (const key of req.exclusiveKeys) {
			const left = (this.keys.get(key) ?? 0) - 1;
			if (left > 0) this.keys.set(key, left);
			else this.keys.delete(key);
		}
		this.pump();
	}

	private canGrant(req: CheckRequirement): boolean {
		if (!req.parallelSafe) return this.running.length === 0;
		if (this.running.some((item) => !item.parallelSafe) || this.running.length >= this.limits.maxConcurrent) return false;
		if (this.usedCpu + req.cpuSlots > this.limits.cpuSlots) return false;
		if (this.limits.memoryMiB !== null
			&& this.usedMemory + req.memoryMiB > this.limits.memoryMiB) return false;
		return !req.exclusiveKeys.some((key) => (this.keys.get(key) ?? 0) > 0);
	}

	private startRunning(req: CheckRequirement): void {
		this.running.push(req);
		this.usedCpu += req.cpuSlots;
		this.usedMemory += req.memoryMiB;
		for (const key of req.exclusiveKeys) this.keys.set(key, (this.keys.get(key) ?? 0) + 1);
	}

	private finish(waiter: PoolWaiter, result: AcquireResult): void {
		if (waiter.timer) clearTimeout(waiter.timer);
		waiter.removeAbort?.();
		waiter.removeAbort = null;
		const index = this.waiters.indexOf(waiter);
		if (index >= 0) this.waiters.splice(index, 1);
		waiter.resolve(result);
		this.pump();
	}

	/** Strict FIFO: only the head is considered, so an exclusive check never starves. */
	private pump(): void {
		while (this.waiters.length > 0) {
			const waiter = this.waiters[0];
			if (waiter.cancelled) {
				this.waiters.shift();
				continue;
			}
			if (!this.canGrant(waiter.req)) break;
			this.waiters.shift();
			waiter.granted = true;
			if (waiter.timer) clearTimeout(waiter.timer);
			waiter.removeAbort?.();
			waiter.removeAbort = null;
			this.startRunning(waiter.req);
			waiter.resolve({ granted: true, cancelled: false,
				queueSeconds: roundSeconds((Date.now() - waiter.enqueuedAt) / 1000) });
		}
	}
}

// -- memory pressure sampling -------------------------------------------------
// The probe is a short read-only observation at the admission boundary only.
// macOS uses the kernel pressure level; Linux uses MemAvailable/MemTotal and is
// explicitly labelled as an availability estimate, not the same classification.
// A failed or unknown probe degrades to exclusive serial admission instead of
// claiming the machine has headroom. It never changes system or Docker settings.
type PressureState = "normal" | "high" | "low" | "unknown";

interface PressureSnapshot {
	state: PressureState;
	platform: string;
	source: string;
	metric: string;
	value: string | null;
	availableMiB: number | null;
	totalMiB: number | null;
	thresholdMiB: number | null;
	synthetic: boolean;
	error: string | null;
}

const PRESSURE_SNAPSHOT_ENV = "CODEX_PI_PRESSURE_SNAPSHOT";
const MAX_PRESSURE_SNAPSHOT_BYTES = 65_536;
const MAX_MEMINFO_BYTES = 65_536;
const PRESSURE_PROBE_LIMIT_MS = 2_000;
const LINUX_MIN_AVAILABLE_MIB = 256;
const LINUX_MIN_AVAILABLE_FRACTION = 0.05;

function pressureOverride(base: PressureSnapshot): PressureSnapshot | null {
	const file = process.env[PRESSURE_SNAPSHOT_ENV];
	if (!file) return null;
	try {
		const stat = fs.statSync(file);
		if (!stat.isFile() || stat.size > MAX_PRESSURE_SNAPSHOT_BYTES) {
			throw new Error("snapshot is not a bounded regular file");
		}
		const parsed = JSON.parse(fs.readFileSync(file, "utf8")) as Record<string, unknown>;
		const state = parsed.state;
		if (state !== "normal" && state !== "high" && state !== "low" && state !== "unknown") {
			throw new Error("snapshot state must be normal/high/low/unknown");
		}
		return { ...base, state, synthetic: true, source: file,
			metric: typeof parsed.metric === "string" ? parsed.metric : "injected test snapshot",
			availableMiB: positiveFinite(parsed.availableMiB), thresholdMiB: positiveFinite(parsed.thresholdMiB),
			value: typeof parsed.value === "string" ? parsed.value : null,
			error: typeof parsed.error === "string" ? parsed.error : null };
	} catch (error) {
		return { ...base, state: "unknown", synthetic: true, source: file,
			error: `invalid pressure snapshot: ${String(error)}` };
	}
}

async function samplePressure(): Promise<PressureSnapshot> {
	const base: PressureSnapshot = { state: "unknown", platform: process.platform,
		source: "none", metric: "none", value: null, availableMiB: null, totalMiB: null,
		thresholdMiB: null, synthetic: false, error: null };
	const injected = pressureOverride(base);
	if (injected) return injected;
	if (process.platform === "darwin") {
		const result = await run("/usr/sbin/sysctl", ["-n", "kern.memorystatus_vm_pressure_level"],
			process.cwd(), undefined, PRESSURE_PROBE_LIMIT_MS);
		const raw = result.stdout.trim();
		const level = /^\d+$/.test(raw) ? Number(raw) : NaN;
		return { ...base, source: "sysctl kern.memorystatus_vm_pressure_level",
			metric: "read-only kernel vm pressure level (1 normal, >=2 elevated)", value: raw || null,
			state: result.code !== 0 || result.timedOut ? "unknown"
				: level === 1 ? "normal" : Number.isFinite(level) && level >= 2 ? "high" : "unknown",
			error: result.timedOut ? "probe timed out" : result.code === 0 ? null : `probe exit ${result.code}` };
	}
	if (process.platform === "linux") {
		try {
			const fd = fs.openSync("/proc/meminfo", "r");
			let raw: string;
			try {
				const buffer = Buffer.alloc(MAX_MEMINFO_BYTES);
				const read = fs.readSync(fd, buffer, 0, buffer.length, 0);
				raw = buffer.subarray(0, read).toString("utf8");
			} finally {
				fs.closeSync(fd);
			}
			const total = /^MemTotal:\s+(\d+) kB$/m.exec(raw);
			const available = /^MemAvailable:\s+(\d+) kB$/m.exec(raw);
			if (!total || !available) {
				return { ...base, source: "/proc/meminfo", metric: "MemAvailable/MemTotal",
					error: "MemTotal or MemAvailable is missing" };
			}
			const totalMiB = Number(total[1]) / 1024;
			const availableMiB = Number(available[1]) / 1024;
			if (!Number.isFinite(totalMiB) || !Number.isFinite(availableMiB) || totalMiB <= 0 || availableMiB < 0 || availableMiB > totalMiB) return {...base,error:"invalid memory availability"};
			const thresholdMiB = Math.max(LINUX_MIN_AVAILABLE_MIB,
				totalMiB * LINUX_MIN_AVAILABLE_FRACTION);
			return { ...base, source: "/proc/meminfo",
				metric: "MemAvailable availability estimate (not macOS-style pressure)",
				value: `${Math.round(availableMiB)} MiB available`,
				availableMiB: Math.round(availableMiB), totalMiB: Math.round(totalMiB),
				thresholdMiB: Math.round(thresholdMiB),
				state: availableMiB < thresholdMiB ? "low" : "normal" };
		} catch (error) {
			return { ...base, source: "/proc/meminfo", metric: "MemAvailable/MemTotal",
				error: String(error) };
		}
	}
	return { ...base, source: process.platform, metric: "no supported probe",
		error: "unsupported platform" };
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
		test_counts: null, log_tail: null, error: null, queueSeconds: null, resourceMode: null,
		resourceDeclaration: null, pressureState: null, pressureSynthetic: null, pool: null, ...patch };
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
		queueSeconds: { type: ["number", "null"] },
		resourceMode: { type: ["string", "null"] },
		resourceDeclaration: { type: ["object", "null"] },
		pressureState: { type: ["string", "null"] },
		pressureSynthetic: { type: ["boolean", "null"] },
		pool: { type: ["object", "null"] },
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
	let pool: CheckPool | null = null;
	const codemodeDeadlines = new Map<string, number>();
	try {
		// The load proof is written only after the native codemode tool registered: a Pi without
		// the public export, or a failing registration, must not let the round claim it loaded.
		cfg = loadConfig(process.env.CODEX_PI_WORKER_CONFIG);
		pool = new CheckPool(poolLimits(cfg));
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
			if (!cfg || !pool) {
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
			if (advice.targeted !== null && params.final !== true) {
				return refuseCheck(checkId,
					`final_required: this is a full acceptance command; run the targeted check first (${advice.targeted}) ` +
					"and pass final:true only on the clean final candidate",
					{ targetedCommand: advice.targeted });
			}
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
			const resolved = resolveCheckRequirement(cfg, matches);
			const resourceObservations: Record<string, unknown> = {
				resourceMode: resolved.req.parallelSafe ? "parallel" : "exclusive",
				resourceDeclaration: resolved.req.declaration,
				pool: pool.snapshot(),
			};
			if (resolved.problem !== null) {
				return refuseCheck(checkId, `contract_metadata: ${resolved.problem}; refusing to guess`,
					{ ...resourceObservations, requiredSeconds: roundSeconds(requiredSeconds) });
			}
			const capacityProblem = resourceCapacityProblem(cfg, resolved.req.declaration);
			if (capacityProblem !== null) {
				return refuseCheck(checkId, capacityProblem,
					{ ...resourceObservations, requiredSeconds: roundSeconds(requiredSeconds) });
			}
			// A nested check runs inside the codemode script's outer deadline, which the
			// native context does not expose directly. This extension recorded its own
			// normalized timeout at the parent tool_call; without that record the envelope
			// is unverifiable and the check must run directly instead. The deadline is
			// re-read after any queue wait, never trusted from enqueue time.
			let outerDeadline: number | null = null;
			const separator = toolCallId.indexOf("/");
			if (separator > 0) {
				const recorded = codemodeDeadlines.get(toolCallId.slice(0, separator));
				if (recorded === undefined) {
					return refuseCheck(checkId,
						"codemode_deadline_unknown: a check from codemode has no verifiable outer deadline; " +
						"run it directly with the check tool",
						{ ...resourceObservations, requiredSeconds: roundSeconds(requiredSeconds),
							reserveSeconds: CHECK_RESERVE_SECONDS });
				}
				outerDeadline = recorded;
			}
            // One acquisition loop; pressure becoming unknown can only downgrade
            // once to an exclusive permit. Every await consumes the original budget.
            const queueStarted = Date.now();
            let req = resolved.req;
            let pressure = await samplePressure();
            let held = false;
            let pressurePatch: Record<string, unknown> = {};
            let queueObservations: Record<string, unknown> = {};
            try {
                for (;;) {
                    if (signal?.aborted) return refuseCheck(checkId, "cancelled_before_spawn", {cancelled:true});
                    if (pressure.state === "high" || pressure.state === "low") {
                        return refuseCheck(checkId, "memory_pressure: refusing new check", {pressureState:pressure.state, pressureSynthetic:pressure.synthetic});
                    }
                    if (pressure.state === "unknown") req = exclusiveRequirement(cfg, resolved.req.declaration);
                    if (pressure.availableMiB !== null && pressure.availableMiB < req.memoryMiB + (pressure.thresholdMiB ?? 0)) {
                        return refuseCheck(checkId, "memory_pressure: available memory below declared requirement plus headroom",
                            {pressureState:pressure.state, availableMiB:pressure.availableMiB, requiredMiB:req.memoryMiB});
                    }
                    const admission = checkWindow(cfg, requested, requiredSeconds, outerDeadline, estimateSource);
                    if (admission.reason) return refuseCheck(checkId, admission.reason, admission.patch);
                    const acquired = await pool.acquire(req, signal, admission.latestStart);
                    queueObservations = {...resourceObservations, resourceMode:req.parallelSafe ? "parallel" : "exclusive",
                        queueSeconds:roundSeconds((Date.now()-queueStarted)/1000), pool:pool.snapshot()};
                    if (!acquired.granted) return refuseCheck(checkId,
                        acquired.cancelled ? "cancelled_while_waiting" : "insufficient_budget_while_waiting",
                        {...queueObservations, cancelled:acquired.cancelled, requiredSeconds});
                    held = true;
                    if (advice.targeted !== null) {
                        const clean = await worktreeClean(cfg, signal);
                        if (!clean.ok) return refuseCheck(checkId, `dirty_final: ${clean.reason}`,
                            {...queueObservations, targetedCommand:advice.targeted});
                    }
                    pressure = await samplePressure();
                    if (pressure.state === "unknown" && req.parallelSafe) {
                        pool.release(req); held = false;
                        req = exclusiveRequirement(cfg, resolved.req.declaration);
                        continue;
                    }
                    break;
                }
                pressurePatch = {pressureState:pressure.state, pressureSynthetic:pressure.synthetic, pressureMetric:pressure.metric};
                if (pressure.state === "high" || pressure.state === "low" || (pressure.availableMiB !== null
                    && pressure.availableMiB < req.memoryMiB + (pressure.thresholdMiB ?? 0))) {
                    return refuseCheck(checkId, "memory_pressure: insufficient headroom after queue wait", {...queueObservations,...pressurePatch});
                }
                if (signal?.aborted) return refuseCheck(checkId, "cancelled_before_spawn", {...queueObservations,cancelled:true});
            const admission = checkWindow(cfg, requested, requiredSeconds, outerDeadline, estimateSource);
            if (admission.reason) return refuseCheck(checkId, admission.reason, {...queueObservations,...pressurePatch,...admission.patch});
            const timeout = admission.timeout;
            const budgetPatch = admission.patch;
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
					checkStructured(checkId, false, { exit_code: result.code, error, elapsedSeconds,
						...queueObservations, ...pressurePatch }), true);
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
				...queueObservations,
				...pressurePatch,
			});
			const counts = summary.test_counts && typeof summary.test_counts === "object"
				? ` counts=${JSON.stringify(summary.test_counts)}` : "";
			const lines = [`check ${params.id}: exit=${summary.exit_code}${summary.timed_out ? " TIMED_OUT" : ""}${summary.cancelled ? " CANCELLED" : ""}${counts} elapsed=${elapsedSeconds}s`,
				`queue=${queueObservations.queueSeconds}s resources=${structured.resourceMode}`,
				`receipt=${summary.receipt}`];
			if (typeof summary.log_tail === "string") lines.push("log_tail:", summary.log_tail);
			return textResult(lines.join("\n"), summary, structured, failed);
			} finally {
				if (held) pool.release(req);
			}
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
