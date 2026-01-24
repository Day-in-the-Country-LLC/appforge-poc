---
name: blocked-task-handling
description: Handle blocked coding tasks that require developer input; use when an agent must stop work due to missing info, credentials, access, or decisions and needs to post a BLOCKED update and write ACE_TASK_DONE.json.
---

# Blocked Task Handling

## Goal
Stop cleanly when developer input is required, leave a clear blocking note, and ensure the system can end the run by writing `ACE_TASK_DONE.json`.

## Workflow

1) Identify the blocker
- State exactly what is missing and why work cannot continue.
- List the minimal information needed to resume.

2) Notify the issue
- Post a single comment to the GitHub issue starting with `BLOCKED:`.
- Keep it short and actionable (what is needed, where to find it, who should provide it).

3) Write `ACE_TASK_DONE.json`
- Create the file in the repo root (same directory as `ACE_TASK.md`).
- Use this exact JSON shape:

```json
{
  "task_id": "task-1",
  "summary": "Blocked: <short reason>.",
  "files_changed": [],
  "commands_run": []
}
```

4) Stop execution
- Do not keep working until the blocker is resolved.
- Exit after writing `ACE_TASK_DONE.json`.

## Notes
- Never write `ACE_TASK_DONE.md` (must be `.json`).
- Do not create a PR when blocked.
