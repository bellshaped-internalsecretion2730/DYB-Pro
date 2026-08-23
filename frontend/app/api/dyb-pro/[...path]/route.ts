import { getCloudflareContext } from "@opennextjs/cloudflare";
import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const SEGMENT = /^[A-Za-z0-9._-]+$/;
const ALLOWED_METHODS = new Set(["GET", "POST", "PATCH", "DELETE"]);

// A daemon pass or a design cycle fans out to Devin child sessions, so it runs for minutes.
const SLOW_SEGMENTS = ["research", "daemon", "cycles", "wetlab"];
const FAST_TIMEOUT_MS = 120_000;
const SLOW_TIMEOUT_MS = 900_000;

function timeoutFor(path: string[]): number {
  return path.some((segment) => SLOW_SEGMENTS.includes(segment))
    ? SLOW_TIMEOUT_MS
    : FAST_TIMEOUT_MS;
}

async function handleRequest(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  if (!path.length || !path.every((segment) => SEGMENT.test(segment))) {
    return Response.json({ detail: "invalid path" }, { status: 400, headers: noStore() });
  }
  if (!ALLOWED_METHODS.has(request.method)) {
    return Response.json({ detail: "method not allowed" }, { status: 405, headers: noStore() });
  }

  const cloudflareEnv = getCloudflareEnv();
  const configuredBackend = process.env.DYB_PRO_BACKEND_URL?.replace(/\/$/, "");
  const backendBinding = configuredBackend ? undefined : cloudflareEnv?.BACKEND;
  const backend = configuredBackend || "http://127.0.0.1:8000";
  const target = `${backendBinding ? "http://dyb-pro-api" : backend}/api/${path.join("/")}${request.nextUrl.search}`;
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const apiKey =
    request.headers.get("x-api-key") ||
    cloudflareEnv?.DYB_PRO_API_KEY ||
    process.env.DYB_PRO_API_KEY ||
    (process.env.NODE_ENV === "production" ? undefined : "dyb-pro-demo-scientist");
  if (!apiKey) {
    return Response.json(
      { detail: "the server-side DYB Pro API key is not configured" },
      { status: 503, headers: noStore() },
    );
  }
  headers.set("x-api-key", apiKey);

  const controller = new AbortController();
  const budgetMs = timeoutFor(path);
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, budgetMs);
  try {
    const init: RequestInit & { duplex?: "half" } = {
      method: request.method,
      headers,
      body: request.method === "GET" ? undefined : request.body,
      signal: controller.signal,
      cache: "no-store",
    };
    if (request.body) init.duplex = "half";
    const upstream = backendBinding
      ? await backendBinding.fetch(new Request(target, init))
      : await fetch(target, init);
    const responseHeaders = noStore();
    for (const name of ["content-type", "content-disposition"]) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch {
    // A timeout is not an outage: the backend keeps working, so say so instead of
    // claiming the engine is down.
    if (timedOut) {
      return Response.json(
        {
          detail: `still running after ${Math.round(budgetMs / 1000)}s — the agents are still ` +
            `working, reopen the pane in a moment to pick up the result`,
        },
        { status: 504, headers: noStore() },
      );
    }
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

function getCloudflareEnv(): CloudflareEnv | undefined {
  try {
    return getCloudflareContext().env;
  } catch {
    // Vitest and non-Cloudflare Next.js processes do not provide a request context.
    return undefined;
  }
}
