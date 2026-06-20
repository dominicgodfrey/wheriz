"""FastAPI web layer: onboarding wizard and (later) the find loop.

The app is built by ``create_app(...)``, which takes the DB path and an ``LLMClient`` so
tests can inject a temp database and a fake model — the web tests never call Ollama.
Templates are server-rendered Jinja2; the deterministic engine and the LLM edge layer do
the real work, the web layer just orchestrates and presents.
"""

from .app import create_app

__all__ = ["create_app"]
