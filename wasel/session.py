"""Session persistence — every conversation is a resumable JSON file."""

from __future__ import annotations

import json
import pathlib
import time
from typing import Any, Dict, List

from .config import SESSIONS_DIR, ensure_home


class Session:
    def __init__(
        self,
        id: str = "",
        cwd: str = "",
        messages: List[Dict[str, Any]] | None = None,
        todos: List[Dict[str, Any]] | None = None,
        provider: str = "",
        model: str = "",
        created: float | None = None,
    ) -> None:
        self.id = id or time.strftime("%Y%m%d-%H%M%S")
        self.cwd = cwd
        self.messages = messages or []
        self.todos = todos or []
        self.provider = provider
        self.model = model
        self.created = created or time.time()

    # ------------------------------------------------------------------ i/o
    def path(self) -> pathlib.Path:
        ensure_home()
        return SESSIONS_DIR / f"{self.id}.json"

    def save(self) -> pathlib.Path:
        ensure_home()
        p = self.path()
        p.write_text(
            json.dumps(
                {
                    "id": self.id,
                    "cwd": self.cwd,
                    "created": self.created,
                    "updated": time.time(),
                    "provider": self.provider,
                    "model": self.model,
                    "todos": self.todos,
                    "messages": self.messages,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return p

    @classmethod
    def load(cls, sid: str) -> "Session":
        p = SESSIONS_DIR / f"{sid}.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(
            id=data.get("id", sid),
            cwd=data.get("cwd", ""),
            messages=data.get("messages", []),
            todos=data.get("todos", []),
            provider=data.get("provider", ""),
            model=data.get("model", ""),
            created=data.get("created"),
        )

    @staticmethod
    def list(limit: int = 20) -> List[Dict[str, Any]]:
        ensure_home()
        rows: List[Dict[str, Any]] = []
        for p in sorted(SESSIONS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rows.append(
                {
                    "id": data.get("id", p.stem),
                    "created": data.get("created", 0),
                    "messages": len(data.get("messages", [])),
                    "cwd": data.get("cwd", ""),
                }
            )
            if len(rows) >= limit:
                break
        return rows
