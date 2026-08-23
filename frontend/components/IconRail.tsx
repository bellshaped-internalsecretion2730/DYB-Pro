"use client";

import { useRef } from "react";

export type RailSection = "ask" | "agents" | "history" | "files" | "handoff" | "pharma";

type Item = { id: RailSection; label: string; hint: string; path: React.ReactNode };

const ITEMS: Item[] = [
  {
    id: "ask",
    label: "Ask and run a design cycle",
    hint: "ask · brief and run",
    path: (
      <>
        <rect x={3} y={4} width={18} height={13} rx={2} />
        <path d="M8 21l0-4" />
      </>
    ),
  },
  {
    id: "agents",
    label: "Agent control and playbook",
    hint: "agents · run control and plan",
    path: (
      <>
        <rect x={4} y={4} width={6} height={6} />
        <rect x={14} y={4} width={6} height={6} />
        <rect x={9} y={14} width={6} height={6} />
      </>
    ),
  },
  {
    id: "history",
    label: "Session history and recent briefs",
    hint: "history · briefs and cycles",
    path: (
      <>
        <circle cx={12} cy={12} r={8} />
        <path d="M12 8v4l3 2" />
      </>
    ),
  },
  {
    id: "files",
    label: "Files, uploads and project data",
    hint: "files · uploads and counts",
    path: (
      <>
        <path d="M6 3h8l4 4v14H6z" />
        <path d="M14 3v4h4M9 13h6M9 17h6" />
      </>
    ),
  },
  {
    id: "handoff",
    label: "Hand the current work to the agent swarm",
    hint: "handoff · give it to the swarm",
    path: (
      <>
        <circle cx={6} cy={12} r={2.5} />
        <circle cx={18} cy={6} r={2.5} />
        <circle cx={18} cy={18} r={2.5} />
        <path d="M8.2 11l7.6-4M8.2 13l7.6 4" />
      </>
    ),
  },
  {
    id: "pharma",
    label: "Drug discovery programs",
    hint: "programs · target, ligand and rounds",
    path: (
      <>
        <path d="M7 3h10M9 3v5l-4 9a3 3 0 0 0 2.7 4h8.6a3 3 0 0 0 2.7-4l-4-9V3" />
        <path d="M7 15h10" />
      </>
    ),
  },
];

export const RAIL_SECTIONS = ITEMS.map((i) => i.id);

export default function IconRail({
  active,
  onSelect,
}: {
  active: RailSection;
  onSelect: (section: RailSection) => void;
}) {
  const railRef = useRef<HTMLDivElement>(null);

  function move(delta: number) {
    const index = ITEMS.findIndex((i) => i.id === active);
    const next = ITEMS[(index + delta + ITEMS.length) % ITEMS.length];
    onSelect(next.id);
    const buttons = railRef.current?.querySelectorAll<HTMLButtonElement>("button");
    buttons?.[ITEMS.indexOf(next)]?.focus();
  }

  return (
    <nav
      className="rail"
      ref={railRef}
      role="tablist"
      aria-orientation="vertical"
      aria-label="left pane sections"
      data-testid="icon-rail"
      onKeyDown={(e) => {
        if (e.key === "ArrowDown") move(1);
        else if (e.key === "ArrowUp") move(-1);
        else if (e.key === "Home") onSelect(ITEMS[0].id);
        else if (e.key === "End") onSelect(ITEMS[ITEMS.length - 1].id);
        else return;
        e.preventDefault();
      }}
    >
      {ITEMS.map((item, i) => (
        <button
          className="rail-item tip"
          type="button"
          role="tab"
          key={item.id}
          aria-selected={active === item.id}
          aria-label={item.label}
          data-tip={item.hint}
          data-testid={`rail-${item.id}`}
          tabIndex={active === item.id ? 0 : -1}
          onClick={() => onSelect(item.id)}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} aria-hidden>
            {item.path}
          </svg>
          <span className="rail-kbd">{i + 1}</span>
        </button>
      ))}
    </nav>
  );
}
