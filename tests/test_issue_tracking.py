"""Tests for issue tracker backend factories."""

from types import SimpleNamespace

from ace.issue_tracking import build_work_item_enricher, build_work_item_tracker


class _TrackingSettings(SimpleNamespace):
    issue_tracker_backend: str


def test_build_work_item_tracker_uses_linear_backend_when_configured(monkeypatch):
    called: dict[str, bool] = {}

    class _LinearSentinel:
        @classmethod
        def create_default(cls) -> str:
            called["linear"] = True
            return "linear-tracker"

    monkeypatch.setattr(
        "ace.issue_tracking.LinearWorkItemTracker",
        _LinearSentinel,
    )
    monkeypatch.setattr(
        "ace.issue_tracking.get_settings",
        lambda: _TrackingSettings(issue_tracker_backend="linear"),
    )

    tracker = build_work_item_tracker(api_client=None, owner="org", repo="repo")

    assert tracker == "linear-tracker"
    assert called["linear"] is True


def test_build_work_item_tracker_uses_github_backend_by_default(monkeypatch):
    called: dict[str, object] = {}

    def fake_build_tracker(api_client, owner, repo=""):
        called["api_client"] = api_client
        called["owner"] = owner
        called["repo"] = repo
        return "github-tracker"

    monkeypatch.setattr(
        "ace.issue_tracking.build_github_work_item_tracker",
        fake_build_tracker,
    )
    monkeypatch.setattr(
        "ace.issue_tracking.get_settings",
        lambda: _TrackingSettings(issue_tracker_backend="github"),
    )
    monkeypatch.setattr("ace.issue_tracking.resolve_github_token", lambda _settings: "token")

    tracker = build_work_item_tracker(api_client="api", owner="my-org", repo="my-repo")

    assert tracker == "github-tracker"
    assert called["api_client"] == "api"
    assert called["owner"] == "my-org"
    assert called["repo"] == "my-repo"


def test_build_work_item_enricher_passes_through_owner_and_repo(monkeypatch):
    called: dict[str, object] = {}

    def fake_build_enricher(api_client, owner, repo=""):
        called["api_client"] = api_client
        called["owner"] = owner
        called["repo"] = repo
        return "github-enricher"

    monkeypatch.setattr(
        "ace.issue_tracking.build_github_work_item_enricher",
        fake_build_enricher,
    )
    monkeypatch.setattr(
        "ace.issue_tracking.resolve_github_token",
        lambda _settings: "token",
    )
    monkeypatch.setattr("ace.issue_tracking.get_settings", lambda: _TrackingSettings(issue_tracker_backend="github"))

    enricher = build_work_item_enricher(api_client="api", owner="acme", repo="repo")

    assert enricher == "github-enricher"
    assert called["api_client"] == "api"
    assert called["owner"] == "acme"
    assert called["repo"] == "repo"
