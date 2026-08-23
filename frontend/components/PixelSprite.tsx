"use client";

/** 8x8 pixel-art sprites, one glyph per agent role family. Purely presentational. */
const GLYPHS: Record<string, string[]> = {
  orchestrator: [
    "..####..",
    ".#....#.",
    ".#.##.#.",
    ".#....#.",
    "..####..",
    "..#..#..",
    ".##..##.",
    "........",
  ],
  literature: [
    "........",
    ".######.",
    ".#....#.",
    ".#.###.#",
    ".#....#.",
    ".#.###.#",
    ".######.",
    "........",
  ],
  metrics: [
    "........",
    "......##",
    "....##.#",
    "..##...#",
    ".#.....#",
    ".#.....#",
    ".#######",
    "........",
  ],
  planner: [
    "...##...",
    "..####..",
    ".##..##.",
    ".#....#.",
    ".##..##.",
    "..####..",
    "...##...",
    "........",
  ],
  default: [
    "..####..",
    ".#....#.",
    ".#.##.#.",
    ".#....#.",
    ".######.",
    ".#.##.#.",
    ".#....#.",
    "........",
  ],
};

/**
 * Per-role pixel palettes. The palette carries role identity (which agent this is);
 * the live backend status stays honest and is carried by the status pip and the
 * activity pulse, never by these colours.
 */
const PALETTES: Record<string, string[]> = {
  orchestrator: ["#4f39f6", "#6366f1", "#06b6d4", "#a78bfa"],
  literature: ["#0e7490", "#06b6d4", "#22d3ee", "#4f39f6"],
  metrics: ["#4f39f6", "#f59e0b", "#6366f1", "#c4b5fd"],
  planner: ["#047857", "#10b981", "#34d399", "#4f39f6"],
  default: ["#4f39f6", "#6366f1", "#06b6d4", "#c7d2fe"],
};

export function glyphFor(family: string): string[] {
  return GLYPHS[family] || GLYPHS.default;
}

export function paletteFor(family: string): string[] {
  return PALETTES[family] || PALETTES.default;
}

export default function PixelSprite({
  family,
  color,
  active,
  title,
}: {
  family: string;
  color: string;
  active: boolean;
  title: string;
}) {
  const glyph = glyphFor(family);
  const palette = paletteFor(family);
  return (
    <svg
      className={active ? "sprite active" : "sprite idle"}
      viewBox="0 0 9 9"
      role="img"
      aria-label={title}
      style={{ color }}
      data-testid={`sprite-${family}`}
    >
      <title>{title}</title>
      {glyph.flatMap((line, y) =>
        line.split("").map((cell, x) =>
          cell === "#" ? (
            <rect
              key={`${x}-${y}`}
              x={x}
              y={y}
              width={1}
              height={1}
              fill={palette[(x + y * 3) % palette.length]}
            />
          ) : null,
        ),
      )}
      {/* the only status-driven paint: the live backend state of this agent */}
      <rect x={7} y={7} width={2} height={2} fill={color} data-testid="sprite-status" />
    </svg>
  );
}
