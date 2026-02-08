---
name: appforge-manager-work-pickup
description: Use when the manager agent needs to order actionable work. Prioritize open PRs with comments first, then In Progress issues, then Ready issues. Skip blocked or developer-waiting items.
---

# Appforge Manager Work Pickup

## Overview
Order actionable work for the agent pool. Favor review comment fixes, then resumptions, then new work.

## Ordering Rules

1) PR comments first
- Items marked `pr_comment` go at the top of the queue.

2) In Progress next
- Items marked `in_progress` come after PR comment work.

3) Ready last
- Items marked `ready` come after in-progress.

## Filters

- Ignore items missing the core `agent` label.
- Ignore items marked blocked or waiting for developer input.

## Output

- Return a JSON array of item keys in the exact order to process.
