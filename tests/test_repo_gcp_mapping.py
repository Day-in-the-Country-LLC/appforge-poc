import json

import pytest

from ace.webhooks.repo_gcp_mapping import load_repo_gcp_mapping


def test_load_repo_gcp_mapping_happy_path(tmp_path):
    mapping_file = tmp_path / "repo-gcp.json"
    mapping_file.write_text(
        json.dumps(
            [
                {"repo": "Day-in-the-Country-LLC/digido", "gcp_project": "digido-assistant"},
                {"repo": "Day-in-the-Country-LLC/appforge-poc", "gcp_project": "appforge-483920"},
            ]
        ),
        encoding="utf-8",
    )

    mapping = load_repo_gcp_mapping(str(mapping_file))
    assert mapping == {
        "day-in-the-country-llc/digido": "digido-assistant",
        "day-in-the-country-llc/appforge-poc": "appforge-483920",
    }


def test_load_repo_gcp_mapping_duplicate_repo_mismatch_fails(tmp_path):
    mapping_file = tmp_path / "repo-gcp.json"
    mapping_file.write_text(
        json.dumps(
            [
                {"repo": "Day-in-the-Country-LLC/digido", "gcp_project": "digido-assistant"},
                {"repo": "day-in-the-country-llc/digido", "gcp_project": "other-project"},
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="❌ ERROR: repo_gcp_mapping_duplicate_repo"):
        load_repo_gcp_mapping(str(mapping_file))
