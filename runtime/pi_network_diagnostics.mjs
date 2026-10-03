/**
 * Dependency-free transport observer preloaded into the Pi process by the
 * Codex-Pi supervisor when the frozen task policy enables diagnostics.
 *
 * The module subscribes to undici's public ``node:diagnostics_channel`` events
 * only. It never replaces fetch or a dispatcher, never inspects request bodies,
 * headers, URLs or prompts, and every write is best effort: any failure is
 * swallowed so diagnostics can never break the model call.
 *
 * The sidecar is a bounded append-only JSONL file. Only the supervisor's direct
 * Pi child process (``process.ppid === CODEX_PI_NETWORK_DIAG_SUPERVISOR``) is a
 * writer: inherited tool children keep the proxy policy but never append, so
 * exactly one process owns the per-round scope and the aggregate byte bound
 * holds. Any dropped event is followed by one ``truncated`` marker (best
 * effort); each append is a single O_APPEND write of at most 512 bytes, so
 * lines never interleave even if a manual run reuses the same file.
 *
 * Each record stores only allowlisted fields: a relative timestamp/duration, the
 * event phase, a safe classification, an allowlisted error code, a numeric proxy
 * CONNECT status and a bounded process identity. Raw exception text, URLs,
 * hostnames, headers, bodies, prompts and credentials are never written.
 *
 * Environment (set only for the Pi child process):
 *   CODEX_PI_NETWORK_DIAG_FILE        absolute sidecar path
 *   CODEX_PI_NETWORK_DIAG_SCOPE       short round scope, never user content
 *   CODEX_PI_NETWORK_DIAG_SUPERVISOR  supervisor pid (for primary attribution)
 */
import { fstatSync, openSync, writeSync } from "node:fs";
import { subscribe } from "node:diagnostics_channel";

const MAX_RECORDS = 256;
const MAX_BYTES = 65536;
const MARKER_RESERVE = 128;
const RECORD_BUDGET = MAX_BYTES - 2 * MARKER_RESERVE;
const MAX_LINE_BYTES = 512;
const MAX_AGE_SECONDS = 86400;
const MAX_DURATION_MS = 3600000;
const CAUSE_DEPTH = 3;
const PROXY_TEXT_LIMIT = 4096;

const FILE = typeof process.env.CODEX_PI_NETWORK_DIAG_FILE === "string"
	? process.env.CODEX_PI_NETWORK_DIAG_FILE.trim() : "";
const RAW_SCOPE = typeof process.env.CODEX_PI_NETWORK_DIAG_SCOPE === "string"
	? process.env.CODEX_PI_NETWORK_DIAG_SCOPE : "";
const SCOPE = /^[A-Za-z0-9_-]{1,32}$/.test(RAW_SCOPE) ? RAW_SCOPE : "unknown";
const SUPERVISOR_PID = Number.parseInt(process.env.CODEX_PI_NETWORK_DIAG_SUPERVISOR || "", 10);
const PRIMARY = Number.isInteger(SUPERVISOR_PID) && SUPERVISOR_PID > 0
	&& process.ppid === SUPERVISOR_PID;
const PROC = Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, "0");
const START = Date.now();

// Allowlisted error codes only; an unknown code is stored as null.
const ERROR_CODES = new Set([
	"ECONNRESET", "ECONNREFUSED", "ECONNABORTED", "EPIPE", "ETIMEDOUT", "ENETDOWN",
	"ENETUNREACH", "EHOSTUNREACH", "ENOTFOUND", "EAI_AGAIN", "EAI_FAIL", "EAI_NONAME",
	"ESERVFAIL", "UND_ERR_CONNECT_TIMEOUT", "UND_ERR_HEADERS_TIMEOUT",
	"UND_ERR_BODY_TIMEOUT", "UND_ERR_SOCKET", "UND_ERR_ABORTED", "UND_ERR_DESTROYED",
	"UND_ERR_CLOSED", "ABORT_ERR", "CERT_HAS_EXPIRED", "UNABLE_TO_VERIFY_LEAF_SIGNATURE",
	"SELF_SIGNED_CERT_IN_CHAIN", "ERR_TLS_CERT_ALTNAME_INVALID", "DEPTH_ZERO_SELF_SIGNED_CERT",
]);
const TIMEOUT_CODES = new Set(["ETIMEDOUT", "UND_ERR_CONNECT_TIMEOUT",
	"UND_ERR_HEADERS_TIMEOUT", "UND_ERR_BODY_TIMEOUT"]);
