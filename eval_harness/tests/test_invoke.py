"""
Tests for invoke.py: the subprocess _meta metadata line is parsed into real
token counts, and legacy output (no _meta line) degrades gracefully. These are
hermetic — no live ferry binary, no HTTP server, no API keys.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from eval_harness import invoke as invoke_mod


# --- subprocess _meta parsing ----------------------------------------------


def test_parse_subprocess_output_reads_tokens_from_meta_line():
    """A leading {"_meta":{...}} line carries provider/model/tokens; the rest is output."""
    stdout = (
        '{"_meta":{"provider":"zai-glm","model":"glm-5.2","inputTokens":142,"outputTokens":37}}\n'
        "This is the answer text.\nSecond line of it."
    )
    result = invoke_mod._parse_subprocess_output(stdout, provider=None)

    assert result.output == "This is the answer text.\nSecond line of it."
    assert result.provider == "zai-glm"
    assert result.model == "glm-5.2"
    assert result.input_tokens == 142
    assert result.output_tokens == 37
    assert result.error is None


def test_parse_subprocess_output_works_without_tokens_in_meta():
    """Token keys may be absent (provider reported no usage) — counts stay None."""
    stdout = '{"_meta":{"provider":"hf-llama","model":"llama-3"}}\nanswer only'
    result = invoke_mod._parse_subprocess_output(stdout, provider=None)

    assert result.output == "answer only"
    assert result.provider == "hf-llama"
    assert result.input_tokens is None
    assert result.output_tokens is None


def test_parse_subprocess_output_falls_back_when_no_meta_line():
    """Old binary / unexpected output: whole stdout becomes output, metadata unknown."""
    stdout = "just plain output\nwith two lines"
    result = invoke_mod._parse_subprocess_output(stdout, provider="zai-glm")

    assert result.output == "just plain output\nwith two lines"
    assert result.provider == "zai-glm"
    assert result.model == "unknown"
    assert result.input_tokens is None
    assert result.output_tokens is None


def test_parse_subprocess_output_falls_back_when_first_line_is_not_json():
    """A first line that starts with '{' but isn't valid _meta is treated as output."""
    stdout = "{not valid json}\nsecond line"
    result = invoke_mod._parse_subprocess_output(stdout, provider=None)

    assert result.output == "{not valid json}\nsecond line"


def test_parse_subprocess_output_provider_override_when_meta_omits_it():
    """If _meta lacks provider, the caller's provider argument is used."""
    stdout = '{"_meta":{"model":"glm-5.2","inputTokens":10,"outputTokens":5}}' + "\n" + "hi"
    result = invoke_mod._parse_subprocess_output(stdout, provider="override")

    assert result.provider == "override"
    assert result.input_tokens == 10
    assert result.output_tokens == 5


def test_parse_subprocess_output_skips_leading_log_banner():
    """The JVM logger prints a banner to stdout AHEAD of the _meta line; parsing
    must skip leading noise lines rather than only checking the first line.

    Regression: the first-line-only check left model='unknown', lost real token
    counts (cost fell back to chars/4), and leaked the banner + _meta JSON into
    the graded answer — and silently disabled the judge's family-exclusion,
    which is gated on model != 'unknown'."""
    stdout = (
        "kotlin-logging: initializing... active logger factory: Slf4jLoggerFactory\n"
        '{"_meta":{"provider":"hf-llama","model":"meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",'
        '"inputTokens":4392,"outputTokens":89}}\n'
        "reasoning...\nFINAL ANSWER: e2e4"
    )
    result = invoke_mod._parse_subprocess_output(stdout, provider=None)

    assert result.provider == "hf-llama"
    assert result.model == "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo"
    assert result.input_tokens == 4392
    assert result.output_tokens == 89
    # Neither the banner nor the _meta line may leak into the scored answer.
    assert result.output == "reasoning...\nFINAL ANSWER: e2e4"
    assert "kotlin-logging" not in result.output
    assert "_meta" not in result.output


# --- HTTP path token reading ------------------------------------------------


def test_invoke_http_reads_tokens_from_response_body(monkeypatch):
    """The HTTP path lifts inputTokens/outputTokens out of the JSON body."""
    import types

    body = (
        b'{"output":"answer","provider":"zai-glm","model":"glm-5.2",'
        b'"toolCalls":[],"inputTokens":99,"outputTokens":12}'
    )

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self) -> bytes:
            return body

    class Request:
        def __init__(self, url, data=None, headers=None, method="GET"):
            self.full_url = url
            self.data = data
            self.headers = headers or {}
            self.method = method

    def fake_urlopen(req, timeout):
        return FakeResp()

    class URLError(Exception):
        pass

    # Build fake urllib.request / urllib.error modules and register them both in
    # sys.modules and as attributes on the urllib package, so the function-local
    # `import urllib.request` / `import urllib.error` resolve to our fakes.
    fake_request = types.ModuleType("urllib.request")
    fake_request.Request = Request
    fake_request.urlopen = fake_urlopen

    fake_error = types.ModuleType("urllib.error")
    fake_error.URLError = URLError

    import urllib

    monkeypatch.setitem(sys.modules, "urllib.request", fake_request)
    monkeypatch.setitem(sys.modules, "urllib.error", fake_error)
    monkeypatch.setattr(urllib, "request", fake_request, raising=False)
    monkeypatch.setattr(urllib, "error", fake_error, raising=False)

    result = invoke_mod._invoke_http(
        skill_input={"company": "X"},
        skill="company-role-research",
        provider=None,
        http_url="http://localhost:8080",
    )

    assert result.output == "answer"
    assert result.provider == "zai-glm"
    assert result.model == "glm-5.2"
    assert result.input_tokens == 99
    assert result.output_tokens == 12
