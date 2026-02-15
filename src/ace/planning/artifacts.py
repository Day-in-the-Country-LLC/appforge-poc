"""Persistence for planning artifacts."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Literal

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
    async def write_artifact(
        self,
        session_id: str,
        filename: str,
        content: str,
        *,
        content_type: str = "text/plain",
    ) -> str:
        """Write an artifact file and return its public URL."""

    async def write_plan_markdown(
        self,
        session_id: str,
        content: str,
    ) -> str:
        """Write the planning PLAN.md artifact and return its public URL."""
        return await self.write_artifact(
            session_id=session_id,
            filename="PLAN.md",
            content=content,
            content_type="text/markdown",
        )

    async def write_issues_json(
        self,
        session_id: str,
        content: str,
    ) -> str:
        return await self.write_artifact(
            session_id=session_id,
            filename="ISSUES.json",
            content=content,
            content_type="application/json",
        )

    async def write_dependencies_mmd(
        self,
        session_id: str,
        content: str,
    ) -> str:
        return await self.write_artifact(
            session_id=session_id,
            filename="DEPENDENCIES.mmd",
            content=content,
            content_type="text/plain",
        )


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

    async def write_artifact(
        self,
        session_id: str,
        filename: str,
        content: str,
        *,
        content_type: Literal["text/plain", "text/markdown", "application/json"] = "text/plain",
    ) -> str:
        return await asyncio.to_thread(
            self._write_artifact_sync,
            session_id=session_id,
            filename=filename,
            content=content,
            content_type=content_type,
        )

    def _write_artifact_sync(
        self,
        session_id: str,
        filename: str,
        content: str,
        content_type: str,
    ) -> str:
        if not session_id:
            raise PlanningArtifactStoreError("❌ ERROR: session_id is required")
        if not filename:
            raise PlanningArtifactStoreError("❌ ERROR: filename is required")
        bucket = self._client.bucket(self._bucket_name)
        blob_name = self._blob_name(session_id, filename)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(str(content).encode("utf-8"), content_type=content_type)
        return f"https://storage.googleapis.com/{self._bucket_name}/{blob_name}"

    def _blob_name(self, session_id: str, filename: str) -> str:
        return f"plans/{session_id}/{filename}"
