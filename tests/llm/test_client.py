"""Tests for the Ollama client: payload shaping, vision encoding, logging, errors.

A fake requester stands in for the HTTP transport, so the suite never touches a live
model (an architecture rule) and needs no third-party HTTP library.
"""

import base64
import json

import pytest

from wwiw.llm.client import LLMClient, LLMError, OllamaClient, OllamaConfig


class FakeRequester:
    """Records the last request and returns a scripted body (or raises)."""

    def __init__(self, body=None, raises=None):
        self.body = body if body is not None else {"response": "ok"}
        self.raises = raises
        self.calls = []

    def __call__(self, method, url, *, json=None, timeout=60.0):
        self.calls.append({"method": method, "url": url, "json": json, "timeout": timeout})
        if self.raises is not None:
            raise self.raises
        return self.body


def _client(tmp_path, requester):
    return OllamaClient(
        config=OllamaConfig(base_url="http://test:11434", text_model="t", vision_model="v"),
        requester=requester,
        log_dir=tmp_path / "llm_logs",
    )


def test_satisfies_protocol(tmp_path):
    client = _client(tmp_path, FakeRequester())
    assert isinstance(client, LLMClient)


def test_generate_text_shapes_payload_and_returns_response(tmp_path):
    req = FakeRequester(body={"response": "the answer"})
    client = _client(tmp_path, req)

    out = client.generate_text("hello", task="parse_query", format="json", options={"temperature": 0})

    assert out == "the answer"
    (call,) = req.calls
    assert call["method"] == "POST"
    assert call["url"] == "http://test:11434/api/generate"
    payload = call["json"]
    assert payload["model"] == "t"
    assert payload["prompt"] == "hello"
    assert payload["stream"] is False
    assert payload["format"] == "json"
    assert payload["options"] == {"temperature": 0}
    assert "images" not in payload


def test_generate_text_omits_optional_fields_when_absent(tmp_path):
    req = FakeRequester()
    client = _client(tmp_path, req)

    client.generate_text("hi", task="phrase_reason")

    payload = req.calls[0]["json"]
    assert "format" not in payload
    assert "options" not in payload
    assert "images" not in payload


def test_generate_vision_base64_encodes_images(tmp_path):
    req = FakeRequester(body={"response": "surfaces"})
    client = _client(tmp_path, req)
    raw = [b"\x89PNG-one", b"JPEG-two"]

    out = client.generate_vision("list surfaces", raw, task="extract_surfaces")

    assert out == "surfaces"
    payload = req.calls[0]["json"]
    assert payload["model"] == "v"
    assert payload["images"] == [base64.b64encode(b).decode("ascii") for b in raw]


def test_call_is_logged_with_sanitized_image_summary(tmp_path):
    req = FakeRequester(body={"response": "surfaces"})
    client = _client(tmp_path, req)

    client.generate_vision("look", [b"abcd", b"ef"], task="extract_surfaces")

    logs = list((tmp_path / "llm_logs").glob("*.json"))
    assert len(logs) == 1
    record = json.loads(logs[0].read_text(encoding="utf-8"))
    assert record["task"] == "extract_surfaces"
    assert record["model"] == "v"
    assert record["response"] == "surfaces"
    assert record["error"] is None
    # Raw image bytes must never reach the log — only a summary.
    assert record["request"]["images"] == "<2 image(s), 4+2 bytes>"
    assert "abcd" not in json.dumps(record)


def test_failed_call_raises_llmerror_and_is_logged(tmp_path):
    req = FakeRequester(raises=ConnectionError("refused"))
    client = _client(tmp_path, req)

    with pytest.raises(LLMError) as exc:
        client.generate_text("hi", task="parse_query")
    assert "parse_query" in str(exc.value)

    record = json.loads(next((tmp_path / "llm_logs").glob("*.json")).read_text(encoding="utf-8"))
    assert record["error"] is not None
    assert "ConnectionError" in record["error"]
    assert record["response"] == ""


def test_is_available_true_when_tags_reachable(tmp_path):
    client = _client(tmp_path, FakeRequester(body={"models": []}))
    assert client.is_available() is True


def test_is_available_false_when_unreachable(tmp_path):
    client = _client(tmp_path, FakeRequester(raises=ConnectionError("down")))
    assert client.is_available() is False


def test_installed_models_lists_names(tmp_path):
    body = {"models": [{"name": "qwen3:8b"}, {"name": "qwen2.5vl:7b"}]}
    client = _client(tmp_path, FakeRequester(body=body))
    assert client.installed_models() == ["qwen3:8b", "qwen2.5vl:7b"]


def test_installed_models_empty_when_unreachable(tmp_path):
    client = _client(tmp_path, FakeRequester(raises=ConnectionError("down")))
    assert client.installed_models() == []
