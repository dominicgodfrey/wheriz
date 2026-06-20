"""Shared web-test scaffolding: a fake LLM and an isolated-app factory.

Every web test runs against a temp-file SQLite database and a ``FakeLLM`` — the suite
never opens a socket to Ollama (an architecture rule). The fake returns scripted task
responses and records its calls so tests can assert what was asked of the model.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from wwiw.web.app import create_app


class FakeLLM:
    """Stand-in for ``LLMClient``: scripted per-task responses, recorded calls."""

    def __init__(self, responses: dict[str, str] | None = None, available: bool = True):
        self.responses = responses or {}
        self.available = available
        self.calls: list[dict] = []

    def is_available(self) -> bool:
        return self.available

    def installed_models(self) -> list[str]:
        return ["fake-model"] if self.available else []

    def generate_text(self, prompt, *, task, format=None, options=None) -> str:
        self.calls.append({"kind": "text", "task": task, "prompt": prompt})
        return self.responses.get(task, "")

    def generate_vision(self, prompt, images, *, task, format=None, options=None) -> str:
        self.calls.append({"kind": "vision", "task": task, "prompt": prompt, "images": list(images)})
        return self.responses.get(task, "")


@pytest.fixture
def make_app(tmp_path):
    """Factory: build an isolated TestClient. ``client.llm`` exposes the FakeLLM."""

    def _make(*, responses: dict[str, str] | None = None, available: bool = True) -> TestClient:
        llm = FakeLLM(responses=responses, available=available)
        app = create_app(db_path=tmp_path / "wwiw.sqlite", llm_client=llm)
        client = TestClient(app)
        client.llm = llm  # type: ignore[attr-defined]  — convenience for assertions
        return client

    return _make
