#!/usr/bin/env python3
"""Streamlit dashboard for local planning session intake and progress polling."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any

import httpx
import streamlit as st

from ace.config.secrets import load_secret

PLANNER_DEFAULT_URL = "http://127.0.0.1:8000"
PLANNER_API_URL_ENV = "PLANNER_API_URL"
PLANNER_URL_ENV = "PLANNER_URL"
PLANNER_TOKEN_ENV = "PLANNER_API_TOKEN"
PLANNER_TOKEN_SECRET_ENV = "PLANNER_API_TOKEN_SECRET"
PLANNER_PASSWORD_SECRET_ENV = "PLANNER_DASHBOARD_PASSWORD_SECRET"
PLANNER_SECRET_VERSION_ENV = "PLANNER_SECRET_VERSION"
PLANNER_SECRET_PROJECT_ENV = "GCP_PROJECT_ID"
GCP_CREDENTIALS_FILE_ENV = "GCP_CREDENTIALS_FILE"


@dataclass
class AppConfig:
    planner_url: str
    planner_token: str | None = None
    planner_token_secret: str | None = None
    planner_password_secret: str = "APPFORGE_PLANNER_DASHBOARD_PASSWORD"
    planner_secret_version: str = "latest"
    planner_secret_project_id: str | None = None
    planner_credentials_file: str | None = None
    poll_interval_seconds: int = 2
    auto_refresh: bool = False


class PlannerApiError(RuntimeError):
    """Raised for non-success responses from the planner API."""


class PlannerApiClient:
    """Small typed wrapper around planning endpoints."""

    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=10.0) as client:
            response = client.request(
                method,
                path,
                headers=self._headers(),
                params=params,
                json=body,
            )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = response.text.strip() or response.reason_phrase
            raise PlannerApiError(
                f"❌ ERROR: planner API request failed ({response.status_code}) {body}"
            ) from exc

        try:
            return response.json()
        except ValueError as exc:  # pragma: no cover - response contract test path
            raise PlannerApiError("❌ ERROR: planner API returned non-JSON response") from exc

    def create_session(self, *, project_slug: str, mode: str, request_text: str) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/planning/sessions",
            body={
                "project_slug": project_slug,
                "mode": mode,
                "request_text": request_text,
            },
        )

    def post_answer(
        self,
        *,
        session_id: str,
        question_id: str,
        answer: str,
        source: str = "user",
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/planning/sessions/{session_id}/messages",
            body={"question_id": question_id, "answer": answer, "source": source},
        )

    def start_session(self, *, session_id: str) -> dict[str, Any]:
        return self._request_json("POST", f"/planning/sessions/{session_id}:start")

    def approve_session_issues(
        self,
        *,
        session_id: str,
        issues: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/planning/sessions/{session_id}/issues/approve",
            body={"issues": issues},
        )

    def get_session(self, *, session_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/planning/sessions/{session_id}")

    def get_events(self, *, session_id: str, after: str | None = None) -> dict[str, Any]:
        params = None if after is None else {"after": after}
        return self._request_json("GET", f"/planning/sessions/{session_id}/events", params=params)

    def get_artifacts(self, *, session_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/planning/sessions/{session_id}/artifacts")


def _detect_gcp_project_id() -> str | None:
    """Return the active GCP project from env or gcloud CLI."""
    from_env = os.environ.get(PLANNER_SECRET_PROJECT_ENV, "").strip()
    if from_env:
        return from_env
    try:
        result = subprocess.run(
            ["gcloud", "config", "get-value", "project", "--quiet"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def parse_args(argv: list[str] | None = None) -> AppConfig:
    default_planner_url = (
        os.environ.get(PLANNER_API_URL_ENV)
        or os.environ.get(PLANNER_URL_ENV)
        or PLANNER_DEFAULT_URL
    )
    default_password_secret = os.environ.get(
        PLANNER_PASSWORD_SECRET_ENV,
        "APPFORGE_PLANNER_DASHBOARD_PASSWORD",
    )
    default_token_secret = os.environ.get(
        PLANNER_TOKEN_SECRET_ENV,
        "APPFORGE_PLANNER_API_TOKEN",
    )
    default_secret_version = os.environ.get(PLANNER_SECRET_VERSION_ENV, "latest")
    default_credentials_file = os.environ.get(
        GCP_CREDENTIALS_FILE_ENV,
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
    )
    parser = argparse.ArgumentParser(description="Run the ACE planning dashboard.")
    parser.add_argument(
        "--planner-url",
        default=default_planner_url,
        help=(
            "Planner API base URL (preferred over env vars "
            f"{PLANNER_API_URL_ENV}/{PLANNER_URL_ENV})"
        ),
    )
    parser.add_argument(
        "--planner-api-token",
        default=os.environ.get(PLANNER_TOKEN_ENV, ""),
        help=(f"Optional bearer token to skip password login (or {PLANNER_TOKEN_ENV})"),
    )
    parser.add_argument(
        "--planner-token-secret",
        default=default_token_secret,
        help=(
            f"Secret Manager secret name for planner bearer token (or {PLANNER_TOKEN_SECRET_ENV})"
        ),
    )
    parser.add_argument(
        "--planner-password-secret",
        default=default_password_secret,
        help=(
            "Secret Manager secret name for dashboard login password "
            f"(or {PLANNER_PASSWORD_SECRET_ENV})"
        ),
    )
    parser.add_argument(
        "--planner-secret-project-id",
        default="",
        help=(
            f"GCP project for Secret Manager (auto-detected from gcloud if omitted, "
            f"or {PLANNER_SECRET_PROJECT_ENV})"
        ),
    )
    parser.add_argument(
        "--planner-secret-version",
        default=default_secret_version,
        help=(f"Version for password/token secrets (or {PLANNER_SECRET_VERSION_ENV})"),
    )
    parser.add_argument(
        "--planner-secret-credentials-file",
        default=default_credentials_file,
        help=(
            "Optional service account JSON used to read Secret Manager "
            f"(or {GCP_CREDENTIALS_FILE_ENV} / GOOGLE_APPLICATION_CREDENTIALS)"
        ),
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=2,
        help="Polling interval in seconds",
    )
    parser.add_argument(
        "--auto-refresh",
        action="store_true",
        help="Enable periodic event polling",
    )
    args = parser.parse_args(argv)
    if args.poll_interval_seconds < 1:
        args.poll_interval_seconds = 2
    token = args.planner_api_token.strip() or None
    project_id = (args.planner_secret_project_id or "").strip() or _detect_gcp_project_id()
    credentials_path = (args.planner_secret_credentials_file or "").strip() or None
    return AppConfig(
        planner_url=args.planner_url.rstrip("/"),
        planner_token=token,
        planner_token_secret=(args.planner_token_secret or "").strip() or None,
        planner_password_secret=(args.planner_password_secret or "").strip(),
        planner_secret_version=(args.planner_secret_version or "latest").strip() or "latest",
        planner_secret_project_id=project_id,
        planner_credentials_file=credentials_path,
        poll_interval_seconds=args.poll_interval_seconds,
        auto_refresh=args.auto_refresh,
    )


def initialize_state() -> None:
    st.session_state.setdefault("active_session_id", None)
    st.session_state.setdefault("active_session", None)
    st.session_state.setdefault("events", [])
    st.session_state.setdefault("next_event_cursor", None)
    st.session_state.setdefault("message_error", "")
    st.session_state.setdefault("message_success", "")
    st.session_state.setdefault("issue_approval_result", None)
    st.session_state.setdefault("last_event_refresh", 0.0)
    st.session_state.setdefault("planner_api_token", None)
    st.session_state.setdefault("planner_authenticated", False)


def clear_session() -> None:
    st.session_state["active_session_id"] = None
    st.session_state["active_session"] = None
    st.session_state["events"] = []
    st.session_state["next_event_cursor"] = None
    st.session_state["message_error"] = ""
    st.session_state["message_success"] = ""
    st.session_state["issue_approval_result"] = None


def clear_planner_auth() -> None:
    st.session_state["planner_api_token"] = None
    st.session_state["planner_authenticated"] = False
    clear_session()


def _planner_token_from_secret(config: AppConfig) -> str | None:
    if not config.planner_secret_project_id:
        raise PlannerApiError(
            "❌ ERROR: Planner secret project id is required for password/token login. "
            "Set --planner-secret-project-id or --planner-secret-project env var."
        )
    if not config.planner_token_secret:
        raise PlannerApiError(
            "❌ ERROR: Planner token secret name is not configured. Set --planner-token-secret."
        )
    try:
        return load_secret(
            config.planner_secret_project_id,
            config.planner_token_secret,
            config.planner_secret_version,
            config.planner_credentials_file,
        ).strip()
    except Exception as exc:
        raise PlannerApiError(
            f"❌ ERROR: failed to load planner token secret ({config.planner_token_secret}): {exc}"
        ) from exc


def _planner_password_from_secret(config: AppConfig) -> str | None:
    if not config.planner_secret_project_id:
        raise PlannerApiError(
            "❌ ERROR: Planner secret project id is required for password login. "
            "Set --planner-secret-project-id or --planner-secret-project env var."
        )
    if not config.planner_password_secret:
        raise PlannerApiError(
            "❌ ERROR: Planner password secret name is not configured. "
            "Set --planner-password-secret."
        )
    try:
        return load_secret(
            config.planner_secret_project_id,
            config.planner_password_secret,
            config.planner_secret_version,
            config.planner_credentials_file,
        ).strip()
    except Exception as exc:
        raise PlannerApiError(
            "❌ ERROR: failed to load planner password secret "
            f"({config.planner_password_secret}): {exc}"
        ) from exc


def _login_with_password(config: AppConfig, password: str) -> None:
    """Verify password against Secret Manager, then fetch bearer token from Secret Manager."""
    expected_password = _planner_password_from_secret(config)
    if not expected_password:
        raise PlannerApiError("❌ ERROR: stored dashboard password is empty in Secret Manager")

    if not hmac.compare_digest(password.strip(), expected_password):
        raise PlannerApiError("❌ ERROR: invalid dashboard password")

    token = _planner_token_from_secret(config)
    if not token:
        raise PlannerApiError("❌ ERROR: API token secret is empty in Secret Manager")

    st.session_state["planner_api_token"] = token
    st.session_state["planner_authenticated"] = True


def apply_style() -> None:
    st.markdown(
        """
