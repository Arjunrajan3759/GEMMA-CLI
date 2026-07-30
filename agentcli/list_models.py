"""
The NIM catalog changes fast (Gemma 3 and 3n both hit end-of-life in 2026).
Don't hardcode model slugs from docs/search results - ask the API directly.

Usage:
    export NVIDIA_API_KEY=nvapi-...
    python list_models.py
"""

import os
import requests

API_KEY = os.environ.get("NVIDIA_API_KEY")
if not API_KEY:
    raise RuntimeError("Set NVIDIA_API_KEY first")

resp = requests.get(
    "https://integrate.api.nvidia.com/v1/models",
    headers={"Authorization": f"Bearer {API_KEY}"},
    timeout=30,
)
resp.raise_for_status()
models = resp.json().get("data", [])

gemma = sorted(m["id"] for m in models if "gemma" in m["id"].lower())

if not gemma:
    print("No gemma models returned by /v1/models for this key.")
    print("Either the endpoint doesn't list all catalog models, or none are")
    print("currently entitled to your key. Cross-check against")
    print("https://build.nvidia.com/models?filters=publisher%3Agoogle by hand.")
else:
    print(f"Found {len(gemma)} live gemma model(s) for this key:\n")
    for m in gemma:
        print(f"  {m}")
