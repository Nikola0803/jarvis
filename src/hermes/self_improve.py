from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent

SOURCE_FILES = [
    "src/hermes/router.py",
    "src/hermes/main.py",
    "src/hermes/telegram_bot.py",
    "src/hermes/config.py",
    "src/hermes/self_improve.py",
    "src/hermes/dashboard/app.py",
    "src/hermes/llm/deepseek_provider.py",
    "src/hermes/llm/claude_provider.py",
    "src/hermes/llm/base.py",
    "src/hermes/voice/elevenlabs_provider.py",
]

_TRIGGERS = [
    "update yourself",
    "improve yourself",
    "change your code",
    "modify your code",
    "update your code",
    "fix your code",
    "edit yourself",
    "rewrite yourself",
    "update jarvis",
    "improve jarvis",
    "change jarvis",
    "modify jarvis",
]


def is_improve_request(text: str) -> bool:
    lower = text.lower().strip()
    for trigger in _TRIGGERS:
        if lower.startswith(trigger) or lower.startswith(f"hey jarvis {trigger}") or lower.startswith(f"jarvis {trigger}"):
            return True
    return False


def _read_sources() -> dict[str, str]:
    result = {}
    for rel in SOURCE_FILES:
        p = REPO_ROOT / rel
        try:
            result[rel] = p.read_text(encoding="utf-8")
        except Exception as exc:
            log.debug("Could not read %s: %s", rel, exc)
    return result


def _run_git(*args: str) -> tuple[bool, str]:
    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Jarvis",
        "GIT_AUTHOR_EMAIL": "jarvis@hermes.local",
        "GIT_COMMITTER_NAME": "Jarvis",
        "GIT_COMMITTER_EMAIL": "jarvis@hermes.local",
    }
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=git_env,
            timeout=30,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except Exception as exc:
        return False, str(exc)


def _restart_later(delay: float = 5.0) -> None:
    log.info("self_improve: restart in %.0fs", delay)

    def _do():
        import time
        time.sleep(delay)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_do, daemon=True).start()


async def apply(instruction: str, anthropic_api_key: str, model: str = "claude-sonnet-4-5") -> str:
    import anthropic

    sources = await asyncio.to_thread(_read_sources)
    if not sources:
        return "Could not read any source files."

    files_block = "\n\n".join(
        f"### {path}\n```python\n{content}\n```"
        for path, content in sources.items()
    )

    prompt = f"""You are modifying Jarvis's own Python source code. Jarvis is a live AI assistant running on a VPS managed by systemd. Changes are written to disk and Jarvis restarts itself to load them.

Operator's instruction:
{instruction}

Current source files:
{files_block}

Respond ONLY with a valid JSON object. No markdown fences. No commentary before or after. Example shape:
{{"summary": "short commit message", "files": {{"src/hermes/router.py": "complete new file content", "src/hermes/main.py": null}}}}

Rules:
- Only include files you actually changed. Set unchanged files to null or omit them entirely.
- Always include COMPLETE file content for changed files, never diffs or partial content.
- Change only what the instruction asks for. Keep all existing functionality unless explicitly told to remove it.
- No em dashes in any text output. Use plain punctuation only.
- This is live production code running 24/7.
"""

    client = anthropic.AsyncAnthropic(api_key=anthropic_api_key)
    try:
        msg = await client.messages.create(
            model=model,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        log.error("self_improve: Claude API error: %s", exc)
        return f"Claude API error: {exc}"
    raw = msg.content[0].text.strip()

    # Strip markdown fences if Claude added them despite instructions
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.error("self_improve: bad JSON: %s\n%.500s", exc, raw)
        return f"Parse error: {exc}"

    summary = result.get("summary", "self-improvement")
    files_changed = {k: v for k, v in result.get("files", {}).items() if v is not None}

    if not files_changed:
        return "No changes were needed."

    # Write changed files to disk
    written: list[str] = []
    for rel_path, content in files_changed.items():
        full = REPO_ROOT / rel_path
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
            written.append(rel_path)
            log.info("self_improve: wrote %s", rel_path)
        except Exception as exc:
            log.error("self_improve: failed to write %s: %s", rel_path, exc)
            return f"Write failed for {rel_path}: {exc}"

    # Git commit
    await asyncio.to_thread(_run_git, "add", *written)
    commit_msg = (
        f"self-improvement: {summary}\n\n"
        "Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>\n"
        f"Claude-Session: https://claude.ai/code/session_01ExoErVpGjQsjwqYb7PBHJo"
    )
    ok, out = await asyncio.to_thread(_run_git, "commit", "-m", commit_msg)
    if ok:
        log.info("self_improve: committed: %s", summary)
    else:
        log.warning("self_improve: git commit: %s", out[:200])

    # Try push (may fail without remote credentials -- changes still take effect locally)
    ok, out = await asyncio.to_thread(_run_git, "push")
    if ok:
        log.info("self_improve: pushed to remote")
        push_note = ""
    else:
        log.warning("self_improve: push skipped: %s", out[:100])
        push_note = " Remote push skipped."

    _restart_later(5.0)
    changed = ", ".join(written)
    return f"Done. {summary}. Changed: {changed}.{push_note} Restarting in 5 seconds."