<style>
  .stApp {
    background: radial-gradient(circle at top left, #1f2a44 0%, #141a2a 45%, #0d121d 100%);
    color: #eef2ff;
  }
  .page-title {
    font-family: "Trebuchet MS", "Segoe UI", "Helvetica Neue", sans-serif;
    font-size: 2.2rem;
    letter-spacing: 0.02em;
    color: #f8fbff;
  }
  .subtle {
    color: #9fb0ce;
    font-size: 0.92rem;
  }
  .session-card {
    border: 1px solid #384b72;
    border-radius: 12px;
    padding: 16px;
    background: rgba(22, 31, 53, 0.86);
    margin-bottom: 14px;
  }
  .status-pill {
    display: inline-block;
    border-radius: 999px;
    padding: 4px 10px;
    font-size: 0.82rem;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    background: #3c4f7a;
    color: #ebf0ff;
  }
  .event-card {
    border: 1px solid #2f4061;
    border-radius: 10px;
    background: rgba(15, 20, 34, 0.8);
    padding: 10px;
    margin-bottom: 8px;
  }
  .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  }
</style>
""",
        unsafe_allow_html=True,
    )


def set_flash(message: str, *, kind: str = "success") -> None:
    if kind == "error":
        st.session_state["message_error"] = message
        st.session_state["message_success"] = ""
    else:
        st.session_state["message_success"] = message
        st.session_state["message_error"] = ""


def render_messages() -> None:
    if st.session_state["message_error"]:
        st.error(st.session_state["message_error"])
        st.session_state["message_error"] = ""
    if st.session_state["message_success"]:
        st.info(st.session_state["message_success"])
        st.session_state["message_success"] = ""


def render_issue_approval_results() -> None:
    approval_result = st.session_state.get("issue_approval_result")
    if not approval_result:
        return

    approved = approval_result.get("approved", [])
    failed = approval_result.get("failed", [])

    if approved:
        st.success(f"Approved {len(approved)} issue(s).")
        with st.expander("Approved issues", expanded=True):
            for issue in approved:
                repo = str(issue.get("repo", "")).strip()
                number = issue.get("number")
                issue_id = str(issue.get("issue_id", "")).strip()
                label = f"{repo}#{number}" if number is not None else "unknown"
                if issue_id:
                    label = f"{label} ({issue_id})"
                st.markdown(f"- ✅ {label}")

    if failed:
        st.error(f"Failed to approve {len(failed)} issue(s).")
        with st.expander("Failed issues", expanded=True):
            for issue in failed:
                repo = str(issue.get("repo", "")).strip()
                number = issue.get("number")
                issue_id = str(issue.get("issue_id", "")).strip()
                error = str(issue.get("error", "unknown error"))
                label = f"{repo}#{number}" if number is not None else "unknown"
                if issue_id:
                    label = f"{label} ({issue_id})"
                st.markdown(f"- ❌ {label}: {error}")

    st.session_state["issue_approval_result"] = None


def load_session(api: PlannerApiClient) -> None:
    session_id = st.session_state.get("active_session_id")
    if not session_id:
        return
    try:
        session = api.get_session(session_id=session_id)
    except PlannerApiError as exc:
        set_flash(f"Failed to load session: {exc}", kind="error")
        clear_session()
        return
    st.session_state["active_session"] = session


def load_events(api: PlannerApiClient, *, force: bool = False) -> None:
    session_id = st.session_state.get("active_session_id")
    if not session_id:
        return

    if force:
        st.session_state["next_event_cursor"] = None
        st.session_state["events"] = []

    after = st.session_state.get("next_event_cursor")
    try:
        event_page = api.get_events(session_id=session_id, after=after)
    except PlannerApiError as exc:
        set_flash(f"Failed to load events: {exc}", kind="error")
        return

    events = event_page.get("events") or []
    if after is None:
        st.session_state["events"] = events
    else:
        st.session_state["events"].extend(events)
    st.session_state["next_event_cursor"] = event_page.get("next_cursor")


def create_session_form(api: PlannerApiClient) -> None:
    st.markdown('<div class="session-card">', unsafe_allow_html=True)
    st.subheader("Create planning session")
    with st.form("planning_session_form"):
        project_slug = st.text_input("Project slug", value="example-project")
        mode = st.selectbox(
            "Mode",
            options=["plan_only", "plan_and_create_issues"],
            index=0,
            help=(
                "Choose `plan_only` for artifacts only or "
                "`plan_and_create_issues` to open GitHub issues."
            ),
        )
        request_text = st.text_area(
            "What should the planner work on?",
            value="",
            height=120,
            placeholder="Plan the next release with measurable success criteria.",
        )
        create = st.form_submit_button("Create session")

    if create:
        if not project_slug.strip() or not request_text.strip():
            set_flash("Project slug and request text are required.", kind="error")
        else:
            try:
                session = api.create_session(
                    project_slug=project_slug.strip(),
                    mode=mode,
                    request_text=request_text.strip(),
                )
            except PlannerApiError as exc:
                set_flash(f"Failed to create session: {exc}", kind="error")
            else:
                st.session_state["active_session_id"] = session["id"]
                st.session_state["active_session"] = session
                st.session_state["events"] = []
                st.session_state["next_event_cursor"] = None
                st.session_state["last_event_refresh"] = 0.0
                load_events(api, force=True)
                set_flash("Session created. Answer each question to continue.")
                st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def render_session_header(session: dict[str, Any]) -> None:
    status = session.get("status", "unknown")
    st.markdown(
        f"""
