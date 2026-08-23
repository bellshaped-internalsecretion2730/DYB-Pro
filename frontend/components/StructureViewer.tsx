"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { PluginUIContext } from "molstar/lib/mol-plugin-ui/context";

export default function StructureViewer({ commitId }: { commitId: string | null }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [message, setMessage] = useState("Select a project with a structure to view it.");

  useEffect(() => {
    let disposed = false;
    let plugin: PluginUIContext | null = null;

    async function load() {
      if (!commitId || !containerRef.current) {
        setMessage("The selected project has no structure at its head commit.");
        return;
      }
      setMessage("Loading structure viewer...");
      try {
        const response = await fetch(api.downloadUrl(`/commits/${commitId}/structure`), {
          cache: "no-store",
        });
        if (!response.ok) throw new Error("no structure");
        const pdb = await response.text();
        if (!pdb.trim()) throw new Error("empty structure");
        const [{ createPluginUI }, { renderReact18 }, { DefaultPluginUISpec }] = await Promise.all([
          import("molstar/lib/mol-plugin-ui/index"),
          import("molstar/lib/mol-plugin-ui/react18"),
          import("molstar/lib/mol-plugin-ui/spec"),
        ]);
        if (disposed || !containerRef.current) return;
        plugin = await createPluginUI({
          target: containerRef.current,
          render: renderReact18,
          spec: {
            ...DefaultPluginUISpec(),
            layout: { initial: { isExpanded: false, showControls: false } },
            components: {
              remoteState: "none",
              controls: { top: "none", bottom: "none", left: "none" },
            },
          },
        });
        const data = await plugin.builders.data.rawData({ data: pdb, label: `commit-${commitId}` });
        const trajectory = await plugin.builders.structure.parseTrajectory(data, "pdb");
        await plugin.builders.structure.hierarchy.applyPreset(trajectory, "default");
        if (!disposed) setMessage("");
      } catch {
        if (!disposed) setMessage("Mol* could not load this structure. Download the raw PDB instead.");
      }
    }

    void load();
    return () => {
      disposed = true;
      plugin?.dispose();
    };
  }, [commitId]);

  return (
    <div>
      {message && <p className="hint">{message}</p>}
      {commitId && (
        <a href={api.downloadUrl(`/commits/${commitId}/structure`)} download>
          Download raw PDB
        </a>
      )}
      <div ref={containerRef} className="structure-viewer" aria-label="3D molecular structure viewer" />
    </div>
  );
}
