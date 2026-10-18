"""Tests for wire_diff's analysis on canned request bodies — no binary, no servers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval_harness import wire_diff


def _req(engine, fetch_name, schema, assistant, tool_content, params):
    tools = [{"type": "function", "function": {"name": fetch_name, "description": "d", "parameters": schema}}]
    first = {"model": "m", **params, "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}], "tools": tools}
    second = dict(first, messages=first["messages"] + [assistant, {"role": "tool", "tool_call_id": "call_1", "content": tool_content}])
    return [{"engine": engine, "bytes": 100, "body": first}, {"engine": engine, "bytes": 120, "body": second}]


def _call(name, args, signature):
    call = {"id": "call_1", "type": "function", "function": {"name": name, "arguments": args}}
    if signature:
        call["extra_content"] = {"google": {"thought_signature": wire_diff.SIGNATURE}}
    return call


def test_summarize_and_render_report_each_difference():
    full = {"properties": {"url": {"type": "string", "format": "uri", "title": "Url"}}, "required": ["url"]}
    lean = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}
    requests = _req(
        "ferryman", "fetch_fetch", full,
        {"role": "assistant", "content": "", "tool_calls": [_call("fetch_fetch", '{"url": "u"}', True)]},
        "page text", {"max_tokens": 2048},
    ) + _req(
        "koog", "fetch", lean,
        {"role": "assistant", "tool_calls": [_call("fetch", '{"url":"u"}', False)]},
        json.dumps({"content": [{"text": "page text"}]}), {"max_completion_tokens": 2048, "stream": False},
    )
    s = wire_diff.summarize(requests)
    assert s["ferryman"]["echoesThoughtSignature"] is True
    assert s["koog"]["echoesThoughtSignature"] is False
    assert s["koog"]["echoedAssistantContent"] == "<absent>"
    assert s["ferryman"]["fetchSchemaKeys"] == ["url.format", "url.title", "url.type"]
    md = wire_diff.render(s)
    assert "dropped: url.format, url.title" in md
    assert "| System prompt identical | yes |" in md
    assert "max_completion_tokens" in md


def test_render_says_so_when_a_capture_is_incomplete():
    s = wire_diff.summarize([{"engine": "ferryman", "bytes": 1, "body": {}}])
    assert "Capture incomplete" in wire_diff.render(s)


def test_mcp_config_is_made_runnable_from_a_temp_dir():
    config = wire_diff._mcp_config()
    fetch = config["mcpServers"]["fetch"]
    assert Path(fetch["command"]).is_absolute()
    assert fetch["args"][-1] == "--allow-private-ips"
    assert "--allow-private-ips" not in config["mcpServers"]["filesystem"]["args"]
