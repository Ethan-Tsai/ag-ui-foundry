export const runtime = "nodejs";

function getBackendBase(): string {
  const endpoint = process.env.AG_UI_ENDPOINT ?? "http://localhost:8000/ag-ui";
  return endpoint.replace(/\/ag-ui$/, "") || "http://localhost:8000";
}

export async function GET(request: Request): Promise<Response> {
  const requestUrl = new URL(request.url);
  const fileId = requestUrl.searchParams.get("file_id");
  if (!fileId) {
    return Response.json({ error: "file_id is required" }, { status: 400 });
  }

  const targetUrl = new URL(`${getBackendBase()}/api/download`);
  targetUrl.search = requestUrl.search;

  try {
    const response = await fetch(targetUrl, { method: "GET" });
    const headers = new Headers();
    const contentType = response.headers.get("content-type");
    const contentDisposition = response.headers.get("content-disposition");
    if (contentType) {
      headers.set("content-type", contentType);
    }
    if (contentDisposition) {
      headers.set("content-disposition", contentDisposition);
    }
    return new Response(response.body, { status: response.status, headers });
  } catch {
    return Response.json({ error: "Backend unreachable" }, { status: 503 });
  }
}
