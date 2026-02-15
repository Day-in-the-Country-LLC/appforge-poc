import signal
import subprocess

from ace.agents.cli_agent import CliAgent


class _StubProc:
    def __init__(self):
        self.pid = 4242
        self.returncode = None
        self.wait_calls = 0
        self.terminate_called = False
        self.kill_called = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise subprocess.TimeoutExpired("cmd", timeout)
        self.returncode = 0
        return 0

    def terminate(self):
        self.terminate_called = True
        self.returncode = 0

    def kill(self):
        self.kill_called = True
        self.returncode = 0


def test_terminate_process_kills_process_group(monkeypatch):
    proc = _StubProc()
    agent = CliAgent(backend="claude", model="claude-haiku-4-5")
    killpg_calls = []

    monkeypatch.setattr("ace.agents.cli_agent.os.getpgid", lambda _pid: 999)
    monkeypatch.setattr(
        "ace.agents.cli_agent.os.killpg",
        lambda pgid, sig: killpg_calls.append((pgid, sig)),
    )

    agent._terminate_process(proc, grace_seconds=0.01)

    assert killpg_calls == [(999, signal.SIGTERM), (999, signal.SIGKILL)]
    assert proc.terminate_called is False
    assert proc.kill_called is False


def test_terminate_process_sigterm_succeeds_no_sigkill(monkeypatch):
    """When SIGTERM is enough (wait succeeds on first call), SIGKILL is never sent."""

    class _GracefulProc(_StubProc):
        def wait(self, timeout=None):
            self.wait_calls += 1
            self.returncode = 0
            return 0

    proc = _GracefulProc()
    agent = CliAgent(backend="claude", model="claude-haiku-4-5")
    killpg_calls = []

    monkeypatch.setattr("ace.agents.cli_agent.os.getpgid", lambda _pid: 999)
    monkeypatch.setattr(
        "ace.agents.cli_agent.os.killpg",
        lambda pgid, sig: killpg_calls.append((pgid, sig)),
    )

    agent._terminate_process(proc, grace_seconds=1.0)

    assert killpg_calls == [(999, signal.SIGTERM)]
    assert proc.terminate_called is False
    assert proc.kill_called is False


def test_terminate_process_falls_back_to_parent_process(monkeypatch):
    proc = _StubProc()
    agent = CliAgent(backend="claude", model="claude-haiku-4-5")

    def _raise_getpgid(_pid):
        raise OSError("pgid unavailable")

    monkeypatch.setattr("ace.agents.cli_agent.os.getpgid", _raise_getpgid)

    agent._terminate_process(proc, grace_seconds=0.01)

    assert proc.terminate_called is True
    assert proc.kill_called is True


def test_run_cli_until_done_uses_new_process_session(monkeypatch, tmp_path):
    captured: dict[str, object] = {}
    done_path = tmp_path / "ACE_TASK_DONE.json"
    done_path.write_text("{}", encoding="utf-8")
    agent = CliAgent(backend="claude", model="claude-haiku-4-5")

    class _PopenCapture(_StubProc):
        def __init__(self, *args, **kwargs):
            super().__init__()
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setattr("ace.agents.cli_agent.subprocess.Popen", _PopenCapture)
    monkeypatch.setattr(
        agent,
        "_terminate_process",
        lambda proc, grace_seconds=5.0: setattr(proc, "returncode", 0),
    )

    _return_code, _stdout, _stderr, terminated_on_done = agent._run_cli_until_done(
        command=["echo", "hello"],
        workdir=tmp_path,
        env={},
        done_path=done_path,
        timeout_seconds=30,
    )

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("start_new_session") is True
    assert terminated_on_done is True
