"""Configuration and credential storage for Wasel.

Everything lives in ``$WASEL_HOME`` (default ``~/.wasel``) so the tool can run
on Termux, in CI, or on a throwaway container without touching the system.
"""

from __future__ import annotations

import json
import os
import pathlib
import stat
from typing import Any, Dict

HOME = pathlib.Path(os.environ.get("WASEL_HOME") or (pathlib.Path.home() / ".wasel"))
CONFIG_PATH = HOME / "config.json"
KEYS_PATH = HOME / "keys.json"
HISTORY_PATH = HOME / "history"
SESSIONS_DIR = HOME / "sessions"

DEFAULT_CONFIG: Dict[str, Any] = {
    # "auto" = try every available provider in `order` until one answers.
    "provider": "auto",
    "model": None,
    "yolo": False,
    "max_steps": 40,
    "context_limit": 32768,
    "compact_ratio": 0.6,
    "order": [
        "pollinations",
        "gemini",
        "groq",
        "openrouter",
        "cerebras",
        "mistral",
        "nvidia",
        "ollama",
        "custom",
    ],
    "stream": True,
    "theme": "default",
}


def ensure_home() -> None:
    for path in (HOME, SESSIONS_DIR):
        path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(HOME, stat.S_IRWXU)
    except OSError:
        pass


def load_config() -> Dict[str, Any]:
    ensure_home()
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                cfg.update(stored)
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    ensure_home()
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def load_keys() -> Dict[str, str]:
    if not KEYS_PATH.exists():
        return {}
    try:
        data = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if isinstance(v, str) and v}
    except (OSError, json.JSONDecodeError):
        return {}


def save_key(provider_id: str, key: str) -> None:
    ensure_home()
    keys = load_keys()
    keys[provider_id] = key.strip()
    KEYS_PATH.write_text(json.dumps(keys, indent=2), encoding="utf-8")
    try:
        os.chmod(KEYS_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def delete_key(provider_id: str) -> bool:
    keys = load_keys()
    if provider_id not in keys:
        return False
    keys.pop(provider_id)
    KEYS_PATH.write_text(json.dumps(keys, indent=2), encoding="utf-8")
    return True


def list_key_sources() -> Dict[str, str]:
    """provider_id -> where the key comes from ('env:VAR' / 'file' / '-')."""
    from .providers import PROVIDERS  # local import to avoid cycles

    out: Dict[str, str] = {}
    for pid, prov in PROVIDERS.items():
        if not prov.env_key:
            out[pid] = "none"
            continue
        if os.environ.get(prov.env_key):
            out[pid] = f"env:{prov.env_key}"
        elif load_keys().get(pid):
            out[pid] = "file"
        else:
            out[pid] = "-"
    return out
