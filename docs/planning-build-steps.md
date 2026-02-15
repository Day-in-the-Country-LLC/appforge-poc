# Planning System Build Steps (Incremental)

This breaks the Planner work into **small, testable steps** so we can implement and validate one step at a time.

Scope assumptions for MVP (adjust anytime):

- Streamlit runs locally (new dashboard app, separate from log viewer).
- Planner routes are mounted on the **existing** webhook FastAPI app (`src/ace/webhooks/app.py`) under `/planning/*`, gated by a `planner` service role. This avoids a second Cloud Run service for MVP.
- MVP supports `plan_only` (generate artifacts) with follow-up questions.
- GitHub issue creation is **out of MVP**, added later.

---

## Step 0: Decisions and Inputs (Stop Here Until Answered)

**Output:** a short config file and explicit MVP decisions.

1. Create a project registry file.
- Add: `docs/projects/example-project.json` (committed, placeholder values).
- Real files (`docs/projects/<project_slug>.json`) are **gitignored** — same pattern as `docs/repo-gcp-mapping.json`.
- Add `docs/projects/*.json` and `!docs/projects/example-project.json` to `.gitignore`.
- Minimum contents: `project_slug`, list of `repos` (owner/name pairs).
- Plan to migrate to Firestore `projects/{slug}` collection in Step 4 so you only migrate once.

2. Decide model/provider for planning MVP.
- Option A: OpenAI API.
- Option B: Anthropic API.
- Option C: mixed (not recommended for MVP).

3. Decide how the worker reads repos.
- **Recommended for MVP:** GitHub API-based scouts (`GET /repos/{owner}/{repo}/git/trees/{sha}?recursive=1` + targeted file reads). Fast, no workspace management, avoids cloning N repos per session.
- Option B: `gh repo clone` each repo into a temp workspace per session. Higher quality but requires workspace management (like the existing `workspaces/` module). Better suited for post-MVP deep analysis.

**Done when:** we have `docs/projects/example-project.json` committed, `.gitignore` updated, at least one real project file created locally, and the MVP choices are recorded at the top of this file.

---

## Step 1: Define Data Models and API Contract

**Output:** typed request/response objects and a stable API surface.

1. Add planning models:
- `src/ace/planning/models.py`
  - `PlanningSession`
  - `PlanningMessage`
  - `PlanningQuestion`
  - `PlanningEvent`
  - `PlanningArtifact` — type (`plan_md`, `issues_json`, `dependencies_mmd`), session reference, content URL (GCS), timestamps
  - `PlanningMode` enum

2. Define API routes (no implementation yet, just stubs on the existing app):
- `POST /planning/sessions`
- `POST /planning/sessions/{id}/messages`
- `POST /planning/sessions/{id}:start`
- `GET /planning/sessions/{id}`
- `GET /planning/sessions/{id}/events?after=<cursor>`
- `GET /planning/sessions/{id}/artifacts`

3. Add OpenAPI examples in docstrings / Pydantic examples.

**Done when:** `uv run python -m pytest` passes, `uv run python -c "import ace"` imports without errors, and route stubs return placeholder 501s.

---

## Step 2: Intake Question Generator (MVP)

**Output:** consistent follow-up questions that prevent misalignment.

> **Rationale:** The intake questions are the core UX contract. Defining them before building the UI (Step 4) or API (Step 3) ensures the UI has real questions to render instead of throwaway mocks.

1. Add `src/ace/planning/intake.py` with a deterministic ruleset (no LLM) based on the request text and mode.
- Later replace with an LLM-driven intake agent.

2. Minimum required questions:
- primary goal category (feature / bugfix / infra / docs)
- success criteria / demo expectations
- repos in scope (confirm or override from project registry)
- whether issue creation is desired (if enabled)

3. Add unit tests for the question generator.

**Done when:** question set is stable, tested, and prevents starting without required answers.

---

## Step 3: Implement Planner API (In-Memory)

**Output:** working API service with sessions + intake Q/A flow.

1. Mount planning routes on the existing webhook app:
- `src/ace/planning/routes.py` — a FastAPI `APIRouter` mounted at `/planning` in `src/ace/webhooks/app.py`.
- Gate behind a `planner` service role (extend the existing `webhook_service_role` pattern: `listener`, `worker`, `planner`, `both`, `all`).

