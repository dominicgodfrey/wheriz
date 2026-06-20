"""Ollama HTTP client (text + vision) with mandatory call logging.

This is the transport boundary to the local LLM. It does not parse prompts or interpret
responses — it ships a prompt to Ollama's ``/api/generate`` endpoint, returns the raw
response text, and logs every call to ``data/llm_logs/`` for auditability.

Two design choices keep this testable without a live model:

* **The HTTP transport is injectable.** ``OllamaClient`` takes an optional ``requester``
  callable ``(method, url, *, json, timeout) -> dict``. The default lazily imports
  ``httpx`` so the module imports cleanly even where ``httpx`` is absent, and tests inject
  a fake requester — the suite never depends on a live LLM (an architecture rule).
* **Logs are sanitized.** Vision payloads carry base64 image bytes; the log stores an
  image *summary* (count + sizes), never the raw bytes — photos are personal and live in
  ``data/photos/``, not in the call log.
"""

from __future__ import annotations

import base64
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

DEFAULT_LOG_DIR = Path("data") / "llm_logs"

# (method, url, *, json=..., timeout=...) -> parsed JSON response body
Requester = Callable[..., dict[str, Any]]


class LLMError(RuntimeError):
    """Any failure talking to the local model: transport, HTTP, or malformed body."""


@runtime_checkable
class LLMClient(Protocol):
    """The surface the task layer depends on. ``OllamaClient`` satisfies it; so do fakes."""

    def generate_text(
        self,
        prompt: str,
        *,
        task: str,
        format: str | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> str: ...

    def generate_vision(
        self,
        prompt: str,
        images: Sequence[bytes],
        *,
        task: str,
        format: str | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class OllamaConfig:
    """Connection + model selection. Defaults target the M0 model classes.

    Adjust ``text_model`` / ``vision_model`` to whatever is pulled locally; the vision
    timeout is generous because image generation on a single GPU is slow.
    """

    base_url: str = "http://127.0.0.1:11434"
    text_model: str = "qwen3:8b"
    vision_model: str = "qwen2.5vl:7b"
    timeout_seconds: float = 120.0


def _default_requester(method: str, url: str, *, json: Any = None, timeout: float = 60.0) -> dict[str, Any]:
    """Real transport. Imports ``httpx`` lazily so module import never requires it."""
    import httpx  # local import: only needed when actually talking to Ollama

    response = httpx.request(method, url, json=json, timeout=timeout)
    response.raise_for_status()
    return response.json()


@dataclass
class OllamaClient:
    """Talks to a local Ollama server. The LLM never ranks — it parses and phrases."""

    config: OllamaConfig = field(default_factory=OllamaConfig)
    requester: Requester = _default_requester
    log_dir: Path = DEFAULT_LOG_DIR

    # --- public API -----------------------------------------------------------

    def generate_text(
        self,
        prompt: str,
        *,
        task: str,
        format: str | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> str:
        """Run a text-model completion and return the raw response string."""
        return self._generate(
            model=self.config.text_model,
            prompt=prompt,
            images=None,
            task=task,
            format=format,
            options=options,
        )

    def generate_vision(
        self,
        prompt: str,
        images: Sequence[bytes],
        *,
        task: str,
        format: str | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> str:
        """Run a vision-model completion over one or more images (raw bytes)."""
        return self._generate(
            model=self.config.vision_model,
            prompt=prompt,
            images=list(images),
            task=task,
            format=format,
            options=options,
        )

    def is_available(self) -> bool:
        """True if the server answers ``/api/tags``. Used as the M2 startup gate."""
        try:
            self.requester("GET", f"{self.config.base_url}/api/tags", timeout=5.0)
            return True
        except Exception:
            return False

    def installed_models(self) -> list[str]:
        """Model names reported by the server, or ``[]`` if it is unreachable."""
        try:
            body = self.requester("GET", f"{self.config.base_url}/api/tags", timeout=5.0)
        except Exception:
            return []
        return [m.get("name", "") for m in body.get("models", [])]

    # --- internals ------------------------------------------------------------

    def _generate(
        self,
        *,
        model: str,
        prompt: str,
        images: list[bytes] | None,
        task: str,
        format: str | None,
        options: Mapping[str, Any] | None,
    ) -> str:
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
        if format is not None:
            payload["format"] = format
        if options:
            payload["options"] = dict(options)
        if images:
            payload["images"] = [base64.b64encode(img).decode("ascii") for img in images]

        url = f"{self.config.base_url}/api/generate"
        started = time.monotonic()
        error: str | None = None
        text = ""
        try:
            body = self.requester("POST", url, json=payload, timeout=self.config.timeout_seconds)
            text = body.get("response", "")
            return text
        except LLMError:
            raise
        except Exception as exc:  # transport/HTTP/JSON — normalize for callers
            error = f"{type(exc).__name__}: {exc}"
            raise LLMError(f"Ollama generate failed for task {task!r}: {error}") from exc
        finally:
            self._log_call(
                task=task,
                model=model,
                payload=payload,
                images=images,
                response=text,
                duration_ms=round((time.monotonic() - started) * 1000, 1),
                error=error,
            )

    def _log_call(
        self,
        *,
        task: str,
        model: str,
        payload: Mapping[str, Any],
        images: list[bytes] | None,
        response: str,
        duration_ms: float,
        error: str | None,
    ) -> None:
        """Write one sanitized JSON record per call. Best-effort: never raises."""
        record = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "task": task,
            "model": model,
            "endpoint": "/api/generate",
            "request": _sanitize_request(payload, images),
            "response": response,
            "duration_ms": duration_ms,
            "error": error,
        }
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S_%f")
            path = self.log_dir / f"{stamp}_{_slug(task)}.json"
            path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # logging must not break a working call


def _sanitize_request(payload: Mapping[str, Any], images: list[bytes] | None) -> dict[str, Any]:
    """Copy the request for logging, replacing raw image bytes with a summary."""
    safe = {k: v for k, v in payload.items() if k != "images"}
    if images:
        sizes = "+".join(str(len(img)) for img in images)
        safe["images"] = f"<{len(images)} image(s), {sizes} bytes>"
    return safe


def _slug(value: str) -> str:
    """Filesystem-safe token for log filenames."""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-") or "call"


__all__ = [
    "DEFAULT_LOG_DIR",
    "LLMClient",
    "LLMError",
    "OllamaClient",
    "OllamaConfig",
    "Requester",
]
