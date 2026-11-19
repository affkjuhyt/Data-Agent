"""AE-03: the audience a question is asked as comes from what the asker may do, never the client."""

from __future__ import annotations

import uuid

import pytest

from services.api.kernel.auth import Principal
from services.api.modules.org.bootstrap import DEFAULT_ROLES
from services.api.modules.qa.questions import audience_of

pytestmark = [pytest.mark.unit]


def principal(*caps: str) -> Principal:
    return Principal(uuid.uuid4(), uuid.uuid4(), "kc", capabilities=frozenset(caps))


def test_the_default_roles_that_ask_have_the_audience_of_their_work() -> None:
    audience = {
        r.code: audience_of(principal(*r.capabilities))
        for r in DEFAULT_ROLES
        if "ask" in r.capabilities
    }
    # The business user and the executive ask for answers; the steward, the data engineer, the
    # analyst and the agent builder work with the detail and may be asked more.
    assert audience == {
        "AC01": "executive",
        "AC02": "executive",
        "AC03": "analyst",
        "AC04": "analyst",
        "AC08": "analyst",
        "AC09": "analyst",
    }


def test_a_role_an_organisation_adds_is_read_from_its_capabilities() -> None:
    assert audience_of(principal("ask", "approve")) == "executive"
    assert audience_of(principal("ask", "view_detail")) == "analyst"
