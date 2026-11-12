"""The agent core: gather context → act → verify, with failover and compaction."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import providers as P
from .tools import TOOL_SCHEMAS, ToolExecutor

AGENT_NAME = "Wasel"

SYSTEM_PROMPT = """You are {name}, a terminal-native AI software engineer running inside the user's workspace.

Your working directory is: {root}
Platform: {platform}

## How you work
1. GATHER — read files or search before guessing. Use glob/grep/read_file to build real context.
2. ACT — make the change with write_file/edit_file, then verify with run_shell (tests, linters, `python -m compileall`).
3. VERIFY — re-read what you changed and confirm the goal is met. Fix your own mistakes; never leave a broken half-edit.

## Rules
- Never invent file contents. Read the file first.
- Prefer small, surgical edits over rewriting whole files.
- Run commands in the workspace. Keep each command focused.
- Use todo_write to track multi-step work; update it as you progress.
- Use the task tool for independent subtasks that would flood the context (it runs in its own context and returns a summary).
- Call finish when the task is complete, with a short summary of what changed.
- If a tool errors, read the error and adapt — do not repeat the same failing call blindly.
- Reply in the user's language. Be concise: short paragraphs, no filler.
"""

SUBAGENT_PROMPT = """You are a focused sub-agent of {name}.
You have your own context window and you must return ONE summary back to the parent agent.
Workspace: {root}
Do the subtask, verify it, then reply with a concise summary of findings or changes (max ~200 words).
"""


def clean_tool_name(name: str) -> str:
    """Strip reasoning-channel artefacts some open-weight models append to names.

    e.g. ``run_shell<|channel|>commentary`` -> ``run_shell``
    """
    name = re.sub(r"<\|[^>]*\|>", "", name or "")
    name = re.sub(r"[^A-Za-z0-9_\-.]", "", name)
    return name.strip("._-")


def _extract_summary(raw: Any) -> str:
    """Models sometimes double-encode the finish payload — dig the text out."""
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if text.startswith("FINISHED:"):
        text = text[len("FINISHED:") :].strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    if isinstance(parsed, dict):
        for key in ("summary", "answer", "result", "text"):
            if isinstance(parsed.get(key), str) and parsed[key].strip():
                return parsed[key].strip()
    return text


class _TempSession:
    """Minimal session stand-in for sub-agents (their transcript is discarded)."""

    def __init__(self) -> None:
        self.id = "subagent"
        self.created = time.time()
        self.cwd = ""
        self.messages: List[Dict[str, Any]] = []
        self.todos: List[Dict[str, Any]] = []
        self.provider = ""
        self.model = ""


def estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    try:
        blob = json.dumps(messages, ensure_ascii=False)
    except (TypeError, ValueError):
        blob = str(messages)
    return max(1, len(blob) // 4)


class Router:
    """Chooses a provider/model and fails over to the next free one on error."""

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self.cooldown = P.Cooldown()
        self.last: Tuple[str, str] = ("pollinations", "openai-fast")
        self.usage = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def candidates(self) -> List[Tuple[str, str]]:
        cands: List[Tuple[str, str]] = []
        wanted = self.cfg.get("provider") or "auto"
        model_override = self.cfg.get("model")
        order = list(self.cfg.get("order") or P.PROVIDERS.keys())
        if wanted != "auto" and wanted in P.PROVIDERS:
            order = [wanted] + [p for p in order if p != wanted]
        for pid in order:
            if pid not in P.PROVIDERS:
                continue
            if not P.provider_ready(pid, self.cfg):
                continue
            if pid in ("pollinations", "ollama", "custom"):
                model = model_override if (wanted == pid and model_override) else P.PROVIDERS[pid].default_model
            else:
                model = model_override if (wanted == pid and model_override) else P.PROVIDERS[pid].default_model
            if not model:
                continue
            cands.append((pid, model))
        return cands

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        on_delta: Optional[Callable[[str], None]] = None,
        on_reason: Optional[Callable[[str], None]] = None,
        stream: bool = True,
    ) -> Tuple[Dict[str, Any], str, str]:
        cands = self.candidates()
        if not cands:
            raise RuntimeError(
                "No provider available. Add a free key (wasel keys set gemini <KEY>) or keep the "
                "key-free Pollinations provider available."
            )
        errors: List[str] = []
        ready = [c for c in cands if self.cooldown.ok(c[0])]
        for pid, model in ready + [c for c in cands if not self.cooldown.ok(c[0])]:
            attempt = 0
            while attempt < 2:
                try:
                    msg = P.chat(
                        pid,
                        model,
                        messages,
                        tools=tools,
                        stream=stream,
                        on_delta=on_delta,
                        on_reason=on_reason,
                    )
                    self.last = (pid, model)
                    self.usage["requests"] += 1
                    return msg, pid, model
                except P.ProviderError as exc:
                    attempt += 1
                    errors.append(f"{pid}: {exc}")
                    if exc.kind == "server" and attempt < 2:
                        time.sleep(P.backoff_delay(attempt))
                        continue
                    if exc.kind == "auth":
                        self.cooldown.fail(pid, 900)
                    elif exc.kind == "ratelimit":
                        self.cooldown.fail(pid, 90)
                    elif exc.kind == "badrequest":
                        self.cooldown.fail(pid, 60)
                    else:
                        self.cooldown.fail(pid, 25)
                    break
        raise RuntimeError("All providers failed:\n  " + "\n  ".join(errors))


class Agent:
    def __init__(
        self,
        cfg: Dict[str, Any],
        ui,
        session,
        root,
        confirm: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.cfg = cfg
        self.ui = ui
        self.session = session
        self.root = root
        self.router = Router(cfg)
        self.depth = 0
        self.quiet = False
        self.last_streamed = False
        self.tools = ToolExecutor(
            root=root,
            yolo=bool(cfg.get("yolo")),
            ui=ui,
            confirm=confirm,
            subagent=self._run_subagent,
        )
        self.messages: List[Dict[str, Any]] = list(session.messages)

    # ------------------------------------------------------------------ misc
    def platform(self) -> str:
        import platform as _p
        return f"{_p.system()} {_p.release()} ({_p.machine()})"

    def system_prompt(self) -> str:
        extra = ""
        agents_md = self.root / "AGENTS.md"
        if agents_md.exists():
            try:
                extra = "\n\n## Project instructions (AGENTS.md)\n" + agents_md.read_text(encoding="utf-8")[:4000]
            except OSError:
                extra = ""
        return SYSTEM_PROMPT.format(name=AGENT_NAME, root=self.root, platform=self.platform()) + extra

    def _context_limit(self, model: str) -> int:
        return int(self.cfg.get("context_limit") or 32768)

    def _maybe_compact(self) -> None:
        limit = self._context_limit(self.router.last[1])
        ratio = float(self.cfg.get("compact_ratio") or 0.6)
        if estimate_tokens(self.messages) < limit * ratio:
            return
        keep = 4
        if len(self.messages) <= keep + 2:
            return
        head = self.messages[0]
        middle = self.messages[1:-keep]
        tail = self.messages[-keep:]
        summary = self._summarize(middle)
        self.messages = [head] + [
            {"role": "user", "content": "[context compacted]\n" + summary},
            {"role": "assistant", "content": "Understood — context compacted, continuing."},
        ] + tail
        self.session.messages = list(self.messages)
        self.ui.info("⟲ context compacted to stay inside the window")

    def _summarize(self, chunk: List[Dict[str, Any]]) -> str:
        blob = json.dumps(chunk, ensure_ascii=False)[:12000]
        prompt = [
            {
                "role": "system",
                "content": "Compress the following conversation into a dense summary. Keep: user goals, "
                "decisions, file paths touched, commands run, findings, and what still has to happen.",
            },
            {"role": "user", "content": blob},
        ]
        try:
            msg, _, _ = self.router.complete(prompt, None, stream=False)
            return (msg.get("content") or "").strip() or "(summary unavailable)"
        except Exception:
            return "(summary unavailable — older messages kept in raw form)"

    # ------------------------------------------------------------------ loop
    def run(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})
        return self._loop()

    def _sink(self, chunk: str) -> None:
        """Default stream handler: print to the terminal."""
        if self.quiet:
            return
        self.ui.stream_text(chunk)

    def _loop(self) -> str:
        max_steps = int(self.cfg.get("max_steps") or 40)
        final = ""
        self.last_streamed = False
        for _ in range(max_steps):
            self._maybe_compact()
            payload = [{"role": "system", "content": self.system_prompt()}] + self.messages
            try:
                msg, pid, model = self.router.complete(
                    payload,
                    TOOL_SCHEMAS,
                    on_delta=self._sink,
                    on_reason=None,
                    stream=bool(self.cfg.get("stream", True)),
                )
            except RuntimeError as exc:
                self.ui.error(str(exc))
                return ""
            self.ui.end_stream()
            content = (msg.get("content") or "").strip()
            calls = msg.get("tool_calls") or []
            for call in calls:
                fn = call.get("function") or {}
                fn["name"] = clean_tool_name(fn.get("name", ""))
                raw = fn.get("arguments") or "{}"
                try:
                    parsed = json.loads(raw) if raw.strip() else {}
                except json.JSONDecodeError:
                    parsed = {"_raw": raw}
                if not isinstance(parsed, dict):
                    parsed = {"value": parsed}
                fn["arguments"] = json.dumps(parsed, ensure_ascii=False)
            if not calls:
                final = _extract_summary(content) or content
                self.last_streamed = True
                self.messages.append({"role": "assistant", "content": content})
                break
            self.messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": calls,
                }
            )
            for call in calls:
                name = (call.get("function") or {}).get("name", "")
                raw_args = (call.get("function") or {}).get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                except json.JSONDecodeError:
                    args = {"_raw": raw_args}
                if not isinstance(args, dict):
                    args = {"value": args}
                self.ui.tool_card(name, self._brief(name, args))
                result = self.tools.execute(name, args)
                self.ui.tool_result(result, limit=700)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or "call",
                        "name": name,
                        "content": result[:20000],
                    }
                )
                if name == "finish":
                    final = _extract_summary(args.get("summary")) or result
                    self.last_streamed = False
                    self.messages.append({"role": "assistant", "content": final})
                    break
            if name == "finish":
                break
        self.session.messages = list(self.messages)
        self.session.todos = list(self.tools.todos)
        return final

    def _brief(self, name: str, args: Dict[str, Any]) -> str:
        if name in ("read_file", "write_file", "edit_file", "list_dir"):
            return str(args.get("path", ""))
        if name == "grep":
            return f"{args.get('pattern', '')} in {args.get('path', '.')}"
        if name == "glob":
            return str(args.get("pattern", ""))
        if name == "run_shell":
            return str(args.get("command", ""))[:120]
        if name == "web_fetch":
            return str(args.get("url", ""))
        if name == "task":
            return str(args.get("description", ""))
        if name == "todo_write":
            return f"{len(args.get('todos') or [])} items"
        return ""

    # -------------------------------------------------------------- subagent
    def _run_subagent(self, description: str, prompt: str) -> str:
        if self.depth >= 1:
            return "Sub-agents cannot nest further. Do the work yourself."
        self.depth += 1
        self.ui.info(f"↳ spawning sub-agent: {description}")
        sub_cfg = dict(self.cfg)
        sub_cfg["max_steps"] = min(12, int(self.cfg.get("max_steps") or 12))
        sub = Agent(sub_cfg, self.ui, _TempSession(), self.root)
        sub.depth = self.depth
        sub.tools.yolo = self.tools.yolo
        sub.tools.confirm = self.tools.confirm
        sub.tools.subagent = None
        try:
            result = sub.run(prompt)
        finally:
            self.depth -= 1
        return result or "(sub-agent produced no output)"
