"""Persistence for planning artifacts."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from ace.config.settings import Settings

try:
    from google.cloud import storage  # type: ignore[import-untyped]
except (ImportError, ModuleNotFoundError):  # pragma: no cover - environment dependent
    storage = None


class PlanningArtifactStoreError(ValueError):
    """Domain error for planning artifact writes."""


class PlanningArtifactStore(ABC):
    """Interface for writing planning output objects."""

    @abstractmethod
    async def write_plan_markdown(self, session_id: str, content: str) -> str:
        """Write the planning PLAN.md artifact and return its public URL."""


class GCSPlanningArtifactStore(PlanningArtifactStore):
    """Write planning artifacts into a GCS bucket."""

    def __init__(self, *, bucket_name: str, client: object) -> None:
        self._bucket_name = bucket_name
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> "GCSPlanningArtifactStore":
        if storage is None:
            raise PlanningArtifactStoreError(
                "❌ ERROR: google-cloud-storage is required for planning artifacts"
            )
        bucket_name = (settings.planning_artifacts_bucket or "").strip()
        if not bucket_name:
            raise PlanningArtifactStoreError(
                "❌ ERROR: PLANNING_ARTIFACTS_BUCKET is required for planning artifacts"
            )
        client = storage.Client()
        return cls(bucket_name=bucket_name, client=client)

    def _blob_name(self, session_id: str) -> str:
        return f"plans/{session_id}/PLAN.md"

    async def write_plan_markdown(self, session_id: str, content: str) -> str:
        return await asyncio.to_thread(self._write_plan_markdown_sync, session_id, content)

    def _write_plan_markdown_sync(self, session_id: str, content: str) -> str:
        if not session_id:
            raise PlanningArtifactStoreError("❌ ERROR: session_id is required")
        bucket = self._client.bucket(self._bucket_name)
        blob_name = self._blob_name(session_id)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(content.encode("utf-8"), content_type="text/markdown")
        return f"https://storage.googleapis.com/{self._bucket_name}/{blob_name}"
