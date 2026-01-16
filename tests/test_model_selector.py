"""Tests for difficulty-based model selection."""

import pytest

from ace.agents.model_selector import Difficulty, ModelSelector


def test_select_easy_model():
    """Test selection of easy difficulty model."""
    selector = ModelSelector()
    labels = ["agent:ready", "difficulty:easy"]

    config = selector.select_model(labels)

    assert config.backend == "codex"
    assert config.model == "gpt-5.1-codex"


def test_select_medium_model():
    """Test selection of medium difficulty model."""
    selector = ModelSelector()
    labels = ["agent:ready", "difficulty:medium"]

    config = selector.select_model(labels)

    assert config.backend == "claude"
    assert config.model == "claude-haiku-4-5"


def test_select_hard_model():
    """Test selection of hard difficulty model."""
    selector = ModelSelector()
    labels = ["agent:ready", "difficulty:hard"]

    config = selector.select_model(labels)

    assert config.backend == "claude"
    assert config.model == "claude-opus-4-1"


def test_no_difficulty_label_raises_error():
    """Test that missing difficulty label raises ValueError."""
    selector = ModelSelector()
    labels = ["agent:ready", "bug", "enhancement"]

    with pytest.raises(ValueError, match="No difficulty label found"):
        selector.select_model(labels)


def test_get_default_model():
    """Test getting default model (easy)."""
    selector = ModelSelector()

    config = selector.get_default_model()

    assert config.backend == "codex"
    assert config.model == "gpt-5.1-codex"


def test_difficulty_enum_values():
    """Test difficulty enum values."""
    assert Difficulty.EASY.value == "difficulty:easy"
    assert Difficulty.MEDIUM.value == "difficulty:medium"
    assert Difficulty.HARD.value == "difficulty:hard"
