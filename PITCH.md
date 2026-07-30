# GemCode — Pitch & Vision

> **Autonomous, Verified Software Engineering Powered by Google Gemma & NVIDIA NIM**

---

## Executive Summary

**GemCode** is a terminal-native, multi-agent AI coding framework designed to bridge the gap between single-turn AI code generation and true autonomous software development.

While conventional AI code assistants generate untested code snippets and rely on the developer to copy, execute, debug, and re-prompt, **GemCode introduces a closed-loop, multi-agent engineering lifecycle**:

$$\text{Planner} \longrightarrow \text{Writer} \longrightarrow \text{Executor} \longrightarrow \text{Tester} \longrightarrow \text{Reviewer}$$

Every task created by the Planner is automatically written, executed in an isolated environment, verified against auto-generated test suites, and strictly reviewed for correctness before completion.

---

## Why We Built GemCode

### The Problem with Current AI Coding Tools

1. **The "Copy-Paste-Debug" Fatigue:** Most AI coding tools operate as passive autocomplete extensions. Developers spend a significant portion of their time taking AI output, creating files, executing scripts, reading stack traces, and manually feeding errors back to the AI.
2. **Untested & Hallucinated Code:** Large language models regularly produce code that looks syntactically plausible but fails silently at runtime or breaks edge cases.
3. **Vendor Lock-in & High Costs:** Existing autonomous agent tools lock developers into proprietary, expensive closed models with high per-token latency.
4. **Lack of Specialized Agent Roles:** A single generic prompt is expected to handle architectural design, low-level implementation, unit test generation, and strict code review simultaneously—resulting in degraded reasoning across all tasks.

---

## What Sets GemCode Apart

### 1. Specialized Multi-Agent Roles (Powered by Gemma & NVIDIA NIM)
Instead of forcing one model to do everything, GemCode routes tasks to specialized Gemma models fine-tuned and optimized for specific software engineering sub-tasks via NVIDIA NIM:

- **The Architect / Planner (`google/gemma-4-31b-it`):** Analyzes developer intent, conducts interactive design discussions, and generates a structured Task DAG (Directed Acyclic Graph).
- **The Engineer / Writer (`google/gemma-4-31b-it`):** Focuses exclusively on writing modular, standard-compliant implementation code.
- **The QA Engineer / Tester (`google/diffusiongemma-26b-a4b-it`):** Generates standalone assertion test scripts independently to validate functionality.
- **The Code Reviewer (`google/diffusiongemma-26b-a4b-it`):** Evaluates runtime stdout, stderr, exit codes, and test results to issue strict `PASS` or `FAIL` decisions with corrective feedback.

### 2. Autonomous Closed-Loop Verification
GemCode does not present code to the developer until it has been empirically proven to work. 
- The **Executor** runs generated code in an isolated local subprocess with a 15-second timeout.
- If execution or test suites fail, the **Reviewer** feeds exact stack traces back to the **Writer** for automated retry attempts (up to 3 retries per task).

### 3. Delightful Terminal UI (TUI) with Real-Time Streaming
Built from the ground up using [Rich](https://github.com/Textualize/rich), GemCode delivers a state-of-the-art terminal interface:
- **Token-by-Token Streaming:** Watch the AI think and write in real time.
- **Live Diagnostics & Metrics:** Built-in `/cost` tracking, live timing indicators, and `/doctor` diagnostics.
- **Context Compaction:** `/compact` summarizes long architectural discussions into dense markdown summaries, eliminating context window overflow.

### 4. Open & Extensible Skill System
Developers can inject customized engineering rules, style guides, or API specifications on the fly using simple slash commands:
```bash
/skill https://github.com/user/repo/blob/main/api-design/SKILL.md
```
GemCode automatically parses public GitHub URLs into raw skill instructions, adapting the Planner’s system prompt dynamically.

### 5. Security & Hygiene First
- **Environment Isolation:** The code execution subprocess strips sensitive API keys (e.g. `NVIDIA_API_KEY`) from the execution environment, preventing generated code from inspecting or exfiltrating host credentials.
- **Path Traversal Protection:** All project names, task IDs, and output paths are sanitized to prevent directory escape vulnerabilities.
- **Local Control:** All project outputs land cleanly in `./outputs/`, giving the developer complete control over generated artifacts.

---

## Target Audience & Use Cases

- **Hackathon Builders & Rapid Prototypers:** Scaffold and verify full CLI applications and microservices in minutes.
- **Backend Engineers:** Offload boilerplate writing, unit test creation, and bug remediation to a self-correcting agent loop.
- **Open-Source Developers:** Standardize code review and system prompt guidelines via reusable GitHub skill files.

---

## Summary

GemCode transforms the terminal into an autonomous engineering environment. By combining **Google's Gemma models**, **NVIDIA NIM's fast inference**, and a **closed-loop verification loop**, GemCode proves that AI coding agents can be fast, secure, accurate, and open-source.
