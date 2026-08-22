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

export function glyphFor(family: string): string[] {
  return GLYPHS[family] || GLYPHS.default;
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
  return (
    <svg
      className={active ? "sprite active" : "sprite"}
      viewBox="0 0 8 8"
      role="img"
      aria-label={title}
      style={{ color }}
      data-testid={`sprite-${family}`}
    >
      <title>{title}</title>
      {glyph.flatMap((line, y) =>
        line.split("").map((cell, x) =>
          cell === "#" ? (
            <rect key={`${x}-${y}`} x={x} y={y} width={1} height={1} fill={color} />
          ) : null,
        ),
      )}
    </svg>
  );
}
