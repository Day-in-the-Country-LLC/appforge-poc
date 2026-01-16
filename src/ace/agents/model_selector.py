"""Model selection based on issue difficulty."""

from dataclasses import dataclass
from enum import Enum

import structlog

from ace.config.settings import get_settings

logger = structlog.get_logger(__name__)


class Difficulty(str, Enum):
    """Issue difficulty levels."""

    EASY = "difficulty:easy"
    MEDIUM = "difficulty:medium"
    HARD = "difficulty:hard"


@dataclass
class ModelConfig:
    """Configuration for a specific model."""

    backend: str
    model: str


class ModelSelector:
    """Selects appropriate backend and model based on issue difficulty."""

    def __init__(self):
        """Initialize model selector with settings."""
        self.settings = get_settings()
        self.difficulty_map = {
            Difficulty.EASY: ModelConfig(
                backend=self.settings.difficulty_easy_backend,
                model=self.settings.difficulty_easy_model,
            ),
            Difficulty.MEDIUM: ModelConfig(
                backend=self.settings.difficulty_medium_backend,
                model=self.settings.difficulty_medium_model,
            ),
            Difficulty.HARD: ModelConfig(
                backend=self.settings.difficulty_hard_backend,
                model=self.settings.difficulty_hard_model,
            ),
        }

    def select_model(self, labels: list[str]) -> ModelConfig:
        """Select model based on issue labels.

        Args:
            labels: List of GitHub issue labels

        Returns:
            ModelConfig with backend and model selection

        Raises:
            ValueError: If no difficulty label found
        """
        for difficulty in Difficulty:
            if difficulty.value in labels:
                config = self.difficulty_map[difficulty]
                logger.info(
                    "model_selected",
                    difficulty=difficulty.value,
                    backend=config.backend,
                    model=config.model,
                )
                return config

        logger.warning("no_difficulty_label_found", labels=labels)
        raise ValueError(
            f"No difficulty label found in {labels}. "
            f"Expected one of: {[d.value for d in Difficulty]}"
        )

    def get_default_model(self) -> ModelConfig:
        """Get default model (easy difficulty).

        Returns:
            ModelConfig for easy difficulty
        """
        return self.difficulty_map[Difficulty.EASY]
