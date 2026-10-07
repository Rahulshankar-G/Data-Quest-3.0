import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

async function proxy(request: NextRequest, context: { params: { path: string[] } }) {
  const apiUrl = process.env.ADPILOT_API_URL ?? "http://127.0.0.1:8000";
  const apiKey = process.env.ADPILOT_API_KEY ?? process.env.PROFITPILOT_API_KEY ?? "";
  const url = new URL(`/api/${context.params.path.join("/")}`, apiUrl);
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.set(key, value));
  const headers = new Headers();
  headers.set("X-API-Key", apiKey);
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer();
  try {
    const response = await fetch(url, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(60_000),
    });
    const responseBody = await response.arrayBuffer();
    return new NextResponse(responseBody, {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "API service is unavailable";
    return NextResponse.json({ detail: message }, { status: 502 });
  }
}

export const GET = proxy;
export const POST = proxy;
