# Browser end-to-end tests

The demo path runs against a **live** backend, because the point of the test is that the daemon, the
research cache and the wet-lab loop actually work - not that a mock returns fixtures.

```bash
# 1. backend (SQLite + eager Celery is enough)
cd backend
CELERY_TASK_ALWAYS_EAGER=true DAEMON_LITERATURE_NETWORK=false uvicorn app.main:app --port 8000

# 2. web app
cd frontend && npm run build && npm run start

# 3. tests
cd frontend
npx playwright install chromium
npm run e2e
```

Or point the suite at the compose stack:

```bash
docker compose up --build -d
cd frontend && npm run e2e
```

| Variable | Default | Purpose |
| --- | --- | --- |
| `PLAYWRIGHT_BASE_URL` | `http://localhost:3000` | web app under test |
| `PLAYWRIGHT_API_BASE` | `http://localhost:8000` | API used to seed the campaign before the UI run |
| `PLAYWRIGHT_API_KEY` | `foldsmith-demo-scientist` | seeded scientist key |

`demo.spec.ts` seeds the campaign over the API in `beforeAll` (the same call the **seed demo
campaign** button makes, which takes ~1 minute), then drives the UI: pick the campaign, label a
residue, run the daemon, browse cached papers, propose a wet-lab pack, register simulated results,
and read the drift plus the next-version proposal. Every control on that path is clicked.
