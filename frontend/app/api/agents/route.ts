export const runtime = "nodejs";

export async function GET() {
  const AG_UI_ENDPOINT = process.env.AG_UI_ENDPOINT ?? "http://localhost:8000/ag-ui";
  
  try {
    const baseUrl = AG_UI_ENDPOINT.replace("/ag-ui", "");
    const targetUrl = baseUrl === "" ? "http://localhost:8000/api/agents" : `${baseUrl}/api/agents`;
    
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    
    const res = await fetch(targetUrl, { signal: controller.signal });
    clearTimeout(timeoutId);
    
    if (res.ok) {
      const data = await res.json();
      return Response.json(data);
    }
    
    return Response.json({ error: "Failed to fetch from backend", agents: [] }, { status: res.status });
  } catch (e) {
    return Response.json({ error: "Backend unreachable", agents: [] }, { status: 503 });
  }
}
