import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const SEGMENT = /^[A-Za-z0-9._-]+$/;
const ALLOWED_METHODS = new Set(["GET", "POST", "PATCH", "DELETE"]);

async function handleRequest(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  if (!path.every((segment) => SEGMENT.test(segment))) {
    return Response.json({ detail: "invalid path" }, { status: 400, headers: noStore() });
  }
  if (!ALLOWED_METHODS.has(request.method)) {
    return Response.json({ detail: "method not allowed" }, { status: 405, headers: noStore() });
  }

  const backend = (process.env.FOLDSMITH_BACKEND_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
  const target = `${backend}/api/${path.join("/")}${request.nextUrl.search}`;
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  headers.set(
    "x-api-key",
    request.headers.get("x-api-key") ||
      process.env.FOLDSMITH_API_KEY ||
      "foldsmith-demo-scientist",
  );

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 120_000);
  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" ? undefined : await request.arrayBuffer(),
      signal: controller.signal,
      cache: "no-store",
    });
    const responseHeaders = noStore();
    for (const name of ["content-type", "content-disposition"]) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    return new Response(await upstream.arrayBuffer(), {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch {
    return Response.json(
      { detail: "design engine is not reachable" },
      { status: 503, headers: noStore() },
    );
  } finally {
    clearTimeout(timer);
  }
}

export const GET = handleRequest;
export const POST = handleRequest;
export const PATCH = handleRequest;
export const DELETE = handleRequest;

function noStore(): Headers {
  return new Headers({ "Cache-Control": "private, no-store" });
}
