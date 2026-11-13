"""Terminal UI: colors, panels, streaming renderer, tool cards.

Pure stdlib ANSI — works in Termux, tmux, SSH and CI logs alike.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
import time
from typing import Iterable, List, Optional

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
UNDER = "\033[4m"


ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def vlen(text: str) -> int:
    """Visible length of a string that may contain ANSI colour codes."""
    return len(ANSI_RE.sub("", text))


class Theme:
    accent = "\033[38;5;51m"   # cyan
    accent2 = "\033[38;5;213m"  # pink
    ok = "\033[38;5;114m"
    warn = "\033[38;5;214m"
    err = "\033[38;5;203m"
    muted = "\033[38;5;244m"
    text = "\033[38;5;252m"
    user = "\033[38;5;222m"


class UI:
    def __init__(self, no_color: bool = False, stream=None, err_stream=None) -> None:
        self.no_color = no_color or os.environ.get("NO_COLOR") is not None
        self.out = stream or sys.stdout
        self.err = err_stream or sys.stderr
        self.tty = bool(getattr(self.out, "isatty", lambda: False)())
        self._live = False

    # ---------------------------------------------------------------- basics
    def width(self) -> int:
        try:
            return max(50, min(shutil.get_terminal_size((100, 24)).columns, 120))
        except OSError:
            return 100

    def c(self, text: str, *styles: str) -> str:
        if self.no_color or not styles:
            return text
        return "".join(styles) + text + RESET

    def write(self, text: str = "") -> None:
        try:
            self.out.write(text)
            self.out.flush()
        except BrokenPipeError:  # e.g. `wasel providers | head`
            pass

    def println(self, text: str = "") -> None:
        self.write(text + "\n")

    def info(self, text: str) -> None:
        self.println(self.c(text, Theme.muted))

    def success(self, text: str) -> None:
        self.println(self.c("✔ ", Theme.ok) + text)

    def warn(self, text: str) -> None:
        self.println(self.c("! ", Theme.warn) + text)

    def error(self, text: str) -> None:
        self.err.write(self.c("✖ ", Theme.err) + text + "\n")
        self.err.flush()

    # ---------------------------------------------------------------- banner
    def logo(self, word: str = "WASEL", color: str = Theme.ok) -> None:
        """Slash-style ASCII wordmark, in the spirit of the Junie splash."""
        rows = self._logo_rows(word)
        for row in rows:
            self.println(self.c(row, color))

    def _logo_rows(self, word: str) -> List[str]:
        glyphs = {
            "W": ["X...X", "X...X", "X...X", "X.X.X", ".X.X."],
            "A": [".XXX.", "X...X", "XXXXX", "X...X", "X...X"],
            "S": [".XXXX", "X....", ".XXX.", "....X", "XXXX."],
            "E": ["XXXXX", "X....", "XXXX.", "X....", "XXXXX"],
            "L": ["X....", "X....", "X....", "X....", "XXXXX"],
            "V": ["X...X", "X...X", "X...X", "X...X", ".X.X."],
            "O": [".XXX.", "X...X", "X...X", "X...X", ".XXX."],
            "I": ["XXXXX", "..X..", "..X..", "..X..", "XXXXX"],
            "N": ["X...X", "XX..X", "X.X.X", "X..XX", "X...X"],
        }
        letters = [glyphs.get(ch.upper()) for ch in word]
        letters = [g for g in letters if g]
        if not letters:
            return []
        out = []
        for r in range(5):
            parts = []
            for g in letters:
                parts.append("".join("///" if c == "X" else "   " for c in g[r]))
            out.append("  ".join(parts))
        return out

    def status_bar(self, left: str, center: str = "", right: str = "") -> None:
        """Bottom bar: cwd on the left, model in the middle, mode on the right."""
        self.println(self.status_bar_text(left, center, right))

    def status_bar_text(self, left: str, center: str = "", right: str = "") -> str:
        w = self.width()
        gap = max(1, w - vlen(left) - vlen(center) - vlen(right))
        left_pad = gap // 2
        right_pad = gap - left_pad
        return left + " " * left_pad + center + " " * right_pad + right

    def banner(self, version: str, provider: str, model: str, root: str) -> None:
        w = self.width()
        title = " WASEL "
        sub = f"v{version} · {provider} · {model} "
        left = self.c(title, Theme.accent, BOLD)
        right = self.c(sub, Theme.muted)
        pad = w - len(title) - len(sub) - 2
        if pad < 1:
            right = ""
            pad = w - len(title) - 2
        self.println()
        self.println(self.c("╭" + "─" * (w - 2) + "╮", Theme.muted))
        self.println(
            self.c("│", Theme.muted)
            + left
            + " " * pad
            + right
            + self.c(" │", Theme.muted)
        )
        self.println(self.c("╰" + "─" * (w - 2) + "╯", Theme.muted))
        self.println(self.c(f"  workspace: {root}", Theme.muted))
        self.println(self.c("  /help for commands · Ctrl-D to exit", Theme.muted))
        self.println()

    # ------------------------------------------------------------- streaming
    def start_stream(self) -> None:
        self._live = True
        self._in_code = False
        self._at_line_start = True
        self._kind = "text"

    def stream_text(self, chunk: str) -> None:
        if not chunk:
            return
        if not hasattr(self, "_at_line_start"):
            self.start_stream()
        parts = chunk.split("\n")
        for i, part in enumerate(parts):
            if i > 0:
                self._end_line()
            if part:
                self._emit(part)

    def _end_line(self) -> None:
        self.write("\n")
        self._at_line_start = True
        self._kind = "text"

    def _emit(self, part: str) -> None:
        if self._at_line_start:
            self._at_line_start = False
            stripped = part.lstrip()
            if stripped.startswith("```"):
                self._in_code = not self._in_code
                self.write(self.c("  ┌─" if self._in_code else "  └─", Theme.muted))
                rest = stripped[3:]
                if rest:
                    self._emit_inline(rest)
                return
            if self._in_code:
                self._kind = "code"
                self.write(self.c("  │ ", Theme.muted))
            else:
                bullet = re.match(r"^(\s*)[-*•]\s+", part)
                if bullet:
                    self._kind = "bullet"
                    self.write(" " * (len(bullet.group(1)) + 2) + self.c("• ", Theme.accent))
                    part = part[bullet.end():]
                elif part.lstrip().startswith("#"):
                    self._kind = "heading"
                    self.write(self.c("▌ ", Theme.accent2, BOLD))
                else:
                    self._kind = "text"
                    self.write("  " + "\033[38;5;252m")
            self._emit_inline(part)
            return
        self._emit_inline(part)

    def _emit_inline(self, text: str) -> None:
        if self._kind == "code":
            self.write(self.c(text, Theme.muted))
        elif self._kind == "heading":
            self.write(self.c(text, Theme.accent2, BOLD))
        else:
            self.write(self._inline(text))

    def end_stream(self) -> None:
        self._live = False
        self.write("\n")

    # ------------------------------------------------------------ tool cards
    def tool_card(self, name: str, detail: str = "", ok: bool = True) -> None:
        icon = self.c("▸", Theme.accent2)
        mark = self.c("✔", Theme.ok) if ok else self.c("✖", Theme.err)
        line = f"{icon} {self.c(name, Theme.accent, BOLD)}"
        if detail:
            line += self.c("  " + detail, Theme.muted)
        self.println(line)
        self.println("  " + mark + self.c(" done", Theme.muted))

    def tool_result(self, text: str, limit: int = 1200) -> None:
        text = text.strip()
        if not text:
            return
        if len(text) > limit:
            text = text[:limit] + f"\n… (+{len(text) - limit} chars truncated)"
        for line in text.splitlines():
            self.println(self.c("    " + line, Theme.muted))

    def panel(self, title: str, lines: Iterable[str], color: str = Theme.muted) -> None:
        w = self.width()
        inner = w - 4
        self.println(self.c("┌─ " + title + " " + "─" * max(0, inner - len(title) - 1), color))
        for line in lines:
            for raw in str(line).split("\n"):
                for chunk in textwrap.wrap(raw, inner) or [""]:
                    self.println(self.c("│ ", color) + self.c(chunk, Theme.text))
        self.println(self.c("└" + "─" * (w - 2), color))

    # ------------------------------------------------------------- markdown
    def markdown(self, text: str) -> None:
        """Very small markdown renderer: headings, bullets, code, bold."""
        in_code = False
        for raw in text.splitlines():
            if raw.strip().startswith("```"):
                in_code = not in_code
                self.println(self.c("  ┌─", Theme.muted))
                continue
            if in_code:
                self.println(self.c("  │ " + raw, Theme.muted))
                continue
            line = raw
            if line.startswith("### "):
                self.println(self.c("▌ " + line[4:].strip(), Theme.accent, BOLD))
                continue
            if line.startswith("## "):
                self.println(self.c("▌ " + line[3:].strip(), Theme.accent2, BOLD))
                continue
            if line.startswith("# "):
                self.println(self.c("▌ " + line[2:].strip(), Theme.accent2, BOLD, UNDER))
                continue
            bullet = re.match(r"^(\s*)[-*•]\s+(.*)$", line)
            if bullet:
                indent = " " * (len(bullet.group(1)) + 2)
                self.println(indent + self.c("• ", Theme.accent) + self._inline(bullet.group(2)))
                continue
            if not line.strip():
                self.println("")
                continue
            self.println("  " + self._inline(line.strip()))

    def _inline(self, text: str) -> str:
        text = re.sub(r"`([^`]+)`", lambda m: self.c(m.group(1), Theme.accent2), text)
        text = re.sub(r"\*\*([^*]+)\*\*", lambda m: self.c(m.group(1), BOLD), text)
        text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", lambda m: self.c(m.group(1), ITALIC), text)
        return text

    # ------------------------------------------------------------- spinner
    def spinner(self, label: str) -> "Spinner":
        return Spinner(self, label)


class Spinner:
    """Tiny animated spinner that only runs on a real TTY."""

    def __init__(self, ui: UI, label: str) -> None:
        import threading

        self.ui = ui
        self.label = label
        self.frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self._i = 0
        self._thread = None
        self._done = threading.Event()

    def start(self) -> None:
        import threading

        if not self.ui.tty or self._thread is not None:
            return
        self._done.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._done.is_set():
            frame = self.frames[self._i % len(self.frames)]
            self._i += 1
            self.ui.write("\r" + self.ui.c(f"{frame} {self.label}", Theme.muted) + "\033[K")
            time.sleep(0.09)

    def _stop(self) -> None:
        if self._thread is not None:
            self._done.set()
            self._thread.join(timeout=0.4)
            self._thread = None
            self.ui.write("\r\033[K")

    def update(self, label: str) -> None:
        self.label = label

    def stop(self) -> None:
        self._stop()
