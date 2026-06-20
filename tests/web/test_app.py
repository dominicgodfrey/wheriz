"""Tests for the app skeleton: landing page and health endpoint."""


def test_index_renders_setup_prompt_when_empty(make_app):
    client = make_app()
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Where was I when" in resp.text
    assert "/onboarding/rooms" in resp.text  # entry point to the wizard
    assert "Start setup" in resp.text


def test_healthz_reports_llm_available(make_app):
    client = make_app(available=True)
    body = client.get("/healthz").json()
    assert body == {"ok": True, "llm_available": True}


def test_healthz_reports_llm_unavailable(make_app):
    client = make_app(available=False)
    assert client.get("/healthz").json()["llm_available"] is False
