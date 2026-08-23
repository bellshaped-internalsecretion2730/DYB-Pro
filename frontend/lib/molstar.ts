"use client";

import type { PluginUIContext } from "molstar/lib/mol-plugin-ui/context";
import type { Structure, StructureElement } from "molstar/lib/mol-model/structure";
import type { StructureRef } from "molstar/lib/mol-plugin-state/manager/structure/hierarchy-state";

/**
 * Thin imperative wrapper over the Mol* plugin, restricted to the operations that are
 * scientifically honest on a Cα-only coarse trace: no surfaces, no side chains, no
 * B-factor/confidence colouring. Everything is loaded through dynamic imports so the
 * bundle stays out of the server render path.
 */

/** Representations that render meaningfully on a Cα-only trace. */
export type Repr = "cartoon" | "backbone" | "spacefill";

/** Colour themes that carry real information for Cα-only coordinates. */
export type ColorTheme = "sequence-id" | "chain-id" | "residue-name" | "hydrophobicity" | "uniform";

/** Canvas-only appearance. This never changes molecular coordinates or exported files. */
export type ViewerBackground = "studio" | "light";

export const REPR_LABELS: Record<Repr, string> = {
  cartoon: "tube",
  backbone: "Cα trace",
  spacefill: "Cα spheres",
};

export const COLOR_LABELS: Record<ColorTheme, string> = {
  "sequence-id": "position",
  "chain-id": "chain",
  "residue-name": "residue",
  hydrophobicity: "hydrophobicity",
  uniform: "uniform",
};

const VIEWER_BACKGROUND_COLORS: Record<ViewerBackground, number> = {
  studio: 0xfcfbf9,
  light: 0xffffff,
};

export async function createViewer(target: HTMLDivElement): Promise<PluginUIContext> {
  const [{ createPluginUI }, { renderReact18 }, { DefaultPluginUISpec }, { PluginConfig }] =
    await Promise.all([
      import("molstar/lib/mol-plugin-ui/index"),
      import("molstar/lib/mol-plugin-ui/react18"),
      import("molstar/lib/mol-plugin-ui/spec"),
      import("molstar/lib/mol-plugin/config"),
    ]);
  return createPluginUI({
    target,
    render: renderReact18,
    spec: {
      ...DefaultPluginUISpec(),
      layout: { initial: { isExpanded: false, showControls: false } },
      components: {
        remoteState: "none",
        controls: { top: "none", bottom: "none", left: "none", right: "none" },
      },
      // Mol* rejects software (SwiftShader) WebGL by default, which is the only WebGL
      // available on GPU-less machines; without this the viewer never initialises.
      config: [[PluginConfig.General.AllowMajorPerformanceCaveat, true]],
    },
  });
}

/** Updates Mol*'s real WebGL clear colour, including screenshots and full screen. */
export async function setViewerBackground(
  plugin: PluginUIContext,
  background: ViewerBackground,
): Promise<void> {
  const { Color } = await import("molstar/lib/mol-util/color");
  plugin.canvas3d?.setProps({
    renderer: { backgroundColor: Color(VIEWER_BACKGROUND_COLORS[background]) },
  });
}

function firstStructure(plugin: PluginUIContext): StructureRef | undefined {
  return plugin.managers.structure.hierarchy.current.structures[0];
}

function structureData(plugin: PluginUIContext, index = 0): Structure | undefined {
  return plugin.managers.structure.hierarchy.current.structures[index]?.cell.obj?.data;
}

/** Replaces whatever is loaded with `pdb`, rendered as `repr` coloured by `color`. */
export async function loadStructure(
  plugin: PluginUIContext,
  pdb: string,
  label: string,
  repr: Repr,
  color: ColorTheme,
): Promise<void> {
  await plugin.clear();
  const data = await plugin.builders.data.rawData({ data: pdb, label });
  const trajectory = await plugin.builders.structure.parseTrajectory(data, "pdb");
  const model = await plugin.builders.structure.createModel(trajectory);
  const structure = await plugin.builders.structure.createStructure(model);
  // A component (not the bare structure) is what the managers-based APIs operate on.
  const component = await plugin.builders.structure.tryCreateComponentStatic(structure, "all");
  if (component) {
    await plugin.builders.structure.representation.addRepresentation(component, {
      type: repr,
      color,
    });
  }
  plugin.managers.camera.reset();
}

