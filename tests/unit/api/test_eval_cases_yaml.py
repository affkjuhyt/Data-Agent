"""EV-08: what a golden-set file may say, refused with the field that is wrong before anything is
written."""

from __future__ import annotations

import pytest

from services.api.modules.eval import cases_yaml as cy

pytestmark = [pytest.mark.unit]

HEAD = "eval_set: S\ncases:\n"


def _problems(body: str) -> list[str]:
    with pytest.raises(cy.ImportProblem) as e:
        cy.parse(HEAD + body)
    return e.value.problems


def test_a_case_needs_a_question_or_an_input_not_both() -> None:
    assert any("question or an input" in p for p in _problems("- {id: a, expected: {}}\n"))
    both = "- {id: a, question: q, input: {q: 1}, expected: {}}\n"
    assert any("question or an input" in p for p in _problems(both))


def test_a_case_id_is_used_once() -> None:
    body = "- {id: a, question: q, expected: {}}\n- {id: a, question: r, expected: {}}\n"
    assert any("used twice" in p for p in _problems(body))


@pytest.mark.parametrize("rubric", ["numeric_tolerance", "judge()", "fuzzy", "exact "])
def test_a_rubric_the_database_would_refuse_is_refused_here(rubric: str) -> None:
    body = f"- {{id: a, question: q, expected: {{}}, rubric: '{rubric}'}}\n"
    assert any(p.startswith("cases.0.rubric") for p in _problems(body))


def test_an_unknown_action_or_field_is_refused() -> None:
    assert _problems("- {id: a, question: q, expected: {}, expected_action: guess}\n")
    assert _problems("- {id: a, question: q, expected: {}, answr: 1}\n")


def test_the_question_becomes_the_input_with_its_language_and_history() -> None:
    file = cy.parse(
        HEAD + "- {id: a, question: q, lang: vi, history: [{role: user, text: t}], expected: {}}\n"
    )
    assert file.cases[0].as_input() == {
        "question": "q",
        "lang": "vi",
        "history": [{"role": "user", "text": "t"}],
    }
