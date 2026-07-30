import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import queue
import threading
from datetime import datetime

import requests
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from rich.prompt import Prompt
from rich.table import Table
from rich.rule import Rule
from rich.columns import Columns
from rich import box

from nim_client import call_model, call_model_stream, NIM_MODELS_URL
from executor import extract_code_block
from orchestrator import (
    plan_from_conversation,
    get_next_session_number,
    get_stats_dir,
    run_plan,
)

AUTO_COMPACT_THRESHOLD = 10
VERSION = "V4"
ACCENT = "bold magenta"
DIM = "dim white"
BORDER = "purple"
HEADER_COLOR = "bright_magenta"

CONVERSATIONAL_PLANNER_SYSTEM = """You are an expert AI software architect and project planner.
Your role is to discuss, refine, and plan a software project with the user in plain, helpful language.
Help the user break down their idea into clear modular tasks, answer questions, suggest improvements, and refine the project plan.
Do NOT output JSON yet unless requested; converse naturally until the user decides to build the plan."""

COMPACT_SYSTEM = "You are a precise summarizer. Compress the conversation below into a dense markdown summary preserving the project goal, decisions made, and open questions."

CUSTOM_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "skills", "custom")
SKILL_MANIFEST = os.path.join(CUSTOM_SKILLS_DIR, ".manifest.json")
HISTORY_DIR = os.path.join(os.path.dirname(__file__), "..", "outputs", "history")

active_skills = []
current_skill = None


def _load_skill_manifest():
    if not os.path.isfile(SKILL_MANIFEST):
        return
    try:
        with open(SKILL_MANIFEST, encoding="utf-8") as f:
            entries = json.load(f)
        for entry in entries:
            path = os.path.join(CUSTOM_SKILLS_DIR, f"{entry['name']}.md")
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                active_skills.append({"name": entry["name"], "content": content, "source": entry["source"]})
    except Exception:
        pass


def _save_skill_manifest():
    os.makedirs(CUSTOM_SKILLS_DIR, exist_ok=True)
    entries = [{"name": s["name"], "source": s["source"]} for s in active_skills]
    with open(SKILL_MANIFEST, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def _load_history(session_num):
    path = os.path.join(HISTORY_DIR, f"session-{session_num}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_history(history, session_num, initial_goal=""):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, f"session-{session_num}.json")
    data = {"session_num": session_num, "initial_goal": initial_goal, "history": history}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _load_history_for_resume():
    if not os.path.isdir(HISTORY_DIR):
        return None
    files = sorted(
        [f for f in os.listdir(HISTORY_DIR) if f.startswith("session-") and f.endswith(".json")],
        reverse=True,
    )
    for fname in files:
        path = os.path.join(HISTORY_DIR, fname)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "history" in data and data["history"]:
                return data
        except Exception:
            continue
    return None


def _build_system_prompt():
    if current_skill:
        return current_skill["content"]
    parts = [CONVERSATIONAL_PLANNER_SYSTEM]
    for s in active_skills:
        parts.append(f"[Custom Skill: {s['name']}]\n{s['content']}")
    return "\n\n".join(parts)


ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))

console = Console()


def _resolve_skill_url(url):
    url = url.strip()
    m = re.match(r"https://github\.com/([^/]+/[^/]+)(/.*)?", url)
    if m:
        repo = m.group(1)
        path = m.group(2) or ""
        if "/blob/" in path:
            raw = re.sub(r"/blob/", "/", path)
            return f"https://raw.githubusercontent.com/{repo}{raw}"
        if not path or path in ("/", "/tree/main", "/tree/master"):
            return f"__repo__{repo}"
        if path.startswith("/tree/"):
            branch = path.split("/")[2]
            file_path = "/".join(path.split("/")[3:])
            if file_path:
                return f"https://raw.githubusercontent.com/{repo}/{branch}/{file_path}"
            return f"__repo__{repo}"
    return None  # not a github.com URL, use as-is


def load_env():
    if not os.path.isfile(ENV_PATH):
        return
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def save_env(key, value):
    with open(ENV_PATH, "w") as f:
        f.write(f"{key}={value}\n")