/** Adds a second structure so two versions can be superposed; returns its index. */
export async function addStructure(
  plugin: PluginUIContext,
  pdb: string,
  label: string,
  repr: Repr,
): Promise<number> {
  const data = await plugin.builders.data.rawData({ data: pdb, label });
  const trajectory = await plugin.builders.structure.parseTrajectory(data, "pdb");
  const model = await plugin.builders.structure.createModel(trajectory);
  const structure = await plugin.builders.structure.createStructure(model);
  const component = await plugin.builders.structure.tryCreateComponentStatic(structure, "all");
  if (component) {
    await plugin.builders.structure.representation.addRepresentation(component, {
      type: repr,
      color: "uniform",
      colorParams: { value: 0x06b6d4 },
    });
  }
  return plugin.managers.structure.hierarchy.current.structures.length - 1;
}

export async function setRepresentation(plugin: PluginUIContext, repr: Repr): Promise<void> {
  const structures = plugin.managers.structure.hierarchy.current.structures;
  for (const structure of structures) {
    for (const component of structure.components) {
      for (const representation of component.representations) {
        await plugin.state.data
          .build()
          .to(representation.cell.transform.ref)
          .update((old: { type: { name: string } }) => {
            old.type.name = repr;
          })
          .commit();
      }
    }
  }
}

export async function setColorTheme(plugin: PluginUIContext, color: ColorTheme): Promise<void> {
  const structure = firstStructure(plugin);
  if (!structure) return;
  await plugin.managers.structure.component.updateRepresentationsTheme(structure.components, {
    color,
  });
}

async function residueLoci(
  plugin: PluginUIContext,
  from: number,
  to: number,
  index = 0,
): Promise<StructureElement.Loci | null> {
  const structure = structureData(plugin, index);
  if (!structure) return null;
  const [{ Script }, { StructureSelection }] = await Promise.all([
    import("molstar/lib/mol-script/script"),
    import("molstar/lib/mol-model/structure"),
  ]);
  const selection = Script.getStructureSelection(
    (Q) =>
      Q.struct.generator.atomGroups({
        "residue-test": Q.core.rel.inRange([
          Q.struct.atomProperty.macromolecular.auth_seq_id(),
          from,
          to,
        ]),
        "group-by": Q.struct.atomProperty.macromolecular.residueKey(),
      }),
    structure,
  );
  return StructureSelection.toLociWithSourceUnits(selection);
}

export async function highlightResidues(
  plugin: PluginUIContext,
  from: number,
  to: number,
): Promise<void> {
  const loci = await residueLoci(plugin, from, to);
  if (loci) plugin.managers.interactivity.lociHighlights.highlightOnly({ loci });
}

export function clearHighlight(plugin: PluginUIContext): void {
  plugin.managers.interactivity.lociHighlights.clearHighlights();
}

export async function selectResidues(
  plugin: PluginUIContext,
  from: number,
  to: number,
): Promise<void> {
  const loci = await residueLoci(plugin, from, to);
  if (loci) plugin.managers.interactivity.lociSelects.selectOnly({ loci });
}

export function clearSelection(plugin: PluginUIContext): void {
  plugin.managers.interactivity.lociSelects.deselectAll();
  plugin.managers.structure.selection.clear();
}

