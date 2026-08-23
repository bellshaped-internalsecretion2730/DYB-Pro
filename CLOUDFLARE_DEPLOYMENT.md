# DYB Pro on Cloudflare

DYB Pro is split into two Cloudflare Workers:

```text
Browser
  -> dyb-pro-web (Next.js through OpenNext)
       -> BACKEND service binding (private Cloudflare request)
            -> dyb-pro-api Worker
                 -> singleton Cloudflare Container
                      - FastAPI / Uvicorn
                      - cycle Celery worker
                      - research Celery worker
                      - Celery beat
```

The backend Worker has no `workers.dev` URL and no route. The browser reaches it only through the
frontend's same-origin proxy and the `BACKEND` service binding. This deployment does not replace or
rename the existing `foresterkane` Worker.

Protect the frontend's custom domain with **Cloudflare Access**. This replaces the repository's old
Next.js Basic Auth middleware: OpenNext does not support Next.js 16 Node middleware, while Access
authenticates users at Cloudflare's edge before any application request reaches the web Worker. The
backend still independently requires its server-side `DYB_PRO_API_KEY` on every API request.

## What Cloudflare does and does not run

Cloudflare runs the web Worker, the API gateway Worker, and the Python orchestration container.
PostgreSQL, Redis, and GPU inference remain managed external services:

- PostgreSQL stores projects, cycles, provenance, and workflow state.
- Redis is the durable Celery broker/result backend. It allows queued work to survive a container
  restart.
- R2 stores structures and other artifacts through its S3-compatible API. Container disk is
  ephemeral and production artifact storage fails closed if R2 is unavailable.
- AlphaFold and ProteinMPNN run behind authenticated GPU endpoints such as NVIDIA BioNeMo NIM.
  Cloudflare orchestrates those calls; it does not provide the required GPU runtime.

The container deliberately stays awake after HTTP activity because Celery continues autonomous work
after a swarm-start request returns. This has a direct Containers cost. Cloudflare can still restart
the instance, so no durable state belongs on its filesystem.

## Prerequisites

1. An eligible paid Cloudflare Workers account with Containers enabled.
2. Node.js 22.12 or newer. The pinned Wrangler/OpenNext toolchain rejects Node 20.
3. Docker capable of building `linux/amd64` images. Native Windows OpenNext builds are not supported;
   use WSL2, a Linux machine, or Linux CI.
4. A Cloudflare Access application protecting the final frontend hostname.
5. A TLS-enabled managed PostgreSQL database and Redis service.
6. An R2 bucket named `dyb-pro-artifacts` and bucket-scoped Object Read & Write credentials.
7. Authenticated AlphaFold and ProteinMPNN endpoints on a separate GPU host.