def _extract_all_code_blocks(text):
    blocks = re.findall(r"```(\w+)?\s*\n(.*?)```", text, re.DOTALL)
    return [[(lang or "python").strip(), block.strip()] for lang, block in blocks]


def _save_code_blocks(last_response, base_dir, filename=None):
    blocks = _extract_all_code_blocks(last_response)
    if not blocks:
        return None
    saved = []
    out_dir = os.path.abspath(os.path.join(base_dir, "saved"))
    os.makedirs(out_dir, exist_ok=True)
    for i, (lang, code) in enumerate(blocks):
        if filename:
            name = os.path.basename(filename.strip().rstrip("/\\"))
            if not name or name in (".", ".."):
                name = f"generated_{i+1}.py"
        else:
            name = f"generated_{i+1}.{lang}" if lang != "python" else f"generated_{i+1}.py"
            if len(blocks) == 1:
                name = f"generated_1.py"
        path = os.path.abspath(os.path.join(out_dir, name))
        if not path.startswith(out_dir):
            raise ValueError(f"Path traversal detected in filename: {filename}")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        saved.append(path)
    return saved


def format_history(history):
    lines = []
    for turn in history:
        role = turn["role"].capitalize()
        lines.append(f"{role}: {turn['content']}")
    lines.append("\nPlease respond to the latest message above as Assistant:")
    return "\n\n".join(lines)


def stream_planner_response(role, system_prompt, user_prompt):
    token_queue = queue.Queue()

    def worker():
        try:
            for token, usage in call_model_stream(role, system_prompt, user_prompt):
                token_queue.put(("token", token))
                if usage is not None:
                    token_queue.put(("usage", usage))
            token_queue.put(("done", None))
        except Exception as e:
            token_queue.put(("error", e))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    start = time.time()
    collected = []
    usage = None
    got_first_token = False
    full_text = ""

    with Live(console=console, refresh_per_second=10, auto_refresh=False) as live:
        while True:
            try:
                item = token_queue.get(timeout=0.1)
            except queue.Empty:
                elapsed = time.time() - start
                if not got_first_token:
                    renderable = Panel(
                        Text(f"Thinking... ({elapsed:.1f}s)", style="yellow"),
                        border_style=BORDER, box=box.ROUNDED,
                    )
                    live.update(renderable)
                    live.refresh()
                continue

            if item[0] == "token":
                token = item[1]
                if not got_first_token:
                    got_first_token = True
                collected.append(token)
                display = "".join(collected[-500:])
                renderable = Panel(
                    Markdown(display, code_theme="monokai"),
                    title=f"[bold]{role.capitalize()}[/bold]",
                    border_style=BORDER, box=box.HEAVY,
                )
                live.update(renderable)
                live.refresh()
            elif item[0] == "usage":
                usage = item[1]
            elif item[0] == "done":
                elapsed = time.time() - start
                full_text = "".join(collected)
                total_tokens = (usage or {}).get("total_tokens", 0)
                sub = Text(f"{elapsed:.1f}s", style="yellow")
                if total_tokens:
                    sub.append(f" | {total_tokens} tokens", style=DIM)
                renderable = Panel(
                    Markdown(full_text, code_theme="monokai"),
                    title=f"[bold]{role.capitalize()}[/bold]",
                    border_style=BORDER, box=box.HEAVY,
                    subtitle=sub,
                )
                live.update(renderable)
                live.refresh()
                break
            elif item[0] == "error":
                raise item[1]

    return full_text, usage


