"""
Wire diff: what each engine actually sends to the model, for the same skill run.

Runs `ferry run` once per engine against a local scripted model (an
OpenAI-compatible server on 127.0.0.1) and a local "company website", with the
repo's real MCP servers in between. The scripted model calls fetch once, then
answers; it also attaches a Gemini-style `thought_signature` to its tool call,
so the diff shows whether each engine echoes it back. Every request body is
captured and compared.

No API key, no internet: this measures the engines, not a model. Useful as a
pre-flight before spending provider credits on the head-to-head.

Usage:
    ./gradlew installDist
    python eval_harness/wire_diff.py                 # prints the diff as markdown
    python eval_harness/wire_diff.py --out diff.md
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENGINES = ("ferryman", "koog")
SIGNATURE = "sig-wire-diff"

_PAGE = """<!doctype html><html><head><title>{name} careers</title></head><body><main>
<h1>Build "{name}" with us</h1>
<p>We are mobile-first: the Android and iOS apps are the product. Android is Kotlin with
Jetpack Compose; shared logic is Kotlin Multiplatform.</p>
<h2>Where we work</h2><ul><li>Remote-friendly across US time zones</li>
<li>Hybrid office in San Francisco, optional</li></ul>
<h2>Open roles</h2><table><tr><th>Role</th><th>Location</th></tr>
<tr><td>Senior Mobile Engineer (Android)</td><td>Remote (US)</td></tr></table>
</main></body></html>"""


# --- local servers -----------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.engine = ""
        self.requests: list[dict] = []
        self.site = ""


def _provider_handler(rec: _Recorder):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the CLI output clean
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = json.loads(raw)
            rec.requests.append({"engine": rec.engine, "bytes": len(raw), "body": body})
            messages = body.get("messages", [])
            names = [t["function"]["name"] for t in body.get("tools") or []]
            fetch = next((n for n in names if n.split("_")[-1] == "fetch" or n == "fetch"), "fetch")
            if not any(m.get("role") == "tool" for m in messages):
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": fetch, "arguments": json.dumps({"url": f"{rec.site}/acme/careers"})},
                        "extra_content": {"google": {"thought_signature": SIGNATURE}},
                    }],
                }
                finish = "tool_calls"
            else:
                message = {"role": "assistant", "content": "**Tech stack:** Jetpack Compose.\n**Sources:** " + rec.site}
                finish = "stop"
            out = json.dumps({
                "id": "chatcmpl-wire-diff", "object": "chat.completion", "created": 0, "model": body.get("model"),
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    return Handler


class _SiteHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.endswith("robots.txt"):
            body, ctype = b"User-agent: *\nAllow: /\n", "text/plain"
        else:
            body, ctype = _PAGE.format(name="Acme").encode(), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve(handler) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# --- workdir -----------------------------------------------------------------


def _mcp_config() -> dict:
    """The repo's .mcp.json, made runnable from a temp dir and allowed to fetch 127.0.0.1."""
    config = json.loads((REPO / ".mcp.json").read_text())
    for spec in config.get("mcpServers", {}).values():
        command = spec.get("command", "")
        if command.startswith("."):
            spec["command"] = str(REPO / command)
        if "mcp_server_fetch" in spec.get("args", []):
            spec["args"] = [*spec["args"], "--allow-private-ips"]
    return config


def _workdir(provider_url: str) -> Path:
    work = Path(tempfile.mkdtemp(prefix="ferry-wire-diff-"))
    (work / "ferryman").mkdir()
    (work / "ferryman" / "skills").symlink_to(REPO / "ferryman" / "skills")
    (work / "ferryman" / "config.toml").write_text(
        'default_provider = "local"\n\n[providers.local]\ntype = "openai-compatible"\n'
        f'baseUrl = "{provider_url}"\nmodel = "scripted"\napiKeyEnv = "WIRE_DIFF_KEY"\n'
    )
    (work / ".mcp.json").write_text(json.dumps(_mcp_config(), indent=2))
    return work


def capture(binary: Path, company: str = "Acme") -> list[dict]:
    """Run the skill once per engine and return every request the model server saw."""
    rec = _Recorder()
    provider = _serve(_provider_handler(rec))
    site = _serve(_SiteHandler)
    rec.site = f"http://127.0.0.1:{site.server_address[1]}"
    work = _workdir(f"http://127.0.0.1:{provider.server_address[1]}/v1")
    try:
        for engine in ENGINES:
            rec.engine = engine
            done = subprocess.run(
                [str(binary), "run", "--skill", "company-role-research", "--engine", engine, "--provider", "local",
                 "--input", json.dumps({"company": company, "role": "Senior Mobile Engineer (Android)"})],
                cwd=work, env=dict(os.environ, WIRE_DIFF_KEY="local"), capture_output=True, text=True, timeout=180,
            )
            if done.returncode != 0:
                raise RuntimeError(f"ferry run --engine {engine} failed: {done.stderr[-800:]}")
    finally:
        provider.shutdown()
        site.shutdown()
        shutil.rmtree(work, ignore_errors=True)
    return rec.requests


