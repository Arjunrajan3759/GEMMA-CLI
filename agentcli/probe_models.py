"""
Empirically test the live Gemma catalog against prompts shaped like your
actual 4 roles, so you pick models based on real output, not guesswork.

Usage:
    export NVIDIA_API_KEY=nvapi-...
    python probe_models.py
"""

from nim_client import call_model, COMPLETION_MODE_MODELS

# One representative prompt per role, kept short to save tokens/quota while
# still showing whether a model can actually do the job.
#
# Candidate lists trimmed to the 2 models confirmed working on this account
# (see nim_client.py comments). The others 404 here - gemma-3-12b-it and
# gemma-3-4b-it aren't entitled to this account, CodeGemma's completions
# endpoint doesn't exist on this deployment. Left as comments in case you
# get broader entitlements later and want to re-test.
PROBES = {
    "planner": {
        "system": "Break the goal into 2-3 short coding tasks. Respond as a numbered list only.",
        "user": "Goal: build a CLI to-do list with add/remove/list commands.",
        "candidates": [
            "nvidia/nemotron-3-super-120b-a12b",
            "deepseek-ai/deepseek-v4-pro",
            "google/gemma-4-31b-it",
            # "google/gemma-3-12b-it",  # 404s on this account
        ],
    },
    "writer": {
        "system": "Write a complete Python function for the task. Output only code.",
        "user": "def is_palindrome(s: str) -> bool:\n    # returns True if s is a palindrome, ignoring case and spaces",
        "candidates": [
            "google/gemma-4-31b-it",
            # "google/codegemma-1.1-7b",  # completions endpoint 404s on this deployment
            # "google/codegemma-7b",       # same
        ],
    },
    "reviewer": {
        "system": "You are a strict code reviewer. Reply with PASS or FAIL and one sentence why.",
        "user": "def add(a, b):\n    return a - b\n\nTask: function should add two numbers.",
        "candidates": [
            "google/diffusiongemma-26b-a4b-it",
            "google/gemma-4-31b-it",
            # "google/gemma-3-12b-it",  # 404s on this account
        ],
    },
    "tester": {
        "system": "Write 2 short assert statements testing this function. Output only code.",
        "user": "def add(a, b):\n    return a + b",
        "candidates": [
            "google/diffusiongemma-26b-a4b-it",
            "google/gemma-4-31b-it",
            # "google/gemma-3-4b-it",  # 404s on this account
        ],
    },
}


def main():
    for role, spec in PROBES.items():
        print(f"\n{'=' * 60}\nROLE: {role}\n{'=' * 60}")
        for model in spec["candidates"]:
            mode = "completion" if model in COMPLETION_MODE_MODELS else "chat"
            print(f"\n--- {model} ({mode}) ---")
            try:
                # temporarily hijack ROLE_MODEL_CANDIDATES-style single call
                from nim_client import _call_one
                out = _call_one(
                    model,
                    system_prompt=spec["system"],
                    user_prompt=spec["user"],
                    temperature=0.3,
                    max_tokens=200,
                    json_mode=False,
                )
                print(out.strip()[:400])
            except Exception as e:
                print(f"[FAIL] {e}")


if __name__ == "__main__":
    main()
