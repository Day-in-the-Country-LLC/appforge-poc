"""Prompt templates for collaborative PR review rounds."""

from __future__ import annotations

from string import Template
from textwrap import dedent


REVIEWER_PROMPT_TEMPLATE = Template(
    dedent(
        """You are a senior code reviewer with strict JSON output requirements.

Use only JSON in your response with this exact schema:
{
  \"reviewer_model\": \"<model id>\",
  \"verdict\": \"approve\" | \"request_changes\" | \"comment\",
  \"confidence\": \"high\" | \"medium\" | \"low\",
  \"blocking_findings\": [
    {
      \"file_path\": \"path/to/file.py\",
      \"line_start\": 10,
      \"line_end\": 20,
      \"severity\": \"blocking\",
      \"category\": \"security\",
      \"description\": \"Issue description\",
      \"suggested_fix\": \"Optional fix recommendation\"
    }
  ],
  \"non_blocking_findings\": [
    {
      \"file_path\": \"path/to/file.py\",
      \"line_start\": 1,
      \"line_end\": 2,
      \"severity\": \"suggestion\",
      \"category\": \"style\",
      \"description\": \"Issue description\",
      \"suggested_fix\": \"Optional fix recommendation\"
    }
  ],
  \"suggested_comments\": [
    \"Optional PR-level comment\"
  ],
  \"reasoning\": \"Brief explanation for the verdict\"
}

Do not include markdown or prose outside JSON.

Round: $round_number
Reviewer model: $reviewer_model

PR metadata:
$context_json
"""
    )
)


CROSS_FEEDBACK_PROMPT_TEMPLATE = Template(
    dedent(
        """You are reviewing the same PR again for a cross-feedback round.

You previously reviewed this PR and produced this verdict:
$prior_verdict_json

The other reviewer produced this verdict:
$other_verdict_json

Re-evaluate the PR based on both prior findings and respond with JSON only using the same
schema as before. Update your verdict if needed.

Round: $round_number
Reviewer model: $reviewer_model

PR metadata:
$context_json
"""
    )
)
