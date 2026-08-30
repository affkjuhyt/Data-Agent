"""Compatibility CLI shim for the kb-ingestion worker."""

from services.kb_ingestion_worker.app import run


if __name__ == "__main__":
    run()
