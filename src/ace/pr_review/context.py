"""PR context gathering for collaborative review flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _require_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"❌ ERROR: {field} must be an integer")
    return value


def _require_non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"❌ ERROR: {field} must be a non-empty string")
    return value.strip()


def _require_non_negative_int(value: Any, field: str) -> int:
    limit = _require_int(value, field)
    if limit < 0:
        raise ValueError(f"❌ ERROR: {field} must be zero or greater")
    return limit


def _optional_string(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("❌ ERROR: expected string for PR context field")
    return value


def _collect_labels(labels_value: Any) -> list[str]:
    if not isinstance(labels_value, list):
        return []
    return [
        item.get("name").strip()
        for item in labels_value
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and item.get("name").strip()
    ]


def _required_dict(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"❌ ERROR: {field} must be an object")
    return value


def _normalize_file_summaries(files_value: Any) -> list[dict[str, Any]]:
    if not isinstance(files_value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for file_record in files_value:
        if not isinstance(file_record, dict):
            continue
        normalized.append(
            {
                "filename": _optional_string(file_record.get("filename")),
                "status": _optional_string(file_record.get("status")),
                "additions": file_record.get("additions", 0),
                "deletions": file_record.get("deletions", 0),
            }
        )
    return normalized


def _normalize_commits(commits_value: Any) -> list[dict[str, str]]:
    if not isinstance(commits_value, list):
        return []
    normalized: list[dict[str, str]] = []
    for commit in commits_value:
        if not isinstance(commit, dict):
            continue
        commit_obj = commit.get("commit") if isinstance(commit.get("commit"), dict) else {}
        author = commit.get("author") or commit.get("committer")
        author_login = None
        if isinstance(author, dict):
            author_login = author.get("login")
        normalized.append(
            {
                "sha": _require_non_empty_string(commit.get("sha"), "commit.sha"),
                "message": _optional_string(commit_obj.get("message")),
                "author": str(author_login) if author_login else "",
            }
        )
    return normalized


def _normalize_comments(comments_value: Any) -> list[dict[str, Any]]:
    if not isinstance(comments_value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for comment in comments_value:
        if not isinstance(comment, dict):
            continue
        normalized.append(comment)
    return normalized


def _normalize_check_runs(check_runs: Any) -> list[dict[str, Any]]:
    if not isinstance(check_runs, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in check_runs:
        if not isinstance(item, dict):
            continue
        normalized.append(item)
    return normalized


def _truncate_diff(diff: str, max_chars: int) -> str:
    if not diff or max_chars == 0:
        return ""
    if len(diff) <= max_chars:
        return diff
    return diff[:max_chars]


@dataclass(frozen=True)
class PRContext:
    """Normalized PR context for review models."""

    pr_number: int
    title: str
    body: str
    diff: str
    files_changed: list[dict[str, Any]]
    commits: list[dict[str, str]]
    existing_comments: list[dict[str, Any]]
    check_runs: list[dict[str, Any]]
    labels: list[str]
    author: str
    base_branch: str
    head_branch: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize context to a JSON-safe shape."""
        return {
            "pr_number": self.pr_number,
            "title": self.title,
            "body": self.body,
            "diff": self.diff,
            "files_changed": self.files_changed,
            "commits": self.commits,
            "existing_comments": self.existing_comments,
            "check_runs": self.check_runs,
            "labels": self.labels,
            "author": self.author,
            "base_branch": self.base_branch,
            "head_branch": self.head_branch,
        }


async def gather_pr_context(
    issue_queue: Any,
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    *,
    max_diff_chars: int = 100000,
) -> PRContext:
    """Fetch PR metadata, file diffs, commit history, comments, and checks."""
    max_chars = _require_non_negative_int(max_diff_chars, "max_diff_chars")
    pr_payload = await issue_queue.get_pull_request(repo_owner, repo_name, pr_number)
    if not isinstance(pr_payload, dict):
        raise ValueError("❌ ERROR: pull request payload must be an object")
    pr_head = _required_dict(pr_payload.get("head"), "pr_payload.head")
    pr_base = _required_dict(pr_payload.get("base"), "pr_payload.base")
    pr_user = _required_dict(pr_payload.get("user"), "pr_payload.user")

    files = await issue_queue.list_pull_request_files(repo_owner, repo_name, pr_number)
    diff = await issue_queue.get_pull_request_diff(repo_owner, repo_name, pr_number)
    if not isinstance(diff, str):
        raise ValueError("❌ ERROR: pull request diff payload must be text")
    truncated_diff = _truncate_diff(diff, max_chars)

    commits = await issue_queue.list_pull_request_commits(repo_owner, repo_name, pr_number)
    comments = await issue_queue.list_pr_review_comments(repo_owner, repo_name, pr_number)
    if not isinstance(files, list):
        raise ValueError("❌ ERROR: pull request files payload must be a list")
    if not isinstance(commits, list):
        raise ValueError("❌ ERROR: pull request commits payload must be a list")
    if not isinstance(comments, list):
        raise ValueError("❌ ERROR: pull request comments payload must be a list")

    head_sha = pr_head.get("sha")
    if not isinstance(head_sha, str) or not head_sha.strip():
        raise ValueError("❌ ERROR: pull request payload missing head.sha")
    check_runs = await issue_queue.get_pr_check_runs(repo_owner, repo_name, head_sha)
    if not isinstance(check_runs, list):
        raise ValueError("❌ ERROR: pull request check-runs payload must be a list")

    normalized = PRContext(
        pr_number=_require_int(pr_number, "pr_number"),
        title=_require_non_empty_string(pr_payload.get("title"), "title"),
        body=_optional_string(pr_payload.get("body")),
        diff=truncated_diff,
        files_changed=_normalize_file_summaries(files),
        commits=_normalize_commits(commits),
        existing_comments=_normalize_comments(comments),
        check_runs=_normalize_check_runs(check_runs),
        labels=_collect_labels(pr_payload.get("labels")),
        author=_optional_string(pr_user.get("login")),
        base_branch=_require_non_empty_string(pr_base.get("ref"), "base.ref"),
        head_branch=_require_non_empty_string(pr_head.get("ref"), "head.ref"),
    )
    return normalized
