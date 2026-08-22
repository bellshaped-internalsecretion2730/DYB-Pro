# DYB Pro design system

Light, hairline-bordered workspace for protein designers: neo-brutalist geometry (strict grids,
sharp 1px containers, crisp type) with high-end restraint. The system is calm and dense —
information carries the visual weight, decoration is minimal, and saturated colour (blurple,
electric indigo, cyan) is reserved for state. Numbers are drawn as charts rather than written out,
and the sentence that explains a number lives in its hover/focus tooltip (`.tip`), never beside it.
Source of truth for the canvas is the Figma file
[`4HWllBrJeNykXShmXUuqqh`](https://www.figma.com/design/4HWllBrJeNykXShmXUuqqh); the tokens below are
mirrored 1:1 as Figma variables and as CSS custom properties in `frontend/app/globals.css`.

## Tokens

| Token | Value | Use |
| --- | --- | --- |
| `--bg` | `#eef0f4` | app base, viewer stage, inset wells |
| `--raised` | `#ffffff` | panes, top bar, strips |
| `--panel` | `#ffffff` | cards, thumbnails |
| `--panel-2` / `--input` | `#f4f6f9` | inputs, secondary buttons, hover, bar tracks |
| `--line` | `#dfe3e9` | 1px hairline borders (the only separator) |
| `--line-strong` | `#c3c9d2` | hover/active hairline, scrollbars, chart markers |
| `--text` | `#0f1216` | primary type (deep slate) |
| `--muted` | `#545c66` | secondary type |
| `--faint` | `#7d858f` | labels, metadata, mono captions |
| `--accent` | `#4f46e5` | blurple: primary action, selected version, mutation markers |
| `--accent-2` | `#2563eb` | electric indigo: links, compare overlay |
| `--accent-3` | `#0891b2` | cyan: tertiary chart series |
| `--ok` | `#0f9d6f` | passed filters, done, shrinking residuals |
| `--warn` | `#b7791f` | filtered/simulated, cancelled |
| `--bad` | `#d92d3c` | errors, failed runs |

Type: `Inter` (UI) and `Geist Mono`/`JetBrains Mono` (`--font-mono`) for identifiers, scores, ACU
counters and timestamps. Sizes run 9.5–13px; uppercase 10px `.label` for zone headers. Radii are
`6px` (`--radius`) for controls, `999px` for pills, `3–5px` for sprites and glass.

Layout: three fixed zones (`.workspace` grid = `--left-w` / `1fr` / `--right-w`), each pane scrolls
independently, collapsing to a single column below 1180px. Every zone boundary is a 1px hairline —
no shadows, no gradients except the single 6%-opacity accent wash behind the viewer stage.

## Zones

- **Left — Ask & Agent Control** (`components/AskPane.tsx`): inline prompt (not a chat bubble,
  `⌘/Ctrl+↵` runs a cycle), project picker, uploads, run controls, orchestrator plan/task list,
  recent briefs and session history — all bound to the existing API calls.
- **Center — Protein Viewer** (`components/ProteinViewer.tsx`) with tabs for the existing version
  DAG, wet-lab shortlist and observation log surfaces. The backbone rendering is an explicitly
  labelled *schematic*: commits carry sequence, scores and mutations but no 3D coordinates, so the
  trace is deterministically derived from the commit id and mutation sites are marked from
  `GraphNode.mutations`. Sequence track appears when the selected commit has a shortlist sequence.
- **Center-below — Folded version strip** (`components/FoldStrip.tsx`): every commit in the project
  graph as a thumbnail with composite score and branch. Click loads it into the viewer; dragging one
  thumbnail onto another loads the drop target and overlays the dragged version for comparison.
- **Right — Pixel agent swarm** (`components/AgentSwarm.tsx`): one pixel sprite per `AgentRun`,
  grouped orchestrator / literature / metrics (sequence, structure, docking) / wetlab planner
  (ranking).

## Pixel-agent states

Sprites are 8×8 glyphs (`components/PixelSprite.tsx`) rendered as SVG rects, coloured by the *real*
`AgentRun.status` returned by `/api/cycles/{id}/agents` — nothing is animated or coloured without
backend state behind it.

| Backend status | Displayed state | Colour | Motion |
| --- | --- | --- | --- |
| `pending` / `queued` | queued | `--idle` `#98a0aa` | none |
| `running` | thinking | `--thinking` `#7c3aed` | pulse |
| `searching` | searching | `--searching` `#2563eb` | pulse |
| `writing` | writing | `--writing` `#059669` | pulse |
| `finished` / `succeeded` | done | `--ok` `#0f9d6f` | none |
| `failed` / `timeout` | failed / timeout | `--bad` `#d92d3c` | none |
| `cancelled` | cancelled | `--warn` `#b7791f` | none |

Motion is limited to one signature: `agent-pulse`, a 2.4s glow on active sprites, disabled under
`prefers-reduced-motion`. There are no spinners.

## Glass rule

`.glass` (72% white + 8px blur + hairline) is the semantic cue for **agent-generated content
only**: agent activity feeds and the orchestrator plan strategy. Human-authored surfaces (inputs,
version metadata, tables) never use it, so frosted panels always answer "the agent wrote this".

## Command palette

`⌘/Ctrl+K` toggles `components/CommandPalette.tsx`: run/cancel a cycle, load the demo project,
upload, switch center surface, exit compare, refresh project. Arrow keys move, `↵` runs, `Esc`
closes. Every command maps to an existing handler — the palette adds no new behaviour.

## Density primitives (research lab)

- `.kpi` cards replace summary paragraphs: uppercase 9.5px key, mono value, hairline box.
- `MiniBars` (`components/MiniBars.tsx`) is the chart primitive used instead of numeric tables —
  one row per series, value on the right, optional `.barmark` hairline for the earlier value, tone
  from `--accent` / `--ok` / `--warn` / `--bad`.
- `.tip` carries the short explanation on hover and keyboard focus (`tabIndex={0}`), so prose and
  charts are never both on screen for the same fact.
- `.seqgrid` renders the sequence as a clickable residue grid; label kinds keep their own hue.

## Honesty constraints

- Run controls that the API does not expose (`pause`, `resume`, `redirect`, `spawn`) are rendered
  disabled with a tooltip instead of faking a request; `cancel` is the live control.
- Provider labelling (`devin` vs `local-simulation`) is unchanged and still data-driven.
