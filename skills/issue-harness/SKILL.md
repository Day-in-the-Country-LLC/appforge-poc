---
name: issue-harness
description: Run a single GitHub issue end-to-end through the orchestration graph using scripts/run_issue_harness.py (manual validation/debugging).
---

# Issue Harness

Use this skill when you need to run one issue through the orchestration graph for validation or debugging.

## Inputs to collect

- Issue target: `owner`, `repo`, `issue number`, OR `--dev <repo>-<issue>`
- Mode: explicit (default) vs `--auto` selection
- Auto target filter: `remote` (default), `local`, or `any`

## Commands

Explicit issue:
```bash
python scripts/run_issue_harness.py --owner ORG --repo REPO --issue 123
```

Dev mode (disables issue comments + status updates):
```bash
python scripts/run_issue_harness.py --dev REPO-123
```

Auto-select first unblocked issue in project:
```bash
python scripts/run_issue_harness.py --auto --target remote
```

## Safety

- Use `--dev` when testing to avoid touching GitHub issue comments/status.
- Confirm the issue is truly ready before running in non-dev mode.

## Notes

- Requires a valid GitHub token (see `src/ace/config/secrets.py`).
- Uses org/project settings from `src/ace/config/settings.py`.
- Auto mode filters by project status + labels and skips blocked issues.
