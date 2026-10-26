import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
const ORIGIN = "http://127.0.0.1:8010";
const MAX_RESPONSE = 65_536;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const Query = Type.Object({ country: Type.String({ minLength: 1, maxLength: 128 }), disease: Type.String({ minLength: 1, maxLength: 128 }), start_date: Type.String({ minLength: 10, maxLength: 10, pattern: "^\\d{4}-\\d{2}-\\d{2}$" }), end_date: Type.String({ minLength: 10, maxLength: 10, pattern: "^\\d{4}-\\d{2}-\\d{2}$" }) }, { additionalProperties: false });
const Start = Type.Object({ query_id: Type.String({ pattern: UUID.source }) }, { additionalProperties: false });
const Status = Type.Object({ retrieval_run_id: Type.String({ pattern: UUID.source }) }, { additionalProperties: false });
async function readLimited(r) { const n = r.headers.get("content-length"); if (n !== null && Number(n) > MAX_RESPONSE)
    throw new Error("RESPONSE_TOO_LARGE"); if (!r.headers.get("content-type")?.toLowerCase().startsWith("application/json"))
    throw new Error("INVALID_RESPONSE"); const reader = r.body?.getReader(); if (!reader)
    throw new Error("INVALID_RESPONSE"); const chunks = []; let size = 0; while (true) {
    const x = await reader.read();
    if (x.done)
        break;
    size += x.value.length;
    if (size > MAX_RESPONSE) {
        await reader.cancel();
        throw new Error("RESPONSE_TOO_LARGE");
    }
    chunks.push(x.value);
} const bytes = new Uint8Array(size); let offset = 0; for (const x of chunks) {
    bytes.set(x, offset);
    offset += x.length;
} try {
    return JSON.parse(new TextDecoder().decode(bytes));
}
catch {
    throw new Error("INVALID_RESPONSE");
} }
export async function api(method, path, body, signal) { const token = process.env.EPIDEMIC_QUERY_API_TOKEN; if (!token || token.length < 32)
    throw new Error("AUTH_UNAVAILABLE"); const timeout = AbortSignal.timeout(10_000); const combined = signal ? AbortSignal.any([signal, timeout]) : timeout; let r; try {
    r = await fetch(ORIGIN + path, { method, redirect: "error", signal: combined, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
}
catch {
    throw new Error("SERVICE_UNAVAILABLE");
} if (!r.ok) {
    if (r.status === 401)
        throw new Error("AUTH_FAILED");
    if (r.status === 404)
        throw new Error("NOT_FOUND");
    if (r.status === 409)
        throw new Error("RETRY_EXHAUSTED");
    if (r.status === 422)
        throw new Error("NO_COMPATIBLE_SOURCE");
    throw new Error("SERVICE_UNAVAILABLE");
} return readLimited(r); }
function id(value) { if (!UUID.test(value))
    throw new Error("INVALID_REQUEST"); return value; }
export default defineToolPlugin({ id: "epidemic-query-tools", name: "Epidemic Query Tools", description: "Normalize, register, and run the controlled WHO retrieval-to-snapshot pipeline without prediction.", activation: { onStartup: true }, configSchema: Type.Object({}, { additionalProperties: false }), tools: (tool) => [
        tool({ name: "normalize_epidemic_query", optional: true, description: "Read-only country, disease, and date normalization. Does not persist and does not start network retrieval.", parameters: Query, execute: (p, _c, x) => api("POST", "/v1/queries/normalize", p, x.signal) }),
        tool({ name: "register_epidemic_query", optional: true, description: "Call only when explicitly asked to register a query. Idempotently writes only query_requests and does not start retrieval.", parameters: Query, execute: (p, _c, x) => api("POST", "/v1/queries", p, x.signal) }),
        tool({ name: "start_epidemic_retrieval", optional: true, description: "Call only when explicitly asked to retrieve network epidemic data for a registered query. Enqueues controlled raw retrieval; after success the Worker automatically normalizes and creates a deterministic training-format snapshot. It does not predict.", parameters: Start, execute: (p, _c, x) => api("POST", `/v1/queries/${id(p.query_id)}/retrievals`, {}, x.signal) }),
        tool({ name: "get_epidemic_retrieval_status", optional: true, description: "Read-only retrieval, normalization, snapshot, and quality status. It explicitly reports that no prediction was performed.", parameters: Status, execute: (p, _c, x) => api("GET", `/v1/retrievals/${id(p.retrieval_run_id)}`, undefined, x.signal) })
    ] });
