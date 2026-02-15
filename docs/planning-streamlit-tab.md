# Streamlit Planning Tab + Multi-Agent Planner (Build Plan)

Goal: add a **Planning** tab to the local Streamlit app so you can submit a planning request to a **project planning team** (multi-agent pipeline), answer **follow-up questions**, and watch progress until it outputs a cross-repo plan and (optionally) creates GitHub issues.

## High-Level Architecture

Components:

- **Streamlit UI (local)**
  - new `Planning` tab
  - submits a planning request
  - shows an ongoing conversation thread
  - answers follow-up questions
  - shows planner progress/events
  - downloads plan artifacts

- **Planner API (Cloud Run)**
  - HTTP API for creating sessions, posting user answers, starting the plan run, streaming/polling events
  - persists session state

- **Planner Worker (Cloud Run, async)**
  - consumes jobs from Pub/Sub
  - runs the multi-agent pipeline (intake -> scouts -> synthesis -> issue writing)
  - posts progress events

- **State Store (durable)**
  - recommended: **Firestore** for session state + message/event log
  - alternative: Redis (not recommended for durable session history)

- **Queue**
  - recommended: dedicated Pub/Sub topic, e.g. `appforge-planner-jobs`


## Why a Separate Planner Service

Planning is long-running and multi-step. Keeping it in the Streamlit process or in a webhook request path creates reliability issues.

Cloud Run + Pub/Sub provides:

- retries
- backpressure
- clean separation between UI and execution
- easy concurrency control (worker concurrency = 1)


## UX: Planning Tab

### Inputs

- Project selector (`project_slug`)
- Optional repo override (advanced)
- Planning request text (what you want)
- Mode:
  - `plan_only` (default): produce plan artifacts only
  - `plan_and_create_issues`: also create GitHub issues

### Outputs

- Conversation thread (planner asks questions, you answer)
- Status indicator
- Event stream / progress log
- Final artifacts:
  - `PLAN.md`
  - `ISSUES.json`
  - `DEPENDENCIES.mmd`
  - `SCOUT_REPORTS/*.json`

### Flow

1. You submit a new planning request.
2. Planner replies with 1–N follow-up questions.
3. You answer them (UI posts answers).
4. When intake is satisfied, you click `Start planning`.
5. UI shows progress until completion.


## Follow-Up Questions (Intake)

### Requirements

- When a task is first submitted, the system asks follow-up questions.
- Questions should be structured and easy to answer.
- The planner must not proceed until required answers are present.

### Recommended Question Schema

Questions returned by the Planner API:

```json
{
  "session_id": "...",
  "questions": [
    {
      "id": "goal",
      "prompt": "What is the primary goal?",
      "type": "single_choice",
      "choices": [
        {"id": "feature", "label": "New feature"},
        {"id": "bugfix", "label": "Bug fix"},
        {"id": "infra", "label": "Infrastructure"},
        {"id": "docs", "label": "Documentation"}
      ],
      "required": true
    },
    {
      "id": "deadline",
      "prompt": "Is there a deadline or demo date?",
      "type": "free_text",
      "required": false
    }
  ]
}
```

Answers posted back:

```json
{
  "answers": {
    "goal": "infra",
    "deadline": "Friday EOD"
  }
}
```

### Intake Completion Rule

- Intake is complete when all `required=true` questions have answers.
- The planner then moves the session to `ready_to_run`.


## Session State Machine

Recommended states:

- `intake_pending`
- `awaiting_user`
- `ready_to_run`
- `queued`
- `running_scouts`
- `synthesizing`
- `review_ready`
- `creating_issues` (optional)
- `done`
- `failed`


## Planner API (Cloud Run)

Minimal endpoints:

1. Create session
- `POST /planning/sessions`

Request:

```json
{
  "project_slug": "appforge",
  "mode": "plan_only",
  "request": "Add end-to-end observability dashboard",
  "repo_overrides": []
}
```

Response:

- `session_id`
- initial questions and/or messages

2. Post user answers / messages
- `POST /planning/sessions/{session_id}/messages`

3. Start planning
- `POST /planning/sessions/{session_id}:start`

4. Get session
- `GET /planning/sessions/{session_id}`

5. Get events
- `GET /planning/sessions/{session_id}/events?after=<cursor>`

Implementation detail:

- Streamlit can poll `events` every 1–2 seconds.
- Events are append-only records: `{ts, level, kind, message, data}`.


## Multi-Agent Planner Pipeline

### Roles

- **Coordinator**
  - owns plan + dependency graph
  - generates final artifacts

- **Repo Scouts**
  - one per repo
  - produce structured reports

- **Issue Writer** (optional)
  - creates issues from `ISSUES.json`

### Execution Order

1. Intake agent generates follow-up questions.
2. After user answers, coordinator builds a repo assignment list.
3. Run scouts (parallel where feasible).
4. Coordinator synthesizes:
  - `PLAN.md`
  - `ISSUES.json`
  - `DEPENDENCIES.mmd`
5. Optional: Issue writer creates GitHub issues and updates dependencies.

### LLM Execution

Two viable approaches:

- **API-native (recommended):**
  - use OpenAI/Anthropic APIs directly from Python
  - predictable, non-interactive
  - easy to capture structured JSON

- **CLI-driven (possible):**
  - run Codex/Claude CLI via subprocess
  - higher variance + more operational edge cases

Given your previous Cloud Run + CLI prompt issues, API-native is the safer planning MVP.


## Project/Repo Discovery

The planner needs to know which repos are in a project.

Recommended config:

- `docs/projects/<project_slug>.json`

Example:

```json
{
  "project_slug": "appforge",
  "repos": [
    "Day-in-the-Country-LLC/appforge-poc",
    "Day-in-the-Country-LLC/appforge-mcp",
    "Day-in-the-Country-LLC/digido"
  ]
}
```


## GitHub Issue Creation

If `mode=plan_and_create_issues`:

- planner uses existing GitHub integration credentials
- creates issues per repo
- writes dependency links as:
  - body references ("Blocked by ...")
  - labels or a dedicated dependency field (depending on your conventions)


## Security

Streamlit (local) calling Planner API needs auth.

Options:

- Simple shared token header:
  - `Authorization: Bearer <PLANNER_API_TOKEN>`
- Cloud Run IAM auth (harder from Streamlit unless you mint ID tokens)
- IAP (heavier)

MVP recommendation: shared token in Secret Manager on the service and in your local env.


## Observability

Planner should log JSON with correlation fields:

- `workflow_id` / `session_id`
- `project_slug`
- `repo`
- `phase` (intake/scouts/synthesis/issues)

This keeps it consistent with your existing lifecycle logging approach.


## Suggested MVP Scope

MVP (fastest path):

- Streamlit Planning tab
- Planner API session creation + intake Q/A
- Planner worker generates artifacts only (`plan_only`)
- No GitHub issue creation in MVP

V2:

- GitHub issue creation
- dependency graph enforcement
- repo checkout automation in worker


## Open Questions (Need Decisions)

1. Where do we store the project->repos list?
- new `docs/projects/*.json` in this repo, or shared `~/.project-docs/...`?

2. Where should the planner run?
- Cloud Run (recommended) or purely local?

3. Which model/provider do you want for planning?
- OpenAI API, Anthropic API, or mix?

4. Should the planner be allowed to read repos by cloning them, or only plan from metadata?
- Cross-repo planning quality is much higher if it can read repos.