def compact_context(history, stats_dir, compaction_counter):
    turns_condensed = len(history)
    history_str = format_history(history)
    compaction_prompt = (
        "Compress the following conversation into a dense summary. Preserve:\n"
        "1. The project goal and current plan state\n"
        "2. Concrete decisions made\n"
        "3. Things explicitly ruled out\n"
        "4. Any open questions\n\n"
        f"Conversation:\n{history_str}"
    )

    console.print(Rule(style=BORDER))
    summary, _ = stream_planner_response("planner", COMPACT_SYSTEM, compaction_prompt)

    os.makedirs(stats_dir, exist_ok=True)
    filename = f"context-{compaction_counter}.md"
    filepath = os.path.join(stats_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# Context Compaction #{compaction_counter}\n\n{summary}\n")

    console.print(f"  [dim]compacted {turns_condensed} turns -> {filename}[/dim]\n")
    new_history = [{"role": "assistant", "content": f"Previous conversation summary:\n{summary}"}]
    return new_history, filepath


def show_header(session_num):
    title = Text()
    title.append("  ", style="")
    title.append("Gem", style="bold bright_magenta")
    title.append("Code", style="bold white")
    title.append(f"  {VERSION}", style="bold yellow")
    title.append(f"  |  Session #{session_num}", style=DIM)
    console.print(title)
    console.print(Rule(style="bright_magenta"))


def show_welcome(session_num):
    commands = Table(show_header=False, box=box.SIMPLE, padding=(0, 2, 0, 0))
    commands.add_column("Cmd", style="cyan", no_wrap=True)
    commands.add_column("Description")
    commands.add_row("/connect <key>", "Set NVIDIA NIM API key")
    commands.add_row("/skill <url>", "Load a custom skill (GitHub file or raw URL)")
    commands.add_row("/skill list", "Show loaded custom skills")
    commands.add_row("/skill clear", "Remove all loaded skills")
    commands.add_row("/use <name>", "Activate a loaded skill as the active prompt")
    commands.add_row("/save [name]", "Save code blocks to outputs/saved/")
    commands.add_row("/export [name]", "Export conversation to markdown")
    commands.add_row("/cost", "Show token usage stats")
    commands.add_row("/diff", "Show git diff of changes")
    commands.add_row("/doctor", "Run environment diagnostics")
    commands.add_row("/clear", "Reset conversation history")
    commands.add_row("/compact", "Compress conversation to summary")
    commands.add_row("/help", "Show this help")
    commands.add_row("build the plan", "Finalize plan and start execution")
    commands.add_row("exit / quit", "Exit session")

    panel = Panel(commands, title="[bold]Commands[/bold]", border_style=BORDER, box=box.ROUNDED)
    console.print(panel)
    console.print()


def cmd_help():
    show_welcome("")


def cmd_clear(history, session_num=0):
    history.clear()
    path = os.path.join(HISTORY_DIR, f"session-{session_num}.json")
    if os.path.isfile(path):
        os.remove(path)
    console.print("[green]Conversation cleared.[/green]")
    return 1


def cmd_export(history, base_dir, session_num, filename=None):
    if not history:
        console.print("[yellow]Nothing to export.[/yellow]")
        return
    if filename:
        raw_name = filename.strip().rstrip("/\\")
        name = os.path.basename(raw_name)
    else:
        name = f"session-{session_num}"
    if not name or name in (".", ".."):
        name = f"session-{session_num}"
    if not name.endswith(".md"):
        name += ".md"
    out_dir = os.path.abspath(os.path.join(base_dir, "exports"))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.abspath(os.path.join(out_dir, name))
    if not path.startswith(out_dir):
        console.print("[red]Error: Invalid export filename (path traversal detected).[/red]")
        return
    lines = [f"# Session {session_num}\n", f"Exported: {datetime.now():%Y-%m-%d %H:%M:%S}\n"]
    for turn in history:
        role = turn["role"].capitalize()
        content = turn["content"]
        usage = turn.get("usage", {})
        tokens = usage.get("total_tokens", 0)
        tag = f" ({tokens} tokens)" if tokens else ""
        lines.append(f"## {role}{tag}\n\n{content}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    console.print(f"[green]Exported to {path}[/green]")


def cmd_cost(history):
    if not history:
        console.print("[yellow]No conversation yet.[/yellow]")
        return
    total_prompt = 0
    total_completion = 0
    total_all = 0
    turns = 0
    has_data = False
    for turn in history:
        usage = turn.get("usage", {})
        if usage:
            has_data = True
            total_prompt += usage.get("prompt_tokens", 0)
            total_completion += usage.get("completion_tokens", 0)
            total_all += usage.get("total_tokens", 0)
            turns += 1
    table = Table(box=box.ROUNDED, title="Session Token Usage", border_style=BORDER)
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="yellow")
    if has_data:
        table.add_row("Prompt tokens", str(total_prompt))
        table.add_row("Completion tokens", str(total_completion))
        table.add_row("Total tokens", str(total_all))
        table.add_row("Model calls", str(turns))
    else:
        table.add_row("Usage data", "[dim]N/A (streaming API did not return counts)[/dim]")
    console.print(table)


def cmd_diff():
    try:
        r1 = subprocess.run(["git", "diff", "--stat"], capture_output=True, text=True, timeout=10, cwd=PROJECT_ROOT)
        stat = r1.stdout.strip()
        r2 = subprocess.run(["git", "diff"], capture_output=True, text=True, timeout=10, cwd=PROJECT_ROOT)
        diff = r2.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        console.print(f"[yellow]git diff failed: {e}[/yellow]")
        return
    if not diff:
        console.print("[green]No uncommitted changes.[/green]")
        return
    if stat:
        console.print(f"[bold]Changed:[/bold] {stat}")
    console.print(Panel(Markdown(f"```diff\n{diff}\n```", code_theme="monokai"), border_style=BORDER, box=box.HEAVY))


def cmd_doctor():
    issues, ok = [], []
    key = os.environ.get("NVIDIA_API_KEY")
    if key:
        ok.append("NVIDIA_API_KEY is set")
    else:
        issues.append("NVIDIA_API_KEY not set")
    try:
        r = requests.get(NIM_MODELS_URL, headers={"Authorization": f"Bearer {key}"}, timeout=10)
        if r.status_code == 200:
            ok.append("NIM API reachable")
        else:
            issues.append(f"NIM API returned {r.status_code}")
    except requests.RequestException as e:
        issues.append(f"NIM API error: {e}")
    try:
        subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=5, cwd=PROJECT_ROOT)
        ok.append("git is available")
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5, cwd=PROJECT_ROOT)
        if r.returncode == 0:
            ok.append(f"Git repo: {r.stdout.strip()}")
        else:
            issues.append("Not a git repository")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        issues.append("git not found")
    ok.append(f"Python {sys.version.split()[0]}")
    table = Table(box=box.ROUNDED, title="Diagnostics", border_style=BORDER)
    table.add_column("Check", style="cyan")
    table.add_column("Status")
    for item in ok:
        table.add_row(item, "[green]OK[/green]")
    for item in issues:
        table.add_row(item, "[red]FAIL[/red]")
    console.print(table)