2. Add an in-memory store (dict) for:
- sessions
- messages
- events

3. Implement intake:
- On `POST /planning/sessions`, create session and call the intake question generator (Step 2).
- On `POST /planning/sessions/{id}/messages`, accept answers and update state.
- Session state machine enforced (`intake_pending` → `ready_to_run`).

4. For local dev, run via the existing uvicorn entrypoint with role set to `planner` or `all`.

**Done when:** you can curl the endpoints locally and see session creation, question list, and state transitions.

---

## Step 4: Add Streamlit Planning Dashboard (Local)

**Output:** a UI that can create a session, show questions, collect answers, and poll events.

> **Note:** This is a **new** Streamlit app (`scripts/ace_dashboard.py`), not a tab on the existing log viewer (`scripts/demo_log_streamlit.py`). The log viewer uses subprocess/threading for `gcloud logging tail` — coupling planning UI state to that model would be fragile. The dashboard can later absorb the log viewer as a second page if desired.

1. Create `scripts/ace_dashboard.py` with multi-page structure:
- **Page: Planning** — session creation form, conversation/messages, questions renderer (single-choice + free-text), submit answer button, start planning button (disabled until intake complete), real-time event list, artifact links.

2. Wire UI to local Planner API base URL.
- Add CLI arg `--planner-url` (preferred) or env var.

**Done when:** with planner API running locally, Streamlit can complete an intake loop end-to-end.

---

## Step 5: Replace In-Memory Store With Firestore

**Output:** durable sessions and events.

1. Add Firestore client wrapper:
- `src/ace/planning/store_firestore.py`

2. Collections:
- `planning_sessions/{session_id}`
- `planning_sessions/{session_id}/messages/*`
- `planning_sessions/{session_id}/events/*`
- `projects/{slug}` — migrate project registry from local JSON files (Step 0).

3. Keep the in-memory store as a test double only.

4. Update API to use Firestore store.

5. Add session TTL / cleanup:
- Sessions in `intake_pending` for >24h → mark `expired`.
- Sessions in `running_*` for >1h → mark `timed_out`.
- Implement as a lightweight sweep (cron or on-startup check).

**Done when:** sessions survive API restart; Streamlit can reconnect to an existing session; expired sessions are cleaned up.

---

## Step 6: Add Pub/Sub Job Queue

**Output:** `start` enqueues a job; worker can consume it.

> **Pattern reuse:** Follow the same publish + decode pattern as `src/ace/webhooks/pubsub_queue.py` (`PubSubWebhookQueue`). Create a `PubSubPlannerQueue` with the same `from_settings` / `publish` / `decode_pubsub_push` structure.

1. Define job schema:
- `PlanningJob { session_id, project_slug, mode, created_at }`

2. Add `src/ace/planning/pubsub_queue.py`:
- `PubSubPlannerQueue` — mirrors `PubSubWebhookQueue`.
- `POST /planning/sessions/{id}:start` publishes to topic `appforge-planner-jobs`.

3. Add events around transitions:
- `queued`, `running`, `done`, `failed`.

**Done when:** starting a session writes an event and publishes a message.

---

## Step 7: Implement Planner Worker Skeleton (No LLM Yet)

**Output:** worker receives jobs and posts progress events.

1. Add worker HTTP handler for Pub/Sub push:
- `POST /internal/pubsub/planner` — mounted on the same app, gated by the `planner` (or `worker`/`all`) service role.

2. Worker validates message schema and sets session state `running_*`.

3. Worker writes a placeholder artifact:
- `PLAN.md` = "stub"

4. Store artifacts in GCS bucket (`appforge-planner-artifacts`).
- Firestore has a 1MB document limit — not suitable for plan artifacts.
- Store a GCS reference URL in the Firestore session/artifact document.

**Done when:** pushing a job causes worker to write events + a stub artifact; artifact URL is accessible from Streamlit.

---

## Step 8: Multi-Agent Planning (API-Native)

**Output:** scout reports + synthesized plan.

> This is the "real value" milestone.

1. Repo discovery
- Load project registry from Firestore `projects/{slug}` (or fall back to `docs/projects/<project_slug>.json` for local dev).

