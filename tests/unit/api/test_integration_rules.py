"""Tích hợp rules that need no database: BR-48 tools, BR-144 lifetime, webhook targets."""

from __future__ import annotations

import datetime

import pytest

from services.api.kernel.problems import Problem
from services.api.modules.integration import rules

pytestmark = [pytest.mark.unit, pytest.mark.security]

ENV = {rules.TARGETS_ENV: "hooks.example.vn"}


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.example.vn/in",
        "https://a.hooks.example.vn/in?x=1",
        "https://hooks.example.vn:443/in",
    ],
)
def test_allowed_targets(url: str) -> None:
    assert rules.check_target(url, ENV) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.vn/in",  # not https
        "https://evil.example.com/in",  # not in the list
        "https://hooks.example.vn.evil.com/in",  # suffix trick
        "https://evil.com\\@hooks.example.vn/in",  # backslash: parsers disagree on the host
        "https://hooks.example.vn@evil.com/in",  # userinfo
        "https://user:pw@hooks.example.vn/in",
        "https://hooks.example.vn:8443/in",  # another port
        "https://hooks.example.vn/in\nHost: evil.com",  # header smuggling
        "https://hooks.exämple.vn/in",  # non-ASCII host
        "https://hooks.example.vn:abc/in",  # unreadable port
    ],
)
def test_refused_targets(url: str) -> None:
    with pytest.raises(Problem):
        rules.check_target(url, ENV)


def test_no_list_means_nothing_is_allowed() -> None:
    with pytest.raises(Problem):
        rules.check_target("https://hooks.example.vn/in", {})


def test_own_identity_gets_only_tools_without_detail_data() -> None:
    assert rules.check_tools(["search_metrics", "get_metric_definition"], "own_identity")
    for tool in ("ask_question", "search_documents", "run_skill", "get_published_result"):
        with pytest.raises(Problem) as err:
            rules.check_tools([tool], "own_identity")
        assert err.value.code == "business_rule_violated"


def test_twelve_months_is_calendar_months() -> None:
    leap = datetime.datetime(2028, 2, 29, 9, tzinfo=datetime.UTC)
    assert rules.twelve_months_after(leap) == datetime.datetime(2029, 2, 28, 9, tzinfo=datetime.UTC)
    now = datetime.datetime(2026, 10, 12, tzinfo=datetime.UTC)
    assert rules.check_expiry(datetime.datetime(2027, 10, 12, tzinfo=datetime.UTC), now)
    with pytest.raises(Problem):
        rules.check_expiry(datetime.datetime(2027, 10, 13, tzinfo=datetime.UTC), now)
