"""Question answering and query-result read APIs (schemas `qa` and `query`)."""

from fastapi import APIRouter

from services.api.modules.qa import query, routes
from services.api.modules.qa.public import RecordedQuestion, answer_of, record_question

router = APIRouter()
router.include_router(routes.router)
router.include_router(query.router)

__all__ = ["RecordedQuestion", "answer_of", "record_question", "router"]
