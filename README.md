<div align="center">

# Wasel — واصل

**An AI agent for your terminal, powered by real free LLM APIs.**
**وكيل ذكاء اصطناعي في الطرفية، يعمل بنماذج مجانية حقيقية.**

[English](#english) · [العربية](#العربية)

</div>

<div align="center">

### ▶ Watch the demo (60 seconds)

The demo video shows a **real** Wasel session: installing, booting, listing files,
writing `hello.py`, running it and reporting back.

<sub>Because the GitHub contents API only accepts text, the MP4 lives in the repo as
base64 parts — rebuild it in one command:</sub>

```bash
git clone https://github.com/YOUSSEFJEDIDI89/wasel-cli.git
cd wasel-cli && bash assets/restore-demo.sh   # → assets/wasel-demo.mp4 (648 KB)
```

</div>

---

## English

### What is Wasel?

Wasel (واصل = "bridge") is a terminal-native AI coding agent in the spirit of
**Claude Code**, **Codex CLI** and **Aider** — but it runs on **free** model
tiers, and it needs **zero dependencies** beyond the Python standard library,
which makes it a first-class citizen on **Termux** (Android), Raspberry Pi,
SSH boxes and locked-down machines.

It is not a chat wrapper. Wasel implements a **real agent loop**: it reads
files, writes code, runs commands, inspects the result, fixes its own mistakes
and keeps a task list — exactly the `gather → act → verify` cycle that powers
the big coding agents.

```text
   you  ──▶  Wasel agent loop  ──▶  free LLM API (Gemini · Groq · OpenRouter · …)
                    │
                    ├─ read_file / write_file / edit_file
                    ├─ glob / grep / list_dir
                    ├─ run_shell (sandboxed to the workspace)
                    ├─ todo_write (plan tracking)
                    ├─ task (sub-agent with its own context)
                    └─ web_fetch / finish
```

### Highlights

| | |
|---|---|
| 🆓 **Real free tiers** | Gemini, Groq, OpenRouter `:free`, Cerebras, Mistral, NVIDIA NIM, plus a **no-API-key** provider that works out of the box |
| 🧠 **Real agent loop** | 11 tools, iterative tool-calling, self-verification, plan tracking (`todo_write`) |
| 🧩 **Sub-agents** | `task` spawns a focused sub-agent in its own context window and returns only its summary |
| 📉 **Automatic failover** | Rate limit, 5xx, network error or bad key → the router moves to the next free provider automatically |
| 🪶 **Zero dependencies** | Pure Python stdlib. No pip compile step on Termux, no virtualenv, no Node |
| 📱 **Termux first-class** | `./install.sh` puts `wasel` on your `PATH`; config lives in `~/.wasel` |
| 🌍 **Bilingual** | English + Arabic interface and documentation |
| 💾 **Resumable sessions** | Every conversation is a JSON file you can `/resume` later |
| 🗜 **Context compaction** | Auto-summarises the transcript before the window overflows |

### Install

#### Termux (Android)

```bash
pkg update && pkg install python git
git clone https://github.com/YOUSSEFJEDIDI89/wasel-cli.git
cd wasel-cli && ./install.sh
export PATH="$HOME/.local/bin:$PATH"
```

#### Linux / macOS

```bash
git clone https://github.com/YOUSSEFJEDIDI89/wasel-cli.git
cd wasel-cli && ./install.sh
export PATH="$HOME/.local/bin:$PATH"
```

#### pip (optional)

```bash
pip install -e .        # provides the `wasel` command
```

> Requires Python **3.8+**. There is nothing to build — the whole project is
> the standard library.

### Quick start

```bash
wasel                                   # interactive agent
wasel -p "list the files here and summarise them"   # headless, one shot
wasel --provider gemini --model gemini-2.5-flash   # pin a provider
wasel --init                            # write an AGENTS.md for your repo
```

It works **immediately, with no API key** (the anonymous `pollinations`
provider). Add a free key whenever you want a bigger context window:

```bash
wasel keys set gemini <YOUR_GEMINI_API_KEY>    # 1M-token context, free tier
wasel keys set groq   <YOUR_GROQ_API_KEY>      # fastest inference
wasel /providers                              # see what is available
```

Keys can also come from the environment: `GEMINI_API_KEY`, `GROQ_API_KEY`,
`OPENROUTER_API_KEY`, `CEREBRAS_API_KEY`, `MISTRAL_API_KEY`, `NVIDIA_API_KEY`.

### Free providers

Wasel speaks the OpenAI `/chat/completions` dialect, so every provider below is
drop-in compatible. Limits change often — check the official dashboards.

| Provider | Default model | Key | Free tier | Where |
|---|---|---|---|---|
| **pollinations** | `openai-fast` | *not needed* | anonymous tier, tools + streaming | — |
| **gemini** | `gemini-2.5-flash` | `GEMINI_API_KEY` | Flash models, 1M context | [AI Studio](https://aistudio.google.com/app/apikey) |
| **groq** | `openai/gpt-oss-120b` | `GROQ_API_KEY` | fast LPU inference, daily token budget | [Console](https://console.groq.com/keys) |
| **openrouter** | `openrouter/free` | `OPENROUTER_API_KEY` | every `*:free` model | [Keys](https://openrouter.ai/keys) |
| **cerebras** | `gpt-oss-120b` | `CEREBRAS_API_KEY` | credits / trial | [Cloud](https://cloud.cerebras.ai) |
| **mistral** | `mistral-small-latest` | `MISTRAL_API_KEY` | experiment tier | [Console](https://console.mistral.ai) |
| **nvidia** | `qwen/qwen3-coder-480b-a35b-instruct` | `NVIDIA_API_KEY` | free catalogue | [Build](https://build.nvidia.com) |
| **ollama** | `llama3.2` | *not needed* | fully local / offline | [Ollama](https://ollama.com) |
| **custom** | *your model* | `CUSTOM_API_KEY` | any OpenAI-compatible server | `CUSTOM_BASE_URL` + `CUSTOM_MODEL` |

### The terminal UI

Wasel ships with a Junie-style terminal face: a slash-logo splash, a short list of
input shortcuts, a highlighted input band with a green block cursor, and a status
bar with the workspace, the active model and the mode flag.

```text
  ///         ///     /////////        ////////////  ///////////////  ///
  ///         ///  ///         ///  ///              ///              ///
  ///         ///  ///////////////     /////////     ////////////     ///
  ///   ///   ///  ///         ///              ///  ///              ///
     ///   ///     ///         ///  ////////////     ///////////////  ///////////////

v1.0.0

/ available commands
@ search a file or a folder
! bash command
? show help

Welcome to Wasel!
What are we going to build today?

 /tmp/projects/demo                      Gemini 2.5 Flash        ⚑ Brave off  ctrl + b
  > analyse the requirements and scaffold the app                          █
 /tmp/projects/demo                      Gemini 2.5 Flash        ⚑ Brave off  ctrl + b
```

| Input | Action |
|---|---|
| `@file.py` | mention a file (press **Tab** to complete it) |
| `!ls -la` | run a shell command directly, without the agent |
| `?` | show the help overlay |
| `/help` | list the slash commands |
| **Ctrl-B** | toggle **Brave mode** (auto-approve risky commands) |
| **Ctrl-C** | clear the current line · **Ctrl-D** | exit |
| **↑ / ↓** | browse the prompt history |

### Real session (captured from a live run)

```text
provider=pollinations model=openai-fast

▸ list_dir
  ✔ done
    .:
      (empty)

▸ write_file  hello.py
  ✔ done
    Created hello.py (26 bytes)

▸ run_shell  python3 hello.py
  ✔ done
    exit=0 time=0.0s
    Hello from Wasel!

Created hello.py with print('Hello from Wasel!')
Executed python3 hello.py:
Hello from Wasel!
```

### Commands

Shell-level commands (useful for scripting):

```bash
wasel keys list                       # show every provider and where its key lives
wasel keys set gemini <KEY>           # store a free key in ~/.wasel/keys.json (0600)
wasel keys rm gemini
wasel providers                       # the free-provider table
wasel models groq                     # ask a provider for its live model list
wasel --list-providers
```

In-REPL commands:
```
/help                 show this help
/status               provider, model, tokens, session
/providers            list free providers and whether a key is present
/provider <id>        pin a provider (or 'auto' to fail over freely)
/model <name>         set the model for the current provider
/models [provider]    query the provider for its live model list
/keys                 list stored keys      /keys set <id> <KEY>   /keys rm <id>
/todos                show the current task list
/compact              force a context compaction
/clear                start a fresh session
/sessions             list saved sessions    /resume <id>
/save                 force-save the session
/init                 write an AGENTS.md starter file
/exit                 leave wasel
```

### Tools

| Tool | Purpose |
|---|---|
| `read_file` | read a file with line numbers |
| `write_file` | create / overwrite a file |
| `edit_file` | surgical string replacement (fails loudly on ambiguity) |
| `list_dir` | list a directory |
| `glob` | find files by pattern |
| `grep` | regex search across the workspace |
| `run_shell` | run a command inside the workspace |
| `todo_write` | maintain the plan (one item `in_progress`) |
| `web_fetch` | download a URL and read its text |
| `task` | delegate to a sub-agent (own context) |
| `finish` | deliver the final answer |

### Safety

* Every file tool is confined to the workspace root (`--root` to change it).
* Risky shell commands (`rm -rf /`, `sudo`, `mkfs`, `curl | sh`, …) require an
  interactive confirmation; `--yolo` disables the prompt.
* API keys are read from the environment or stored in `~/.wasel/keys.json`
  with `0600` permissions — never in the repository.

### How it works

```text
wasel/
├── wasel/
│   ├── agent.py       # the agent loop: tools → provider → tools → …
│   ├── providers.py   # provider registry + OpenAI-compatible client (SSE streaming)
│   ├── tools.py       # tool schemas + sandboxed executors
│   ├── ui.py          # ANSI terminal renderer (panels, streaming, tool cards)
│   ├── session.py     # resumable JSON sessions
│   ├── config.py      # ~/.wasel config + key storage
│   └── cli.py         # REPL, slash commands, headless mode
└── install.sh         # Termux-friendly installer
```

The loop is deliberately small and readable:

1. send the system prompt + transcript + tool schemas to the provider,
2. if the model asks for tools → run them, append the results, go back to 1,
3. if the model answers without tools → the turn is done,
4. if the transcript approaches the context limit → summarise it and continue.

### Roadmap

- [ ] Parallel tool execution for read-only tools
- [ ] MCP (Model Context Protocol) client support
- [ ] `/review` command that diffs the working tree and self-critiques
- [ ] Voice input / TTS output for Termux
- [ ] More providers (Cloudflare Workers AI, Hugging Face router)

### License

MIT — see [LICENSE](LICENSE).

---

## العربية

### ما هو «واصل»؟

**واصل** وكيل ذكاء اصطناعي يعمل داخل الطرفية، على غرار **Claude Code** و**Codex CLI**
و**Aider**، لكنه يعتمد على **نماذج مجانية حقيقية** ولا يحتاج سوى **مكتبة بايثون
المعيارية** — لذلك يعمل مباشرة على **Termux** (أندرويد) وأجهزة الراسبيري باي
والخوادم البعيدة.

ليس مجرد محادثة: **وصل** ينفّذ حلقة وكيل حقيقية:

* يقرأ الملفات (`read_file`, `list_dir`, `glob`, `grep`).
* يكتب ويعدّل الكود (`write_file`, `edit_file`).
* ينفّذ الأوامر ويتحقق من النتيجة (`run_shell`).
* يتابع المهام عبر قائمة (`todo_write`) ويستدعي وكلاء فرعية (`task`).
* عند امتلاء نافذة السياق يختصر السجل تلقائياً (`compact`).

### التثبيت على Termux

```bash
pkg update && pkg install python git
git clone https://github.com/YOUSSEFJEDIDI89/wasel-cli.git
cd wasel-cli && ./install.sh
export PATH="$HOME/.local/bin:$PATH"
wasel
```

يعمل ** فوراً بدون أي مفتاح API** عبر المزوّد المجاني `pollinations`.
ولحصول على سياق أكبر ونماذج أقوى، أضف مفتاحاً مجانياً:

```bash
wasel keys set gemini <مفتاحك>     # سياق 1M رمز على الطبقة المجانية
wasel keys set groq   <مفتاحك>     # أسرع استدلال مجاني
```

### أهم الأوامر

| الأمر | الوظيفة |
|---|---|
| `/providers` | عرض المزوّدين المجانيين وحالة المفاتيح |
| `/provider <id>` | تثبيت مزوّد معيّن أو `auto` للتبديل التلقائي |
| `/model <name>` | اختيار الموديل |
| `/keys set <id> <KEY>` | حفظ مفتاح مجاني |
| `/todos` | عرض قائمة المهام |
| `/compact` | تلخيص السياق الحالي |
| `/sessions` / `/resume <id>` | حفظ واستئناف الجلسات |
| `/init` | توليد ملف `AGENTS.md` لمشروعك |

### الخدمات المجانية المدعومة

Gemini · Groq · OpenRouter (`*:free`) · Cerebras · Mistral · NVIDIA NIM ·
Pollinations (بدون مفتاح) · Ollama (محلي بالكامل) · أي خادم متوافق مع
OpenAI عبر `CUSTOM_BASE_URL`.

> حدود الاستخدام المجاني تتغير باستمرار — راجع اللوحات الرسمية لكل مزوّد.

### الرخصة

MIT — راجع [LICENSE](LICENSE).

---

<div align="center">
  <sub>Built with ❤️ for people who want a serious agent on a phone.</sub>
</div>
