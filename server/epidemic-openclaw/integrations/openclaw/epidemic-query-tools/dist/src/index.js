import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
const ORIGIN = "http://127.0.0.1:8010";
const MAX_RESPONSE = 65_536;
const Paths = { normalize: "/v1/queries/normalize", register: "/v1/queries" };
const Params = Type.Object({
    country: Type.String({ minLength: 1, maxLength: 128 }),
    disease: Type.String({ minLength: 1, maxLength: 128 }),
    start_date: Type.String({ minLength: 10, maxLength: 10, pattern: "^\\d{4}-\\d{2}-\\d{2}$" }),
    end_date: Type.String({ minLength: 10, maxLength: 10, pattern: "^\\d{4}-\\d{2}-\\d{2}$" }),
}, { additionalProperties: false });
async function readLimited(response) {
    const length = response.headers.get("content-length");
    if (length !== null && Number(length) > MAX_RESPONSE)
        throw new Error("RESPONSE_TOO_LARGE");
    if (!response.headers.get("content-type")?.toLowerCase().startsWith("application/json"))
        throw new Error("INVALID_RESPONSE");
    const reader = response.body?.getReader();
    if (!reader)
        throw new Error("INVALID_RESPONSE");
    const chunks = [];
    let size = 0;
    while (true) {
        const item = await reader.read();
        if (item.done)
            break;
        size += item.value.length;
        if (size > MAX_RESPONSE) {
            await reader.cancel();
            throw new Error("RESPONSE_TOO_LARGE");
        }
        chunks.push(item.value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
        bytes.set(chunk, offset);
        offset += chunk.length;
    }
    try {
        return JSON.parse(new TextDecoder().decode(bytes));
    }
    catch {
        throw new Error("INVALID_RESPONSE");
    }
}
export async function callApi(path, input, signal) {
    const token = process.env.EPIDEMIC_QUERY_API_TOKEN;
    if (!token || token.length < 32)
        throw new Error("AUTH_UNAVAILABLE");
    const timeout = AbortSignal.timeout(10_000);
    const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
    let response;
    try {
        response = await fetch(ORIGIN + Paths[path], { method: "POST", redirect: "error", signal: combined, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` }, body: JSON.stringify(input) });
    }
    catch {
        throw new Error("SERVICE_UNAVAILABLE");
    }
    if (!response.ok) {
        if (response.status === 401)
            throw new Error("AUTH_FAILED");
        if (response.status === 404)
            throw new Error("NOT_FOUND");
        if (response.status === 409)
            throw new Error("AMBIGUOUS");
        if (response.status === 422)
            throw new Error("INVALID_REQUEST");
        throw new Error("SERVICE_UNAVAILABLE");
    }
    return readLimited(response);
}
export default defineToolPlugin({
    id: "epidemic-query-tools", name: "Epidemic Query Tools",
    description: "Normalize and idempotently register local epidemic queries.",
    activation: { onStartup: true }, configSchema: Type.Object({}, { additionalProperties: false }),
    tools: (tool) => [
        tool({ name: "normalize_epidemic_query", optional: true, description: "Read-only country, disease, and date normalization. Does not persist and does not start network retrieval.", parameters: Params, execute: (params, _config, context) => callApi("normalize", params, context.signal) }),
        tool({ name: "register_epidemic_query", optional: true, description: "Call only when the user explicitly asks to register or create a query. Idempotently writes only query_requests and does not start retrieval.", parameters: Params, execute: (params, _config, context) => callApi("register", params, context.signal) }),
    ],
});
