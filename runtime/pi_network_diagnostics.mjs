/**
 * Dependency-free transport observer preloaded into the Pi process by the
 * Codex-Pi supervisor when the frozen task policy enables diagnostics.
 *
 * The module subscribes to undici's public ``node:diagnostics_channel`` events
 * only. It never replaces fetch or a dispatcher, never inspects request bodies,
 * headers, URLs or prompts, and every write is best effort: any failure is
 * swallowed so diagnostics can never break the model call.
 *
 * The sidecar is a bounded append-only JSONL file. Each record stores only
 * allowlisted fields: a relative timestamp/duration, the event phase, a safe
 * classification, an allowlisted error code, a numeric proxy CONNECT status and
 * a process-local identity. Raw exception text, URLs, hostnames, headers,
 * bodies, prompts and credentials are never written.
 *
 * Environment (set only for the Pi child process):
 *   CODEX_PI_NETWORK_DIAG_FILE        absolute sidecar path
 *   CODEX_PI_NETWORK_DIAG_SCOPE       short round scope, never user content
 *   CODEX_PI_NETWORK_DIAG_SUPERVISOR  supervisor pid (for primary attribution)
 */
import { openSync, writeSync } from "node:fs";
import { subscribe } from "node:diagnostics_channel";

const MAX_RECORDS = 256;
const MAX_BYTES = 65536;
const MARKER_RESERVE = 128;
const MAX_LINE_BYTES = 512;
const MAX_AGE_SECONDS = 86400;
const MAX_DURATION_MS = 3600000;

const FILE = typeof process.env.CODEX_PI_NETWORK_DIAG_FILE === "string"
	? process.env.CODEX_PI_NETWORK_DIAG_FILE.trim() : "";
const RAW_SCOPE = typeof process.env.CODEX_PI_NETWORK_DIAG_SCOPE === "string"
	? process.env.CODEX_PI_NETWORK_DIAG_SCOPE : "";
const SCOPE = /^[A-Za-z0-9_-]{1,32}$/.test(RAW_SCOPE) ? RAW_SCOPE : "unknown";
const SUPERVISOR_PID = Number.parseInt(process.env.CODEX_PI_NETWORK_DIAG_SUPERVISOR || "", 10);
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
const ABORT_CODES = new Set(["ABORT_ERR", "UND_ERR_ABORTED", "UND_ERR_DESTROYED", "UND_ERR_CLOSED"]);
const TLS_CODES = new Set(["CERT_HAS_EXPIRED", "UNABLE_TO_VERIFY_LEAF_SIGNATURE",
	"SELF_SIGNED_CERT_IN_CHAIN", "ERR_TLS_CERT_ALTNAME_INVALID", "DEPTH_ZERO_SELF_SIGNED_CERT"]);

let fd = null;
let stopped = false;
let failed = false;
let records = 0;
let bytes = 0;

function relativeSeconds() {
	try {
		const value = (Date.now() - START) / 1000;
		return Math.max(0, Math.min(MAX_AGE_SECONDS, Math.round(value * 1000) / 1000));
	} catch {
		return 0;
	}
}

function safeCode(error) {
	for (const candidate of [error, error?.cause, error?.cause?.cause]) {
		if (candidate && typeof candidate === "object" && typeof candidate.code === "string"
				&& ERROR_CODES.has(candidate.code)) {
			return candidate.code;
		}
	}
	return null;
}

function safeName(error) {
	for (const candidate of [error, error?.cause, error?.cause?.cause]) {
		if (candidate && typeof candidate === "object" && typeof candidate.name === "string"
				&& candidate.name.length <= 64) {
			return candidate.name;
		}
	}
	return "";
}

function rawMessage(error) {
	for (const candidate of [error, error?.cause, error?.cause?.cause]) {
		if (candidate && typeof candidate === "object" && typeof candidate.message === "string") {
			return candidate.message;
		}
	}
	return "";
}

function proxyStatus(error) {
	const text = rawMessage(error);
	if (!text || text.length > 4096) {
		return null;
	}
	const explicit = /proxy response \((\d{3})\)/i.exec(text);
	if (explicit) {
		const status = Number.parseInt(explicit[1], 10);
		return status >= 100 && status <= 599 ? status : null;
	}
	if (/http tunneling/i.test(text) || (/\bproxy\b/i.test(text) && /\bconnect\b/i.test(text))) {
		const number = /(\d{3})/.exec(text);
		if (number) {
			const status = Number.parseInt(number[1], 10);
			return status >= 100 && status <= 599 ? status : null;
		}
		return null;
	}
	return undefined;
}

function classify(error, headersSeen) {
	const proxy = proxyStatus(error);
	if (proxy !== undefined) {
		return { "class": "proxy_connect_failure", status: proxy };
	}
	const code = safeCode(error);
	const name = safeName(error);
	if (TIMEOUT_CODES.has(code) || /timeout/i.test(name)) {
		return { "class": "timeout", status: null };
	}
	if (ABORT_CODES.has(code) || name === "AbortError") {
		// Ordinary response-stream cleanup cancellation is never a failure.
		return { "class": "abort_cleanup", status: null };
	}
	if (DNS_CODES.has(code)) {
		return { "class": "dns_failure", status: null };
	}
	if (code === "ECONNRESET") {
		return { "class": "connection_reset", status: null };
	}
	if (code === "ECONNREFUSED") {
		return { "class": "connection_refused", status: null };
	}
	if (TLS_CODES.has(code) || /cert|tls|ssl/i.test(name)) {
		return { "class": "tls_failure", status: null };
	}
	if (headersSeen) {
		return { "class": "post_header_error", status: null };
	}
	if (code) {
		return { "class": "transport_error", status: null };
	}
	return { "class": "unknown", status: null };
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
		let line = JSON.stringify(record);
		if (Buffer.byteLength(line, "utf8") > MAX_LINE_BYTES) {
			line = JSON.stringify({ at: record.at, phase: "oversized", scope: SCOPE, proc: PROC });
		}
		if (records >= MAX_RECORDS || bytes + line.length + 1 > MAX_BYTES - MARKER_RESERVE) {
			if (records <= MAX_RECORDS) {
				const marker = JSON.stringify({ at: relativeSeconds(), phase: "truncated",
					scope: SCOPE, proc: PROC });
				if (bytes + marker.length + 1 > MAX_BYTES) {
					stopped = true;
					return;
				}
				if (ensureFile()) {
					try {
						writeSync(fd, marker + "\n");
						bytes += marker.length + 1;
						records += 1;
					} catch {
						failed = true;
					}
				}
			}
			stopped = true;
			return;
		}
		if (!ensureFile()) {
			return;
		}
		writeSync(fd, line + "\n");
		bytes += line.length + 1;
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
	let primary = null;
	if (Number.isInteger(SUPERVISOR_PID) && SUPERVISOR_PID > 0) {
		primary = process.ppid === SUPERVISOR_PID;
	}
	return { scope: SCOPE, proc: PROC, primary: primary };
}

function install() {
	if (typeof FILE !== "string" || FILE.length === 0) {
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
				"class": classified["class"], code: safeCode(message?.error),
				status: classified.status, hdr: request ? withHeaders.has(request) : false,
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
