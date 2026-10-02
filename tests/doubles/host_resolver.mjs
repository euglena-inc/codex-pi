// Maps the host package specifier to the offline stub for the extension harness.
// Node's module hook runs in a separate thread, so the stub URL is absolute.
export async function resolve(specifier, context, nextResolve) {
	if (specifier === "@earendil-works/pi-coding-agent") {
		return { url: new URL("./host_package_stub.mjs", import.meta.url).href, shortCircuit: true };
	}
	return nextResolve(specifier, context);
}
