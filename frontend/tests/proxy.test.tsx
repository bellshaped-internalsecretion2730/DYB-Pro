import { afterEach, describe, expect, it, vi } from "vitest";

import { NextRequest } from "next/server";

import { GET, POST } from "@/app/api/dyb-pro/[...path]/route";

function req(path: string, method = "GET"): NextRequest {
  return new NextRequest(`http://localhost:3000/api/dyb-pro/${path}`, { method });
}

function params(path: string[]) {
  return { params: Promise.resolve({ path }) };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("backend proxy", () => {
  it("reports an unreachable backend as 503", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(Object.assign(new Error("connect ECONNREFUSED"), { name: "Error" })),
    );

    const res = await GET(req("lab/projects"), params(["lab", "projects"]));
    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ detail: "design engine is not reachable" });
  });

  it("reports a slow daemon pass as 504 still-running, not as an outage", async () => {
    // The daemon fans out to Devin children, so the pass outlives the request: the proxy must
    // say the agents are still working instead of claiming the engine is down.
    vi.stubGlobal(
      "fetch",
      vi.fn((_target: string, init: RequestInit) => {
        return new Promise((_resolve, reject) => {
          init.signal?.addEventListener("abort", () =>
            reject(Object.assign(new Error("aborted"), { name: "AbortError" })),
          );
        });
      }),
    );
    vi.useFakeTimers();

    const pending = POST(
      req("lab/projects/p1/research/process", "POST"),
      params(["lab", "projects", "p1", "research", "process"]),
    );
    await vi.advanceTimersByTimeAsync(900_000);
    const res = await pending;

    expect(res.status).toBe(504);
    expect(await res.json()).toEqual(
      expect.objectContaining({ detail: expect.stringContaining("still running after 900s") }),
    );
  });
});
