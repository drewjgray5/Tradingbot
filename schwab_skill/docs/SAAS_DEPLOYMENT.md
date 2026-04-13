# SaaS deployment notes

## Stack

- **API:** FastAPI `webapp.main_saas:app`
- **Workers:** Celery `webapp.tasks` with queues `scan`, `orders`, and default `celery`
- **Broker / cache:** Redis (`REDIS_URL`)
- **Database:** PostgreSQL recommended (`DATABASE_URL`, e.g. `postgresql+psycopg2://user:pass@host:5432/dbname`)
- **Auth:** Supabase JWT — set `SUPABASE_JWT_SECRET` (HS256). Optional browser sign-in uses `SUPABASE_URL` + `SUPABASE_ANON_KEY` on the **web** service only.

## Required secrets (API + workers)

| Variable | Purpose |
|----------|---------|
| `CREDENTIAL_ENCRYPTION_KEY` | URL-safe base64, 32 bytes — encrypts rows in `user_credentials` |
| `SUPABASE_JWT_SECRET` | Validates `Authorization: Bearer` tokens |
| `SCHWAB_MARKET_APP_KEY` / `SCHWAB_MARKET_APP_SECRET` | Market API app |
| `SCHWAB_ACCOUNT_APP_KEY` / `SCHWAB_ACCOUNT_APP_SECRET` | Account/trading app |
| `SCHWAB_CALLBACK_URL` | Redirect URI for the **account** Schwab app (browser OAuth callback) |
| `SCHWAB_MARKET_CALLBACK_URL` | Redirect URI for the **market** Schwab app — register `https://<api-host>/api/oauth/schwab/market/callback` on the market app |
| `DATABASE_URL` | SQLAlchemy URL |
| `REDIS_URL` | Celery + rate limits + scan cooldown |

## Web service only (optional browser sign-in)

| Variable | Purpose |
|----------|---------|
| `SUPABASE_URL` | Project URL (e.g. `https://xyzcompany.supabase.co`) — returned by `GET /api/public-config` for the dashboard |
| `SUPABASE_ANON_KEY` | **anon** key from Supabase → Settings → API (public; not the service_role key) |
| `WEB_IMPLEMENTATION_GUIDE_URL` | Optional `http://` or `https://` URL — when set, the dashboard **Schwab setup guide** link opens this URL in a new tab. If unset and Schwab OAuth is configured, the UI links to the built-in **`/static/connect-schwab-guide.html`** (see `docs/CONNECT_SCHWAB_END_USERS.md`). |

If either is unset, the dashboard still works by pasting a JWT under **Advanced**. Workers do **not** need these.

Optional: `SCHWAB_TOKEN_ENCRYPTION_KEY` — Fernet key for Schwab token files (see `schwab_auth.py`).

## Stripe subscriber billing

Point Stripe’s webhook endpoint at **`POST /api/billing/webhook/stripe`** on your public API URL (same path in test and live dashboards; use separate webhook signing secrets per mode).

| Variable | Purpose |
|----------|---------|
| `STRIPE_SECRET_KEY` | Secret API key (`sk_test_...` / `sk_live_...`) — API + checkout/portal |
| `STRIPE_WEBHOOK_SECRET` | Signing secret from the Stripe webhook endpoint (`whsec_...`) |
| `STRIPE_PRICE_ID` | Recurring **Price** id for Checkout (`price_...`) |
| `STRIPE_CHECKOUT_SUCCESS_URL` | Redirect after successful checkout (if not sent in request body) |
| `STRIPE_CHECKOUT_CANCEL_URL` | Redirect if user cancels checkout |
| `STRIPE_PORTAL_RETURN_URL` | Return URL after Customer Portal (`POST /api/billing/portal-session`) |
| `SAAS_BILLING_ENFORCE` | Set to `1` / `true` to require **`trialing`** or **`active`** subscription for scans, order execution, and position sync (API + Celery workers). Default off for backward compatibility. |

**JWT-authenticated billing routes:** `POST /api/billing/checkout-session` (optional JSON body `success_url`, `cancel_url`), `POST /api/billing/portal-session` (requires existing Stripe customer). **`GET /api/me`** includes `subscription_status`, `subscription_current_period_end`, `has_stripe_customer`, `billing_enforced`, and `subscription_active`.

