const AA = "ACDEFGHIKLMNPQRSTVWY";

export type ParsedInput =
  | { kind: "sequence"; name: string; sequence: string; residues: number }
  | { kind: "structure"; format: "pdb" | "mmcif"; name: string; text: string; atoms: number }
  | { kind: "error"; message: string };

function structureFormat(text: string): "pdb" | "mmcif" | null {
  const head = text.trimStart().slice(0, 400);
  if (text.includes("_atom_site.") || head.startsWith("data_")) return "mmcif";
  if (/^(HEADER|TITLE|REMARK|ATOM|HETATM|MODEL|CRYST1)/m.test(head)) return "pdb";
  return null;
}

/**
 * Classifies pasted text exactly the way the backend's `detect_kind` does, so what the UI
 * claims about an input matches what an upload of the same text would produce.
 * Amino-acid sequences are cleaned, never guessed: an unknown letter is an error, not a gap.
 */
export function parseInput(raw: string, fallbackName = "pasted"): ParsedInput {
  const text = raw.trim();
  if (!text) return { kind: "error", message: "nothing pasted" };

  const format = structureFormat(text);
  if (format) {
    const atoms = text
      .split("\n")
      .filter((line) => line.startsWith("ATOM") || line.startsWith("HETATM")).length;
    if (format === "pdb" && atoms === 0) {
      return { kind: "error", message: "PDB text has no ATOM records" };
    }
    return { kind: "structure", format, name: fallbackName, text, atoms };
  }

  let name = fallbackName;
  let body = text;
  if (text.startsWith(">")) {
    const lines = text.split("\n");
    const records = lines.filter((l) => l.startsWith(">")).length;
    if (records > 1) {
      return { kind: "error", message: `multi-record FASTA (${records}); paste one sequence` };
    }
    name = lines[0].slice(1).trim().split(/\s+/)[0] || fallbackName;
    body = lines.slice(1).join("");
  }

  const sequence = body.replace(/[\s\d*-]/g, "").toUpperCase();
  if (!sequence) return { kind: "error", message: "no residues found" };
  const bad = Array.from(new Set(sequence.split("").filter((c) => !AA.includes(c))));
  if (bad.length > 0) {
    return { kind: "error", message: `not amino acids: ${bad.slice(0, 6).join(" ")}` };
  }
  if (sequence.length < 10) {
    return { kind: "error", message: `only ${sequence.length} residues; need at least 10` };
  }
  return { kind: "sequence", name, sequence, residues: sequence.length };
}

/** Positions (1-based) where two sequences differ, for the honest local comparison. */
export function diffPositions(a: string, b: string): number[] {
  const out: number[] = [];
  const shared = Math.min(a.length, b.length);
  for (let i = 0; i < shared; i += 1) if (a[i] !== b[i]) out.push(i + 1);
  for (let i = shared; i < Math.max(a.length, b.length); i += 1) out.push(i + 1);
  return out;
}

/** Wraps pasted text as a File so the existing upload endpoint can ingest it unchanged. */
export function asFile(parsed: ParsedInput): File | null {
  if (parsed.kind === "sequence") {
    const fasta = `>${parsed.name}\n${parsed.sequence.replace(/(.{60})/g, "$1\n")}\n`;
    return new File([fasta], `${parsed.name}.fasta`, { type: "text/x-fasta" });
  }
  if (parsed.kind === "structure") {
    const ext = parsed.format === "pdb" ? "pdb" : "cif";
    return new File([parsed.text], `${parsed.name}.${ext}`, {
      type: parsed.format === "pdb" ? "chemical/x-pdb" : "chemical/x-cif",
    });
  }
  return null;
}
