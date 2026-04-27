export const runtime = "nodejs";

export async function GET(request: Request) {
  const AG_UI_ENDPOINT = process.env.AG_UI_ENDPOINT ?? "http://localhost:8000/ag-ui";
  const { searchParams } = new URL(request.url);
  const agentName = searchParams.get("agent_name");
  
  if (!agentName) {
    return Response.json({ error: "agent_name is required" }, { status: 400 });
  }

  try {
    const baseUrl = AG_UI_ENDPOINT.replace("/ag-ui", "");
    const targetUrl = baseUrl === "" ? `http://localhost:8000/api/agent-info?agent_name=${encodeURIComponent(agentName)}` : `${baseUrl}/api/agent-info?agent_name=${encodeURIComponent(agentName)}`;
    
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 15000);
    
    const res = await fetch(targetUrl, { signal: controller.signal });
    clearTimeout(timeoutId);
    
    if (res.ok) {
      const data = await res.json();
      return Response.json(data);
    }
    
    return Response.json({ error: "Failed to fetch from backend", details: await res.text() }, { status: res.status });
  } catch (e) {
    return Response.json({ error: "Backend unreachable" }, { status: 503 });
  }
}
