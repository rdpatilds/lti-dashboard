# Performance Dashboard, an LTI 1.3 tool

A POC LTI 1.3 tool that Canvas launches in an iframe. A student sees her own Redshift history
next to a live Canvas read. A teacher sees the whole course roster with the same split. Every
section carries a badge naming where its rows came from, HISTORY for Redshift and LIVE for the
Canvas API, and a section that fails renders an error box in place of its rows instead of
blanking the page. Registered, launched and verified against the local Canvas as both a student
and an admin.

docs/LTI-EXPLAINED.md explains LTI, the launch chain this tool performs, the id_token, and what
changes for production. Read it first if LTI is new to you.

## Prerequisites

- The cplatform Canvas Docker instance up on http://localhost:3100, with the SAT-101 course and its users seeded. `docker ps --filter name=cplatform` shows cplatform-web-1 running.
- The Redshift Serverless workgroup `canvas` provisioned and seeded by D:\Canvas\redshift, so `nudges.student_course_status`, `nudges.assignment_status`, `nudges.recommendations` and `nudges.learning_paths` have rows. The nudge-agent project fills the third and fourth tables.
- AWS credentials in your shell that can call the Redshift Data API. There is no database password.
- `uv` for the Python side.
- `node` and Google Chrome for the verification script. Playwright drives the installed Chrome, it does not download one.

## Run it

Run these in order from D:\Canvas\lti-dashboard.

1. Install the Python dependencies.

   ```
   uv sync
   ```

2. Start the tool. It listens on 0.0.0.0:8800 and generates `keys/tool.pem` on first start.

   ```
   uv run app.py
   ```

   Check it is up:

   ```
   curl -s http://localhost:8800/
   curl -s http://localhost:8800/config.json
   curl -s http://localhost:8800/.well-known/jwks.json
   ```

3. Register the tool in Canvas. The script is idempotent, and `--show` prints the state without changing anything.

   ```
   uv run scripts/register_tool.py
   uv run scripts/register_tool.py --show
   ```

   Expect a developer key named "Kaplan Performance Dashboard" with client id 10000000000003, lock_deploying False, and an account tool with id 6.

4. Launch it. Open http://localhost:3100/courses/1 as `elena.rossi@sat.test` with `Password123!` and click Performance Dashboard in the course nav. Expect Elena's six sections. Then log in as `admin@cplatform.test` with `cplatform-admin`. The course nav is collapsed for teachers, so open the hamburger or go straight to http://localhost:3100/courses/1/external_tools/6. Expect one table of all ten students. Recommendation rows in both views link to the Canvas module item the nudge agent's next-step resolver picks for them. The href is `nudges.recommendations.next_url`, which the resolver fills from `nudges.content_items`. Her personalised path comes from `nudges.learning_paths`, written by the nudge agent's `path` command, which reruns on every scan.

The first launch after Redshift has been idle is slow. A paused Serverless workgroup takes tens
of seconds to resume, and the statement poll gives up after 60. If the four HISTORY sections
show error boxes, reload.

## Verify it

The script logs in, clicks the course nav link, waits for the iframe, checks the HISTORY and LIVE
badges and the role-specific text, saves a screenshot and the frame text, and checks the global
nav entry. Ten checks per run.

```
npm install
node scripts/verify_launch.mjs --login elena.rossi@sat.test --password Password123! --expect student --out docs/launch-elena.png
node scripts/verify_launch.mjs --login admin@cplatform.test --password cplatform-admin --expect teacher --out docs/launch-admin.png
```

Both runs passed 10/10 against the local Canvas. The screenshots and their `.txt` page text are
in docs/. After a launch, http://localhost:8800/debug/last-claims returns the verified claims
with `sub`, `email`, `picture` and the `lis` values redacted. That is where
docs/id-token-claims-elena.json and docs/id-token-claims-admin.json came from.

## Run it with exactly one worker

The pending-login store (state to nonce) is a module-level dict in `lti.py`, and the last
verified claims are a module-level variable in `app.py`. Both are per process. A second uvicorn
worker would reject every launch whose login hit the other worker. Do not pass `--workers`, and
do not put more than one process behind a load balancer.

## Keys

The tool's RSA signing key is generated on first start and written to `keys/tool.pem`. That
directory is gitignored. Later starts reuse the key, so the `kid` published at
`/.well-known/jwks.json` stays stable and Canvas does not have to be re-registered. Delete
`keys/tool.pem` only if you intend to re-register the tool.

## Environment

`CANVAS_URL` defaults to `http://localhost:3100` and `CANVAS_TOKEN` to `cplatform-dev-token`.
Redshift goes through the Data API under your own IAM identity, workgroup `canvas`, database
`dev`, region `us-east-1`.

## Files

- `app.py` is the FastAPI app. It owns the routes, the parallel section fetch, and the claims redaction for the debug endpoint.
- `lti.py` owns the launch. It builds the OIDC redirect, keeps the state store, verifies the id_token against the Canvas JWKS, and turns claims into a `Launch`.
- `config.py` is the tool configuration Canvas receives, with both placements and the two custom fields. Stdlib only, because the register script imports it.
- `data.py` fetches the sections. Redshift through the Data API for HISTORY, Canvas REST for LIVE, one `Section` per fetch with its badge and origin.
- `views.py` renders the page, the landing text, and the refused page as HTML.
- `scripts/register_tool.py` creates the developer key, binds it, unlocks the registration, and installs the tool once at the account level. Rerunnable.
- `scripts/verify_launch.mjs` drives Chrome through a real launch and writes the screenshot and page text.
- `docs/LTI-EXPLAINED.md` is the explainer.
- `docs/id-token-claims-elena.json` and `docs/id-token-claims-admin.json` are redacted claims from the two verified launches.
- `docs/launch-elena.png` and `docs/launch-admin.png` are the screenshots, and the matching `.png.txt` files are the rendered page text.
- `pyproject.toml` and `uv.lock` pin the Python dependencies. `package.json` and `package-lock.json` pin Playwright.
- `keys/tool.pem` is the generated signing key. `logs/app.log` is the app log. Both directories are gitignored, as is `node_modules/`.

## Endpoints

| Path | Purpose |
| --- | --- |
| `GET\|POST /lti/login` | OIDC third-party initiation, 302 to Canvas `authorize_redirect` |
| `POST /lti/launch` | verifies the `id_token` and renders the dashboard |
| `GET /.well-known/jwks.json` | the tool's public key |
| `GET /config.json` | the LTI tool configuration `scripts/register_tool.py` posts |
| `GET /` | one-line health text |
| `GET /debug/last-claims` | the last verified claims, with `sub`, `email`, `picture` and the `lis` values redacted |
