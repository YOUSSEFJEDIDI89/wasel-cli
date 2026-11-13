"""Junie-style input box: a highlighted band with a green block cursor.

Pure ``termios`` — no curses, no readline dependency, works on Termux.
"""

from __future__ import annotations

import os
import re
import select
import sys
import termios
import tty
from typing import Callable, List, Optional, Tuple

from .ui import Theme, UI, vlen

BLOCK = "█"


class Prompt:
    """A single-line editor rendered as a filled band, like Junie's input box."""

    def __init__(
        self,
        ui: UI,
        history: Optional[List[str]] = None,
        completions: Optional[Callable[[str], List[str]]] = None,
        on_brave: Optional[Callable[[bool], None]] = None,
        status: Optional[Callable[[], str]] = None,
    ) -> None:
        self.ui = ui
        self.history = history if history is not None else []
        self.completions = completions
        self.on_brave = on_brave
        self.status = status
        self.brave = False
        self._hist_index = len(self.history)

    # ------------------------------------------------------------------ util
    def _tty(self) -> bool:
        try:
            return sys.stdin.isatty()
        except (AttributeError, ValueError):
            return False

    # ----------------------------------------------------------------- read
    def read(self) -> Optional[str]:
        """Return the submitted line, or None when the user exits (Ctrl-D)."""
        if not self._tty():
            try:
                line = input(self.ui.c("❯ ", Theme.accent2))
            except EOFError:
                return None
            self.history.append(line)
            return line

        buf = ""
        pos = 0
        self._hist_index = len(self.history)
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            self._draw(buf, pos)
            while True:
                try:
                    ch = os.read(fd, 1)
                except OSError:
                    return None
                if not ch:
                    return None
                b = ch.decode("utf-8", "ignore")

                if b in ("\r", "\n"):
                    self._finalize(buf)
                    self.history.append(buf)
                    return buf

                if b == "\x03":  # Ctrl-C -> clear the line
                    buf, pos = "", 0
                    self._draw(buf, pos)
                    continue

                if b == "\x04":  # Ctrl-D -> exit
                    self._erase()
                    return None

                if b == "\x02":  # Ctrl-B -> toggle brave mode
                    self.brave = not self.brave
                    if self.on_brave:
                        self.on_brave(self.brave)
                    self._draw(buf, pos)
                    continue

                if b == "\x15":  # Ctrl-U
                    buf, pos = "", 0
                    self._draw(buf, pos)
                    continue

                if b == "\x01":  # Ctrl-A
                    pos = 0
                    self._draw(buf, pos)
                    continue

                if b == "\x05":  # Ctrl-E
                    pos = len(buf)
                    self._draw(buf, pos)
                    continue

                if b in ("\x7f", "\x08"):  # backspace
                    if pos > 0:
                        buf = buf[: pos - 1] + buf[pos:]
                        pos -= 1
                    self._draw(buf, pos)
                    continue

                if b == "\x1b":  # escape sequence
                    seq = self._read_seq(fd)
                    if seq == "left":
                        pos = max(0, pos - 1)
                    elif seq == "right":
                        pos = min(len(buf), pos + 1)
                    elif seq == "up":
                        if self._hist_index > 0:
                            self._hist_index -= 1
                            buf = self.history[self._hist_index]
                            pos = len(buf)
                    elif seq == "down":
                        if self._hist_index < len(self.history) - 1:
                            self._hist_index += 1
                            buf = self.history[self._hist_index]
                        else:
                            self._hist_index = len(self.history)
                            buf = ""
                        pos = len(buf)
                    elif seq == "delete":
                        if pos < len(buf):
                            buf = buf[:pos] + buf[pos + 1:]
                    elif seq == "tab":
                        done = self._complete(buf, pos)
                        if done is not None:
                            buf, pos = done
                    self._draw(buf, pos)
                    continue

                if b == "\t":
                    done = self._complete(buf, pos)
                    if done is not None:
                        buf, pos = done
                    self._draw(buf, pos)
                    continue

                if ord(b[0]) < 32:
                    continue

                buf = buf[:pos] + b + buf[pos:]
                pos += len(b)
                self._draw(buf, pos)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    # ------------------------------------------------------------------ seq
    def _read_seq(self, fd: int) -> str:
        seq = ""
        for _ in range(4):
            r, _, _ = select.select([fd], [], [], 0.05)
            if not r:
                break
            try:
                ch = os.read(fd, 1)
            except OSError:
                break
            if not ch:
                break
            seq += ch.decode("utf-8", "ignore")
            if seq[-1] in ("A", "B", "C", "D", "H", "F") or seq == "[3~":
                break
        if seq in ("[A", "OA"):
            return "up"
        if seq in ("[B", "OB"):
            return "down"
        if seq in ("[C", "OC"):
            return "right"
        if seq in ("[D", "OD"):
            return "left"
        if seq in ("[H", "OH", "[1~"):
            return "home"
        if seq in ("[F", "OF", "[4~"):
            return "end"
        if seq == "[3~":
            return "delete"
        return ""

    # ------------------------------------------------------------ completion
    def _complete(self, buf: str, pos: int) -> Optional[Tuple[str, int]]:
        if not self.completions:
            return None
        head = buf[:pos]
        at = head.rfind("@")
        if at == -1:
            return None
        token = head[at + 1 :]
        if " " in token:
            return None
        matches = self.completions(token)
        if not matches:
            return None
        best = matches[0]
        if len(matches) > 1:
            self.ui.println(self.ui.c("  matches: ", Theme.muted) + self.ui.c(", ".join(matches[:8]), Theme.muted))
        new = head[:at] + "@" + best + buf[pos:]
        return new, len(head[:at]) + 1 + len(best)

    # ---------------------------------------------------------------- render
    def _band(self, buf: str, pos: int) -> str:
        width = self.ui.width()
        margin = 2
        before, after = buf[:pos], buf[pos:]
        body_visible = 2 + len(before) + 1 + len(after)  # "> " + text + cursor block
        # never let the band reach the last column: a write that fills the row
        # exactly would trigger the terminal's pending-wrap and push the band
        # onto the next line.
        pad = max(0, width - margin - body_visible - 1)
        body = (
            "\033[38;5;114m\033[1m> "
            + "\033[38;5;252m" + before
            + "\033[38;5;114m" + BLOCK
            + "\033[38;5;252m" + after
        )
        return " " * margin + "\033[48;5;238m" + body + " " * pad + "\033[0m"

    def _status_line(self) -> str:
        if not self.status:
            return ""
        text = self.status()
        width = self.ui.width()
        plain = vlen(text)
        return text + " " * max(0, width - plain)

    def _erase(self) -> None:
        self.ui.write("\x1b[G\x1b[2K\n\x1b[G\x1b[2K")

    def _finalize(self, buf: str) -> None:
        """Turn the live band into a normal line of scrollback."""
        # clear the band, echo the text on the very same row, then wipe the
        # status row below it so the agent output starts on a clean line.
        self.ui.write("\x1b[G\x1b[2K")
        self.ui.write(self.ui.c("> ", Theme.ok) + self.ui.c(buf, Theme.text) + "\n")
        self.ui.write("\x1b[G\x1b[2K")

    def _draw(self, buf: str, pos: int) -> None:
        # DECSC/DECRC keep the cursor exactly where the band was drawn, so the
        # status row can live below it without any fragile relative moves.
        out = "\x1b[G\033[2K" + self._band(buf, pos)
        status = self._status_line()
        if status:
            out += "\x1b7"                      # save cursor (band row)
            out += "\n\x1b[G\033[2K" + status  # status row below
            out += "\x1b8"                      # back to the band row
        out += f"\033[{pos + 2}C" if pos + 2 > 0 else ""
        self.ui.write(out)