Workers need the same `SAAS_BILLING_ENFORCE` and database visibility as the API so queued jobs respect cancellation.

## Per-user OAuth

**Browser (dashboard):** With account + market callback URLs configured, users can use **Connect Schwab (account)** and **Connect Schwab (market)** in the onboarding wizard. Each flow uses the matching Schwab developer app registration and redirect URI.

Users can also POST `/api/credentials/schwab` with:

- `account_oauth_json` — JSON string from the **account** app token response (access + refresh).
- `market_oauth_json` — JSON string from the **market** app token response.

Alternatively: legacy `access_token` + `refresh_token` for the account app **and** set `SAAS_PLATFORM_MARKET_SKILL_DIR` to a directory on the worker/API host that contains a valid `tokens_market.enc` for the market app (shared platform session).

## Migrations

Revision **`saas005`** adds `users.live_execution_enabled` (default false). After upgrading, existing users remain unable to send live orders until they opt in via `POST /api/settings/enable-live-trading` (see README SaaS section).

**Existing database** (already had webapp tables):

```bash
cd schwab_skill
alembic upgrade head
```

**Empty Postgres** (first deploy): either run once:

```bash
python scripts/saas_bootstrap.py
```

or set `SAAS_BOOTSTRAP_SCHEMA=1` on the API for a single boot (runs `create_all` + `alembic stamp saas002`), then unset and use `SAAS_RUN_ALEMBIC=1` or manual `alembic upgrade head` for future revisions.

For containers, run `python scripts/saas_bootstrap.py` in an init container or set bootstrap env once on the API process.

## Celery

Workers **must** listen to `scan` and `orders`:

```bash
celery -A webapp.tasks worker -Q scan,orders,celery --loglevel=info
```

