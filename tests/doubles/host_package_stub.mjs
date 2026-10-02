// Offline stand-in for the host package in the extension harness. It registers a
// minimal `codemode` tool through the extension API, like Pi 1.0.0's export, and
// refuses anything but the settled {models:false, mode:"on"} registration.
// The real QuickJS integration is covered by scripts/validate_codemode.py only.
export const createCodemodeExtension = process.env.PI_STUB_UNAVAILABLE
	? undefined
	: (options = {}) => {
		if (options.models !== false || options.mode !== "on") {
			throw new Error(`unexpected codemode options: ${JSON.stringify(options)}`);
		}
		return (pi) => {
			pi.registerTool({
				name: "codemode",
				label: "codemode",
				description: "offline codemode stand-in",
				parameters: { type: "object", properties: { code: { type: "string" } }, required: ["code"] },
				outputSchema: { type: "string" },
				execute: async (_id, params) => ({
					content: [{ type: "text", text: String(params?.code ?? "") }],
					details: {},
					structuredContent: String(params?.code ?? ""),
				}),
			});
		};
	};
