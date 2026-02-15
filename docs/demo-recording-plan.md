# Demo Recording Plan (Live Worker Logs + Project Colors)

This document is a non-truncated version of the recommendation for capturing a demo that shows webhook-to-worker processing working end-to-end.

## Short Answer

Yes, you can show this cleanly in a recording.

- Use a **3-pane demo layout**.
- Use **Cloud Logging tail** for true live log flow.
- Use a **local colorizer** for per-project hex color mapping (Looker is not true live tail).

## Recommended Demo Layout

Use one screen with 3 panes:

1. Left pane: GitHub issue / project board
2. Center pane: live worker logs (colorized by `target_gcp_project`)
3. Right pane: Slack channel (`appforge-notification-bot` messages)

This gives one clear story:

1. Move issue to `Ready`
2. Watch listener/worker logs arrive live
3. Watch Slack confirmation / failure message

## Why Not Looker Studio For The Live Demo

Looker Studio is good for dashboarding and near-real-time updates, but it is not a true stream/tail console.

- Good for: trend panels, KPIs, historical timelines
- Not ideal for: second-by-second live event playback during a screen recording

For a live demo, Cloud Logging tail + terminal colorization is easier to follow.

## Practical Recording Setup

## 1) Prepare windows

- Browser tab 1: GitHub issue or board
- Browser tab 2: Slack channel
- Terminal pane: log tail command

## 2) Tail listener + worker logs (with project colors)

> **Setup:** `docs/repo-gcp-mapping.json` is gitignored. If you don't have it yet, copy from
> the example and fill in your real values:
> `cp docs/repo-gcp-mapping.example.json docs/repo-gcp-mapping.json`

Run the helper script (uses `docs/repo-gcp-mapping.json` colors):

```bash
uv run python scripts/demo_log_tail.py \
  --project <your-gcp-project> \
  --mapping-file docs/repo-gcp-mapping.json
```

Optional filters for a cleaner recording:

```bash
uv run python scripts/demo_log_tail.py \
  --project <your-gcp-project> \
  --mapping-file docs/repo-gcp-mapping.json \
  --target-gcp-project <target-gcp-project> \
  --workflow-id <workflow-id>
```

## Streamlit Option (local viewer)

If you want a simple app window for screen recording:

```bash
uv run appforge-dashboard
```

Then use sidebar controls in the app to:

- Start / Stop streaming
- Filter by `workflow_id`, `issue_key`, `target_gcp_project`
- Clear buffered rows

## 3) Trigger run

- Move a test issue to `Ready` (or post webhook event that triggers processing).
- Keep the trigger action visible in the left pane.

## 4) Narrate correlation fields

Point out these fields on-screen:

- `workflow_id`
- `issue_key`
- `target_gcp_project`
- `stage`
- `resolution`

## 5) Show Slack confirmation

In the right pane, show the Slack message that corresponds to the same issue/workflow.

## Color Coding Options

## Option A (Fastest): terminal colorizer script

Already implemented:

- `scripts/demo_log_tail.py`
- reads `target_gcp_project` from webhook lifecycle logs
- applies your hex color map from `docs/repo-gcp-mapping.json`
- prints concise rows:
  `time | service | target_gcp_project | stage | resolution | issue_key | workflow_id`

This gives true streaming visuals for the recording.

## Option B (Dashboard): Looker Studio

Use for post-run analytics visuals (not live tail).

- Build panel for outcomes by `target_gcp_project`
- Add filters for `workflow_id` and `issue_key`
- Optional `CASE` field for project color labels

## Suggested Demo Script (2-3 minutes)

1. "Issue is currently not running."
2. "I move it to `Ready`."
3. "Listener receives webhook and enqueues."
4. "Worker dequeues and starts agent."
5. "Final resolution is emitted (`success` / `blocked` / `failure` / `timeout`)."
6. "Slack notification confirms the same workflow."
7. "Logs include `target_gcp_project`, so we can monitor across all projects in one place."

## Recording Tool

Use OBS if possible (better for multi-pane clarity). QuickTime is acceptable for a simple single-display capture.

## Next Improvement (Optional)

If you want this reusable for every demo, add `scripts/demo_log_tail.py` with:

- built-in project->hex mapping
- normalized one-line output
- optional `--workflow-id` filter
- optional `--project` filter
