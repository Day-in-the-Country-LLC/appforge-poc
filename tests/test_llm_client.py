"""Tests for LangSmith tracer initialization behavior."""

from __future__ import annotations

import threading

import pytest

from ace.agents import llm_client


def test_get_tracer_singleton_initializes_once_under_thread_race(monkeypatch: pytest.MonkeyPatch) -> None:
    init_count: dict[str, int] = {"count": 0}
    original_init = llm_client._LangSmithTracer.__init__

    def instrumented_init(self: llm_client._LangSmithTracer) -> None:
        init_count["count"] += 1
        original_init(self)

    monkeypatch.setattr(llm_client, "_TRACER", None)
    monkeypatch.setattr(llm_client._LangSmithTracer, "__init__", instrumented_init)

    start = threading.Barrier(2)
    instances: list[llm_client._LangSmithTracer] = []
    lock = threading.Lock()

    def worker() -> None:
        start.wait()
        instance = llm_client._get_tracer()
        with lock:
            instances.append(instance)

    # Reset state in case this test runs after eager initialization.
    llm_client._TRACER = None
    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert instances
    assert len({id(item) for item in instances}) == 1
    assert init_count["count"] == 1


def test_extract_openai_text_parses_expected_formats() -> None:
    assert (
        llm_client._extract_openai_text({"output_text": "flat"})
        == "flat"
    )
    assert (
        llm_client._extract_openai_text(
            {"output": [{"content": [{"text": "from_output"}]}]}
        )
        == "from_output"
    )
    assert (
        llm_client._extract_openai_text({"choices": [{"message": {"content": "via_choices"}}]})
        == "via_choices"
    )


def test_extract_openai_text_raises_for_unknown_payload() -> None:
    with pytest.raises(ValueError, match="❌ ERROR: Unrecognized OpenAI response format"):
        llm_client._extract_openai_text({"result": "n/a", "meta": {"state": "ok"}})

    with pytest.raises(ValueError, match="keys=\\['output'\\]") as exc:
        llm_client._extract_openai_text({"output": "bad-output-format"})
    assert "output" in str(exc.value)

    with pytest.raises(ValueError, match="keys=\\['choices'\\]") as exc:
        llm_client._extract_openai_text({"choices": "bad-choices-format"})
    assert "choices" in str(exc.value)
