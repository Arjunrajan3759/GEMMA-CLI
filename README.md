# GemCode — Gemma-powered coding agent (TUI)

GemCode is a terminal-based AI coding agent. The Planner (Gemma 4 31B) breaks your goal into tasks. Each task runs through a writer → executor → tester → reviewer loop (with retries) using Gemma models via NVIDIA NIM, until it passes or hits the retry limit.

Built with [Rich](https://github.com/Textualize/rich) for a live-streaming terminal UI — shows thinking time, token usage, and streams model output token-by-token as it arrives.

## Setup

```bash
pip install -r requirements.txt
```

## Quick start

```bash
python agentcli/main.py
```

Inside the interactive session:

1. **`/connect nvapi-<your-key>`** — paste your NVIDIA NIM API key (saved to `.env`)
2. Chat naturally with the Planner to refine your goal
3. **`build the plan`** — finalize and start the task execution loop
4. **`exit`** / **`quit`** — end the session

### Commands

| Command | Description |
|---|---|
| `/connect <key>` | Set NVIDIA NIM API key |
| `/skill <url>` | Load a custom skill from a GitHub raw URL |
| `/skill list` | Show currently loaded custom skills |
| `/skill clear` | Remove all loaded skills |
| `/save [name]` | Save code blocks from last response to `outputs/saved/` |
| `/export [name]` | Export full conversation to `outputs/exports/` |
| `/cost` | Show token usage stats |
| `/diff` | Show git diff of uncommitted changes |
| `/doctor` | Run environment diagnostics (API key, network, git) |
| `/clear` | Reset conversation history |
| `/compact` | Compress long conversations into a summary |
| `/help` | Show all available commands |
| `build the plan` | Finalize plan and start writer→tester→reviewer loop |
| `exit` / `quit` | Exit session |

### Custom skills

Load any public GitHub skill file to inject extra context into the Planner:

```
/skill https://github.com/user/repo/blob/main/skill-name/SKILL.md
```

GemCode auto-converts GitHub URLs to raw URLs. Works with any markdown skill file (e.g. from [OpenSkills](https://github.com/CODE-SAURABH/OpenSkills)). Use `/skill list` to see loaded skills and `/skill clear` to reset.

You can also pass an initial goal as an argument:

```bash
python agentcli/main.py "build a palindrome checker CLI"
```

Output lands in `./outputs/<project_name>/task_<id>/`.

## Verifying your setup

```bash
cd agentcli
python nim_client.py   # confirm every role resolves end to end
```

## Model catalog notes

Two separate checks matter here, and they can give different answers:

1. `list_models.py` (queries `/v1/models`) shows the models listed in the catalog for your key.
2. `probe_models.py` (actually calls each one) confirms which of those genuinely work end-to-end. Some models may 404 even when listed — a model can be catalog-listed without being entitled to your account, and some endpoints aren't available on every NIM deployment.

**Trust `probe_models.py`, not `list_models.py`.** The catalog lists what NVIDIA offers broadly; it doesn't confirm your account can call it.

### What actually works

- `google/gemma-4-31b-it` — best reasoning, used for **planner + writer**
- `google/diffusiongemma-26b-a4b-it` — fast, and empirically correct on both review and test-writing tasks, used for **reviewer + tester**

Two roles per model, differentiated by system prompt (`skills/*.md`) rather than by separate model backends.

If you get more entitlements, re-run `probe_models.py` and update `ROLE_MODEL_CANDIDATES` in `nim_client.py`.

## Project layout

```
  agentcli/
    main.py            # TUI entrypoint (GemCode)
    nim_client.py      # NIM API wrapper + streaming + model config
    orchestrator.py    # plan, run_task, run — agent orchestration loop
    executor.py        # subprocess runner for generated code
    list_models.py     # query /v1/models catalog
    probe_models.py    # test which models actually work for your key
    outputs/           # generated projects, saved code, exports
  skills/
    planner.md         # system prompt: breaks goal into tasks
    writer.md          # system prompt: writes code per task
    reviewer.md        # system prompt: judges correctness
    tester.md          # system prompt: writes test scripts
  .env                 # API key (auto-created by /connect, gitignored)
  .gitignore
  requirements.txt
```

## Swapping models

Edit `ROLE_MODEL_CANDIDATES` in `agentcli/nim_client.py`. If it's a completion-mode (non `-it`) model, also add its slug to `COMPLETION_MODE_MODELS`.

## Known cut corners (intentional, for time)

- Tasks run sequentially, not in parallel.
- No cross-task dependency injection yet — `depends_on` is tracked in the plan JSON but not enforced by the orchestrator.
- Sandbox is a subprocess with a timeout, not a full container.
