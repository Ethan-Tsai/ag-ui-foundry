export const runtime = "nodejs";

function getBackendBase(): string {
  const endpoint = process.env.AG_UI_ENDPOINT ?? "http://localhost:8000/ag-ui";
  return endpoint.replace(/\/ag-ui$/, "") || "http://localhost:8000";
}

export async function POST(request: Request): Promise<Response> {
  try {
    const formData = await request.formData();
    const targetUrl = `${getBackendBase()}/api/upload`;
    const response = await fetch(targetUrl, {
      method: "POST",
      body: formData,
    });

    const contentType = response.headers.get("content-type") ?? "application/json";
    return new Response(response.body, {
      status: response.status,
      headers: { "content-type": contentType },
    });
  } catch {
    return Response.json(
      { error: "Backend unreachable", uploaded: {}, failed: [] },
      { status: 503 },
    );
  }
}
