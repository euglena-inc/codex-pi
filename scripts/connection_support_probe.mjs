#!/usr/bin/env node
/**
 * Deterministic mechanism probe for the Codex-Pi transport diagnostics preload.
 *
 * The validator runs this file with NODE_OPTIONS importing
 * `runtime/pi_network_diagnostics.mjs`, so the observer under test is loaded
 * exactly the way Pi receives it. Modes:
 *
 *   synthetic   publish a fixed sequence of undici diagnostics-channel events
 *               (classification, privacy and abort-cleanup negatives)
 *   real        local fault server: connection reset before headers, plus a
 *               healthy request that must not produce a failure record
 *   flood       publish more events than the sidecar bound and stop
 *   write-failure  publish with an unwritable sidecar path; must exit 0
 *
 * This probe never contacts an external network and never prints raw error
 * text. It is mechanism evidence only, never model-availability evidence.
 */
import diagnosticsChannel from "node:diagnostics_channel";
import net from "node:net";

const mode = process.argv[2] || "synthetic";

function createRequest() {
	return { origin: "http://synthetic.invalid", path: "/probe", method: "GET" };
}

function publishError(request, error) {
	diagnosticsChannel.channel("undici:request:error").publish({ request, error });
}

function failure(message, code) {
	const error = new Error(message);
	if (code) {
		error.code = code;
	}
	return error;
}

function synthetic() {
	let published = 0;
	const emitCase = (error, headers) => {
		const request = createRequest();
		diagnosticsChannel.channel("undici:request:create").publish({ request });
		if (headers) {
			diagnosticsChannel.channel("undici:request:headers").publish(
				{ request, response: { statusCode: 200 } });
		}
		publishError(request, error);
		published += 1;
	};
	const abortError = () => {
		const abort = new Error("The operation was aborted");
		abort.name = "AbortError";
		abort.code = "ABORT_ERR";
		return abort;
	};
	// 1 pre-header reset with text
	emitCase(failure("socket hang up", "ECONNRESET"), false);
	// 2 ordinary response-stream cleanup abort after headers: never a failure
	emitCase(abortError(), true);
	// 3 proxy CONNECT rejection with a numeric tunnel status
	emitCase(failure("Proxy response (503) !== 200 when HTTP Tunneling"), false);
	// 4 proxy tunnel failure without a numeric status
	emitCase(failure("Proxy CONNECT tunnel rejected"), false);
	// 5-8 DNS, timeout, refused and TLS codes
	emitCase(failure("getaddrinfo ENOTFOUND", "ENOTFOUND"), false);
	emitCase(failure("headers timeout", "UND_ERR_HEADERS_TIMEOUT"), false);
	emitCase(failure("connect ECONNREFUSED", "ECONNREFUSED"), false);
	emitCase(failure("certificate has expired", "CERT_HAS_EXPIRED"), false);
	// 9 genuine post-header error with no code
	emitCase(failure("terminated after response start"), true);
	// 10 adversarial secret-bearing message assembled at runtime
	{
		const secret = ["https://", "alice", ":", "s3cr3t", "@", "private.invalid",
			"/api?token=", "ZZTOKENZZ"].join("");
		emitCase(failure(`request failed: ${secret}`, "ESOMETHING"), false);
	}
	// 11 unknown-to-classifier allowlisted socket code
	emitCase(failure("socket closed", "UND_ERR_SOCKET"), false);
	// 12 review repro 1: code only, no message, must stay connection_reset
	emitCase(failure("", "ECONNRESET"), false);
	// 13 review negative: oversized message is never proxy evidence
	emitCase(failure(`Proxy response (503) when HTTP Tunneling ${"x".repeat(5000)}`,
		"ECONNRESET"), false);
	// 14 review repro 2: explicit proxy evidence in a bounded cause outranks
	// the outer ambiguous abort code
	{
		const inner = failure("Proxy response (503) !== 200 when HTTP Tunneling");
		inner.name = "AbortError";
		inner.code = "UND_ERR_ABORTED";
		const outer = failure("Connection error.");
		outer.code = "UND_ERR_ABORTED";
		outer.cause = inner;
		emitCase(outer, false);
	}
	// 15-16 ambiguous abort codes alone never prove normal cleanup
	emitCase(failure("aborted", "UND_ERR_ABORTED"), false);
	emitCase(failure("aborted", "UND_ERR_ABORTED"), true);
	// 17 a real AbortError name before headers is still ordinary cleanup
	emitCase(abortError(), false);
	// 18 no usable evidence at all stays unknown
	emitCase(failure("", "ESOMETHING"), false);
	console.log(JSON.stringify({ mode, published, exit: 0 }));
}

function flood() {
	const request = createRequest();
	diagnosticsChannel.channel("undici:request:create").publish({ request });
	for (let index = 0; index < 1000; index += 1) {
		publishError(request, failure("socket hang up", "ECONNRESET"));
	}
	console.log(JSON.stringify({ mode, published: 1000, exit: 0 }));
}

async function real() {
	// Accept then destroy: ECONNRESET before any header.
	const broken = net.createServer((socket) => {
		socket.once("data", () => socket.destroy());
	});
	await new Promise((resolve) => broken.listen(0, "127.0.0.1", resolve));
	const brokenPort = broken.address().port;
	let reset = false;
	try {
		await fetch(`http://127.0.0.1:${brokenPort}/reset`);
	} catch {
		reset = true;
	}
	await new Promise((resolve) => setTimeout(resolve, 200));
	broken.close();
	// Healthy local request: must not add a failure record.
	const healthy = net.createServer((socket) => {
		socket.end("HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok");
	});
	await new Promise((resolve) => healthy.listen(0, "127.0.0.1", resolve));
	const healthyPort = healthy.address().port;
	let healthyOk = false;
	try {
		const response = await fetch(`http://127.0.0.1:${healthyPort}/ok`);
		healthyOk = response.status === 200 && (await response.text()) === "ok";
	} catch {
		healthyOk = false;
	}
	await new Promise((resolve) => setTimeout(resolve, 200));
	healthy.close();
	console.log(JSON.stringify({ mode, reset, healthyOk, exit: 0 }));
}

if (mode === "synthetic") {
	synthetic();
} else if (mode === "flood") {
	flood();
} else if (mode === "real") {
	await real();
} else if (mode === "write-failure") {
	publishError(createRequest(), failure("socket hang up", "ECONNRESET"));
	console.log(JSON.stringify({ mode, exit: 0 }));
} else {
	console.error(`unknown mode: ${mode}`);
	process.exit(2);
}