const DNS_CODES = new Set(["ENOTFOUND", "EAI_AGAIN", "EAI_FAIL", "EAI_NONAME", "ESERVFAIL"]);
const TLS_CODES = new Set(["CERT_HAS_EXPIRED", "UNABLE_TO_VERIFY_LEAF_SIGNATURE",
	"SELF_SIGNED_CERT_IN_CHAIN", "ERR_TLS_CERT_ALTNAME_INVALID", "DEPTH_ZERO_SELF_SIGNED_CERT"]);
// Concrete socket failures; ABORT/DESTROYED/CLOSED stay separate because the code
// alone never proves that an abort was ordinary response-stream cleanup.
const SOCKET_CODES = new Set(["ECONNRESET", "ECONNREFUSED", "ECONNABORTED", "EPIPE",
	"ENETDOWN", "ENETUNREACH", "EHOSTUNREACH", "UND_ERR_SOCKET"]);

let fd = null;
let stopped = false;
let failed = false;
let markerWritten = false;
let records = 0;

function relativeSeconds() {
	try {
		const value = (Date.now() - START) / 1000;
		return Math.max(0, Math.min(MAX_AGE_SECONDS, Math.round(value * 1000) / 1000));
	} catch {
		return 0;
	}
}

/** Bounded error/cause chain; never follows cycles or more than CAUSE_DEPTH levels. */
function chain(error) {
	const levels = [];
	let current = error;
	for (let depth = 0; depth < CAUSE_DEPTH; depth += 1) {
		if (!current || (typeof current !== "object" && typeof current !== "function")) {
			break;
		}
		if (levels.includes(current)) {
			break;
		}
		levels.push(current);
		current = current.cause;
	}
	return levels;
}

function codes(error) {
	const found = [];
	for (const level of chain(error)) {
		if (typeof level.code === "string" && ERROR_CODES.has(level.code)
				&& !found.includes(level.code)) {
			found.push(level.code);
		}
	}
	return found;
}

function names(error) {
	const found = [];
	for (const level of chain(error)) {
		if (typeof level.name === "string" && level.name.length <= 64
				&& !found.includes(level.name)) {
			found.push(level.name);
		}
	}
	return found;
}

/** Proxy evidence from any bounded level; undefined when no usable text exists. */
function proxyEvidence(error) {
	let mention = undefined;
	for (const level of chain(error)) {
		const text = typeof level.message === "string" ? level.message : "";
		if (!text || text.length > PROXY_TEXT_LIMIT) {
			// A missing or oversized message is never proxy evidence.
			continue;
		}
		const explicit = /proxy response \((\d{3})\)/i.exec(text);
		if (explicit) {
			const status = Number.parseInt(explicit[1], 10);
			if (status >= 100 && status <= 599) {
				return status;
			}
		}
		if (/http tunneling/i.test(text)
				|| (/\bproxy\b/i.test(text) && /\bconnect\b/i.test(text))) {
			const number = /(\d{3})/.exec(text);
			const status = number ? Number.parseInt(number[1], 10) : null;
			if (status !== null && status >= 100 && status <= 599) {
				return status;
			}
			mention = null;
		}
	}
	return mention;
}

/** Classify one error conservatively; evidence outranks ambiguous abort codes. */
function classify(error, headersSeen) {
	const known = codes(error);
	const errorNames = names(error);
	const proxy = proxyEvidence(error);
	if (proxy !== undefined) {
		return { "class": "proxy_connect_failure", status: proxy,
			code: known.length ? known[0] : null };
	}
	if (known.some((code) => TIMEOUT_CODES.has(code)) || errorNames.some((name) => /timeout/i.test(name))) {
		return { "class": "timeout", status: null, code: known.length ? known[0] : null };
	}
	if (known.some((code) => DNS_CODES.has(code))) {
		return { "class": "dns_failure", status: null, code: known[0] };
	}
	if (known.includes("ECONNRESET")) {
		return { "class": "connection_reset", status: null, code: "ECONNRESET" };
	}
	if (known.includes("ECONNREFUSED")) {
		return { "class": "connection_refused", status: null, code: "ECONNREFUSED" };
	}
	if (known.some((code) => TLS_CODES.has(code))) {
		return { "class": "tls_failure", status: null, code: known[0] };
	}
	if (known.some((code) => SOCKET_CODES.has(code))) {
		return { "class": "transport_error", status: null, code: known[0] };
	}
	// An AbortError name is the explicit cleanup signal; ambiguous abort codes
	// without it must not be reported as healthy cleanup.
	if (errorNames.includes("AbortError")) {
		return { "class": "abort_cleanup", status: null, code: known.length ? known[0] : null };
	}
	if (headersSeen) {
		return { "class": "post_header_error", status: null, code: known.length ? known[0] : null };
	}
	if (known.length) {
		return { "class": "transport_error", status: null, code: known[0] };
	}
	return { "class": "unknown", status: null, code: null };
}

