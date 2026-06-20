"""Tests for prompt template loading and rendering.

These assert the templates exist, render with their declared variables, and leave no
unsubstituted ``$placeholder`` behind — without depending on any model.
"""

import re

import pytest

from wwiw.llm import prompts

EXPECTED_PROMPTS = {
    "parse_residence": {"description"},
    "parse_loss_interview": {"zones", "answers"},
    "extract_surfaces": {"zone_name"},
    "parse_search_query": {"now", "items", "query"},
    "phrase_reason": {"item", "zone", "surface", "grounding"},
}

# Matches a leftover $name or ${name} that substitution should have filled.
_LEFTOVER = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?")


def test_all_five_tasks_have_a_prompt_on_disk():
    assert set(prompts.available_prompts()) == set(EXPECTED_PROMPTS)


@pytest.mark.parametrize("name, expected_vars", EXPECTED_PROMPTS.items())
def test_template_declares_exactly_its_variables(name, expected_vars):
    assert prompts.template_identifiers(name) == expected_vars


@pytest.mark.parametrize("name, expected_vars", EXPECTED_PROMPTS.items())
def test_render_fills_every_placeholder(name, expected_vars):
    rendered = prompts.render(name, **{v: f"<{v}>" for v in expected_vars})
    assert rendered.strip()
    assert not _LEFTOVER.search(rendered), "unsubstituted placeholder remains"
    for v in expected_vars:
        assert f"<{v}>" in rendered


def test_render_raises_on_missing_variable():
    with pytest.raises(KeyError):
        prompts.render("parse_residence")  # 'description' not provided


def test_unknown_prompt_raises():
    with pytest.raises(FileNotFoundError):
        prompts.load_template("does_not_exist")
