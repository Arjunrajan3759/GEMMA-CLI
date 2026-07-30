"""
Runs generated code in an isolated subprocess with a timeout.
Deliberately simple: one temp dir per task, python subprocess, capture
stdout/stderr/exit code. No network/filesystem sandboxing beyond a fresh
temp dir - good enough for a hackathon demo on trusted, self-generated code.
"""

import subprocess
import tempfile
import os
import sys

TIMEOUT_SECONDS = 15

# sys.executable is whatever interpreter is currently running THIS script -
# works on Windows (python.exe, no python3.exe by default), Mac, and Linux.
# Hardcoding "python3" breaks on Windows with exit code 9009 (command not found).
PYTHON = sys.executable


def run_python(code: str, filename: str = "solution.py", cwd: str | None = None) -> dict:
    """Write `code` to `filename` inside a temp dir (or `cwd` if given) and run it.
    Returns dict with stdout, stderr, exit_code, timed_out, filepath, dir.
    """
    work_dir = cwd or tempfile.mkdtemp(prefix="agentcli_")
    safe_filename = os.path.basename(filename) if filename else "solution.py"
    filepath = os.path.join(work_dir, safe_filename)
    with open(filepath, "w") as f:
        f.write(code)

    # Minimal safe environment excluding sensitive variables like NVIDIA_API_KEY
    safe_env_keys = {
        "PATH", "SystemRoot", "SYSTEMROOT", "PATHEXT", "WINDIR", "windir",
        "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
        "PYTHONPATH", "PYTHONIOENCODING", "PYTHONUTF8", "HOME", "USERPROFILE",
    }
    safe_env = {k: v for k, v in os.environ.items() if k in safe_env_keys}

    try:
        proc = subprocess.run(
            [PYTHON, safe_filename],
            cwd=work_dir,
            env=safe_env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
            "timed_out": False,
            "filepath": filepath,
            "dir": work_dir,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "stdout": e.stdout or "",
            "stderr": (e.stderr or "") + f"\n[TIMED OUT after {TIMEOUT_SECONDS}s]",
            "exit_code": -1,
            "timed_out": True,
            "filepath": filepath,
            "dir": work_dir,
        }


def extract_code_block(text: str) -> str:
    """Pull the first ```python ... ``` (or plain ```) block out of a model response.
    Falls back to the raw text if no fences are found.
    """
    import re
    match = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()