# --- analysis ----------------------------------------------------------------


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content)
    return ""


def _fetch_tool(body: dict) -> dict:
    tools = body.get("tools") or []
    return next((t["function"] for t in tools if t["function"]["name"] in ("fetch", "fetch_fetch")), {})


def _schema_keys(schema: dict) -> set[str]:
    """Every JSON-schema keyword used under properties, e.g. 'url.format'."""
    keys = set()
    for name, prop in (schema.get("properties") or {}).items():
        keys |= {f"{name}.{k}" for k in prop}
    return keys


def summarize(requests: list[dict]) -> dict:
    """Per engine: what its first request and its tool-result request look like."""
    out = {}
    for engine in ENGINES:
        mine = [r for r in requests if r["engine"] == engine]
        if len(mine) < 2:
            out[engine] = {"error": f"expected 2 requests, saw {len(mine)}"}
            continue
        first, second = mine[0]["body"], mine[1]["body"]
        msgs = second["messages"]
        assistant = next(m for m in msgs if m["role"] == "assistant")
        tool_msg = next(m for m in msgs if m["role"] == "tool")
        call = assistant["tool_calls"][0]
        fetch = _fetch_tool(first)
        out[engine] = {
            "requestBytes": [r["bytes"] for r in mine],
            "params": {k: v for k, v in first.items() if k not in ("messages", "tools")},
            "system": _text(first["messages"][0]["content"]),
            "user": _text(first["messages"][1]["content"]),
            "toolCount": len(first.get("tools") or []),
            "toolBytes": len(json.dumps(first.get("tools") or [])),
            "fetchName": fetch.get("name"),
            "fetchSchemaKeys": sorted(_schema_keys(fetch.get("parameters") or {})),
            "echoedAssistantContent": assistant.get("content", "<absent>"),
            "echoedArguments": call["function"]["arguments"],
            "echoesThoughtSignature": SIGNATURE in json.dumps(call),
            "toolResult": _text(tool_msg.get("content")),
        }
    return out


def render(summary: dict) -> str:
    f, k = summary["ferryman"], summary["koog"]
    if "error" in f or "error" in k:
        return f"Capture incomplete: ferryman={f.get('error', 'ok')}, koog={k.get('error', 'ok')}\n"
    dropped = sorted(set(f["fetchSchemaKeys"]) - set(k["fetchSchemaKeys"]))
    rows = [
        ("System prompt identical", "yes" if f["system"] == k["system"] else "**no**", ""),
        ("User message identical", "yes" if f["user"] == k["user"] else "**no**", ""),
        ("Request params", json.dumps(f["params"]), json.dumps(k["params"])),
        ("Request bytes (call 1, call 2)", f["requestBytes"], k["requestBytes"]),
        ("Tools advertised / bytes", f"{f['toolCount']} / {f['toolBytes']}", f"{k['toolCount']} / {k['toolBytes']}"),
        ("Fetch tool name", f["fetchName"], k["fetchName"]),
        ("Fetch schema keywords", len(f["fetchSchemaKeys"]), f"{len(k['fetchSchemaKeys'])} (dropped: {', '.join(dropped) or 'none'})"),
        ("Echoed assistant `content`", json.dumps(f["echoedAssistantContent"]), json.dumps(k["echoedAssistantContent"])),
        ("Echoed tool arguments", f"`{f['echoedArguments']}`", f"`{k['echoedArguments']}`"),
        ("Echoes Gemini `thought_signature`", f["echoesThoughtSignature"], k["echoesThoughtSignature"]),
        ("Tool result chars", len(f["toolResult"]), len(k["toolResult"])),
        ("Tool result starts", f"`{f['toolResult'][:60]!r}`", f"`{k['toolResult'][:60]!r}`"),
    ]
    lines = ["| What the model receives | ferryman (hand-rolled) | koog |", "|---|---|---|"]
    lines += [f"| {name} | {a} | {b} |" for name, a, b in rows]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diff what each engine sends to the model.")
    parser.add_argument("--binary", type=Path, default=REPO / "build" / "install" / "ferry" / "bin" / "ferry")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.binary.exists():
        print(f"ferry binary not found at {args.binary}; run ./gradlew installDist", file=sys.stderr)
        return 1
    markdown = render(summarize(capture(args.binary)))
    if args.out:
        args.out.write_text(markdown)
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
