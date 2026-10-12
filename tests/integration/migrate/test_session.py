"""scoped_session sets the row-level security settings per transaction, with bound parameters."""

import uuid

import pytest
from sqlalchemy import text

from libs.dataagent_db import session as db_session

pytestmark = pytest.mark.integration


@pytest.fixture
def org_role_url(ddl_server, monkeypatch):
    # The admin DSN stands in for svc_org's login; what is tested is the settings, not the grants.
    monkeypatch.setenv("DATAAGENT_DB_URL__SVC_ORG", ddl_server["dataagent"])
    db_session.engine_for.cache_clear()
    yield
    db_session.engine_for.cache_clear()


@pytest.mark.asyncio
async def test_the_transaction_carries_org_user_and_extra_settings(org_role_url):
    org, user = uuid.uuid4(), uuid.uuid4()
    async with db_session.scoped_session("svc_org", org, user, knowledge_reader="on") as s:
        row = (
            await s.execute(
                text(
                    "SELECT current_setting('app.org_id'), current_setting('app.user_id'), "
                    "current_setting('app.knowledge_reader')"
                )
            )
        ).one()
    assert row == (str(org), str(user), "on")
    # Transaction-local: the next transaction on a pooled connection starts without them.
    async with db_session.scoped_session("svc_org", uuid.uuid4()) as s:
        assert (await s.execute(text("SELECT current_setting('app.user_id')"))).scalar() == ""
    await db_session.engine_for("svc_org").dispose()


@pytest.mark.asyncio
async def test_an_unknown_setting_is_refused(org_role_url):
    with pytest.raises(ValueError, match="unknown app settings"):
        async with db_session.scoped_session("svc_org", uuid.uuid4(), orgid="typo"):
            pass