function ensureFile() {
	if (fd !== null) {
		return true;
	}
	if (failed || stopped) {
		return false;
	}
	try {
		fd = openSync(FILE, "a", 0o600);
		return true;
	} catch {
		failed = true;
		return false;
	}
}

function emit(record) {
	if (stopped || failed || typeof FILE !== "string" || FILE.length === 0) {
		return;
	}
	try {
		if (!ensureFile()) {
			return;
		}
		let line = JSON.stringify(record);
		if (Buffer.byteLength(line, "utf8") > MAX_LINE_BYTES) {
			line = JSON.stringify({ at: record.at, phase: "oversized", scope: SCOPE, proc: PROC });
		}
		const size = fstatSync(fd).size;
		if (records >= MAX_RECORDS || size + line.length + 1 > RECORD_BUDGET) {
			if (!markerWritten) {
				markerWritten = true;
				const marker = JSON.stringify({ at: relativeSeconds(), phase: "truncated",
					scope: SCOPE, proc: PROC });
				try {
					if (fstatSync(fd).size + marker.length + 1 <= MAX_BYTES) {
						writeSync(fd, marker + "\n");
					}
				} catch {
					failed = true;
				}
			}
			stopped = true;
			return;
		}
		writeSync(fd, line + "\n");
		records += 1;
	} catch {
		// Diagnostics write failure must never break the model call.
		failed = true;
	}
}

function boundedDuration(startedAt) {
	if (!Number.isFinite(startedAt)) {
		return null;
	}
	const value = Math.round(Date.now() - startedAt);
	if (!Number.isFinite(value)) {
		return null;
	}
	return Math.max(0, Math.min(MAX_DURATION_MS, value));
}

function safeIdentity() {
	return { scope: SCOPE, proc: PROC, primary: true };
}

function install() {
	// Single-writer scope: inherited tool children keep the proxy policy but are
	// never diagnostic writers, and a manual run without the supervisor identity
	// stays silent instead of sharing an unbounded file.
	if (typeof FILE !== "string" || FILE.length === 0 || !PRIMARY) {
		return;
	}
	emit({ at: 0, phase: "observer_ready", scope: SCOPE, proc: PROC });
	const started = new WeakMap();
	const withHeaders = new WeakSet();
	subscribe("undici:request:create", (message) => {
		try {
			const request = message?.request;
			if (request && (typeof request === "object" || typeof request === "function")) {
				started.set(request, Date.now());
			}
		} catch {
			// never propagate observer errors
		}
	});
	subscribe("undici:request:headers", (message) => {
		try {
			const request = message?.request;
			if (request && (typeof request === "object" || typeof request === "function")) {
				withHeaders.add(request);
			}
		} catch {
			// never propagate observer errors
		}
	});
	subscribe("undici:request:error", (message) => {
		try {
			const request = message?.request;
			const identity = safeIdentity();
			const startedAt = request && (typeof request === "object" || typeof request === "function")
				? started.get(request) : undefined;
			const classified = classify(message?.error, request ? withHeaders.has(request) : false);
			emit({ at: relativeSeconds(), dur: boundedDuration(startedAt), phase: "request_error",
				"class": classified["class"], code: classified.code, status: classified.status,
				hdr: request ? withHeaders.has(request) : false,
				scope: identity.scope, proc: identity.proc, primary: identity.primary });
		} catch {
			// never propagate observer errors
		}
	});
}

try {
	install();
} catch {
	// Loading diagnostics must never break the Pi process.
}
