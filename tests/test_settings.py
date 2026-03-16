"""Settings singleton and override behavior tests."""

from __future__ import annotations

from ace.config import settings as settings_module


def _restore_settings_state(
    original_overrides: dict[str, object],
    original_cached: settings_module.Settings | None,
) -> None:
    settings_module._SETTINGS_OVERRIDES.clear()
    settings_module._SETTINGS_OVERRIDES.update(original_overrides)
    settings_module._SETTINGS = original_cached


def test_get_settings_returns_cached_instance() -> None:
    original_overrides = dict(settings_module._SETTINGS_OVERRIDES)
    original_cached = settings_module._SETTINGS
    try:
        settings_module._SETTINGS_OVERRIDES.clear()
        settings_module._SETTINGS = None

        first = settings_module.get_settings()
        second = settings_module.get_settings()

        assert first is second
    finally:
        _restore_settings_state(original_overrides, original_cached)


def test_set_settings_overrides_invalidates_cached_settings() -> None:
    original_overrides = dict(settings_module._SETTINGS_OVERRIDES)
    original_cached = settings_module._SETTINGS
    try:
        settings_module._SETTINGS_OVERRIDES.clear()
        settings_module._SETTINGS = None

        first = settings_module.get_settings()
        settings_module.set_settings_overrides(webhook_service_role="listener")
        second = settings_module.get_settings()

        assert first is not second
        assert second.webhook_service_role == "listener"
    finally:
        _restore_settings_state(original_overrides, original_cached)
