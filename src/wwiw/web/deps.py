"""Request-scoped dependencies: a DB connection, the templates, the LLM client.

A fresh SQLite connection is opened per request and closed after — sync endpoints run in
a threadpool, and sqlite3 connections don't cross threads safely, so per-request is the
simple correct choice. The templates and LLM client are app-wide singletons stashed on
``app.state`` by ``create_app``.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .. import db
from ..llm.client import LLMClient


def get_conn(request: Request) -> Iterator[db.sqlite3.Connection]:
    """Yield a connection to the app's database, closing it when the request ends."""
    conn = db.connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_templates(request: Request) -> Jinja2Templates:
    """The shared Jinja2 environment."""
    return request.app.state.templates


def get_llm(request: Request) -> LLMClient:
    """The configured LLM client (real Ollama in prod, a fake in tests)."""
    return request.app.state.llm_client


__all__ = ["get_conn", "get_llm", "get_templates"]
