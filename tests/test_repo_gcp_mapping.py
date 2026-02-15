import json

import pytest

from ace.webhooks.repo_gcp_mapping import load_repo_gcp_mapping


def test_load_repo_gcp_mapping_happy_path(tmp_path):
    mapping_file = tmp_path / "repo-gcp.json"
    mapping_file.write_text(
        json.dumps(
            [
                {"repo": "Acme-Corp/widget-api", "gcp_project": "widget-prod-123456"},
                {"repo": "Acme-Corp/platform-tools", "gcp_project": "platform-tools-789012"},
            ]
        ),
        encoding="utf-8",
    )

    mapping = load_repo_gcp_mapping(str(mapping_file))
    assert mapping == {
        "acme-corp/widget-api": "widget-prod-123456",
        "acme-corp/platform-tools": "platform-tools-789012",
    }


def test_load_repo_gcp_mapping_with_valid_color(tmp_path):
    mapping_file = tmp_path / "repo-gcp.json"
    mapping_file.write_text(
        json.dumps(
            [
                {
                    "repo": "Acme-Corp/widget-api",
                    "gcp_project": "widget-prod-123456",
                    "color": "7FB3FF",
                },
            ]
        ),
        encoding="utf-8",
    )

    mapping = load_repo_gcp_mapping(str(mapping_file))
    assert mapping == {"acme-corp/widget-api": "widget-prod-123456"}


def test_load_repo_gcp_mapping_with_invalid_color_fails(tmp_path):
    mapping_file = tmp_path / "repo-gcp.json"
    mapping_file.write_text(
        json.dumps(
            [
                {
                    "repo": "Acme-Corp/widget-api",
                    "gcp_project": "widget-prod-123456",
                    "color": "ZZZZZZ",
                },
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="color must be a valid hex color"):
        load_repo_gcp_mapping(str(mapping_file))


def test_load_repo_gcp_mapping_duplicate_repo_mismatch_fails(tmp_path):
    mapping_file = tmp_path / "repo-gcp.json"
    mapping_file.write_text(
        json.dumps(
            [
                {"repo": "Acme-Corp/widget-api", "gcp_project": "widget-prod-123456"},
                {"repo": "acme-corp/widget-api", "gcp_project": "other-project"},
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="❌ ERROR: repo_gcp_mapping_duplicate_repo"):
        load_repo_gcp_mapping(str(mapping_file))
