export const runtime = "nodejs";

const AG_UI_ENDPOINT = process.env.AG_UI_ENDPOINT ?? "http://localhost:8000/ag-ui";
const BACKEND_BASE = process.env.AG_UI_ENDPOINT
  ? new URL(process.env.AG_UI_ENDPOINT).origin
  : "http://localhost:8000";

let cachedAgentNames: string[] | null = null;
let cacheTimestamp = 0;
const CACHE_TTL_MS = 60_000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeAgentId(agentId: string | undefined, fallback: string): string {
  return agentId?.trim() || fallback;
}

function normalizeFileIds(raw: unknown): Record<string, string> {
  if (isRecord(raw)) {
    const result: Record<string, string> = {};
    for (const [filename, fileId] of Object.entries(raw)) {
      const name = String(filename || "").trim();
      const id = String(fileId || "").trim();
      if (name && id) {
        result[name] = id;
      }
    }
    return result;
  }

  if (Array.isArray(raw)) {
    const result: Record<string, string> = {};
    raw.forEach((item, index) => {
      if (typeof item === "string" && item.trim()) {
        result[`file_${index + 1}`] = item.trim();
        return;
      }
      if (isRecord(item)) {
        const fileId = String(item.fileId ?? item.file_id ?? "").trim();
        const filename = String(item.filename ?? item.name ?? `file_${index + 1}`).trim();
        if (fileId && filename) {
          result[filename] = fileId;
        }
      }
    });
    return result;
  }

  return {};
}

function parseFileIdsFromQuery(requestUrl: URL): Record<string, string> {
  const raw = requestUrl.searchParams.get("fileIds");
  if (!raw) {
    return {};
  }
  try {
    const parsed = JSON.parse(raw);
    return normalizeFileIds(parsed);
  } catch {
    return {};
  }
}

function parseStructuredInputsFromQuery(requestUrl: URL): Record<string, unknown> {
  const raw = requestUrl.searchParams.get("structuredInputs");
  if (!raw) {
    return {};
  }
  try {
    const parsed = JSON.parse(raw);
    return isRecord(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

async function getAgentNames(): Promise<string[]> {
  const now = Date.now();
  if (cachedAgentNames && now - cacheTimestamp < CACHE_TTL_MS) {
    return cachedAgentNames;
  }

  try {
    const res = await fetch(`${BACKEND_BASE}/api/agents`, {
      signal: AbortSignal.timeout(4000),
    });
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data.agents) && data.agents.length > 0) {
        const names = data.agents.map((a: { name: string }) => a.name);
        cachedAgentNames = names;
        cacheTimestamp = now;
        return names;
      }
    }
  } catch {
    // Fall through to cached/default value.
  }

  return cachedAgentNames ?? [process.env.AZURE_AI_PROJECT_AGENT_NAME ?? "agent"];
}

function mergeForwardedProps(
  payload: Record<string, unknown>,
  normalizedAgent: string,
  fileIds: Record<string, string>,
  structuredInputs: Record<string, unknown>,
): void {
  const existingForwarded = isRecord(payload.forwardedProps) ? payload.forwardedProps : {};
  const merged: Record<string, unknown> = {
    ...existingForwarded,
    agentName: normalizedAgent,
  };

  if (Object.keys(fileIds).length > 0) {
    merged.fileIds = fileIds;
  }
  if (Object.keys(structuredInputs).length > 0) {
    merged.structuredInputs = structuredInputs;
  }

  payload.forwardedProps = merged;
  payload.forwarded_props = merged;
}

async function proxyRequest(request: Request): Promise<Response> {
  const requestUrl = new URL(request.url);
  const queryFileIds = parseFileIdsFromQuery(requestUrl);
  const queryStructuredInputs = parseStructuredInputsFromQuery(requestUrl);

  const targetUrl = new URL(AG_UI_ENDPOINT);
  targetUrl.search = requestUrl.search;
  targetUrl.searchParams.delete("fileIds");
  targetUrl.searchParams.delete("structuredInputs");

  const headers = new Headers(request.headers);
  headers.delete("host");

  let body: BodyInit | undefined;

  if (request.method !== "GET" && request.method !== "HEAD") {
    const contentType = request.headers.get("content-type") ?? "";

    if (contentType.includes("application/json")) {
      const json: unknown = await request.json();
      const method = isRecord(json) ? json.method : undefined;

      if (method === "info") {
        const agentNames = await getAgentNames();
        return Response.json({
          version: "0.0.0",
          audioFileTranscriptionEnabled: false,
          agents: Object.fromEntries(
            agentNames.map((name) => [
              name,
              { name, className: "AgentFrameworkAgent", description: "Project agent" },
            ]),
          ),
        });
      }

      const payloadSource = isRecord(json) && "body" in json ? json.body : json;
      const payload = isRecord(payloadSource) ? payloadSource : {};
      const params = isRecord(json) && isRecord(json.params) ? json.params : {};

      const fallbackName = process.env.AZURE_AI_PROJECT_AGENT_NAME ?? "agent";
      const normalized = normalizeAgentId(
        typeof params.agentId === "string" ? params.agentId : undefined,
        fallbackName,
      );

      if (!targetUrl.searchParams.has("agentId")) {
        targetUrl.searchParams.set("agentId", normalized);
      }

      if (isRecord(json) && "params" in json) {
        json.params = { ...params, agentId: normalized };
      }

      mergeForwardedProps(payload, normalized, queryFileIds, queryStructuredInputs);

      body = JSON.stringify(payload);
      headers.set("content-type", "application/json");
      headers.delete("content-length");
    } else {
      body = await request.arrayBuffer();
    }
  }

  const response = await fetch(targetUrl, {
    method: request.method,
    headers,
    body,
  });
  return new Response(response.body, { status: response.status, headers: response.headers });
}

export async function GET(request: Request): Promise<Response> {
  return proxyRequest(request);
}

export async function POST(request: Request): Promise<Response> {
  return proxyRequest(request);
}
