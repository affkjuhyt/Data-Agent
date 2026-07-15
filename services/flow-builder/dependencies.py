from fastapi import HTTPException

from libs.database_connector.postgres_connector import client


def ensure_db_available():
    if not client.is_connected():
        raise HTTPException(503, "DB not available")
