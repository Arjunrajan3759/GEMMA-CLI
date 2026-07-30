import json
import os
import time

import requests

NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NIM_COMPLETIONS_URL = "https://integrate.api.nvidia.com/v1/completions"
NIM_MODELS_URL = "https://integrate.api.nvidia.com/v1/models"


def _get_api_key():
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError(
            "NVIDIA_API_KEY not set. Use /connect in the interactive CLI, "
            "or set the NVIDIA_API_KEY environment variable."
        )
    return key


class NimError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


COMPLETION_MODE_MODELS = {
    "google/codegemma-7b",
    "google/codegemma-1.1-7b",
    "google/gemma-2b",
    "google/recurrentgemma-2b",
}

ROLE_MODEL_CANDIDATES = {
    "planner":  ["google/gemma-4-31b-it", "google/diffusiongemma-26b-a4b-it"],
    "writer":   ["google/gemma-4-31b-it", "google/diffusiongemma-26b-a4b-it"],
    "reviewer": ["google/gemma-4-31b-it", "google/diffusiongemma-26b-a4b-it"],
    "tester":   ["google/diffusiongemma-26b-a4b-it", "google/gemma-4-31b-it"],
}

DEFAULT_TIMEOUT = 30


def _build_payload(model, system_prompt, user_prompt, temperature, max_tokens, json_mode, stream=False):
    if model in COMPLETION_MODE_MODELS:
        merged_prompt = f"{system_prompt}\n\n{user_prompt}\n"
        return {"model": model, "prompt": merged_prompt, "temperature": temperature, "max_tokens": max_tokens}, NIM_COMPLETIONS_URL, "completion"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload, NIM_URL, "chat"


def _call_one(model, system_prompt, user_prompt, temperature, max_tokens, json_mode, timeout=DEFAULT_TIMEOUT):
    api_key = _get_api_key()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"}

    payload, url, mode = _build_payload(model, system_prompt, user_prompt, temperature, max_tokens, json_mode, stream=False)

    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise NimError(resp.status_code, f"{model}: {resp.text[:300]}")

    data = resp.json()
    if mode == "completion":
        return data["choices"][0]["text"], data.get("usage", {})
    return data["choices"][0]["message"]["content"], data.get("usage", {})


def _call_one_stream(model, system_prompt, user_prompt, temperature, max_tokens, json_mode, timeout=DEFAULT_TIMEOUT):
    api_key = _get_api_key()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"}

    payload, url, mode = _build_payload(model, system_prompt, user_prompt, temperature, max_tokens, json_mode, stream=True)

    resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=timeout)
    if resp.status_code != 200:
        raise NimError(resp.status_code, f"{model}: {resp.text[:300]}")

    usage = None

    if mode == "completion":
        for line in resp.iter_lines():
            if not line:
                continue
            line = line.decode("utf-8")
            if line.startswith("data: "):
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                chunk = json.loads(data_str)
                choices = chunk.get("choices", [])
                if choices and "text" in choices[0]:
                    yield choices[0]["text"], None
                if "usage" in chunk:
                    usage = chunk["usage"]
        if usage:
            yield "", usage
        return

    for line in resp.iter_lines():
        if not line:
            continue
        line = line.decode("utf-8")
        if line.startswith("data: "):
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            chunk = json.loads(data_str)
            if "usage" in chunk:
                usage = chunk["usage"]
            choices = chunk.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                if "content" in delta:
                    yield delta["content"], None

    if usage:
        yield "", usage


def _call_with_retries(model, system_prompt, user_prompt, temperature, max_tokens, json_mode, max_attempts=3):
    last_error = None
    current_timeout = DEFAULT_TIMEOUT
    timeout_attempts = 0

    for attempt in range(1, max_attempts + 1):
        try:
            return _call_one(model, system_prompt, user_prompt, temperature, max_tokens, json_mode, timeout=current_timeout)
        except NimError as e:
            last_error = e
            if e.status_code < 500:
                raise
            if attempt < max_attempts:
                time.sleep(1.5 * attempt)
        except requests.exceptions.Timeout as e:
            last_error = e
            timeout_attempts += 1
            if timeout_attempts >= 2:
                raise
            current_timeout = 60
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < max_attempts:
                time.sleep(1.5 * attempt)

    raise last_error


def call_model(role, system_prompt, user_prompt, temperature=0.3, max_tokens=2048, json_mode=False):
    if role not in ROLE_MODEL_CANDIDATES:
        raise ValueError(f"Unknown role '{role}'. Valid roles: {list(ROLE_MODEL_CANDIDATES)}")

    errors = []
    for model in ROLE_MODEL_CANDIDATES[role]:
        try:
            text, usage = _call_with_retries(model, system_prompt, user_prompt, temperature, max_tokens, json_mode)
            return text
        except Exception as e:
            errors.append(str(e))
            continue

    raise RuntimeError(
        f"All model candidates failed for role '{role}':\n" + "\n".join(errors)
    )


def call_model_stream(role, system_prompt, user_prompt, temperature=0.3, max_tokens=2048, json_mode=False):
    if role not in ROLE_MODEL_CANDIDATES:
        raise ValueError(f"Unknown role '{role}'. Valid roles: {list(ROLE_MODEL_CANDIDATES)}")

    errors = []
    for model in ROLE_MODEL_CANDIDATES[role]:
        try:
            yield from _call_one_stream(model, system_prompt, user_prompt, temperature, max_tokens, json_mode)
            return
        except Exception as e:
            errors.append(str(e))
            continue

    raise RuntimeError(
        f"All model candidates failed for role '{role}':\n" + "\n".join(errors)
    )


if __name__ == "__main__":
    for role, candidates in ROLE_MODEL_CANDIDATES.items():
        try:
            out = call_model(
                role,
                system_prompt="You are a helpful assistant. Reply in one short sentence.",
                user_prompt="Say hello and name yourself.",
                max_tokens=64,
            )
            print(f"[OK]   {role:10s}: {out.strip()}")
        except Exception as e:
            print(f"[FAIL] {role:10s}: {e}")
