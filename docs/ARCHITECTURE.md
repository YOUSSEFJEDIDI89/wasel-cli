# Wasel — Architecture

This document explains how Wasel turns a chat model into an agent, and why the
codebase stays small enough to read in one sitting.

## 1. The agent loop (Claude Code style)

The loop lives in `wasel/agent.py` and is deliberately tiny:

```python
while steps_left:
    reply = provider.complete(system_prompt + transcript, tools)
    if not reply.tool_calls:
        return reply.content          # turn is over
    for call in reply.tool_calls:
        result = tools.execute(call)  # side effect happens here
        transcript.append(tool_result)
```

That is the same *gather → act → verify* cycle used by mature coding agents:

| Phase | What happens | Wasel tools |
|---|---|---|
| Gather | read the code base before touching it | `read_file`, `list_dir`, `glob`, `grep` |
| Act | change the world | `write_file`, `edit_file`, `run_shell` |
| Verify | check the result, fix mistakes | `run_shell`, `read_file`, `finish` |

The model is never asked to "answer about" the workspace — it is given tools
and the loop keeps handing it the outcome of its own actions.

## 2. Provider layer (`wasel/providers.py`)

Every supported provider speaks the OpenAI `/chat/completions` dialect, so the
agent core only ever sees one message shape:

```json
{"role": "assistant", "content": "...", "tool_calls": [{"function": {"name": "...", "arguments": "{...}"}}]}
```

* **Streaming** — SSE frames are parsed incrementally; `delta.content` is
  pushed to the terminal as it arrives, `delta.tool_calls` fragments are
  merged by index (open-weight models split the JSON arguments across chunks).
* **Failure classification** — HTTP 401/403 → `auth`, 429 → `ratelimit`,
  5xx → `server`, socket errors → `network`. Each class gets its own cooldown
  in `Cooldown`, so one broken provider never poisons the session.
* **Retry** — server errors are retried once with exponential backoff.
* **Fallback** — if streaming breaks mid-flight the client transparently
  re-issues the request without streaming.

### Router / failover

`Router.complete()` walks the ordered candidate list:

```text
pollinations → gemini → groq → openrouter → cerebras → mistral → nvidia → ollama → custom
```

A provider is skipped when it has no key, or when it is in cooldown. The first
provider that answers wins. This is what makes "free" practical: a daily quota
on one provider simply means the agent silently continues on another.

## 3. Tool layer (`wasel/tools.py`)

Tools are declared once as JSON Schema (OpenAI function-calling format) and
implemented as `_<tool>` methods on `ToolExecutor`.

* **Sandbox** — every path is resolved against the workspace root and rejected
  if it escapes (`../` protection).
* **Edit safety** — `edit_file` refuses an `old_string` that appears more than
  once unless `replace_all` is set, so ambiguous edits fail loudly instead of
  corrupting a file.
* **Shell safety** — commands are matched against a dangerous-pattern list
  (`rm -rf /`, `sudo`, `mkfs`, `dd if=`, `curl | sh`, fork bombs …). A match
  asks for confirmation in the TUI; `--yolo` disables the prompt.
* **Output limits** — shell output is truncated at 8 KB, file reads at 2 000
  lines, so one bad command cannot flood the context window.

## 4. Context management

* **Compaction** — when the estimated token count crosses
  `context_limit × compact_ratio`, the oldest messages are summarised by the
  model itself and replaced with a single summary message. The tail of the
  conversation is preserved.
* **Sessions** — every turn is written to `~/.wasel/sessions/<id>.json` and can
  be resumed with `/resume <id>`.

## 5. Terminal UI (`wasel/ui.py`)

Pure ANSI, no curses, no dependencies:

* banner, panels and tool cards drawn with box-drawing characters,
* token-by-token streaming with inline markdown styling (bold, code, bullets),
* a blinking cursor while the provider is thinking.

Because it is plain ANSI it behaves identically under Termux, `tmux`, `ssh`
and CI log collectors.

## 6. Why standard library only?

On Termux, `pip install` of anything with a wheel that needs compiling is a
support burden. Wasel therefore uses:

* `urllib.request` for HTTP + SSE streaming,
* `json`, `re`, `pathlib`, `subprocess`, `argparse` for everything else.

The result: `git clone && ./install.sh` works on a phone, offline, in under a
minute, and never touches the system Python.
