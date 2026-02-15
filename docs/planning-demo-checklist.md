# Planner Demo Checklist

Use this checklist for a reliable end-to-end planning demo.

## Pre-Demo Preconditions

- [ ] GCP Cloud Run worker has role `both` (or `all`) and `PLANNING_PUBSUB_TOPIC` is set.
- [ ] `appforge-planner-artifacts` bucket exists and the worker SA has object write access.
- [ ] Firestore `datastore.googleapis.com` is enabled and accessible by the worker SA.
- [ ] `PLANNING_STORE_BACKEND` is set to `firestore` in the worker environment.
- [ ] Dashboard/API auth is configured:
  - local: `PLANNER_API_TOKEN` (or no token for dev)
  - cloud: bearer token path from `docs/webhook-listener.md`.

## Demo Execution

- [ ] Start the planning API locally or confirm Cloud Run worker is running.
- [ ] Open the dashboard (`python scripts/ace_dashboard.py --planner-url <API_URL>`).
- [ ] Create a planning session for a valid project slug.
- [ ] Submit answers for all intake questions.
- [ ] Verify session status transitions from `intake_pending` → `ready_to_run`.
- [ ] Click **Start planning**.
- [ ] Confirm `/planning/sessions/{id}/events` shows:
  - intake stage events,
  - queued/running events,
  - planning event logs for synthesis/artifact writes.
- [ ] Confirm final status becomes `done`.
- [ ] Open artifact links and verify all three expected artifacts are accessible:
  - `plan_md`
  - `issues_json`
  - `dependencies_mmd`

## Observability Verification

- [ ] Structured log stream contains planning events (`planning_lifecycle`) with:
  - `session_id`
  - `project_slug`
  - `phase` (`intake`, `scouting`, `synthesis`, `done`)
- [ ] Structured event stream includes both session-level Firestore events and end-to-end transition events in `/planning/sessions/{id}/events`.
- [ ] Retry the same session flow at least once to demonstrate consistency.

## Post-Demo Notes

- [ ] Capture timestamps for: session creation, run start, artifact write, final done.
- [ ] Save one short screen recording from session creation through artifact links.