Cloudflare references: [Next.js on Workers](https://developers.cloudflare.com/workers/framework-guides/web-apps/nextjs/),
[Containers getting started](https://developers.cloudflare.com/containers/get-started/), and
[container environment secrets](https://developers.cloudflare.com/containers/examples/env-vars-and-secrets/).

## Devin key to create

Create an organization **Service User API key** in Devin under **Settings -> Service users**. Use the
one-time `cog_...` value as `DEVIN_API_KEY`, and set the associated `org-...` identifier as
`DEVIN_ORG_ID`. Do not use a personal token or expose this key in Next.js/browser variables.

The service user can use the standard Member role. A custom least-privilege role needs
`UseDevinSessions`, `ViewOrgSessions`, `ManageOrgSessions`, and `ManageOrgPlaybooks`. Add
`ManageOrgKnowledge` only when campaign memory is enabled, and `ManageOrgSchedules` only when Devin
schedules are enabled. Connect `https://github.com/yacine-baghli/DYB-Pro` to Devin before starting
the swarm.

Devin references: [API overview](https://docs.devin.ai/api-reference/overview),
[authentication](https://docs.devin.ai/api-reference/authentication), and
[v3 RBAC](https://docs.devin.ai/api-reference/v3/overview).

## Prepare configuration

Install locked dependencies from a Node 22.12+ shell:

```powershell
Set-Location frontend
npm ci
Set-Location ..\infrastructure\cloudflare-api
npm ci
```

Create untracked local secret files and replace every placeholder:

```powershell
Copy-Item frontend\.dev.vars.example frontend\.dev.vars
Copy-Item infrastructure\cloudflare-api\.dev.vars.example infrastructure\cloudflare-api\.dev.vars
```

Important relationships:

- `frontend/.dev.vars` `DYB_PRO_API_KEY` must equal the backend
  `SEED_SCIENTIST_API_KEY`.
- `CORS_ORIGINS` must contain the exact final frontend origin.
- `DATABASE_URL` should use the `postgresql+psycopg://` SQLAlchemy scheme and require TLS.
- `REDIS_URL` should normally use `rediss://`.
- `S3_ENDPOINT_URL` is `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`; the bucket name is already
  fixed to `dyb-pro-artifacts` in Wrangler configuration.
- Keep `ALLOW_LOCAL_SIMULATION=false` in Cloudflare production. Missing real providers must produce
  an explicit unavailable/failed result, never fabricated AlphaFold or ProteinMPNN output.

Wrangler declares all production secrets in `secrets.required`. A deploy fails instead of silently
starting with missing credentials.

## Validate before deployment

From `frontend`:

```powershell
npm run cf-typegen
npm run typecheck
npm test
npm run build:cloudflare
```

From `infrastructure/cloudflare-api`:

```powershell
npm run cf-typegen
npm run typecheck
docker build --platform linux/amd64 -f ..\..\backend\Dockerfile.cloudflare ..\..\backend
```

Generated `cloudflare-env.d.ts` and `worker-configuration.d.ts` files are committed deliberately.
Regenerate them whenever a binding, variable, required secret, compatibility date, or Durable Object
class changes.

## Provision R2

Authenticate Wrangler with `npx wrangler login`, then create the bucket once:

```powershell
Set-Location infrastructure\cloudflare-api
npx wrangler r2 bucket create dyb-pro-artifacts
```

Create an R2 API token scoped only to that bucket with Object Read & Write permission. Put its access
key ID and secret into the backend `.dev.vars`; do not use a general Cloudflare API token as an S3
credential.

## Deploy

Deploy the private API Worker and its container first. `--secrets-file` uploads values without
putting them on the command line:

```powershell
Set-Location infrastructure\cloudflare-api
npx wrangler deploy --strict --secrets-file .dev.vars
npx wrangler containers list
npx wrangler containers images list
```

The first container provision can take several minutes. Then deploy the web Worker:

```powershell
Set-Location ..\..\frontend
npm run deploy:cloudflare -- --secrets-file .dev.vars
```

The frontend initially receives a `dyb-pro-web.<workers-subdomain>.workers.dev` address. Attach a
custom domain in Cloudflare, create an Access self-hosted application and allow policy for that
hostname, update the backend `CORS_ORIGINS` secret, and redeploy the API Worker if the origin changes.

## Smoke checks

1. Open the web Worker and confirm the same-origin `/api/dyb-pro/healthz` request returns `200`.
2. Upload protein and target sequences and set a concrete design goal.
3. Start the swarm and confirm the HTTP request returns while the Celery workers continue the run.
4. Verify Workers/Containers logs contain lifecycle and request metadata but no sequences or keys.
5. Verify a structure artifact appears in R2 and its database row reports `backend=s3`.
6. Verify AlphaFold/ProteinMPNN runs show their real provider/model provenance. A missing GPU endpoint
   must be visible as skipped or failed, not as a coarse local prediction.

## Operational notes

- `dyb-pro-api` is a singleton `standard-3` container (2 vCPU, 8 GiB RAM, 16 GB ephemeral disk).
  Change the instance type only after measuring Python and Celery memory use.
- A single Celery beat process is safe only while `max_instances` remains `1`.
- Deploying requires Docker even when only Worker code changed because Wrangler manages the image
  and rollout together.
- Real secrets belong in Cloudflare encrypted secrets or a CI secret manager. Never commit
  `.dev.vars`, `.env`, database URLs, service-user keys, or R2 credentials.
- The deployment scaffold does not create PostgreSQL, Redis, GPU endpoints, DNS, or billing plans.
