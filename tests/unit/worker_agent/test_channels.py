"""What goes out by email and Teams (worker-agent channels/): a card carries a link into the app and
no personal data (BR-67/68), and the worker only ever calls a Teams webhook on Microsoft's hosts."""

from __future__ import annotations

import pytest

from services.worker_agent.channels.cards import Message, hitl_card, probe_card, verification_card
from services.worker_agent.channels.senders import teams_payload, webhook_allowed

pytestmark = [pytest.mark.unit]

APP = "http://localhost:30280"


def test_an_approval_card_is_a_title_and_a_link_into_the_app() -> None:
    m = hitl_card(
        title="Báo giá vàng (demo): duyệt gửi kết quả", kind="checkpoint", risk="high", app_url=APP
    )
    assert isinstance(m, Message)
    assert m.subject == "[Cần bạn] Báo giá vàng (demo): duyệt gửi kết quả"
    assert m.link == f"{APP}/needs-you"
    assert "Mức rủi ro: cao" in m.text and m.link in m.text
    # BR-67: never a decision in the message itself; it is taken in the app.
    assert "duyệt" in m.text.lower() and "trả lời email" not in m.text.lower()


def test_a_verification_card_carries_the_code_and_nothing_else() -> None:
    m = verification_card("123456", app_url=APP)
    assert "123456" in m.text and "123456" not in m.subject


def test_a_teams_message_is_an_adaptive_card_with_one_open_button() -> None:
    body = teams_payload(probe_card(app_url=APP))
    [attachment] = body["attachments"]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = attachment["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["actions"] == [{"type": "Action.OpenUrl", "title": "Mở ứng dụng", "url": APP}]


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        (
            "https://prod-12.westus.logic.azure.com:443/workflows/abc/triggers/manual/paths/invoke?sig=x",
            True,
        ),
        ("https://default1234.environment.api.powerplatform.com/powerautomate/automations/x", True),
        ("https://contoso.webhook.office.com/webhookb2/abc", True),
        # Not Microsoft, not https, or an address inside the cluster: the worker must not call it.
        ("http://contoso.webhook.office.com/webhookb2/abc", False),
        ("https://openbao.dataagent-database.svc.cluster.local:8120/v1/sys", False),
        ("https://10.0.0.5/hook", False),
        ("https://evil.com/logic.azure.com", False),
        ("https://logic.azure.com.evil.com/x", False),
        ("https://user:pw@prod-1.westus.logic.azure.com/x", False),
    ],
)
def test_only_a_microsoft_https_webhook_is_called(url: str, ok: bool) -> None:
    assert webhook_allowed(url) is ok
