"""Tool layer — the part that turns a chat model into an actual agent.

Mirrors the tool taxonomy of mature coding agents (read/write/edit, search,
shell, task tracking, sub-agent delegation) but keeps every implementation
dependency-free so it installs on Termux with plain ``python3``.
"""

from __future__ import annotations

import fnmatch
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file from the workspace. Returns the content with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, relative to the workspace root."},
                    "offset": {"type": "integer", "description": "First line to read (1-based)."},
                    "limit": {"type": "integer", "description": "How many lines to read (default 400)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a file or overwrite it with new content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path, relative to the workspace root."},
                    "content": {"type": "string", "description": "Full content of the file."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace an exact string inside a file. old_string must appear exactly once unless replace_all is true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List the entries of a directory in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory (default: workspace root)."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files by glob pattern, e.g. '**/*.py'. Returns up to 200 paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents with a regular expression. Returns matching lines with file:line prefixes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Directory or file to search (default: root)."},
                    "include": {"type": "string", "description": "Glob filter, e.g. '*.py'."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a shell command inside the workspace and return stdout+stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 120, max 300)."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": "Track the plan as a task list. Keep exactly one item 'in_progress'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                                "activeForm": {"type": "string"},
                            },
                            "required": ["content", "status"],
                        },
                    }
                },
                "required": ["todos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Download a URL and return its text content (HTML is stripped).",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task",
            "description": "Delegate a self-contained subtask to a sub-agent that works in its own context and returns a short summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "3-5 word label for the subtask."},
                    "prompt": {"type": "string", "description": "The full instruction for the sub-agent."},
                },
                "required": ["description", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Call this when the task is complete to deliver the final answer.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]

DANGEROUS = [
    (r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/", "recursive delete of /"),
    (r"\brm\s+-rf?\s+~", "recursive delete of home"),
    (r"\bsudo\b", "sudo"),
    (r"\bmkfs\b", "filesystem format"),
    (r"\bdd\s+if=", "raw disk write"),
    (r":\(\)\s*\{", "fork bomb"),
    (r"\bshutdown\b|\breboot\b", "power state change"),
    (r"\bchmod\s+-R\s+777\s+/", "chmod 777 /"),
    (r"\bcurl\b[^|]*\|\s*(ba)?sh\b", "curl piped to shell"),
    (r"\bgit\s+push\b[^|]*--force", "force push"),
]

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".tox", "dist", "build"}


class ToolError(Exception):
    pass