def cli():
    global current_skill
    load_env()

    parser = argparse.ArgumentParser(prog="GemCode", description="Gemma-powered coding agent")
    parser.add_argument("goal", nargs="*", help="initial project goal (optional)")
    parser.add_argument("--out", default="./outputs", help="output directory (default: ./outputs)")
    parser.add_argument("--verbose", "-v", action="store_true", help="enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    base_dir = args.out
    stats_dir = get_stats_dir(base_dir)

    _load_skill_manifest()
    if active_skills:
        console.print(f"[dim]{len(active_skills)} skill(s) loaded from previous session[/dim]")

    saved_data = _load_history_for_resume()
    if saved_data:
        session_num = saved_data["session_num"]
        history = saved_data["history"]
        initial_goal_text = saved_data.get("initial_goal", "")
        user_turns_since_compaction = 0
        compaction_counter = 1
        show_header(session_num)
        show_welcome(session_num)
        console.print(f"[green]Resumed session #{session_num}[/green]")
        console.print()
    else:
        session_num = get_next_session_number(base_dir, stats_dir=stats_dir)
        history = []
        user_turns_since_compaction = 0
        compaction_counter = 1
        initial_goal_text = " ".join(args.goal) if args.goal else ""
        show_header(session_num)
        show_welcome(session_num)

    if not os.environ.get("NVIDIA_API_KEY"):
        console.print("[yellow]No API key found. Use /connect to set one.[/yellow]\n")

    if initial_goal_text:
        if not os.environ.get("NVIDIA_API_KEY"):
            console.print("[yellow]Set your API key with /connect first, then re-enter.[/yellow]")
        else:
            console.print(f"[bold magenta]You:[/bold magenta] {initial_goal_text}")
            history.append({"role": "user", "content": initial_goal_text})
            user_turns_since_compaction += 1
            response, usage = stream_planner_response("planner", _build_system_prompt(), format_history(history))
            history.append({"role": "assistant", "content": response, "usage": usage or {}})
            _save_history(history, session_num, initial_goal_text)

    while True:
        skill_tag = f" [dim]({current_skill['name']})[/dim]" if current_skill else ""
        user_input = Prompt.ask(f"[bold magenta]You{skill_tag}[/bold magenta]").strip()

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit"):
            console.print("[yellow]Goodbye.[/yellow]")
            break

        if user_input.startswith("/"):
            cmd = user_input.strip().lower()
            if cmd.startswith("/connect"):
                parts = user_input.split(None, 1)
                if len(parts) < 2:
                    console.print("[yellow]Usage: /connect <your-api-key>[/yellow]")
                    continue
                os.environ["NVIDIA_API_KEY"] = parts[1].strip()
                save_env("NVIDIA_API_KEY", parts[1].strip())
                console.print("[green]Connected. Key saved to .env[/green]")

            elif cmd.startswith("/skill"):
                parts = user_input.split(None, 2)
                sub = parts[1].strip().lower() if len(parts) > 1 else ""

                if sub == "list":
                    if not active_skills:
                        console.print("[yellow]No custom skills loaded.[/yellow]")
                    else:
                        t = Table(box=box.ROUNDED, title="Loaded Skills", border_style=BORDER)
                        t.add_column("Name", style="cyan")
                        t.add_column("Source")
                        for s in active_skills:
                            t.add_row(s["name"], s["source"])
                        console.print(t)
                    continue

                if sub == "clear":
                    active_skills.clear()
                    if os.path.isdir(CUSTOM_SKILLS_DIR):
                        for f in os.listdir(CUSTOM_SKILLS_DIR):
                            if f.endswith(".md"):
                                os.remove(os.path.join(CUSTOM_SKILLS_DIR, f))
                    _save_skill_manifest()
                    console.print("[green]Custom skills cleared.[/green]")
                    continue

                url = parts[2].strip() if len(parts) > 2 else parts[1].strip() if len(parts) > 1 else ""
                if not url:
                    console.print("[yellow]Usage: /skill <url>[/yellow]")
                    console.print("  [dim]GitHub file: /skill https://github.com/user/repo/blob/main/path/SKILL.md[/dim]")
                    console.print("  [dim]Raw file:   /skill https://raw.githubusercontent.com/user/repo/main/path/SKILL.md[/dim]")
                    continue

                raw_url = _resolve_skill_url(url)
                if raw_url and raw_url.startswith("__repo__"):
                    repo = raw_url[8:]
                    console.print(f"[yellow]That's a GitHub repo, not a specific file.[/yellow]")
                    console.print("  [dim]Browse skills and use the exact file URL, e.g.:[/dim]")
                    console.print(f"  [dim]/skill https://github.com/{repo}/blob/main/code-reviewer/SKILL.md[/dim]")
                    console.print(f"  [dim]Or a raw URL directly:[/dim]")
                    console.print(f"  [dim]/skill https://raw.githubusercontent.com/{repo}/main/code-reviewer/SKILL.md[/dim]")
                    continue

                url = raw_url or url

                try:
                    resp = requests.get(url, timeout=15)
                    resp.raise_for_status()
                except requests.RequestException as e:
                    console.print(f"[yellow]Failed to download: {e}[/yellow]")
                    continue

                content = resp.text

                raw_name = url.rstrip("/").split("/")[-1]
                if raw_name.lower().replace(".md", "") in ("", "skill"):
                    parts_u = url.rstrip("/").split("/")
                    if len(parts_u) >= 2:
                        name = parts_u[-2]
                    else:
                        name = "unnamed-skill"
                else:
                    name = raw_name.replace(".md", "")

                name = os.path.basename(name.strip().rstrip("/\\"))
                if not name or name in (".", ".."):
                    name = "unnamed-skill"

                if "```" not in content and "#" not in content:
                    console.print("[yellow]Downloaded content doesn't look like a skill markdown file. Provide a raw GitHub URL pointing to a .md file.[/yellow]")
                    continue

                abs_skills_dir = os.path.abspath(CUSTOM_SKILLS_DIR)
                os.makedirs(abs_skills_dir, exist_ok=True)
                path = os.path.abspath(os.path.join(abs_skills_dir, f"{name}.md"))
                if not path.startswith(abs_skills_dir):
                    console.print("[red]Error: Invalid skill name (path traversal detected).[/red]")
                    continue
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)

                for i, s in enumerate(active_skills):
                    if s["name"] == name:
                        active_skills.pop(i)
                        break
                active_skills.append({"name": name, "content": content, "source": url})
                _save_skill_manifest()
                console.print(f"[green]Loaded custom skill: {name}[/green]")

            elif cmd.startswith("/use"):
                parts = user_input.split(None, 1)
                if len(parts) < 2:
                    if current_skill:
                        console.print(Rule(style="purple"))
                        console.print(f"[green]Deactivated skill: {current_skill['name']}[/green]")
                        current_skill = None
                    else:
                        console.print("[yellow]Usage: /use <skill-name>[/yellow]")
                        if active_skills:
                            console.print("  [dim]Loaded skills:[/dim]")
                            for s in active_skills:
                                console.print(f"  [cyan]{s['name']}[/cyan]")
                    continue
                name = parts[1].strip()
                found = [s for s in active_skills if s["name"] == name]
                if not found:
                    console.print(f"[yellow]Skill '{name}' not found. Use /skill list to see loaded skills.[/yellow]")
                else:
                    current_skill = found[0]
                    console.print(Rule(style="green"))
                    console.print(f"[green]Activated skill: {name}[/green]")
                    console.print(f"[dim]System prompt replaced with skill instructions.[/dim]")

            elif cmd.startswith("/save"):
                if not history or len(history) < 2:
                    console.print("[yellow]No response to save yet.[/yellow]")
                    continue
                parts = user_input.split(None, 1)
                fn = parts[1].strip() if len(parts) > 1 else None
                saved = _save_code_blocks(history[-1]["content"], base_dir, fn)
                if not saved:
                    console.print("[yellow]No code blocks in the last response.[/yellow]")
                else:
                    for p in saved:
                        console.print(f"[green]Saved to {p}[/green]")

            elif cmd.startswith("/export"):
                parts = user_input.split(None, 1)
                fn = parts[1].strip() if len(parts) > 1 else None
                cmd_export(history, base_dir, session_num, fn)

            elif cmd == "/cost":
                cmd_cost(history)

            elif cmd == "/diff":
                cmd_diff()

            elif cmd == "/doctor":
                cmd_doctor()

            elif cmd == "/clear":
                compaction_counter = cmd_clear(history, session_num)

            elif cmd == "/help":
                cmd_help()

            elif cmd == "/compact":
                if len(history) < 2:
                    console.print("[yellow]Need at least one exchange to compact.[/yellow]")
                    continue
                history, _ = compact_context(history, stats_dir, compaction_counter)
                compaction_counter += 1
                user_turns_since_compaction = 0

            else:
                console.print(f"[yellow]Unknown: {user_input}. Try /help.[/yellow]")
            continue

        if not os.environ.get("NVIDIA_API_KEY"):
            console.print("[yellow]Set your API key with /connect first.[/yellow]")
            continue

        if user_input.lower() == "build the plan":
            if not history:
                console.print("[yellow]No conversation to build from.[/yellow]")
                continue
            console.print()
            plan_json = plan_from_conversation(history)
            goal_summary = initial_goal_text or (history[0]["content"] if history else "")
            run_plan(plan_json, goal=goal_summary, base_dir=base_dir, session_num=session_num, stats_dir=stats_dir)
            break

        if user_turns_since_compaction >= AUTO_COMPACT_THRESHOLD:
            history, _ = compact_context(history, stats_dir, compaction_counter)
            compaction_counter += 1
            user_turns_since_compaction = 0

        history.append({"role": "user", "content": user_input})
        user_turns_since_compaction += 1

        response, usage = stream_planner_response("planner", _build_system_prompt(), format_history(history))
        history.append({"role": "assistant", "content": response, "usage": usage or {}})
        _save_history(history, session_num, initial_goal_text)


if __name__ == "__main__":
    cli()
