"use client";

export type WebGLSupport = { ok: boolean; webgl2: boolean; error?: string };

/**
 * Probe for a usable WebGL context before Mol* touches the canvas, so a machine
 * without one gets an honest message instead of an uncaught plugin error.
 * `failIfMajorPerformanceCaveat: false` matches the plugin config we run with, so
 * software rendering counts as available.
 */
export function detectWebGL(): WebGLSupport {
  if (typeof document === "undefined") return { ok: false, webgl2: false, error: "no document" };
  const attribs: WebGLContextAttributes = { failIfMajorPerformanceCaveat: false };
  try {
    const canvas = document.createElement("canvas");
    if (canvas.getContext("webgl2", attribs)) return { ok: true, webgl2: true };
    if (canvas.getContext("webgl", attribs) || canvas.getContext("experimental-webgl", attribs)) {
      return { ok: true, webgl2: false };
    }
    return { ok: false, webgl2: false, error: "no WebGL context could be created" };
  } catch (err) {
    return { ok: false, webgl2: false, error: String(err) };
  }
}
