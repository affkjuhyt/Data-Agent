import pytest

from services.worker_document.handler import DOCUMENT_RECEIVED, HANDLERS

pytestmark = pytest.mark.unit


def test_document_received_is_dispatched_to_document_worker() -> None:
    assert DOCUMENT_RECEIVED == "document.received"
    assert HANDLERS[DOCUMENT_RECEIVED]
