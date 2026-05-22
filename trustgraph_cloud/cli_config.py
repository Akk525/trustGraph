from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path.home() / ".trustgraph"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _load() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2))
    CONFIG_FILE.chmod(0o600)


def get_api_url() -> Optional[str]:
    return os.environ.get("TRUSTGRAPH_API_URL") or _load().get("api_url")


def get_token() -> Optional[str]:
    """Return the best available credential. API key > JWT token. Env vars > config file."""
    key = os.environ.get("TRUSTGRAPH_API_KEY")
    if key:
        return key
    token = os.environ.get("TRUSTGRAPH_API_TOKEN")
    if token:
        return token
    cfg = _load()
    return cfg.get("api_key") or cfg.get("access_token")


def save_login(api_url: str, access_token: str) -> None:
    cfg = _load()
    cfg["api_url"] = api_url
    cfg["access_token"] = access_token
    _save(cfg)


def save_api_key(raw_key: str) -> None:
    cfg = _load()
    cfg["api_key"] = raw_key
    _save(cfg)