2. Repo scouts (MVP: GitHub API-based, no cloning)
- For each repo, call the GitHub API to read the file tree and key files:
  - `GET /repos/{owner}/{repo}/git/trees/{sha}?recursive=1` for structure.
  - Targeted `GET /repos/{owner}/{repo}/contents/{path}` for README, entrypoints, config files.
- Each scout outputs structured JSON: summary, entrypoints, risks, work items.
- Post-MVP: optionally clone repos for deeper analysis using the existing `workspaces/` module.

3. Coordinator synthesis
- Combine scout reports into:
  - `PLAN.md`
  - `ISSUES.json`
  - `DEPENDENCIES.mmd`

4. Persist artifacts to GCS and link them to the session.

**Done when:** a session produces real artifacts for a real multi-repo project.

---

## Step 9: Authentication Between Streamlit and Planner API

**Output:** planner endpoints aren't public.

Two layers:

1. **Cloud Run IAM (recommended for production):**
- Set `--ingress internal` on the Cloud Run service.
- Streamlit (or any caller) authenticates via a service account with `roles/run.invoker`.
- More secure than shared tokens; no rotation concerns.

2. **Bearer token (local dev fallback):**
- Add `Authorization: Bearer <token>` middleware on `/planning/*` routes.
- Store token in Secret Manager for Cloud Run and in local `.env` for Streamlit.

**Done when:** unauthenticated requests get 401; Streamlit works with either auth method.

---

## Step 10: Terraform + Deployment

**Output:** Planner routes deployed and wired on the existing webhook service.

Since planner routes are mounted on the existing webhook FastAPI app (no separate service for MVP), the Terraform delta is small:

Terraform items:

- Pub/Sub topic `appforge-planner-jobs` + push subscription to the existing webhook worker service.
- GCS bucket `appforge-planner-artifacts`.
- Firestore collections (if not already enabled).
- IAM additions on existing webhook service account:
  - Can read/write Firestore `planning_sessions` and `projects` collections.
  - Can write to the GCS artifacts bucket.
  - Can publish to the planner jobs topic.
- Update `WEBHOOK_SERVICE_ROLE` env var on the worker to include `planner` (or use `all`).

> **Post-MVP:** If planner load justifies it, split into a dedicated Cloud Run service. The `APIRouter` can be remounted with zero code changes.

**Done when:** production endpoints work end-to-end.

---

## Step 11: Observability + Demo Readiness

**Output:** end-to-end visibility and reliable demo.

1. Structured logs include:
- `session_id`
- `project_slug`
- `phase` (intake, scouting, synthesis, done)

2. Reuse the existing `log_lifecycle_event` pattern from `src/ace/webhooks/lifecycle.py` — add planning-specific stages.

3. Streamlit dashboard shows:
- real-time event list (poll `/planning/sessions/{id}/events`)
- final artifact links (poll `/planning/sessions/{id}/artifacts`)

4. Add a demo checklist doc.

**Done when:** you can screen-record a session from submission → intake → plan → artifacts.

---

## Step 12 (Later): GitHub Issue Creation

**Output:** planner can create issues from `ISSUES.json`.

- Add an Issue Writer stage.
- Enforce dependency relationships.
- Post outcome summary back into session events.

**Done when:** issues are created correctly across repos and match the plan.

---

## Execution Order (Recommended)

Steps are now numbered to reflect the recommended build order:

```
Step 0  →  Decisions & project registry
Step 1  →  Data models & API contract
Step 2  →  Intake question generator
Step 3  →  Planner API (in-memory)
Step 4  →  Streamlit dashboard
Step 5  →  Firestore persistence
Step 6  →  Pub/Sub job queue
Step 7  →  Planner worker skeleton
Step 8  →  Multi-agent planning (real value)
Step 9  →  Authentication
Step 10 →  Terraform + deployment
Step 11 →  Observability + demo readiness
```

Notes:

- **Step 5 can be deferred** until after the UI is proven (Steps 3–4). But you'll want durability before relying on it for real sessions.
- **Step 8 is the "real value" milestone** — everything before it is plumbing.
- The linear order above is the recommended path. Steps 9–11 can be parallelized if needed.