**Small instances (e.g. 512MB RAM):** Celery’s default **prefork** pool starts several child processes; each one imports pandas and the signal scanner and can exceed the limit alone. Set `CELERY_WORKER_POOL=solo` (one process, tasks run serially). Optionally lower `SCAN_STAGE_A_MAX_WORKERS` / `SCAN_STAGE_B_MAX_WORKERS` and use a small SQLAlchemy pool (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`). The Render blueprints in this repo set these for the worker service.

## Tunables

| Env | Default | Meaning |
|-----|---------|---------|
| `SAAS_SCAN_COOLDOWN_SEC` | `60` | Min seconds between scan enqueue per user |
| `SAAS_RATE_SCAN_PER_MIN` | `12` | Scans per user per window |
| `SAAS_RATE_ORDER_PER_MIN` | `30` | Order enqueue per user per window |
| `SAAS_RATE_LIMIT_WINDOW_SEC` | `60` | Fixed window for rate limits |
| `SAAS_HEALTH_REQUIRE_REDIS` | `1` | If `0`, readiness skips Redis |
| `WEB_ALLOWED_ORIGINS` | localhost | CORS allowlist (comma-separated) |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT` | `5` / `10` / `30` | Postgres pool (non-SQLite) |
| `CELERY_WORKER_POOL` | (Celery default) | e.g. `solo` on low-RAM workers |
| `CELERY_WORKER_CONCURRENCY` | (pool default) | Cap prefork/gevent concurrency when set |

## Docker

From `schwab_skill/`:

```bash
docker compose -f docker-compose.saas.yml up --build
```

Set secrets via environment file or your host’s secret manager — **never** commit real values.

## Hosting fit

- **Fly.io / Railway / Render:** Docker + managed Postgres + Redis; scale API replicas statelessly; scale Celery processes for queue depth.
- **Supabase:** Use hosted Postgres + Auth; point `DATABASE_URL` and `SUPABASE_JWT_SECRET` at your project.

## Render (Blueprint)

**Two layouts:**

- **Standalone repo:** repository root is the `schwab_skill` folder (`Dockerfile.saas` and `render.yaml` there). Push and connect that repo to Render.
- **Monorepo (e.g. `Tradingbot` on GitHub with a `schwab_skill/` subfolder):** use the `render.yaml` at the **repository root** (it sets `rootDir: schwab_skill` on the Docker services). Connect that repo to Render.

1. Push to GitHub/GitLab/Bitbucket.
2. In [Render](https://dashboard.render.com/): **New** → **Blueprint** → select the Git repository whose **root** contains `render.yaml` → leave the blueprint file as **`render.yaml`** (default) or type exactly `render.yaml`. Do **not** put a documentation URL in the blueprint path field (the comment link inside `render.yaml` is not the file path).
3. When prompted, set the `sync: false` variables (Schwab, Supabase JWT, optional `SUPABASE_URL` + `SUPABASE_ANON_KEY` for dashboard sign-in, encryption key, callback URL, CORS).
4. Set **`WEB_ALLOWED_ORIGINS`** to your public site origin (comma-separated if needed), e.g. `https://<your-web-service>.onrender.com`.
5. **First deploy on an empty database:** either set **`SAAS_BOOTSTRAP_SCHEMA=1`** on the web service for one deploy, then remove it; or run `python scripts/saas_bootstrap.py` against `DATABASE_URL` once. After that, keep **`SAAS_RUN_ALEMBIC=1`** on the web service (already in the Blueprint) so migrations apply on boot, or run `alembic upgrade head` in CI.
6. Register **`SCHWAB_CALLBACK_URL`** on the **account** app and **`SCHWAB_MARKET_CALLBACK_URL`** on the **market** app (typically `…/api/oauth/schwab/callback` vs `…/api/oauth/schwab/market/callback`).
7. Optional Stripe: add the billing env vars from the table above and point Stripe’s webhook to `POST /api/billing/webhook/stripe` on your public API URL.

The API serves the UI at `/` and static assets under `/static`; your live URL is the web service’s HTTPS URL on Render.

## Legal and user-facing disclosures

Ship with **`/static/legal.html`** reachable at your public origin (dashboard footers already link to it). The repo copy for review and edits is **`docs/LEGAL_DISCLOSURES.md`**—keep the static page and that file aligned if you change wording. Have qualified counsel review before customer-facing launch.

### Render: “Blueprint file … not found on main branch”

1. **Confirm the file on GitHub:** open `https://github.com/<owner>/<repo>/blob/main/render.yaml` (adjust owner/repo/branch). You must see `render.yaml` at the **repository root** for the monorepo layout, not only under `schwab_skill/`.
2. **Same repo in Render:** the connected repository must be that repo (e.g. a fork without `render.yaml` at root will fail).
3. **Branch:** Blueprint uses the branch you select (often **`main`**). If your default branch is different, pick it in the Blueprint flow.
4. **Filename:** exactly **`render.yaml`** (all lowercase), at the root of the branch Render is reading.
5. **Push:** if you only have the file locally, commit and push it, then retry **New → Blueprint** or reconnect the repo.

The line `# https://render.com/docs/infrastructure-as-code` in this repo’s `render.yaml` is a **comment** pointing to Render’s docs—it is not a path to enter in the dashboard.

### Deploy: `ProgrammingError` (sqlalche.me/e/20/f405) on Postgres

That link is a generic **ProgrammingError** wrapper—the real message is in the logs (e.g. `relation "users" does not exist` or a syntax error).

- **Empty database + Alembic only:** Older revisions assumed tables already existed. The first migration now calls `Base.metadata.create_all` when `users` is missing so a fresh **Render Postgres** can run `SAAS_RUN_ALEMBIC=1` without `SAAS_BOOTSTRAP_SCHEMA`. If you still see errors, use **`SAAS_BOOTSTRAP_SCHEMA=1`** once (see step 5 above) or run `python scripts/saas_bootstrap.py` against `DATABASE_URL`.
- **`postgres://` URL:** Render sometimes supplies this scheme; the app normalizes it to **`postgresql+psycopg2://`** automatically in `webapp/db.py`.

### Deploy: `OperationalError` (sqlalche.me/e/20/e3q8)

That code wraps **`OperationalError`**: the driver could not complete a DB operation—usually **connection refused**, **SSL required**, **auth failed**, **timeout**, or the DB is **still provisioning / suspended** (free tier).

- **SSL:** For hostnames ending in **`.render.com`**, if the URL has no `sslmode`, the app appends **`sslmode=require`**. Override with env **`DATABASE_SSLMODE=disable`** for local testing only.
- **Wrong `DATABASE_URL`:** Web and worker must use the **internal** URL Render injects from the database resource (Blueprint), not a stale or external string unless you know it’s valid from that service.
- Read the **line above** the SQLAlchemy link in the logs (e.g. `could not connect`, `SSL connection required`) for the exact cause.