class ToolExecutor:
    """Runs tool calls safely inside a workspace root."""

    def __init__(
        self,
        root: pathlib.Path,
        yolo: bool = False,
        ui=None,
        confirm: Optional[Callable[[str], bool]] = None,
        subagent: Optional[Callable[[str, str], str]] = None,
    ) -> None:
        self.root = root.resolve()
        self.yolo = yolo
        self.ui = ui
        self.confirm = confirm
        self.subagent = subagent
        self.todos: List[Dict[str, Any]] = []

    # ------------------------------------------------------------- helpers
    def resolve(self, path: str) -> pathlib.Path:
        p = pathlib.Path(path or ".")
        p = p if p.is_absolute() else (self.root / p)
        p = p.resolve()
        try:
            p.relative_to(self.root)
        except ValueError:
            raise ToolError(
                f"path {path!r} is outside the workspace root ({self.root}). "
                "Work inside the workspace only."
            ) from None
        return p

    def _rel(self, p: pathlib.Path) -> str:
        try:
            return str(p.relative_to(self.root))
        except ValueError:
            return str(p)

    # -------------------------------------------------------------- dispatch
    def execute(self, name: str, args: Dict[str, Any]) -> str:
        handler = getattr(self, f"_t_{name}", None)
        if handler is None:
            return f"Unknown tool '{name}'."
        try:
            return handler(args or {})
        except ToolError as exc:
            return f"ERROR: {exc}"
        except Exception as exc:  # pragma: no cover - defensive
            return f"ERROR: {type(exc).__name__}: {exc}"

    # ------------------------------------------------------------ file tools
    def _t_read_file(self, args: Dict[str, Any]) -> str:
        path = self.resolve(args.get("path", ""))
        if not path.exists():
            raise ToolError(f"file not found: {self._rel(path)}")
        if path.is_dir():
            raise ToolError(f"{self._rel(path)} is a directory — use list_dir.")
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        offset = max(1, int(args.get("offset") or 1))
        limit = min(2000, int(args.get("limit") or 400))
        chunk = lines[offset - 1 : offset - 1 + limit]
        body = "\n".join(f"{offset + i:>6}\t{line}" for i, line in enumerate(chunk))
        header = f"{self._rel(path)} ({len(lines)} lines, showing {len(chunk)} from {offset})"
        return f"{header}\n{body}"

    def _t_write_file(self, args: Dict[str, Any]) -> str:
        path = self.resolve(args.get("path", ""))
        content = args.get("content") or ""
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        path.write_text(content, encoding="utf-8")
        return f"{'Updated' if existed else 'Created'} {self._rel(path)} ({len(content)} bytes)"

    def _t_edit_file(self, args: Dict[str, Any]) -> str:
        path = self.resolve(args.get("path", ""))
        if not path.exists():
            raise ToolError(f"file not found: {self._rel(path)}")
        old = args.get("old_string") or ""
        new = args.get("new_string") or ""
        if old == "":
            raise ToolError("old_string must not be empty.")
        text = path.read_text(encoding="utf-8", errors="replace")
        count = text.count(old)
        if count == 0:
            raise ToolError("old_string not found in file — read the file again and copy the exact text.")
        if count > 1 and not args.get("replace_all"):
            raise ToolError(f"old_string appears {count} times — add more context or set replace_all=true.")
        path.write_text(text.replace(old, new, -1 if args.get("replace_all") else 1), encoding="utf-8")
        return f"Edited {self._rel(path)} ({count} replacement{'s' if count > 1 else ''})"

    def _t_list_dir(self, args: Dict[str, Any]) -> str:
        path = self.resolve(args.get("path") or ".")
        if not path.exists():
            raise ToolError(f"directory not found: {self._rel(path)}")
        if not path.is_dir():
            raise ToolError(f"{self._rel(path)} is not a directory.")
        rows = []
        for entry in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if entry.name in SKIP_DIRS:
                continue
            if entry.is_dir():
                rows.append(f"  {entry.name}/")
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                rows.append(f"  {entry.name}  ({size} B)")
        return f"{self._rel(path) or '.'}:\n" + ("\n".join(rows) if rows else "  (empty)")

    def _t_glob(self, args: Dict[str, Any]) -> str:
        pattern = args.get("pattern") or "*"
        base = self.resolve(args.get("path") or ".")
        if not base.is_dir():
            base = base.parent
        hits = []
        for p in base.rglob("*"):
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            rel = str(p.relative_to(base))
            if p.is_dir():
                continue
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(p.name, pattern) or p.match(pattern):
                hits.append(str(p.relative_to(self.root)))
            if len(hits) >= 200:
                break
        return "\n".join(hits) if hits else "(no matches)"

    def _t_grep(self, args: Dict[str, Any]) -> str:
        pattern = args.get("pattern") or ""
        base = self.resolve(args.get("path") or ".")
        include = args.get("include") or ""
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"invalid regex: {exc}") from None
        files = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file())
        out: List[str] = []
        for f in files:
            if any(part in SKIP_DIRS for part in f.parts):
                continue
            if include and not fnmatch.fnmatch(f.name, include):
                continue
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if rx.search(line):
                        out.append(f"{self._rel(f)}:{i}: {line.strip()[:300]}")
                        if len(out) >= 120:
                            return "\n".join(out) + "\n… (truncated)"
            except OSError:
                continue
        return "\n".join(out) if out else "(no matches)"

    # ----------------------------------------------------------- shell tool
    def _t_run_shell(self, args: Dict[str, Any]) -> str:
        command = (args.get("command") or "").strip()
        if not command:
            raise ToolError("command is empty.")
        timeout = min(300, max(5, int(args.get("timeout") or 120)))
        risk = self._risk(command)
        if risk and not self.yolo:
            if self.confirm is None or not self.confirm(f"Command looks risky ({risk}):\n  {command}\nRun it?"):
                raise ToolError("User declined the command. Choose a safer alternative.")
        start = time.time()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"ERROR: command timed out after {timeout}s"
        out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        out = out.strip()
        if len(out) > 8000:
            out = out[:8000] + f"\n… (truncated, {len(out) - 8000} more chars)"
        meta = f"exit={proc.returncode} time={time.time() - start:.1f}s"
        return f"{meta}\n{out}" if out else meta

    def _risk(self, command: str) -> str:
        low = command.lower()
        for pattern, label in DANGEROUS:
            if re.search(pattern, low):
                return label
        return ""

    # ------------------------------------------------------------ misc tools
    def _t_todo_write(self, args: Dict[str, Any]) -> str:
        todos = args.get("todos")
        if not isinstance(todos, list):
            raise ToolError("todos must be a list of {content, status} objects.")
        clean = []
        for item in todos:
            if not isinstance(item, dict):
                continue
            clean.append(
                {
                    "content": str(item.get("content", ""))[:200],
                    "status": item.get("status", "pending"),
                    "activeForm": str(item.get("activeForm", ""))[:200],
                }
            )
        self.todos = clean
        return "Task list updated:\n" + "\n".join(
            f"[{'x' if t['status'] == 'completed' else ('>' if t['status'] == 'in_progress' else ' ')}] {t['content']}"
            for t in clean
        )

    def _t_web_fetch(self, args: Dict[str, Any]) -> str:
        url = (args.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ToolError("url must start with http:// or https://")
        limit = int(args.get("max_chars") or 6000)
        req = urllib.request.Request(url, headers={"User-Agent": "wasel-cli/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read(limit * 4).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise ToolError(f"HTTP {exc.code}") from None
        except Exception as exc:
            raise ToolError(f"fetch failed: {exc}") from None
        if "<html" in raw[:2000].lower():
            raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
            raw = re.sub(r"(?s)<[^>]+>", " ", raw)
            raw = re.sub(r"&nbsp;", " ", raw)
            raw = re.sub(r"[ \t]{2,}", " ", raw)
            raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw[:limit]

    def _t_task(self, args: Dict[str, Any]) -> str:
        if self.subagent is None:
            raise ToolError("sub-agents are disabled in this session.")
        desc = args.get("description") or "subtask"
        prompt = args.get("prompt") or ""
        return self.subagent(desc, prompt)

    def _t_finish(self, args: Dict[str, Any]) -> str:
        return "FINISHED: " + str(args.get("summary") or "")
