"""Text a person sends that a model will read, screened before anything is stored or queued
(AGC-03): a sentence that tries to replace the model's instructions is refused with 422, the
same way on every route that hands text to a model (questions, MCP, private questions)."""

from __future__ import annotations

from libs.pii.injection import overrides_instructions
from services.api.kernel.problems import FieldError, Problem

INSTRUCTION_REFUSED = (
    "Câu hỏi có câu ra lệnh cho hệ thống (như “bỏ qua mọi hướng dẫn”) nên không được xử lý. "
    "Hãy hỏi lại về dữ liệu."
)


def refuse_instructions(field: str, *texts: str) -> None:
    """Raise 422 validation_failed (`field`, instruction_override) when any of `texts` does."""
    if any(overrides_instructions(t) for t in texts):
        raise Problem(
            422,
            "validation_failed",
            INSTRUCTION_REFUSED,
            errors=[
                FieldError(field=field, code="instruction_override", message=INSTRUCTION_REFUSED)
            ],
        )
