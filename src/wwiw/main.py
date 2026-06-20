"""Entry point: serve the wwiw web app on localhost.

    python -m wwiw.main      # -> http://127.0.0.1:8741

Desktop-only for now; binding to ``127.0.0.1`` keeps it off the network. Switching the
host to the LAN address later makes it phone-reachable with no other change.
"""

from __future__ import annotations

from .web.app import create_app

HOST = "127.0.0.1"
PORT = 8741

# Module-level app so `uvicorn wwiw.main:app` works too.
app = create_app()


def main() -> None:
    """Run the development server."""
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