<div class="session-card">
  <div class="page-title">Session {session.get("id", "—")}</div>
  <div class="subtle">Project: {session.get("project_slug", "—")}
   • Mode: {session.get("mode", "plan_only")}</div>
  <p><span class="status-pill">Status: {status}</span></p>
  <p class="subtle mono">{session.get("request_text", "")}</p>
</div>
""",
        unsafe_allow_html=True,
    )


def render_conversation(session: dict[str, Any]) -> None:
    st.subheader("Conversation")
    questions = session.get("questions", [])
    answers = session.get("answers", {})
    if not questions:
        st.info("No questions available for this session yet.")
        return

    for index, question in enumerate(questions, start=1):
        question_id = question.get("id", f"q-{index}")
        answered = question_id in answers
        if answered:
            st.success(f"Q{index}: {question.get('text')}")
            st.markdown(
                f"<div class='mono subtle'>A: {answers[question_id]}</div>",
                unsafe_allow_html=True,
            )
        else:
            st.info(f"Q{index}: {question.get('text')}")
            st.markdown("<div class='subtle'>Awaiting answer</div>", unsafe_allow_html=True)


def render_question_entry(api: PlannerApiClient, session: dict[str, Any]) -> None:
    session_id = session.get("id")
    if not session_id:
        return

    questions = session.get("questions", [])
    answers = session.get("answers", {})
    next_question = next(
        (question for question in questions if question.get("id") not in answers),
        None,
    )
    if next_question is None:
        st.success("Intake complete. You can start planning now.")
        return

    question_id = next_question.get("id", "")
    question_type = next_question.get("question_type", "text")
    text = next_question.get("text", "")
    options = next_question.get("options", [])
    default_value = options[0] if options else ""

    st.markdown("<hr/>", unsafe_allow_html=True)
    st.subheader("Answer next question")
    with st.form(f"answer_form_{question_id}"):
        st.markdown(f"**{text}**")
        if question_type == "single_choice" and options:
            answer = st.radio(
                "Select one",
                options=options,
                index=0,
                label_visibility="collapsed",
            )
        else:
            answer = st.text_area("Your answer", value=default_value, height=90)

        posted = st.form_submit_button("Submit answer")
    if not posted:
        return

    if not str(answer).strip():
        set_flash("Answer cannot be empty.", kind="error")
        return
    try:
        api.post_answer(
            session_id=session_id,
            question_id=question_id,
            answer=str(answer).strip(),
            source="user",
        )
    except PlannerApiError as exc:
        set_flash(f"Failed to submit answer: {exc}", kind="error")
        return

    load_session(api)
    load_events(api)
    set_flash("Answer recorded.")
    st.rerun()


def render_start_button(api: PlannerApiClient, session: dict[str, Any]) -> None:
    status = session.get("status")
    can_start = status == "ready_to_run"
    if st.button("Start planning", type="primary", disabled=not can_start):
        session_id = session.get("id")
        if not session_id:
            set_flash("No active session id.", kind="error")
            return
        try:
            started = api.start_session(session_id=session_id)
        except PlannerApiError as exc:
            set_flash(f"Failed to start session: {exc}", kind="error")
            return
        st.session_state["active_session"] = started
        load_events(api, force=True)
        set_flash("Planning started.")
        st.rerun()


def render_events(
    api: PlannerApiClient,
    poll_interval: int,
    auto_refresh: bool,
) -> None:
    st.subheader("Events")
    if st.button("Refresh events now"):
        load_events(api, force=True)

    if not st.session_state["events"]:
        st.info("No events yet.")
        return

    st.caption(f"Loaded events: {len(st.session_state['events'])}")
    for event in reversed(st.session_state["events"]):
        event_type = event.get("event_type", "unknown")
        payload = event.get("payload", {})
        payload_render = json.dumps(payload, indent=2, sort_keys=True)
        ts = event.get("created_at", "")
        st.markdown(
            f"""