/** Paints residue ranges (e.g. the mutations of this version) on top of the base theme. */
export async function paintResidues(
  plugin: PluginUIContext,
  ranges: [number, number][],
  colorHex: number,
): Promise<void> {
  const structure = firstStructure(plugin);
  if (!structure || ranges.length === 0) return;
  const [{ setStructureOverpaint }, { Color }, { Script }, { StructureSelection }] =
    await Promise.all([
      import("molstar/lib/mol-plugin-state/helpers/structure-overpaint"),
      import("molstar/lib/mol-util/color"),
      import("molstar/lib/mol-script/script"),
      import("molstar/lib/mol-model/structure"),
    ]);
  await setStructureOverpaint(plugin, structure.components, Color(colorHex), async (s) => {
    const selection = Script.getStructureSelection(
      (Q) =>
        Q.struct.generator.atomGroups({
          "residue-test": Q.core.logic.or(
            ranges.map(([from, to]) =>
              Q.core.rel.inRange([
                Q.struct.atomProperty.macromolecular.auth_seq_id(),
                from,
                to,
              ]),
            ),
          ),
          "group-by": Q.struct.atomProperty.macromolecular.residueKey(),
        }),
      s,
    );
    return StructureSelection.toLociWithSourceUnits(selection);
  });
}

export async function clearPaint(plugin: PluginUIContext): Promise<void> {
  const structure = firstStructure(plugin);
  if (!structure) return;
  const [{ clearStructureOverpaint }] = await Promise.all([
    import("molstar/lib/mol-plugin-state/helpers/structure-overpaint"),
  ]);
  await clearStructureOverpaint(plugin, structure.components);
}

export async function focusResidues(
  plugin: PluginUIContext,
  from: number,
  to: number,
): Promise<void> {
  const loci = await residueLoci(plugin, from, to);
  if (loci) plugin.managers.camera.focusLoci(loci);
}

export async function labelResidue(plugin: PluginUIContext, residue: number): Promise<void> {
  const loci = await residueLoci(plugin, residue, residue);
  if (loci) await plugin.managers.structure.measurement.addLabel(loci);
}

/** Cα–Cα distance between two residues of the loaded model. */
export async function measureDistance(
  plugin: PluginUIContext,
  a: number,
  b: number,
): Promise<void> {
  const first = await residueLoci(plugin, a, a);
  const second = await residueLoci(plugin, b, b);
  if (!first || !second) return;
  await plugin.managers.structure.measurement.addDistance(first, second);
}

export function clearMeasurements(plugin: PluginUIContext): void {
  const { labels, distances, angles, dihedrals } = plugin.managers.structure.measurement.state;
  for (const group of [labels, distances, angles, dihedrals]) {
    for (const cell of group) void plugin.state.data.build().delete(cell.transform.ref).commit();
  }
}

/**
 * Superposes structure 2 onto structure 1 over paired Cα atoms and returns the
 * Cα-trace RMSD. This compares two coarse models, not two experimental structures.
 */
export async function superposeLoaded(plugin: PluginUIContext): Promise<number | null> {
  const refs = plugin.managers.structure.hierarchy.current.structures;
  if (refs.length < 2) return null;
  const a = refs[0].cell.obj?.data;
  const b = refs[1].cell.obj?.data;
  if (!a || !b) return null;
  const [{ StructureElement }, { alignAndSuperpose }, { StateTransforms }] = await Promise.all([
    import("molstar/lib/mol-model/structure"),
    import("molstar/lib/mol-model/structure/structure/util/superposition"),
    import("molstar/lib/mol-plugin-state/transforms"),
  ]);
  const [result] = alignAndSuperpose([StructureElement.Loci.all(a), StructureElement.Loci.all(b)]);
  if (!result) return null;
  await plugin
    .build()
    .to(refs[1].cell.transform.ref)
    .insert(StateTransforms.Model.TransformStructureConformation, {
      transform: { name: "matrix", params: { data: result.bTransform, transpose: false } },
    })
    .commit();
  return result.rmsd;
}

export function resetCamera(plugin: PluginUIContext): void {
  plugin.managers.camera.reset();
}

export async function screenshot(plugin: PluginUIContext): Promise<string | null> {
  const helper = plugin.helpers.viewportScreenshot;
  if (!helper) return null;
  return helper.getImageDataUri();
}

/** Reads the provenance line the backend writes into the PDB, e.g. `model:coarse-geometric`. */
export function structureSource(pdb: string): string | null {
  const match = /^REMARK\s+.*\((model:[^)]+)\)/m.exec(pdb);
  return match ? match[1] : null;
}

export function residueCount(pdb: string): number {
  return pdb.split("\n").filter((line) => line.startsWith("ATOM")).length;
}
