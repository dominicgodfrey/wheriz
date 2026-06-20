"""Application factory + top-level routes (landing, health).

``create_app`` wires the DB path and an ``LLMClient`` onto ``app.state`` and mounts the
feature routers. Keeping construction in a factory (not a module-level global) is what
lets the test suite spin up an isolated app per test with a temp DB and a fake model.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .. import db
from ..llm.client import LLMClient, OllamaClient
from . import find, log, onboarding, stats
from .deps import get_conn, get_llm, get_templates

TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app(
    *,
    db_path: str | Path = db.DEFAULT_DB_PATH,
    llm_client: LLMClient | None = None,
) -> FastAPI:
    """Build the wwiw web app. Inject ``db_path`` / ``llm_client`` for tests."""
    app = FastAPI(title="Where Was I When")
    app.state.db_path = str(db_path)
    app.state.llm_client = llm_client if llm_client is not None else OllamaClient()
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        conn=Depends(get_conn),
        templates: Jinja2Templates = Depends(get_templates),
    ):
        """Landing page: onboarding progress, the entry point, and any open-search nudge."""
        followup = db.next_followup_search(conn)
        followup_item = db.get_item(conn, followup.item_id) if followup else None
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "zone_count": db.count_zones(conn),
                "item_count": db.count_items(conn),
                "followup": followup,
                "followup_item": followup_item,
            },
        )

    @app.get("/healthz", response_class=JSONResponse)
    def healthz(llm: LLMClient = Depends(get_llm)):
        """Liveness + whether the local model is reachable (user-initiated check)."""
        return {"ok": True, "llm_available": llm.is_available()}

    app.include_router(onboarding.router)
    app.include_router(find.router)
    app.include_router(log.router)
    app.include_router(stats.router)
    return app


__all__ = ["TEMPLATES_DIR", "create_app"]