<div class="event-card">
  <strong>{event_type}</strong>
  <div class="subtle mono">{ts}</div>
  <pre class="mono">{payload_render}</pre>
</div>
""",
            unsafe_allow_html=True,
        )

    if auto_refresh:
        now = time.time()
        last = st.session_state.get("last_event_refresh", 0.0)
        if now - last >= poll_interval:
            st.session_state["last_event_refresh"] = now
            load_events(api, force=False)
            st.rerun()
        remaining = max(0.0, poll_interval - (now - last))
        st.caption(f"Auto-refresh in {remaining:.0f}s." if remaining else "Auto-refreshing...")


def render_artifacts(api: PlannerApiClient, session_id: str) -> None:
    st.subheader("Artifacts")
    try:
        artifacts_payload = api.get_artifacts(session_id=session_id)
    except PlannerApiError as exc:
        set_flash(f"Failed to load artifacts: {exc}", kind="error")
        return

    artifacts = artifacts_payload.get("artifacts", [])
    if not artifacts:
        st.info("No artifacts yet.")
        return

    for artifact in artifacts:
        artifact_type = artifact.get("artifact_type", "artifact")
        content_url = artifact.get("content_url", "")
        label = f"{artifact_type} · {artifact.get('id', '')}".strip()
        if content_url:
            st.link_button(label, content_url)
        else:
            st.write(label)


def _session_created_issues() -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    for event in st.session_state.get("events", []):
        if event.get("event_type") != "issues_written":
            continue
        for issue in event.get("payload", {}).get("created", []) or []:
            repo = str(issue.get("repo", "")).strip()
            title = str(issue.get("title", "")).strip()
            issue_id = str(issue.get("issue_id", "")).strip()
            try:
                number = int(issue.get("number", 0))
            except (TypeError, ValueError):
                number = 0
            if not repo or number <= 0:
                continue
            issue_key = (repo, number)
            if issue_key in seen:
                continue
            seen.add(issue_key)
            created.append(
                {
                    "repo": repo,
                    "number": number,
                    "title": title,
                    "issue_id": issue_id,
                    "url": str(issue.get("url", "")).strip(),
                }
            )
    return created


def _is_planner_authenticated() -> bool:
    return bool(st.session_state.get("planner_api_token")) and bool(
        st.session_state.get("planner_authenticated")
    )


def _set_direct_token(token: str) -> None:
    st.session_state["planner_api_token"] = token
    st.session_state["planner_authenticated"] = True


def render_auth_sidebar(config: AppConfig) -> None:
    st.sidebar.title("Planner")
    st.sidebar.markdown(f"**API URL**  \n`{config.planner_url}`")

    if st.session_state.get("planner_authenticated") and st.session_state.get("planner_api_token"):
        st.sidebar.success("Signed in")
        if st.sidebar.button("Sign out"):
            clear_planner_auth()
            st.rerun()
        return

    st.sidebar.markdown("### Sign in")

    if not config.planner_secret_project_id:
        st.sidebar.warning(
            "Could not detect a GCP project for Secret Manager.\n\n"
            "Run `gcloud config set project <PROJECT>` or "
            f"set `{PLANNER_SECRET_PROJECT_ENV}`.\n\n"
            f"Or pass `--planner-api-token` to skip password login."
        )
        return

    st.sidebar.caption(f"GCP project: `{config.planner_secret_project_id}`")

    with st.sidebar.form("planner_login_form"):
        password = st.text_input("Dashboard password", type="password")
        submit = st.form_submit_button("Sign in")

    if not submit:
        return

    if not password.strip():
        set_flash("Password is required.", kind="error")
        return

    try:
        _login_with_password(config, password)
    except PlannerApiError as exc:
        set_flash(f"Login failed: {exc}", kind="error")
    else:
        set_flash("Signed in.")
        st.rerun()


def render_issue_approval(api: PlannerApiClient, session: dict[str, Any]) -> None:
    session_id = session.get("id")
    if not session_id:
        return

    created_issues = _session_created_issues()
    if not created_issues:
        return

    st.subheader("Created Issues")
    st.caption("Bulk-approve planning-created issues and send them to the Ready project queue.")

    options = []
    issue_by_option: dict[str, dict[str, Any]] = {}
    for index, issue in enumerate(created_issues, start=1):
        issue_label = issue["title"] or issue["issue_id"]
        option = f"{index}. {issue['repo']}#{issue['number']} · {issue_label}"
        options.append(option)
        issue_by_option[option] = issue

    with st.form("issue_approval_form"):
        selected = st.multiselect(
            "Issues to approve",
            options=options,
            default=options,
            help="Move selected issues to the configured Ready status in your GitHub Project.",
        )
        approved = st.form_submit_button("Approve selected issues")

    if not approved:
        return

    if not selected:
        set_flash("Select at least one issue to approve.", kind="error")
        return

    approve_payload: list[dict[str, Any]] = []
    for key in selected:
        issue = issue_by_option[key]
        approve_payload.append(
            {
                "issue_id": issue.get("issue_id"),
                "repo": issue["repo"],
                "number": issue["number"],
                "title": issue.get("title"),
            }
        )

    try:
        result = api.approve_session_issues(
            session_id=session_id,
            issues=approve_payload,
        )
    except PlannerApiError as exc:
        set_flash(f"Failed to approve issues: {exc}", kind="error")
        return

    st.session_state["issue_approval_result"] = result
    set_flash(
        "Issue approval requested. Review per-item results below.",
    )
    load_events(api)
    st.rerun()


def render_planning_page(api: PlannerApiClient, config: AppConfig) -> None:
    st.markdown("<div class='page-title'>ACE Planning Dashboard</div>", unsafe_allow_html=True)
    st.caption("Create planning sessions, answer intake questions, and monitor progress events.")

    render_messages()

    if st.session_state["active_session_id"] is None:
        create_session_form(api)
        return

    session = st.session_state.get("active_session") or {}
    render_session_header(session)

    if not session:
        load_session(api)
        session = st.session_state.get("active_session") or {}
        if not session:
            clear_session()
            st.rerun()
            return

    col_left, col_right = st.columns([2, 1])
    with col_left:
        render_conversation(session)
        render_question_entry(api, session)
        render_start_button(api, session)
    with col_right:
        render_events(api, config.poll_interval_seconds, config.auto_refresh)
        render_artifacts(api, session.get("id", ""))
        render_issue_approval(api, session)
        render_issue_approval_results()

    st.button("Reset session", on_click=clear_session, type="secondary")


def render_about_page() -> None:
    st.markdown("<div class='page-title'>About</div>", unsafe_allow_html=True)
    st.markdown(
        """
        The planning dashboard talks to the local Planner API.

        1. Create a new session using a project slug and request text.
        2. Answer generated intake questions.
        3. Start planning when the session moves to `ready_to_run`.
        4. Watch events and artifact links while execution advances.
        """
    )


def render_sidebar(config: AppConfig) -> None:
    st.sidebar.checkbox(
        "Auto-refresh events",
        value=config.auto_refresh,
        key="planner_auto_refresh",
    )
    config.auto_refresh = st.session_state["planner_auto_refresh"]
    st.sidebar.slider(
        "Poll interval (seconds)",
        min_value=1,
        max_value=20,
        value=config.poll_interval_seconds,
        key="planner_poll_interval",
    )
    config.poll_interval_seconds = st.session_state["planner_poll_interval"]
    if st.sidebar.button("Clear active session"):
        clear_session()
        st.rerun()


def main() -> None:
    config = parse_args()
    run_planner_app(config)


def run_planner_app(config: AppConfig, *, selected_page: str = "Planning") -> None:
    if selected_page not in {"Planning", "About"}:
        selected_page = "Planning"

    initialize_state()

    if config.planner_token:
        _set_direct_token(config.planner_token)
        st.info("Using CLI-provided planner token.")

    render_auth_sidebar(config)
    render_sidebar(config)
    if config.auto_refresh != st.session_state["planner_auto_refresh"]:
        config.auto_refresh = st.session_state["planner_auto_refresh"]
    if config.poll_interval_seconds != st.session_state["planner_poll_interval"]:
        config.poll_interval_seconds = st.session_state["planner_poll_interval"]

    api: PlannerApiClient | None = None
    if _is_planner_authenticated() and st.session_state.get("planner_api_token"):
        api = PlannerApiClient(
            config.planner_url,
            token=str(st.session_state["planner_api_token"]).strip(),
        )

    if selected_page == "About":
        render_about_page()
        return

    if not api:
        st.markdown(
            "<div class='page-title'>ACE Planning Dashboard</div>",
            unsafe_allow_html=True,
        )
        st.markdown("---")
        st.subheader("Sign in to continue")
        if not config.planner_secret_project_id:
            st.warning(
                "Could not detect a GCP project for Secret Manager.\n\n"
                f"Run `gcloud config set project <PROJECT>` or set "
                f"`{PLANNER_SECRET_PROJECT_ENV}`, then refresh.\n\n"
                "Or pass `--planner-api-token` to skip password login."
            )
        else:
            st.caption(f"GCP project: `{config.planner_secret_project_id}`")
            with st.form("main_login_form"):
                password = st.text_input("Dashboard password", type="password")
                submit = st.form_submit_button("Sign in", type="primary")
            if submit:
                if not password.strip():
                    st.error("Password is required.")
                else:
                    try:
                        _login_with_password(config, password)
                    except PlannerApiError as exc:
                        st.error(f"Login failed: {exc}")
                    else:
                        st.rerun()
        return

    if api and st.session_state["active_session_id"]:
        load_session(api)
        load_events(api)

    render_planning_page(api, config)


def _main_with_shell_layout() -> None:
    config = parse_args()
    st.set_page_config(page_title="ACE Planning Dashboard", layout="wide")
    apply_style()

    page = st.sidebar.radio("Page", ["Planning", "About"], key="planner_shell_page")
    run_planner_app(config, selected_page=page)


if __name__ == "__main__":
    _main_with_shell_layout()
