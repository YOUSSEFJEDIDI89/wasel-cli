"""Command line interface: interactive REPL + headless mode."""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time
from typing import Any, Dict, List

from . import providers as P
from .agent import Agent, estimate_tokens
from .config import (
    HISTORY_PATH,
    delete_key,
    ensure_home,
    list_key_sources,
    load_config,
    load_keys,
    save_config,
    save_key,
)
from .session import Session
from .ui import UI, Theme
from .version import VERSION

HELP = """Commands
  /help                 show this help
  /status               provider, model, tokens, session
  /providers            list free providers and whether a key is present
  /provider <id>        pin a provider (or 'auto' to fail over freely)
  /model <name>         set the model for the current provider
  /models [provider]    query the provider for its live model list
  /keys                 list stored keys          /keys set <id> <KEY>  /keys rm <id>
  /todos                show the current task list
  /compact              force a context compaction
  /clear                start a fresh session (history cleared)
  /sessions             list saved sessions      /resume <id>  to continue one
  /save                 force-save the session now
  /init                 write an AGENTS.md starter file in the workspace
  /exit                 leave wasel
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wasel",
        description="Wasel — a free-LLM AI agent for your terminal (Termux friendly).",
    )
    p.add_argument("-p", "--prompt", help="run one prompt headlessly and exit")
    p.add_argument("--provider", help="force a provider id (pollinations, gemini, groq, ...)")
    p.add_argument("--model", help="model id to use with the selected provider")
    p.add_argument("--root", default=".", help="workspace root (default: current directory)")
    p.add_argument("--session", help="resume an existing session id")
    p.add_argument("--yolo", action="store_true", help="auto-approve risky shell commands")
    p.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    p.add_argument("--no-stream", action="store_true", help="disable token streaming")
    p.add_argument("--version", action="store_true", help="print version and exit")
    p.add_argument("--init", action="store_true", help="write AGENTS.md starter file and exit")
    p.add_argument("--list-providers", action="store_true", help="list free providers and exit")
    return p


class CLI:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.ui = UI(no_color=args.no_color)
        self.cfg = load_config()
        if args.provider:
            self.cfg["provider"] = args.provider
        if args.model:
            self.cfg["model"] = args.model
        if args.yolo:
            self.cfg["yolo"] = True
        if args.no_stream:
            self.cfg["stream"] = False
        self.root = pathlib.Path(args.root).expanduser().resolve()
        self.session = self._load_session()
        self.agent = Agent(self.cfg, self.ui, self.session, self.root, confirm=self.confirm)

    # ------------------------------------------------------------- sessions
    def _load_session(self) -> Session:
        if self.args.session:
            try:
                s = Session.load(self.args.session)
                self.cfg.setdefault("provider", "auto")
                return s
            except Exception:
                self.ui.warn(f"session '{self.args.session}' not found — starting a new one")
        return Session(id=time.strftime("%Y%m%d-%H%M%S"), cwd=str(self.root))

    # ---------------------------------------------------------------- flow
    def run(self) -> int:
        if self.args.version:
            print(f"wasel {VERSION}")
            return 0
        if self.args.init:
            return self._init_project()
        if self.args.list_providers:
            self._print_providers()
            return 0

        ensure_home()
        if self.args.prompt:
            return self._headless(self.args.prompt)
        return self._interactive()

    # ------------------------------------------------------------ headless
    def _headless(self, prompt: str) -> int:
        cands = self.agent.router.candidates()
        pid, model = cands[0] if cands else ("-", "-")
        self.ui.info(f"provider={pid} model={model}")
        self.agent.quiet = True
        answer = self.agent.run(prompt)
        self.agent.quiet = False
        self.session.save()
        print()
        print(answer.strip())
        return 0 if answer.strip() else 1

    # ----------------------------------------------------------- interactive
    def _interactive(self) -> int:
        self.ui.banner(VERSION, self.cfg.get("provider", "auto"), self.cfg.get("model") or "auto", str(self.root))
        self._print_hint()
        history = self._load_history()
        while True:
            try:
                line = input(self.ui.c("❯ ", Theme.accent2, Theme.accent)).strip()
            except KeyboardInterrupt:
                self.ui.println()
                self.ui.info("interrupted — Ctrl-D or /exit to quit")
                continue
            except EOFError:
                break
            if not line:
                continue
            history.append(line)
            if line.startswith("/"):
                if self._command(line):
                    break
                continue
            try:
                answer = self.agent.run(line)
            except Exception as exc:  # pragma: no cover
                self.ui.error(f"{type(exc).__name__}: {exc}")
                continue
            if answer.strip() and not self.agent.last_streamed:
                self.ui.markdown(answer)
            self.session.save()
            self._save_history(history)
        self.session.save()
        self.ui.info("bye 👋")
        return 0

    def _load_history(self) -> List[str]:
        if HISTORY_PATH.exists():
            try:
                return HISTORY_PATH.read_text(encoding="utf-8").splitlines()
            except OSError:
                return []
        return []

    def _save_history(self, history: List[str]) -> None:
        try:
            HISTORY_PATH.write_text("\n".join(history[-500:]), encoding="utf-8")
        except OSError:
            pass

    def _print_hint(self) -> None:
        ready = [pid for pid, ok in list_key_sources().items() if ok != "-" or pid in ("pollinations", "ollama")]
        self.ui.info("ready providers: " + ", ".join(ready))
        if len(ready) <= 1:
            self.ui.info("tip: /providers set gemini <KEY> unlocks Gemini's 1M-context free tier")

    # ------------------------------------------------------------- commands
    def _command(self, line: str) -> bool:
        parts = line.split()
        cmd, args = parts[0], parts[1:]
        if cmd in ("/exit", "/quit", "/q"):
            return True
        if cmd == "/help":
            self.ui.println(HELP)
        elif cmd == "/status":
            self._status()
        elif cmd == "/providers":
            self._print_providers()
        elif cmd == "/provider":
            if not args:
                self.ui.info("current: " + str(self.cfg.get("provider")))
            elif args[0] == "auto":
                self.cfg["provider"] = "auto"
                save_config(self.cfg)
                self.ui.success("provider = auto (failover across free providers)")
            elif args[0] in P.PROVIDERS:
                self.cfg["provider"] = args[0]
                save_config(self.cfg)
                self.ui.success(f"provider = {args[0]}")
            else:
                self.ui.error(f"unknown provider '{args[0]}'")
        elif cmd == "/model":
            if not args:
                self.ui.info("current: " + str(self.cfg.get("model") or "provider default"))
            else:
                self.cfg["model"] = args[0]
                save_config(self.cfg)
                self.ui.success(f"model = {args[0]}")
        elif cmd == "/models":
            pid = args[0] if args else self.cfg.get("provider")
            if pid == "auto":
                pid = "pollinations"
            ok, models = P.list_models(pid)
            if ok:
                self.ui.panel(f"models @ {pid}", models[:60])
            else:
                prov = P.PROVIDERS.get(pid)
                self.ui.panel(f"models @ {pid} (offline list)", prov.models if prov else [])
        elif cmd == "/keys":
            self._keys(args)
        elif cmd == "/todos":
            todos = self.agent.tools.todos
            if not todos:
                self.ui.info("no task list yet")
            else:
                self.ui.panel(
                    "todos",
                    [
                        f"[{'x' if t['status'] == 'completed' else ('>' if t['status'] == 'in_progress' else ' ')}] {t['content']}"
                        for t in todos
                    ],
                )
        elif cmd == "/compact":
            self.agent.messages = self.agent.messages[:1] + [
                {"role": "user", "content": "[context compacted]\n" + self.agent._summarize(self.agent.messages[1:])}
            ]
            self.ui.success("context compacted")
        elif cmd == "/clear":
            self.agent.messages = []
            self.agent.tools.todos = []
            self.session.messages = []
            self.ui.success("session cleared")
        elif cmd == "/sessions":
            rows = Session.list()
            self.ui.panel(
                "sessions",
                [f"{r['id']}  {r['messages']} msgs  {r['cwd']}" for r in rows] or ["(none)"],
            )
        elif cmd == "/resume":
            if not args:
                self.ui.error("usage: /resume <session-id>")
            else:
                try:
                    self.session = Session.load(args[0])
                    self.agent.messages = list(self.session.messages)
                    self.ui.success(f"resumed session {args[0]}")
                except Exception:
                    self.ui.error(f"no such session: {args[0]}")
        elif cmd == "/save":
            self.session.save()
            self.ui.success("session saved")
        elif cmd == "/init":
            self._init_project()
        else:
            self.ui.error(f"unknown command '{cmd}' — try /help")
        return False

    def _keys(self, args: List[str]) -> None:
        if not args:
            sources = list_key_sources()
            self.ui.panel(
                "api keys",
                [
                    f"{pid:<14} {prov.name:<32} {'key: ' + src if src != '-' else 'no key'}"
                    for pid, prov in P.PROVIDERS.items()
                    for src in [sources.get(pid, "-")]
                ],
            )
            return
        if args[0] == "set" and len(args) >= 3:
            pid = args[1]
            if pid not in P.PROVIDERS:
                self.ui.error(f"unknown provider '{pid}'")
                return
            save_key(pid, args[2])
            self.ui.success(f"stored key for {pid}")
            return
        if args[0] == "rm" and len(args) >= 2:
            ok = delete_key(args[1])
            self.ui.success(f"removed key for {args[1]}" if ok else f"no key stored for {args[1]}")
            return
        self.ui.error("usage: /keys | /keys set <provider> <KEY> | /keys rm <provider>")

    def _status(self) -> None:
        pid, model = self.agent.router.last
        self.ui.panel(
            "status",
            [
                f"version      : {VERSION}",
                f"provider    : {self.cfg.get('provider')} (last used: {pid} / {model})",
                f"model       : {self.cfg.get('model') or 'provider default'}",
                f"workspace   : {self.root}",
                f"session     : {self.session.id}",
                f"messages    : {len(self.agent.messages)} (~{estimate_tokens(self.agent.messages)} tokens)",
                f"yolo mode   : {bool(self.cfg.get('yolo'))}",
                f"requests    : {self.agent.router.usage['requests']}",
            ],
        )

    def _print_providers(self) -> None:
        sources = list_key_sources()
        rows = []
        for pid, prov in P.PROVIDERS.items():
            state = sources.get(pid, "-")
            if not prov.needs_key:
                key_state = "no key needed"
            elif state != "-":
                key_state = f"key ✓ ({state})"
            else:
                key_state = "no key"
            rows.append(
                f"{pid:<13} {prov.default_model:<42} {key_state:<16} {prov.notes}\n"
                f"{'':<13} signup: {prov.signup}"
            )
        self.ui.panel("free providers (every one of these has a free tier)", rows)
        self.ui.info("keys come from env vars (GEMINI_API_KEY, GROQ_API_KEY, …) or /keys set <provider> <KEY>")

    def _init_project(self) -> int:
        target = self.root / "AGENTS.md"
        if target.exists():
            self.ui.warn(f"{target} already exists")
            return 0
        target.write_text(
            "# Project instructions\n\n"
            "Describe the project here. Wasel reads this file on every run and injects it\n"
            "into the system prompt, so keep it short and concrete.\n\n"
            "## Conventions\n- \n\n"
            "## Commands\n- build: \n- test: \n",
            encoding="utf-8",
        )
        self.ui.success(f"created {target}")
        return 0

    # ------------------------------------------------------------- confirm
    def confirm(self, question: str) -> bool:
        try:
            answer = input(self.ui.c("? ", Theme.warn) + question + " [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("y", "yes")


def main() -> int:
    args = build_parser().parse_args()
    return CLI(args).run()
