from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class Artifact:
    name: str
    path: str       # local filesystem path (local backend); empty string for S3
    size_bytes: int
    storage_backend: str = "local"          # "local" | "s3"
    s3_key: Optional[str] = None            # S3 object key (s3 backend only)
    presigned_url: Optional[str] = None     # signed download URL (s3 backend only)
    content_type: Optional[str] = None


@runtime_checkable
class ArtifactStore(Protocol):
    def register(self, job_id: str, source_path: str, artifact_name: str) -> Artifact: ...
    def list(self, job_id: str) -> list[Artifact]: ...
    def get(self, job_id: str, artifact_name: str) -> Optional[Artifact]: ...


class LocalArtifactStore:
    """
    Stores artifacts under {jobs_dir}/{job_id}/artifacts/.

    Phase 2 migration: replace with an S3-backed store that uploads artifacts
    and returns pre-signed URLs, implementing the same ArtifactStore protocol.
    """

    def __init__(self, jobs_dir: Path) -> None:
        self._jobs_dir = jobs_dir

    def _artifact_dir(self, job_id: str) -> Path:
        return self._jobs_dir / job_id / "artifacts"

    def register(self, job_id: str, source_path: str, artifact_name: str) -> Artifact:
        src = Path(source_path)
        dest_dir = self._artifact_dir(job_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / artifact_name
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        return Artifact(name=artifact_name, path=str(dest), size_bytes=dest.stat().st_size)

    def list(self, job_id: str) -> list[Artifact]:
        artifact_dir = self._artifact_dir(job_id)
        if not artifact_dir.exists():
            return []
        return [
            Artifact(name=f.name, path=str(f), size_bytes=f.stat().st_size)
            for f in sorted(artifact_dir.iterdir())
            if f.is_file()
        ]

    def get(self, job_id: str, artifact_name: str) -> Optional[Artifact]:
        path = self._artifact_dir(job_id) / artifact_name
        if not path.exists():
            return None
        return Artifact(name=artifact_name, path=str(path), size_bytes=path.stat().st_size)
