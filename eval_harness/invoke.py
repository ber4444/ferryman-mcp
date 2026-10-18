"""
Thin adapter: call a ferryman skill and return its raw output plus a tool-call
trace.

Two invocation modes, selected at runtime:

  * "http" (default): POSTs to a running `ferry serve` HTTP channel. This is
    the programmatic entry point the eval-harness contract guarantees — both
    CLI and HTTP hit the same Orchestrator.runSkill, so HTTP is representative.
  * "subprocess": shells out to the installed `ferry run` CLI. Used when no
    server is running (e.g. CI without a long-lived process).

Both channels take an optional ``engine`` — ``"ferryman"`` (the hand-rolled
orchestrator) or ``"koog"`` (the same skill rebuilt on JetBrains Koog). When one
is requested, the engine ferry reports back must match it: a result from the
wrong engine (an older server that ignores the field) is an error, never a
silently mislabeled row.

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
    # Real token counts from the provider's `usage` block, threaded through the
    # Kotlin side (SkillResult → channel). When present, cost is exact; when
    # None (provider reported no usage, or an error row), the scorecard falls
    # back to the chars/4 estimate.
    input_tokens: int | None = None
    output_tokens: int | None = None
    # Character counts of the input payload and output text. Used only as the
    # fallback token estimate when real `usage` data is absent.
    input_chars: int = 0
    output_chars: int = 0
    # Agent host that produced the output, as ferry reported it. None when an
    # older binary/server didn't say (only acceptable when no engine was requested).
    engine: str | None = None

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
    engine: str | None = None,
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
        engine: optional agent host ("ferryman" | "koog"); None keeps ferry's default
            and skips the engine check.

    Returns:
        InvocationResult with the skill's output text and routing metadata.
    """
    import time

    started = time.monotonic()
    result = _dispatch(skill_input, skill, provider, mode, http_url, binary, engine)
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
    engine: str | None = None,
) -> InvocationResult:
    """Select and run the invocation mode. Timed by the caller."""
    if mode == "auto":
        try:
            return _invoke_http(skill_input, skill, provider, http_url, engine)
        except (ConnectionError, RuntimeError) as http_err:
            try:
                return _invoke_subprocess(skill_input, skill, provider, binary, engine)
            except FileNotFoundError as e:
                raise RuntimeError(
                    f"Neither HTTP channel ({http_url}) nor ferry binary ({binary}) "
                    f"available. HTTP error: {http_err}; subprocess error: {e}.",
                ) from e
    elif mode == "http":
        return _invoke_http(skill_input, skill, provider, http_url, engine)
    elif mode == "subprocess":
        return _invoke_subprocess(skill_input, skill, provider, binary, engine)
    raise ValueError(f"unknown mode: {mode!r} (expected 'http', 'subprocess', or 'auto')")


def _invoke_http(
    skill_input: dict[str, Any],
    skill: str,
    provider: str | None,
    http_url: str,
    engine: str | None = None,
) -> InvocationResult:
    """POST to the HTTP channel's /invoke endpoint."""
    # urllib is stdlib — no dependency required for the default adapter.
    import urllib.error
    import urllib.request

    payload = {"skill": skill, "input": json.dumps(skill_input)}
    if provider:
        payload["provider"] = provider
    if engine:
        payload["engine"] = engine
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
    # RuntimeError (not a per-case error) so auto mode falls back to the binary,
    # which is the build that has the requested engine.
    _check_engine(engine, body.get("engine"), channel=f"HTTP channel at {http_url}", exc=RuntimeError)
    return InvocationResult(
        output=body.get("output", ""),
        provider=body.get("provider", "unknown"),
        model=body.get("model", "unknown"),
        tool_calls=body.get("toolCalls", []),
        input_tokens=body.get("inputTokens"),
        output_tokens=body.get("outputTokens"),
        engine=body.get("engine"),
    )


def _invoke_subprocess(
    skill_input: dict[str, Any],
    skill: str,
    provider: str | None,
    binary: str,
    engine: str | None = None,
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
    if engine:
        cmd += ["--engine", engine]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        # A hung ferry process used to crash the whole eval batch. Record it
        # as a per-case error so the runner's loop continues to the next case.
        return InvocationResult(
            output="",
            provider=provider or "unknown",
            model="unknown",
            error=f"ferry subprocess timed out after 120s (cmd: {' '.join(cmd)})",
        )
    if completed.returncode != 0:
        return InvocationResult(
            output="",
            provider=provider or "unknown",
            model="unknown",
            error=completed.stderr.strip() or f"ferry exited {completed.returncode}",
        )
    # The CLI prints a `{"_meta":{...}}` JSON line to stdout first (provider,
    # model, token counts), then the answer text. Split them off so we carry
    # real token counts instead of a chars/4 estimate. If the first line isn't
    # a _meta line (old binary / unexpected output) we fall back to treating the
    # whole stdout as output, matching the pre-metadata behaviour.
    result = _parse_subprocess_output(completed.stdout, provider)
    try:
        _check_engine(engine, result.engine, channel=f"ferry binary {resolved}", exc=ValueError)
    except ValueError as e:
        return InvocationResult(output="", provider=result.provider, model=result.model, error=str(e))
    return result


def _check_engine(requested: str | None, reported: str | None, *, channel: str, exc: type[Exception]) -> None:
    """Raise ``exc`` if an engine was requested and ferry ran (or reported) a different one."""
    if requested and reported != requested:
        raise exc(
            f"{channel} ran engine {reported or '(unreported — older build?)'!r}, "
            f"but {requested!r} was requested. Rebuild with `./gradlew installDist` "
            f"or restart `ferry serve`."
        )


def _parse_subprocess_output(
    stdout: str,
    provider: str | None,
) -> InvocationResult:
    """
    Split ferry CLI stdout into routing metadata and the answer text.

    The CLI emits a ``{"_meta":{...}}`` JSON line carrying provider, model and
    real token counts, followed by the answer text. It is *not* guaranteed to be
    the very first line: the JVM logger prints a
    ``kotlin-logging: initializing...`` banner to stdout ahead of it. So we scan
    the leading lines for the _meta payload, discard any noise lines before it,
    and treat everything after it as the answer. When no _meta line is present
    (older binary / unexpected output) the whole stdout is the answer so we
    degrade gracefully.
    """
    lines = stdout.splitlines()
    for i, line in enumerate(lines):
        meta = _maybe_meta(line)
        if meta is None:
            continue
        meta_obj = meta["_meta"]
        rest = "\n".join(lines[i + 1 :])
        return InvocationResult(
            output=rest.strip(),
            provider=meta_obj.get("provider", provider or "unknown"),
            model=meta_obj.get("model", "unknown"),
            tool_calls=meta_obj.get("toolCalls", []),
            input_tokens=meta_obj.get("inputTokens"),
            output_tokens=meta_obj.get("outputTokens"),
            engine=meta_obj.get("engine"),
        )
    # No metadata line anywhere — treat the entire stdout as output (legacy path).
    return InvocationResult(
        output=stdout.strip(),
        provider=provider or "unknown",
        model="unknown",
    )


def _maybe_meta(line: str) -> dict[str, Any] | None:
    """Return the parsed _meta object if ``line`` is a ``{"_meta":{...}}`` JSON line."""
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("_meta"), dict):
        return None
    return parsed
