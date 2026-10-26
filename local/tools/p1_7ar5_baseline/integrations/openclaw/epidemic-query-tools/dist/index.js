import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
const ORIGIN = "http://127.0.0.1:8010";
const MAX_RESPONSE = 65_536;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const Entity = { country: Type.String({ minLength: 1, maxLength: 128 }), disease: Type.String({ minLength: 1, maxLength: 128 }) };
const Query = Type.Union([
    Type.Object({ ...Entity, start_date: Type.String({ minLength: 10, maxLength: 10, pattern: "^\\d{4}-\\d{2}-\\d{2}$" }), end_date: Type.String({ minLength: 10, maxLength: 10, pattern: "^\\d{4}-\\d{2}-\\d{2}$" }), source_preference: Type.Optional(Type.Literal("official")) }, { additionalProperties: false }),
    Type.Object({ ...Entity, time_window_kind: Type.Union([Type.Literal("latest_rolling_year"), Type.Literal("latest_complete_calendar_year"), Type.Literal("current_year_to_latest_period")]), source_preference: Type.Optional(Type.Literal("official")) }, { additionalProperties: false })
]);
const Start = Type.Object({ query_id: Type.String({ pattern: UUID.source }) }, { additionalProperties: false });
const Status = Type.Object({ retrieval_run_id: Type.String({ pattern: UUID.source }) }, { additionalProperties: false });
const Discovery = Type.Object({ country_iso3: Type.String({ pattern: "^[A-Z]{3}$" }), disease_id: Type.String({ pattern: UUID.source }), desired_metrics: Type.Array(Type.Union([Type.Literal("cases_new"), Type.Literal("deaths_new"), Type.Literal("incidence_rate")]), { minItems: 1, maxItems: 10 }), time_window_kind: Type.Union([Type.Literal("latest_rolling_year"), Type.Literal("latest_complete_calendar_year"), Type.Literal("current_year_to_latest_period")]), preferred_source_tiers: Type.Array(Type.Union([Type.Literal("NATIONAL_HEALTH"), Type.Literal("GOVERNMENT_OPEN_DATA"), Type.Literal("REGIONAL_HEALTH"), Type.Literal("INTERNATIONAL_PUBLIC_HEALTH"), Type.Literal("PUBLIC_HEALTH_RESEARCH")]), { minItems: 1, maxItems: 5 }), language_preferences: Type.Array(Type.String({ minLength: 2, maxLength: 16 }), { maxItems: 5 }), max_candidates: Type.Integer({ minimum: 1, maximum: 10 }) }, { additionalProperties: false });
const DiscoveryStatus = Type.Object({ discovery_run_id: Type.String({ pattern: UUID.source }) }, { additionalProperties: false });
const CandidateSubmission = Type.Object({ discovery_run_id: Type.String({ pattern: UUID.source }), canonical_url: Type.String({ pattern: "^https://", minLength: 12, maxLength: 2048 }), title: Type.String({ minLength: 1, maxLength: 500 }), publisher: Type.String({ minLength: 1, maxLength: 500 }), provider_id: Type.String({ pattern: "^[a-z0-9][a-z0-9._-]{1,119}$" }), query_sha256: Type.String({ pattern: "^[0-9a-f]{64}$" }) }, { additionalProperties: false });
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
    throw new Error("AUTH_UNAVAILABLE"); const timeout = AbortSignal.timeout(method === "POST" && path.endsWith("/retrievals") ? 180_000 : 10_000); const combined = signal ? AbortSignal.any([signal, timeout]) : timeout; let r; try {
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
export default defineToolPlugin({ id: "epidemic-query-tools", name: "Epidemic Query Tools", description: "Run controlled WHO COVID-19 or dengue retrieval-to-snapshot pipelines without prediction.", activation: { onStartup: true }, configSchema: Type.Object({}, { additionalProperties: false }), tools: (tool) => [
        tool({ name: "normalize_epidemic_query", optional: true, description: "Read-only country, disease, and time intent normalization. For 最近一年/最新一年/过去一年/过去12个月 use time_window_kind=latest_rolling_year; never guess dates. Explicit dates remain unchanged. Does not persist or retrieve.", parameters: Query, execute: (p, _c, x) => api("POST", "/v1/queries/normalize", p, x.signal) }),
        tool({ name: "register_epidemic_query", optional: true, description: "Idempotently persist the normalized query, including its relative time intent. Never rewrite a relative intent to fixed dates. Does not start retrieval.", parameters: Query, execute: (p, _c, x) => api("POST", "/v1/queries", p, x.signal) }),
        tool({ name: "start_epidemic_retrieval", optional: true, description: "For a registered query, probe the fixed official WHO source watermark server-side, freeze the resolved range for this run, then retrieve, normalize, quality-check and create an immutable snapshot. Never supply guessed dates. It does not predict.", parameters: Start, execute: (p, _c, x) => api("POST", `/v1/queries/${id(p.query_id)}/retrievals`, {}, x.signal) }),
        tool({ name: "get_epidemic_retrieval_status", optional: true, description: "Read-only status including frozen window, source and metric watermarks, freshness, coverage, artifact SHA, immutable snapshot and dashboard deep link. It explicitly reports prediction_performed=false.", parameters: Status, execute: (p, _c, x) => api("GET", `/v1/retrievals/${id(p.retrieval_run_id)}`, undefined, x.signal) }),
        tool({ name: "start_epidemic_source_discovery", optional: true, description: "Use only when the user explicitly requests a new, alternative, additional, or currently unsupported public source. Enqueues controlled catalog discovery; accepts no URL, code, credentials, headers, cookies, or paths. Never approves or imports a candidate.", parameters: Discovery, execute: (p, _c, x) => api("POST", "/v1/source-discovery", p, x.signal) }),
        tool({ name: "get_epidemic_source_discovery_status", optional: true, description: "Read-only discovery and quarantine review status. Reports candidates, risks, review_required=true, and the local review-console link. Candidates are not formal data and are never approved by this tool.", parameters: DiscoveryStatus, execute: (p, _c, x) => api("GET", `/v1/source-discovery/${id(p.discovery_run_id)}`, undefined, x.signal) }),
        tool({ name: "submit_source_candidate", optional: true, description: "For the isolated source-discovery agent only. Submit one public HTTPS result for deterministic URL safety checks and pending human-review staging. It never approves, promotes, downloads, or writes formal data.", parameters: CandidateSubmission, execute: (p, _c, x) => api("POST", `/v1/source-discovery/${id(p.discovery_run_id)}/agent-candidates`, { canonical_url: p.canonical_url, title: p.title, publisher: p.publisher, provider_id: p.provider_id, query_sha256: p.query_sha256 }, x.signal) })
    ] });
