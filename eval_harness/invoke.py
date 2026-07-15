"""
Thin adapter: call a ferryman skill and return its raw output plus a tool-call
trace.

Two invocation modes, selected at runtime:

  * "http" (default): POSTs to a running `ferry serve` HTTP channel. This is
    the programmatic entry point the eval-harness contract guarantees — both
    CLI and HTTP hit the same Orchestrator.runSkill, so HTTP is representative.
  * "subprocess": shells out to the installed `ferry run` CLI. Used when no
    server is running (e.g. CI without a long-lived process).

The harness degrades gracefully: if neither a server nor the binary is
available, invoke() raises a clear error rather than returning fake output —
the hard rule is "never fabricate what it found".
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_SKILL = "company-role-research"
DEFAULT_HTTP_URL = os.environ.get("FERRY_HTTP_URL", "http://localhost:8080")
DEFAULT_BINARY = os.environ.get("FERRY_BINARY", "ferry")


@dataclass
class InvocationResult:
    """Raw output of one skill invocation."""

    output: str
    provider: str
    model: str
    tool_calls: list[str] = field(default_factory=list)
    latency_ms: int | None = None
    error: str | None = None
    # Character counts of the input payload and output text. Used to estimate
    # token counts (and thus cost) until real `usage` data flows through the
    # Kotlin providers → SkillResult → InvokeResponse. Marked estimated downstream.
    input_chars: int = 0
    output_chars: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None


def invoke(
    skill_input: dict[str, Any],
    *,
    skill: str = DEFAULT_SKILL,
    provider: str | None = None,
    mode: str = "auto",
    http_url: str = DEFAULT_HTTP_URL,
    binary: str = DEFAULT_BINARY,
) -> InvocationResult:
    """
    Invoke a ferryman skill and return its raw output.

    Args:
        skill_input: the input dict passed to the skill (e.g. {"company": ..., "role": ...}).
        skill: the skill name to run.
        provider: optional provider id override; None uses the skill's hint or config default.
        mode: "http" | "subprocess" | "auto" (auto tries http then falls back to subprocess).
        http_url: base URL of a running `ferry serve` instance.
        binary: name/path of the installed ferry CLI for subprocess mode.

    Returns:
        InvocationResult with the skill's output text and routing metadata.
    """
    import time

    started = time.monotonic()
    result = _dispatch(skill_input, skill, provider, mode, http_url, binary)
    result.latency_ms = int((time.monotonic() - started) * 1000)
    # Record char counts so the scorecard can estimate cost. The input payload
    # is what we sent (serialized); the output is what came back.
    result.input_chars = len(json.dumps(skill_input))
    result.output_chars = len(result.output)
    return result


def _dispatch(
    skill_input: dict[str, Any],
    skill: str,
    provider: str | None,
    mode: str,
    http_url: str,
    binary: str,
) -> InvocationResult:
    """Select and run the invocation mode. Timed by the caller."""
    if mode == "auto":
        try:
            return _invoke_http(skill_input, skill, provider, http_url)
        except (ConnectionError, RuntimeError) as http_err:
            try:
                return _invoke_subprocess(skill_input, skill, provider, binary)
            except FileNotFoundError as e:
                raise RuntimeError(
                    f"Neither HTTP channel ({http_url}) nor ferry binary ({binary}) "
                    f"available. HTTP error: {http_err}; subprocess error: {e}.",
                ) from e
    elif mode == "http":
        return _invoke_http(skill_input, skill, provider, http_url)
    elif mode == "subprocess":
        return _invoke_subprocess(skill_input, skill, provider, binary)
    raise ValueError(f"unknown mode: {mode!r} (expected 'http', 'subprocess', or 'auto')")


def _invoke_http(
    skill_input: dict[str, Any],
    skill: str,
    provider: str | None,
    http_url: str,
) -> InvocationResult:
    """POST to the HTTP channel's /invoke endpoint."""
    # urllib is stdlib — no dependency required for the default adapter.
    import urllib.error
    import urllib.request

    payload = {"skill": skill, "input": json.dumps(skill_input)}
    if provider:
        payload["provider"] = provider
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{http_url.rstrip('/')}/invoke",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        raise ConnectionError(f"ferry HTTP channel not reachable at {http_url}: {e}") from e
    return InvocationResult(
        output=body.get("output", ""),
        provider=body.get("provider", "unknown"),
        model=body.get("model", "unknown"),
        tool_calls=body.get("toolCalls", []),
    )


def _invoke_subprocess(
    skill_input: dict[str, Any],
    skill: str,
    provider: str | None,
    binary: str,
) -> InvocationResult:
    """Shell out to the installed ferry CLI's `run` command."""
    # Resolve the binary: check PATH first, then the Gradle install location.
    resolved = binary
    if shutil.which(binary) is None:
        gradle_path = Path(__file__).resolve().parent.parent / "build" / "install" / "ferry" / "bin" / "ferry"
        if gradle_path.exists():
            resolved = str(gradle_path)
        else:
            raise FileNotFoundError(f"ferry binary not found on PATH or at {gradle_path}")
    cmd = [resolved, "run", "--skill", skill, "--input", json.dumps(skill_input)]
    if provider:
        cmd += ["--provider", provider]
    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if completed.returncode != 0:
        return InvocationResult(
            output="",
            provider=provider or "unknown",
            model="unknown",
            error=completed.stderr.strip() or f"ferry exited {completed.returncode}",
        )
    # The CLI prints the output text; routing metadata is in logs/routing.jsonl.
    return InvocationResult(
        output=completed.stdout.strip(),
        provider=provider or "unknown",
        model="unknown",
    )
