from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    base_workspace: Path = Path(".trustgraph-cloud")
    max_concurrent_workers: int = 1
    log_level: str = "INFO"

    model_config = {
        "env_prefix": "TRUSTGRAPH_",
        "env_file": ".env",
        "extra": "ignore",
    }

    @property
    def jobs_dir(self) -> Path:
        return self.base_workspace / "jobs"

    def job_workspace(self, job_id: str) -> Path:
        return self.jobs_dir / job_id


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Reset cached settings. Used in tests only."""
    global _settings
    _settings = None
