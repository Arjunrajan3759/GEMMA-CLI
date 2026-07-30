# GemCode — Gemma-powered Coding Agent (TUI)

GemCode is a terminal-based AI coding agent powered by Google's Gemma models via NVIDIA NIM API. The **Planner** (`google/gemma-4-31b-it`) discusses and breaks your software goal into modular tasks. Each task then runs through an automated **Writer → Executor → Tester → Reviewer** loop (with retries and feedback) until it passes or hits the retry limit.

Built with [Rich](https://github.com/Textualize/rich) for a live-streaming terminal UI — displaying thinking time, token usage, and real-time streaming output.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation & Setup](#installation--setup)
- [Running the Application](#running-the-application)
- [Interactive Commands](#interactive-commands)
- [Custom Skills](#custom-skills)
- [Model Verification & Diagnostics](#model-verification--diagnostics)
- [Project Layout](#project-layout)
- [Current Limitations](#current-limitations)
- [License](#license)

---

## Prerequisites

Before running GemCode, ensure you have:

- **Python 3.10+** (Python 3.8+ supported)
- **Git** installed on your system
- An **NVIDIA NIM API Key** (Get one at [NVIDIA Build](https://build.nvidia.com/google/gemma-4-31b-it))

---

## Installation & Setup

Follow these steps to clone and set up GemCode on your local device:

### 1. Clone the Repository

Open your terminal or command prompt and clone the repository onto your system:

```bash
git clone https://github.com/your-username/GemCode.git
cd GemCode
```

### 2. Set Up a Virtual Environment (Recommended)

Create and activate an isolated Python virtual environment:

- **On macOS / Linux:**
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```

- **On Windows (PowerShell):**
  ```powershell
  python -m venv venv
  .\venv\Scripts\Activate.ps1
  ```

- **On Windows (CMD):**
  ```cmd
  python -m venv venv
  venv\Scripts\activate.bat
  ```

### 3. Install Dependencies

Install the pinned dependencies specified in `requirements.txt`:

```bash
pip install -r requirements.txt
```

### 4. Configure Your API Key

You can configure your NVIDIA NIM API key using either of these two methods:

- **Method A: Using `.env` file**
  Copy `.env.example` to `.env` and insert your API key:
  ```bash
  cp .env.example .env
  ```
  Edit `.env`:
  ```env
  NVIDIA_API_KEY=nvapi-your-actual-api-key-here
  ```

- **Method B: In-App Connection Command**
  You can launch the app directly and use the `/connect` command:
  ```bash
  /connect nvapi-your-actual-api-key-here
  ```

---

## Running the Application

### 1. Launching Interactive TUI Mode

To start the interactive terminal session, run:

```bash
python agentcli/main.py
```

Inside the interactive prompt:
1. (Optional) Set your API key if not in `.env`: `/connect nvapi-<your-key>`
2. Chat naturally with the **Planner** to outline and refine your project goal.
3. Type **`build the plan`** to finalize the architecture and start the task execution loop.
4. Generated code and outputs will land in `./outputs/<project_name>/task_<id>/`.
5. Type **`exit`** or **`quit`** to leave the session.

### 2. Passing an Initial Goal via Command Line

You can pass a project goal directly as a command-line argument:

```bash
python agentcli/main.py "build a palindrome checker CLI application"
```

---

## Interactive Commands

Inside the GemCode CLI session, the following slash commands are available:

| Command | Description |
|---|---|
| `/connect <key>` | Set and save your NVIDIA NIM API key to `.env` |
| `/skill <url>` | Load a custom skill from a GitHub raw or blob URL |
| `/skill list` | List all currently loaded custom skills |
| `/skill clear` | Clear all custom skills |
| `/use [name]` | Activate/deactivate a loaded skill for system prompt replacement |
| `/save [name]` | Save generated code blocks to `outputs/saved/` |
| `/export [name]` | Export conversation history to `outputs/exports/<name>.md` |
| `/cost` | Display token usage statistics for the session |
| `/diff` | Display `git diff` of uncommitted working directory changes |
| `/doctor` | Run environment diagnostics (API key, reachability, git) |
| `/clear` | Reset current conversation history |
| `/compact` | Compress long conversation history into a dense markdown summary |
| `/help` | Display command help panel |
| `build the plan` | Finalize project plan and launch the task execution loop |
| `exit` / `quit` | Exit the CLI application |

---

## Custom Skills

Inject extra domain knowledge or guidelines into the Planner agent by loading custom markdown skills from GitHub:

```text
/skill https://github.com/user/repo/blob/main/code-reviewer/SKILL.md
```

GemCode automatically converts standard GitHub blob URLs into raw URLs. Use `/use <skill-name>` to switch system prompts dynamically.

---

## Model Verification & Diagnostics

Verify your environment setup and catalog model entitlement:

```bash
cd agentcli

# 1. Run basic NIM API health check
python nim_client.py

# 2. Query available Gemma models
python list_models.py

# 3. Empirically probe model performance across roles
python probe_models.py
```

### Configured Models

- `google/gemma-4-31b-it`: Used for **Planner** and **Writer** roles (strong reasoning and code output).
- `google/diffusiongemma-26b-a4b-it`: Used for **Reviewer** and **Tester** roles (fast judgment and assertion generation).

---

## Project Layout

```text
GemCode/
├── agentcli/
│   ├── main.py            # TUI entrypoint & command handler
│   ├── nim_client.py      # NVIDIA NIM API client & streaming handler
│   ├── orchestrator.py    # Plan generator & task execution loop
│   ├── executor.py        # Isolated subprocess runner for Python code
│   ├── list_models.py     # Utility to list catalog models
│   └── probe_models.py    # Utility to test model entitlements
├── skills/
│   ├── planner.md         # Planner system prompt instructions
│   ├── writer.md          # Writer system prompt instructions
│   ├── reviewer.md        # Reviewer system prompt instructions
│   └── tester.md          # Tester system prompt instructions
├── .env.example           # Environment template file
├── .gitignore             # Git ignore rule definitions
├── LICENSE                # MIT License
├── README.md              # Project documentation
└── requirements.txt       # Pinned dependencies (requests, rich)
```

---

## Current Limitations

While fully functional for hackathon demos and agentic coding, GemCode currently has the following known limitations:

1. **Sequential Task Execution:**
   Tasks in a project plan execute sequentially. Parallel execution of independent tasks is not yet implemented.

2. **Subprocess Execution Scope:**
   Generated Python code is executed locally in a temporary directory via Python subprocess with a 15-second timeout and strict environment secret filtering (excluding `NVIDIA_API_KEY`). However, it is not sandboxed within a containerized environment (such as Docker or gVisor).

3. **Target Language Optimization:**
   The automated **Tester** and **Executor** loop is currently optimized specifically for Python (`solution.py` and `test_solution.py`). Multi-language execution (e.g. Node.js, Rust, Go) requires extending `executor.py`.

4. **NVIDIA NIM Model Entitlements:**
   Inference availability depends on NVIDIA NIM API service entitlements and rate limits for specific Gemma models (`google/gemma-4-31b-it` and `google/diffusiongemma-26b-a4b-it`).

---

## License

This project is licensed under the [MIT License](LICENSE).
