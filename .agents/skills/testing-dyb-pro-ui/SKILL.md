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
$env:DAEMON_LITERATURE_NETWORK="false"  # keep the lab daemon offline; literature calls otherwise hit OpenAlex/Semantic Scholar and are slow/rate-limited
python -m uvicorn app.main:app --port 8000
```

Interpreter note: `python` / `C:\Python311\python.exe` may not exist on the box even if a blueprint
references them. The working interpreter has been `C:\devin\python\python.exe` - check with
`Get-Command python` / `Test-Path` before assuming, and use PowerShell syntax (`;`, not `&&`).

`npm run start` prints a warning that it "does not work with output: standalone"; it nonetheless serves
correctly on :3000. After rebuilding you MUST hard-reload the browser (Ctrl+Shift+R) or you will keep
testing the previously served bundle - an easy way to report a stale UI as the new one.

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
- Right pane: `aside[aria-label="pixel agent swarm"]`. It stays empty ("Run a cycle to watch...") until a
  cycle has run; with `CELERY_TASK_ALWAYS_EAGER=true` the cycle completes synchronously (~30s) so every
  agent lands on `done` and the intermediate `thinking`/`searching`/`writing` state colours never render.
  Run a real Celery worker + Redis if those states must be verified.

### Research lab tab (`/api/lab` subsystem)

- Center tab labelled `research lab`; component tree `ResearchWorkspace` -> `DaemonPane`, `ResearchFeed`,
  `LabelStudio`, `WetlabLoop`, `LearnedPane` via `frontend/lib/research.ts`.
- Handles: `[data-testid=campaign-summary]`, `daemon-refresh`, `daemon-tick`, `label-create`,
  `wetlab-plan`, `wetlab-simulate`, `proposal-refresh`, `proposal-blocked`, `seed-campaign`.
- `seed-campaign` takes ~1-2 min and is **disabled once `digest.result_count > 0`** (coded idempotency
  guard, not a bug). Seed over the API first, as `frontend/e2e/demo.spec.ts` does, then test the UI state:
  `POST /api/lab/projects/{id}/research/seed-campaign` with header `X-API-Key: dyb-pro-demo-scientist`.
- Cross-check the UI against the API rather than trusting the rendering: `/api/lab/projects/{id}/research`
  (`digest.version_count|paper_count|result_count`, `learned.drift[]`, `learned.headline`),
  `/api/lab/commits/{fullCommitId}/wetlab/plan|results`, `/api/lab/projects/{id}/research/proposal`.
  Commit ids shown in the UI are TRUNCATED to 12 chars; the lab routes need the full 64-char id, which
  you can get from `GET /api/projects/{id}/commits` (a short id returns `{"detail":"version not found"}`).
- Running a design cycle also feeds the lab daemon (new commits queue `results_registered`/`version_created`
  tasks), so `queue`/`events`/`knowledge v` badges move - handy for proving the daemon reacts.
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
- **`MiniBars` charts collapsing in narrow columns.** `.barrow` is
  `grid-template-columns: minmax(96px,148px) minmax(0,1fr) auto` with a `white-space: nowrap` mono readout,
  so inside a narrow column (e.g. the 2-col `.center-body.lab` grid at a 1024px viewport) the fixed name
  column and the readout eat the row and `.bartrack` collapses to ~10-30px, with bar fills under 3px -
  the chart becomes unreadable even though the numbers are right. Worse, each `.barrow` is its own grid,
  so track width varies per row and bar lengths are NOT comparable across rows. Always measure rather
  than eyeball:
  `[...document.querySelectorAll('.bartrack')].map(t=>t.getBoundingClientRect().width)`
  and compare `.barfill` `style.width` percentages against the API values. Possible fixes to suggest:
  a `min-width` on `.bartrack`, letting the readout wrap, shrinking the name column, or hoisting the
  columns onto the shared `.barchart` grid so all rows align.
- **`.center-body.lab` horizontal overflow.** The 2-column lab grid overflowed by ~61px
  (`scrollWidth 762` vs `clientWidth 701`) at a 1024px viewport, producing a horizontal scrollbar.
  Check `document.querySelector('.center-body.lab')` scrollWidth vs clientWidth.
- **`.tip` tooltips carry prose that was deleted from the surface.** `globals.css` hides
  `.tip::after` with `opacity:0; visibility:hidden` and reveals it on `:hover` and `:focus-visible`
  (spans carry `tabIndex={0}`). If the tooltip fails, that explanatory text is simply gone, so verify
  BOTH paths with screenshots: hover and screenshot while hovering; then click the element, move the
  mouse far away, press `shift+Tab` then `Tab` (a plain click does not match `:focus-visible`) and
  screenshot the focused state. Note `scrollWidth` of a `.tip` element is inflated by the tooltip
  pseudo-element, so it produces false "clipped text" positives.

## Window resizing on Windows for responsive checks

`xdotool` is not available; resize Chrome via Win32 from PowerShell
(`user32.dll` `MoveWindow` on `(Get-Process chrome | ? MainWindowHandle -ne 0).MainWindowHandle`),
then `ShowWindow(handle, 3)` to re-maximize.

## Devin Secrets Needed

None - local simulation and the demo API key cover the whole golden path.
