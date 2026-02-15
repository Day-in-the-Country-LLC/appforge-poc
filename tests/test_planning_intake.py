"""Tests for deterministic planning intake generation."""

from ace.planning.intake import generate_intake_questions, intake_complete
from ace.planning.models import PlanningMode


def test_generate_intake_questions_includes_required_fields() -> None:
    questions = generate_intake_questions(
        "Implement a new checkout flow for feature launch",
        mode=PlanningMode.PLAN_ONLY,
        project_slug="example-project",
        project_repos=[
            "acme-corp/widget-api",
            "acme-corp/widget-web",
        ],
    )
    question_ids = [question.id for question in questions]
    assert question_ids == [
        "primary_goal_category",
        "success_criteria",
        "scope_repos",
    ]
    assert all(question.required for question in questions)
    assert questions[0].options == ["feature", "bugfix", "infra", "docs"]
    assert (
        "Defaults for project 'example-project': "
        "acme-corp/widget-api, acme-corp/widget-web" in questions[2].text
    )


def test_goal_category_is_deterministic() -> None:
    request_text = "There is a serious deployment bug and regression in production"
    questions_a = generate_intake_questions(
        request_text,
        mode=PlanningMode.PLAN_ONLY,
        project_slug="example-project",
        project_repos=[],
    )
    questions_b = generate_intake_questions(
        request_text,
        mode=PlanningMode.PLAN_ONLY,
        project_slug="example-project",
        project_repos=[],
    )
    assert [question.id for question in questions_a] == [question.id for question in questions_b]
    assert questions_a[0].options[0] == "bugfix"


def test_issue_creation_question_toggle() -> None:
    with_issue = generate_intake_questions(
        "Draft docs for release notes",
        mode=PlanningMode.PLAN_ONLY,
        project_slug="example-project",
        issue_creation_enabled=True,
    )
    without_issue = generate_intake_questions(
        "Draft docs for release notes",
        mode=PlanningMode.PLAN_ONLY,
        project_slug="example-project",
        issue_creation_enabled=False,
    )
    assert "create_issues" in {question.id for question in with_issue}
    assert "create_issues" not in {question.id for question in without_issue}


def test_intake_complete_checks_required_answers() -> None:
    questions = generate_intake_questions(
        "Fix authentication bug",
        mode=PlanningMode.PLAN_ONLY,
        project_slug="example-project",
    )
    assert not intake_complete(questions, {})
    assert not intake_complete(
        questions,
        {
            "primary_goal_category": "bugfix",
            "success_criteria": "Smoke tests pass",
        },
    )
    assert intake_complete(
        questions,
        {
            "primary_goal_category": "bugfix",
            "success_criteria": "Smoke tests pass",
            "scope_repos": "Acme/app",
        },
    )
