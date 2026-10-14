from services.worker_document.handler import DOCUMENT_RECEIVED, HANDLERS


def test_document_received_is_dispatched_to_document_worker() -> None:
    assert DOCUMENT_RECEIVED == "document.received"
    assert HANDLERS[DOCUMENT_RECEIVED]
