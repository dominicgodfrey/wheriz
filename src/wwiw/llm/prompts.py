"""Prompt template loading. Templates live in ``prompts/`` — one file per task.

Architecture rule: prompt text is never inlined in business logic, so the task layer
calls ``render(name, **vars)`` and the words live in version-controlled ``.txt`` files
that can be tuned without touching code.

``string.Template`` (``$var`` placeholders) is used deliberately: prompts are full of
JSON ``{ }`` braces, which ``str.format`` would choke on. ``substitute`` raises on a
missing variable, so an under-filled prompt fails loudly instead of shipping a literal
``$placeholder`` to the model.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).parent / "prompts"
_SUFFIX = ".txt"


@lru_cache(maxsize=None)
def load_template(name: str) -> Template:
    """Load a prompt template by bare name (no suffix). Cached after first read."""
    path = PROMPTS_DIR / f"{name}{_SUFFIX}"
    if not path.is_file():
        raise FileNotFoundError(f"No prompt template {name!r} at {path}")
    return Template(path.read_text(encoding="utf-8"))


def render(name: str, /, **variables: object) -> str:
    """Render a prompt with the given variables. Raises ``KeyError`` if any are missing."""
    return load_template(name).substitute(variables)


def template_identifiers(name: str) -> set[str]:
    """The ``$placeholder`` names a template expects — handy for validation/tests."""
    return set(load_template(name).get_identifiers())


def available_prompts() -> list[str]:
    """Sorted bare names of every prompt template on disk."""
    return sorted(p.stem for p in PROMPTS_DIR.glob(f"*{_SUFFIX}"))


__all__ = [
    "PROMPTS_DIR",
    "available_prompts",
    "load_template",
    "render",
    "template_identifiers",
]
