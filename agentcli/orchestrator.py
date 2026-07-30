import json
import os
import re
import glob
import logging
from datetime import datetime

from nim_client import call_model
from executor import run_python, extract_code_block

SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "skills")
MAX_RETRIES = 3

logger = logging.getLogger(__name__)


def load_skill(name: str) -> str:
    path = os.path.join(SKILLS_DIR, f"{name}.md")
    with open(path) as f:
        return f.read()


def _extract_json(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    if raw.startswith("json"):
        raw = raw[4:].strip()
    return raw


def _call_json_planner(system: str, user_prompt: str, max_attempts: int = 2) -> dict:
    for attempt in range(1, max_attempts + 1):
        raw = call_model("planner", system_prompt=system, user_prompt=user_prompt, max_tokens=1024)
        raw = _extract_json(raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("Planner JSON parse failed (attempt %d/%d): %s", attempt, max_attempts, e)
            if attempt < max_attempts:
                user_prompt += (
                    "\n\nYour previous response was not valid JSON. "
                    "Respond with ONLY valid JSON, no markdown fences, no commentary."
                )
            else:
                raise ValueError(f"Planner did not return valid JSON after {max_attempts} attempts:\n{raw[:500]}") from e


def plan(goal: str) -> dict:
    system = load_skill("planner")
    return _call_json_planner(system, goal)


def plan_from_conversation(history: list) -> dict:
    system = load_skill("planner")
    history_lines = []
    for turn in history:
        role = turn["role"].capitalize()
        history_lines.append(f"{role}: {turn['content']}")
    history_str = "\n\n".join(history_lines)

    user_prompt = (
        "Based on the conversation history below, produce the finalized project plan "
        "in the exact JSON format specified in your instructions:\n\n"
        f"{history_str}"
    )
    return _call_json_planner(system, user_prompt)


def _call_reviewer(reviewer_sys: str, review_prompt: str, max_attempts: int = 2) -> dict:
    for attempt in range(1, max_attempts + 1):
        review_raw = call_model("reviewer", reviewer_sys, review_prompt, max_tokens=400)
        review_raw = review_raw.strip().strip("`")
        if review_raw.startswith("json"):
            review_raw = review_raw[4:].strip()
        try:
            return json.loads(review_raw)
        except json.JSONDecodeError:
            logger.warning("Reviewer JSON parse failed (attempt %d/%d)", attempt, max_attempts)
            if attempt < max_attempts:
                review_prompt += (
                    "\n\nYour previous response was not valid JSON. "
                    "Respond with ONLY valid JSON in this exact format:\n"
                    '{"passed": true/false, "feedback": "...", "style_note": "..."}'
                )
            else:
                return {"passed": False, "feedback": review_raw[:300]}
    return {"passed": False, "feedback": "Reviewer failed to produce valid JSON"}


def run_task(task: dict, project_dir: str, dependency_code: dict | None = None) -> dict:
    """Writer -> execute -> tester -> reviewer -> retry loop for one task.

    dependency_code: dict of {task_id: {"title": str, "code": str}} from completed
    dependent tasks, injected as context for the writer.
    """
    writer_sys = load_skill("writer")
    tester_sys = load_skill("tester")
    reviewer_sys = load_skill("reviewer")

    feedback = None
    code = None
    result = None

    for attempt in range(1, MAX_RETRIES + 1):
        logger.info("Task '%s' attempt %d/%d", task["title"], attempt, MAX_RETRIES)
        print(f"  [writer] attempt {attempt}/{MAX_RETRIES} ...")
        user_prompt = f"Task: {task['title']}\n{task['description']}"
        if dependency_code:
            dep_blocks = []
            for dep_id, dep_info in dependency_code.items():
                dep_blocks.append(
                    f"### Code from dependency task #{dep_id}: {dep_info['title']}\n"
                    f"```python\n{dep_info['code']}\n```"
                )
            user_prompt += "\n\n" + "\n\n".join(dep_blocks)
        if feedback:
            user_prompt += f"\n\nPrevious attempt feedback to fix:\n{feedback}"

        writer_out = call_model("writer", writer_sys, user_prompt, max_tokens=1500)
        code = extract_code_block(writer_out)
        logger.debug("Writer code:\n%s", code)

        print("  [executor] running generated code ...")
        result = run_python(code, filename="solution.py", cwd=project_dir)
        logger.debug("Execution exit_code=%d stdout=%r stderr=%r", result["exit_code"], result["stdout"], result["stderr"])

        print("  [tester] generating + running test script ...")
        tester_prompt = (
            f"Task: {task['title']}\n{task['description']}\n\n"
            f"Writer's code:\n```python\n{code}\n```"
        )
        if dependency_code:
            dep_blocks = []
            for dep_id, dep_info in dependency_code.items():
                dep_blocks.append(
                    f"### Code from dependency task #{dep_id}: {dep_info['title']}\n"
                    f"```python\n{dep_info['code']}\n```"
                )
            tester_prompt += "\n\n" + "\n\n".join(dep_blocks)
        tester_out = call_model("tester", tester_sys, tester_prompt, max_tokens=800)
        test_code = extract_code_block(tester_out)
        test_result = run_python(test_code, filename="test_solution.py", cwd=project_dir)
        tests_passed = "TESTS_PASSED" in test_result["stdout"]
        logger.debug("Tests passed=%s stdout=%r", tests_passed, test_result["stdout"])

        print("  [reviewer] judging ...")
        review_prompt = (
            f"Task: {task['title']}\n{task['description']}\n\n"
            f"Code:\n```python\n{code}\n```\n\n"
            f"Run stdout: {result['stdout']!r}\nRun stderr: {result['stderr']!r}\n"
            f"Run exit_code: {result['exit_code']}\n"
            f"Test run stdout: {test_result['stdout']!r}"
        )
        review = _call_reviewer(reviewer_sys, review_prompt)

        passed = review.get("passed", False) and result["exit_code"] == 0 and tests_passed
        style_note = str(review.get("style_note", "")).strip()
        if passed:
            print(f"  [done] task '{task['title']}' passed on attempt {attempt}")
            return {
                "status": "passed",
                "attempts": attempt,
                "code": code,
                "filepath": result["filepath"],
                "style_note": style_note,
            }

        feedback = review.get("feedback", "Tests or execution failed; fix the issue.")
        print(f"  [retry] {feedback}")
        logger.info("Task '%s' attempt %d failed: %s", task["title"], attempt, feedback)

    return {
        "status": "failed",
        "attempts": MAX_RETRIES,
        "code": code,
        "filepath": result["filepath"] if result else None,
        "style_note": style_note if "style_note" in locals() else "",
    }


def get_stats_dir(base_dir: str = "./outputs") -> str:
    candidates = [
        "Session_summaries",
        os.path.join("..", "Session_summaries"),
        os.path.join(base_dir, "Session_summaries"),
    ]
    for cand in candidates:
        if os.path.isdir(cand):
            return os.path.abspath(cand)
    target = os.path.abspath("Session_summaries")
    os.makedirs(target, exist_ok=True)
    return target


def get_next_session_number(base_dir: str = "./output", stats_dir: str = None) -> int:
    target_dir = stats_dir or get_stats_dir(base_dir)
    if not os.path.exists(target_dir):
        return 1
    pattern = os.path.join(target_dir, "Session-stats-*.md")
    existing_files = glob.glob(pattern)
    nums = []
    for f in existing_files:
        basename = os.path.basename(f)
        match = re.search(r"Session-stats-(\d+)\.md", basename)
        if match:
            nums.append(int(match.group(1)))
    return max(nums) + 1 if nums else 1


def write_session_stats(base_dir: str, session_num: int, goal: str, plan_json: dict, results: dict, timestamp: str, stats_dir: str = None) -> None:
    target_dir = stats_dir or get_stats_dir(base_dir)
    filename = f"Session-stats-{session_num}.md"
    filepath = os.path.join(target_dir, filename)
    os.makedirs(target_dir, exist_ok=True)

    lines = [
        f"# Session Stats #{session_num}\n",
        f"- **Session Number**: {session_num}",
        f"- **Timestamp**: {timestamp}",
        f"- **Original Goal**: {goal}\n",
        "## Task Execution Results\n",
    ]

    tasks = plan_json.get("tasks", [])
    for task in tasks:
        tid = task["id"]
        title = task["title"]
        if tid in results:
            r = results[tid]
            status = r.get("status", "unknown")
            attempts = r.get("attempts", 0)
            style_note = r.get("style_note", "").strip()
            if status == "passed" and attempts == 1:
                note = "Passed cleanly on attempt 1."
            elif status == "passed":
                note = f"Passed on attempt {attempts} after resolving initial reviewer/tester feedback."
            elif status == "failed":
                note = f"Failed after maximum retries ({attempts} attempts)."
            else:
                note = "Errored during execution."

            lines.append(f"### Task #{tid}: {title}")
            lines.append(f"- **Status**: `{status}`")
            lines.append(f"- **Attempts**: {attempts}")
            lines.append(f"- **Note**: {note}")
            if style_note:
                lines.append(f"- **Style Note**: {style_note}")
            lines.append("")
        else:
            lines.append(f"### Task #{tid}: {title}")
            lines.append("- **Status**: `pending`\n")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _collect_dependency_code(task: dict, completed_results: dict, tasks: list) -> dict | None:
    dep_ids = task.get("depends_on", [])
    if not dep_ids:
        return None
    dep_code = {}
    title_map = {t["id"]: t["title"] for t in tasks}
    for dep_id in dep_ids:
        dep_result = completed_results.get(dep_id)
        if dep_result and dep_result.get("code"):
            dep_code[dep_id] = {
                "title": title_map.get(dep_id, f"task_{dep_id}"),
                "code": dep_result["code"],
            }
    return dep_code if dep_code else None


def run_plan(plan_json: dict, goal: str, base_dir: str = "./outputs", session_num: int = 1, stats_dir: str = None) -> dict:
    raw_project_name = str(plan_json.get("project_name", "project")).strip()
    project_name = os.path.basename(raw_project_name.rstrip("/\\"))
    if not project_name or project_name in (".", ".."):
        project_name = "project"

    abs_base = os.path.abspath(base_dir)
    project_dir = os.path.abspath(os.path.join(abs_base, project_name))
    if not project_dir.startswith(abs_base) or project_dir == abs_base:
        raise ValueError(f"Invalid project_name escaping output directory: {raw_project_name}")

    tasks = plan_json["tasks"]
    target_stats_dir = stats_dir or get_stats_dir(base_dir)

    print(f"Project: {project_name}")
    for t in tasks:
        print(f"  #{t['id']} {t['title']} (depends_on={t.get('depends_on', [])})")
    print()

    os.makedirs(project_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = {}

    for task in tasks:
        print(f"=== Task #{task['id']}: {task['title']} ===")
        safe_task_id = os.path.basename(str(task['id']).strip().rstrip("/\\"))
        if not safe_task_id or safe_task_id in (".", ".."):
            safe_task_id = "0"

        task_dir = os.path.abspath(os.path.join(project_dir, f"task_{safe_task_id}"))
        if not task_dir.startswith(project_dir):
            raise ValueError(f"Invalid task ID escaping project directory: {task['id']}")

        os.makedirs(task_dir, exist_ok=True)

        dep_code = _collect_dependency_code(task, results, tasks)
        if dep_code:
            deps_list = list(dep_code.keys())
            logger.info("Injecting dependency code from tasks %s into '%s'", deps_list, task["title"])
            print(f"  [deps] using code from task(s) #{', #'.join(str(d) for d in deps_list)}")

        try:
            res = run_task(task, task_dir, dependency_code=dep_code)
            results[task["id"]] = res
        except Exception as e:
            logger.exception("Task '%s' crashed", task["title"])
            print(f"  [error] task '{task['title']}' crashed: {e}")
            results[task["id"]] = {"status": "error", "attempts": 0, "code": None, "filepath": None}

        write_session_stats(base_dir, session_num, goal, plan_json, results, timestamp, stats_dir=target_stats_dir)
        print()

    print("=== Summary ===")
    for tid, r in results.items():
        print(f"  Task #{tid}: {r['status']} (attempts={r['attempts']}) -> {r['filepath']}")

    write_session_stats(base_dir, session_num, goal, plan_json, results, timestamp, stats_dir=target_stats_dir)
    return results


def run(goal: str, base_dir: str = "./outputs") -> None:
    print(f"[planner] designing plan for: {goal!r}\n")
    plan_json = plan(goal)
    stats_dir = get_stats_dir(base_dir)
    session_num = get_next_session_number(base_dir, stats_dir=stats_dir)
    run_plan(plan_json, goal=goal, base_dir=base_dir, session_num=session_num, stats_dir=stats_dir)


if __name__ == "__main__":
    import sys
    goal = " ".join(sys.argv[1:]) or "Write a function that checks if a string is a palindrome"
    run(goal)
