# Foldsmith design system

Dark-first, true-black, hairline-bordered workspace for protein designers. The system is calm and
dense: information carries the visual weight, decoration is minimal, and colour is reserved for
state. Source of truth for the canvas is the Figma file
[`4HWllBrJeNykXShmXUuqqh`](https://www.figma.com/design/4HWllBrJeNykXShmXUuqqh); the tokens below are
mirrored 1:1 as Figma variables and as CSS custom properties in `frontend/app/globals.css`.

## Tokens

| Token | Value | Use |
| --- | --- | --- |
| `--bg` | `#000000` | app base, viewer stage |
| `--raised` | `#08090a` | panes, top bar, strips |
| `--panel` | `#0c0d0f` | cards, thumbnails |
| `--panel-2` / `--input` | `#121316` | inputs, secondary buttons, hover |
| `--line` | `#1b1d20` | 1px hairline borders (the only separator) |
| `--line-strong` | `#2a2d31` | hover/active hairline, scrollbars |
| `--text` | `#ecedee` | primary type |
| `--muted` | `#9ba1a6` | secondary type |
| `--faint` | `#6b7075` | labels, metadata, mono captions |
| `--accent` | `#5e6ad2` | primary action, selected version, mutation markers |
| `--accent-2` | `#5b8cff` | links, compare overlay |
| `--ok` | `#35c08e` | passed filters, done |
| `--warn` | `#e8a33d` | filtered/simulated, cancelled |
| `--bad` | `#f0525b` | errors, failed runs |

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
| `pending` / `queued` | queued | `--idle` `#4a4f55` | none |
| `running` | thinking | `--thinking` `#a78bfa` | pulse |
| `searching` | searching | `--searching` `#5b8cff` | pulse |
| `writing` | writing | `--writing` `#35c08e` | pulse |
| `finished` / `succeeded` | done | `--ok` `#35c08e` | none |
| `failed` / `timeout` | failed / timeout | `--bad` `#f0525b` | none |
| `cancelled` | cancelled | `--warn` `#e8a33d` | none |

Motion is limited to one signature: `agent-pulse`, a 2.4s glow on active sprites, disabled under
`prefers-reduced-motion`. There are no spinners.

## Glass rule

`.glass` (55% `#12141a` + 8px blur + hairline) is the semantic cue for **agent-generated content
only**: agent activity feeds and the orchestrator plan strategy. Human-authored surfaces (inputs,
version metadata, tables) never use it, so frosted panels always answer "the agent wrote this".

## Command palette

`⌘/Ctrl+K` toggles `components/CommandPalette.tsx`: run/cancel a cycle, load the demo project,
upload, switch center surface, exit compare, refresh project. Arrow keys move, `↵` runs, `Esc`
closes. Every command maps to an existing handler — the palette adds no new behaviour.

## Honesty constraints

- Run controls that the API does not expose (`pause`, `resume`, `redirect`, `spawn`) are rendered
  disabled with a tooltip instead of faking a request; `cancel` is the live control.
- Provider labelling (`devin` vs `local-simulation`) is unchanged and still data-driven.
