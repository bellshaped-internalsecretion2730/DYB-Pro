import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import SequenceLoader from "@/components/SequenceLoader";
import { asFile, diffPositions, parseInput } from "@/lib/seqinput";
import { residueOf } from "@/components/StructureViewer";
import { detectWebGL } from "@/lib/webgl";
import { project } from "./fixtures";

const PDB = [
  "REMARK  DYB Pro CA trace (model:template-threading:seq-I6E) seq-L5T",
  "ATOM      1  CA  MET A   1       3.206   0.960  -0.000  1.00  0.00           C",
  "ATOM      2  CA  THR A   2       6.349  -1.176   0.002  1.00  0.00           C",
].join("\n");

describe("pasted input is classified, never guessed", () => {
  it("reads a plain sequence", () => {
    const parsed = parseInput("MTYKLILNGKTLKGETTTEA");
    expect(parsed).toMatchObject({ kind: "sequence", residues: 20 });
  });

  it("reads single-record FASTA and names it from the header", () => {
    const parsed = parseInput(">GB1 domain\nMTYKLILNGK\nTLKGETTTEA\n");
    expect(parsed).toMatchObject({ kind: "sequence", name: "GB1", residues: 20 });
  });

  it("rejects multi-record FASTA, junk letters and stubs", () => {
    expect(parseInput(">a\nMTYKLILNGK\n>b\nMTYKLILNGK").kind).toBe("error");
    expect(parseInput("MTYKLILNGKXBZJ")).toMatchObject({ kind: "error" });
    expect(parseInput("MTYK")).toMatchObject({ kind: "error" });
    expect(parseInput("   ")).toMatchObject({ kind: "error" });
  });

  it("recognises PDB and mmCIF coordinates", () => {
    expect(parseInput(PDB)).toMatchObject({ kind: "structure", format: "pdb", atoms: 2 });
    expect(parseInput("data_test\n_atom_site.id\n1\n")).toMatchObject({
      kind: "structure",
      format: "mmcif",
    });
    expect(parseInput("HEADER something\nTITLE nothing\n").kind).toBe("error");
  });

  it("wraps input as the file the upload endpoint already ingests", () => {
    const fasta = asFile(parseInput("MTYKLILNGKTLKGETTTEA"));
    expect(fasta?.name).toBe("pasted.fasta");
    expect(asFile(parseInput(PDB))?.name).toBe("pasted.pdb");
    expect(asFile(parseInput("nope"))).toBeNull();
  });

  it("diffs position by position without pretending to align", () => {
    expect(diffPositions("MTYK", "MTIK")).toEqual([3]);
    expect(diffPositions("MTYK", "MTYKL")).toEqual([5]);
  });
});

describe("sequence loader stays honest about what loading means", () => {
  const renderLoader = () =>
    render(
      <SequenceLoader
        project={project}
        referenceSequence="MTYKLILNGKTLKGETTTEA"
        referenceLabel="GB1-v12"
        busy={false}
        onRenderStructure={vi.fn()}
        onCommitFile={vi.fn()}
        onSetTarget={vi.fn()}
        onCompare={vi.fn()}
      />,
    );

  it("states that target sequences feed AlphaFold and docking", () => {
    renderLoader();
    fireEvent.click(screen.getByRole("button", { name: "Protein + target" }));
    expect(screen.getByText(/target sequence feeds AlphaFold and docking/)).toBeInTheDocument();
    expect(screen.getByText(/pasted sequences get no coordinates/)).toBeInTheDocument();
    expect(screen.getByLabelText("working sequence or structure")).toBeInTheDocument();
  });
});

describe("viewer guards", () => {
  it("parses residue numbers out of mutation labels", () => {
    expect(residueOf("T2I")).toBe(2);
    expect(residueOf("root")).toBeNull();
  });

  it("reports WebGL honestly instead of throwing", () => {
    const getContext = vi
      .spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockImplementation(() => {
        throw new Error("blocked");
      });
    expect(detectWebGL()).toMatchObject({ ok: false });
    getContext.mockImplementation(((id: string) => (id === "webgl" ? {} : null)) as never);
    expect(detectWebGL()).toMatchObject({ ok: true, webgl2: false });
    getContext.mockRestore();
  });
});
