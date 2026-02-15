"""Unit tests for planning review helper functions."""

from __future__ import annotations

import json

import pytest

from ace.planning.routes import _extract_json_payload, _truncate_payload


class TestExtractJsonPayload:
    """Tests for _extract_json_payload."""

    def test_plain_json_object(self) -> None:
        raw = '{"key": "value"}'
        assert _extract_json_payload(raw) == {"key": "value"}

    def test_plain_json_array(self) -> None:
        raw = "[1, 2, 3]"
        assert _extract_json_payload(raw) == [1, 2, 3]

    def test_fenced_json_block(self) -> None:
        raw = 'Here is the result:\n```json\n{"issues": []}\n```'
        assert _extract_json_payload(raw) == {"issues": []}

    def test_fenced_block_without_json_tag(self) -> None:
        raw = '```\n{"a": 1}\n```'
        assert _extract_json_payload(raw) == {"a": 1}

    def test_prose_before_json(self) -> None:
        raw = 'The output is: {"result": true}'
        assert _extract_json_payload(raw) == {"result": True}

    def test_prose_before_json_array(self) -> None:
        raw = '[{"id": 1}]'
        assert _extract_json_payload(raw) == [{"id": 1}]

    def test_multiple_fenced_blocks_first_invalid(self) -> None:
        raw = '```\nnot json\n```\n```json\n{"valid": true}\n```'
        assert _extract_json_payload(raw) == {"valid": True}

    def test_nested_objects(self) -> None:
        nested = {"outer": {"inner": [1, 2, {"deep": True}]}}
        raw = f"```json\n{json.dumps(nested)}\n```"
        assert _extract_json_payload(raw) == nested

    def test_whitespace_padding(self) -> None:
        raw = '   \n  {"padded": true}  \n   '
        assert _extract_json_payload(raw) == {"padded": True}

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="model response was empty"):
            _extract_json_payload("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="model response was empty"):
            _extract_json_payload("   \n  ")

    def test_no_json_raises(self) -> None:
        with pytest.raises(ValueError, match="did not contain valid JSON"):
            _extract_json_payload("This is just plain text with no JSON.")

    def test_non_string_raises(self) -> None:
        with pytest.raises(ValueError, match="expected model response to be text"):
            _extract_json_payload(123)  # type: ignore[arg-type]

    def test_deduplicates_candidates(self) -> None:
        raw = '{"key": "value"}'
        result = _extract_json_payload(raw)
        assert result == {"key": "value"}

    def test_fenced_json_preferred_over_fallback(self) -> None:
        raw = 'Prefix ```json\n{"fenced": true}\n``` suffix {"unfenced": true}'
        result = _extract_json_payload(raw)
        assert result == {"fenced": True}

    def test_broken_json_in_fenced_falls_through(self) -> None:
        raw = '```json\n{"broken": tru}\n```\n```json\n{"valid": 1}\n```'
        assert _extract_json_payload(raw) == {"valid": 1}

    def test_curly_brace_fallback_extracts_inner_object(self) -> None:
        raw = 'The items are: [{"id": 1}]'
        result = _extract_json_payload(raw)
        assert result == {"id": 1}


class TestTruncatePayload:
    """Tests for _truncate_payload."""

    def test_empty_string(self) -> None:
        assert _truncate_payload("", max_len=100) == ""

    def test_within_limit(self) -> None:
        payload = '{"issues": []}'
        assert _truncate_payload(payload, max_len=100) == payload

    def test_plain_text_truncation(self) -> None:
        payload = "x" * 200
        result = _truncate_payload(payload, max_len=50)
        assert result == "x" * 50 + "..."
        assert len(result) == 53

    def test_issues_json_truncates_at_boundary(self) -> None:
        issues = {"issues": [{"id": f"ISSUE-{i:03d}", "title": f"Issue {i}"} for i in range(20)]}
        payload = json.dumps(issues)
        result = _truncate_payload(payload, max_len=200)
        parsed = json.loads(result)
        assert "issues" in parsed
        assert len(parsed["issues"]) < 20
        assert len(result) <= 200

    def test_non_issues_json_falls_back_to_slice(self) -> None:
        payload = json.dumps({"data": "x" * 300})
        result = _truncate_payload(payload, max_len=50)
        assert result.endswith("...")
