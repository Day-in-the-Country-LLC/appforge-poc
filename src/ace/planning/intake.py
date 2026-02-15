"""Deterministic intake question generator for planning sessions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Sequence

from ace.planning.models import PlanningMode, PlanningQuestion

_KEYWORD_TO_CATEGORY = [
    (
        "feature",
        [
            "feature",
            "add",
            "implement",
            "build",
            "launch",
            "shipping",
            "ship",
            "develop",
        ],
    ),
    ("bugfix", ["bug", "fix", "broken", "regression", "error", "crash", "failure"]),
    (
        "infra",
        [
            "infra",
            "infra-structure",
            "infrastructure",
            "deploy",
            "deployment",
            "terraform",
            "gcp",
            "cloud",
            "pipeline",
        ],
    ),
    ("docs", ["doc", "documentation", "readme", "docs", "onboarding", "spec"]),
]

_CATEGORY_OPTIONS = ["feature", "bugfix", "infra", "docs"]


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9\s]+", " ", value.lower())


def _infer_goal_category(request_text: str) -> str:
    normalized = _normalize_text(request_text)
    haystack = f" {normalized} "
    scores: dict[str, int] = {}
    for category, keywords in _KEYWORD_TO_CATEGORY:
        score = 0
        for keyword in keywords:
            token = f" {keyword} "
            if token in haystack:
                score += haystack.count(token)
        scores[category] = score

    for category, _ in _KEYWORD_TO_CATEGORY:
        if scores.get(category, 0) > 0:
            return category

    return "feature"


def _scope_repos_text(project_slug: str, project_repos: Sequence[str] | None) -> str:
    if project_repos:
        repo_list = ", ".join(project_repos)
        return (
            f"Confirm repos in scope (comma-separated). "
            f"Defaults for project '{project_slug}': {repo_list}"
        )
    return (
        f"Confirm repos in scope (comma-separated). "
        f"No default repos configured for project '{project_slug}'"
    )


def generate_intake_questions(
    request_text: str,
    *,
    mode: PlanningMode,
    project_slug: str,
    project_repos: Sequence[str] | None = None,
    issue_creation_enabled: bool = False,
) -> list[PlanningQuestion]:
    """Return deterministic intake questions from request context."""
    inferred_category = _infer_goal_category(request_text)
    questions: list[PlanningQuestion] = []

    if inferred_category not in _CATEGORY_OPTIONS:
        inferred_category = _CATEGORY_OPTIONS[0]
    goal_options = [
        option for option in _CATEGORY_OPTIONS if option == inferred_category
    ] + [option for option in _CATEGORY_OPTIONS if option != inferred_category]

    questions.append(
        PlanningQuestion(
            id="primary_goal_category",
            session_id="placeholder",
            text="What is the primary goal category?",
            question_type="single_choice",
            required=True,
            options=goal_options,
        )
    )

    questions.append(
        PlanningQuestion(
            id="success_criteria",
            session_id="placeholder",
            text="What are the success criteria or demo expectations?",
            question_type="free_text",
            required=True,
            options=[],
        )
    )

    questions.append(
        PlanningQuestion(
            id="scope_repos",
            session_id="placeholder",
            text=_scope_repos_text(project_slug=project_slug, project_repos=project_repos),
            question_type="free_text",
            required=True,
            options=[],
        )
    )

    if issue_creation_enabled:
        issue_question = "Should this planning session create GitHub issues?"
        if mode.value != "plan_only":
            issue_question = (
                "For this planning mode, should this session create GitHub issues?"
            )
        questions.append(
            PlanningQuestion(
                id="create_issues",
                session_id="placeholder",
                text=issue_question,
                question_type="single_choice",
                required=True,
                options=["yes", "no"],
            )
        )

    return questions


def intake_complete(questions: Sequence[PlanningQuestion], answers: Mapping[str, str]) -> bool:
    """Return True when all required questions have non-empty answers."""
    for question in questions:
        if not question.required:
            continue
        raw = answers.get(question.id)
        if not raw:
            return False
        if isinstance(raw, str) and not raw.strip():
            return False
    return True
