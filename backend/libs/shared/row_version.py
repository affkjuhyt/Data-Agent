"""Row-version bump for tables under optimistic locking."""
import uuid
from typing import Any

from sqlalchemy import func


def with_new_version(**values: Any) -> dict[str, Any]:
    """UPDATE values plus a fresh row_version, so held ETags stop matching."""
    return {**values, "row_version": uuid.uuid4(), "updated_at": func.now()}
