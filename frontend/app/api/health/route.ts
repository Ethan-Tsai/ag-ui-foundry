export const runtime = "nodejs";

export async function GET() {
  const AG_UI_ENDPOINT = process.env.AG_UI_ENDPOINT ?? "http://localhost:8000/ag-ui";
  
  try {
    // Attempt to hit the base URL of the backend (e.g. http://localhost:8000/)
    const baseUrl = AG_UI_ENDPOINT.replace("/ag-ui", "");
    const targetUrl = baseUrl === "" ? "http://localhost:8000" : baseUrl;
    
    // Add a short timeout so the UI doesn't hang forever
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 2000);
    
    const res = await fetch(targetUrl, { signal: controller.signal });
    clearTimeout(timeoutId);
    
    if (res.ok) {
      return Response.json({ status: "online" });
    }
  } catch (e) {
    // Error connecting
  }
  
  return Response.json({ status: "offline" }, { status: 503 });
}
