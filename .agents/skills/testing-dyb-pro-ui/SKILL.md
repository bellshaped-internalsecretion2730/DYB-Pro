---
name: testing-dyb-pro-ui
description: How to bring up and end-to-end test the DYB Pro frontend (three-zone workspace) against the local FastAPI backend on Windows, including running real design cycles without a Devin key.
---

# Testing the DYB Pro web UI end-to-end

## Bring up the stack (no credentials needed)

Backend (SQLite fallback, port 8000), from `backend/`:

```powershell
$env:ALLOW_LOCAL_SIMULATION="true"      # lets a design cycle run without a Devin key (provider badge: local-simulation)
$env:CELERY_TASK_ALWAYS_EAGER="true"    # run agent tasks inline when Redis is unavailable
$env:DAEMON_LITERATURE_NETWORK="false"
python -m uvicorn app.main:app --port 8000
```

Frontend (port 3000), from `frontend/`: `npm run dev` (or `npm run build; npm run start`).
Default API key `dyb-pro-demo-scientist` is sent automatically by the frontend; the other seeded keys
are `dyb-pro-demo-admin` and `dyb-pro-demo-viewer` (viewer is read-only, RBAC is enforced).

Redis is often unavailable in the sandbox - `CELERY_TASK_ALWAYS_EAGER=true` is the workaround.
Caveat: with eager mode the `POST /cycles` request runs the whole cycle synchronously, so transient
UI states (`cycle running...`, `queued`/`running` agent pills) may flash by or never render; the run
button just stays disabled until the cycle is already `committed`. To observe live agent-status
transitions, run a real Celery worker + Redis (`docker compose up`) instead of eager mode.

## Seeding data

Click `load demo project` in the left ASK pane - it seeds and selects "GB1 stability + Fc binding (demo)"
with a full commit history. Reset local state by deleting `backend\dybpro.db` and `backend\artifacts`.
A cycle in local-simulation mode adds ~15 commits, so repeated cycles inflate the fold strip quickly
(relevant to the layout bug below).

## Useful selectors / handles

- Left pane: `aside[aria-label="ask and agent control"]`, `textarea[aria-label="research brief"]`,
  `select[aria-label="project"]`. Ctrl+Enter in the brief starts a cycle.
- Center tabs are `button[aria-selected]` with text `protein viewer` / `version DAG` / `wet-lab shortlist` /
  `observation log`; fold strip thumbnails are `button[aria-label^="version "]` inside `.thumbs`.
- Right pane: `aside[aria-label="pixel agent swarm"]`.
- Command palette: Ctrl+K (input placeholder `run a command...`), type to filter, Enter to run, Escape closes.
- Drag-to-compare needs a real mouse down, several intermediate moves, then up on the target thumbnail;
  a single `left_click_drag` may not trigger the HTML5 drag handlers.

## Things to check that have been broken before

- **Horizontal overflow driven by the fold strip.** `.thumbs` is `display:flex; overflow-x:auto`, but if any
  ancestor (`.fold-strip`, `.viewer`, `.center-body`, `main`) loses `min-width: 0` the row stretches the whole
  page instead of scrolling: with 31 commits the document `scrollWidth` was 3884px at a 1280px viewport.
  Verify with `document.documentElement.scrollWidth` vs `window.innerWidth` after loading a project with many
  commits, at both ~1280px and ~1100px widths. Symptom: controls such as `exit compare` land far offscreen.
- **Export links.** Shortlist `export CSV/JSON/FASTA` anchors point straight at the API URL, so the browser
  navigation carries no `X-API-Key` header and the response is `{"detail":"missing X-API-Key header"}`.
  Always click the export control in the UI rather than assuming the link works. (Known open bug.)
- Responsive breakpoint: below 1180px the workspace should collapse to a single column
  (`grid-template-columns` becomes one track).

## Window resizing on Windows for responsive checks

`xdotool` is not available; resize Chrome via Win32 from PowerShell
(`user32.dll` `MoveWindow` on `(Get-Process chrome | ? MainWindowHandle -ne 0).MainWindowHandle`),
then `ShowWindow(handle, 3)` to re-maximize.

## Devin Secrets Needed

None - local simulation and the demo API key cover the whole golden path.
