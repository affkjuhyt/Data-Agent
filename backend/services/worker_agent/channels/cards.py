"""What a message outside the app says (email, Teams): a short title and a link into the app.

BR-67: a decision is never taken in a message that cannot prove who answers, so a card offers no
option, only the way into Cần bạn (buttons on Teams need a registered bot, not a webhook). BR-68:
a card carries no personal data: the request's title, its kind and risk, never its context.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

RISK_TEXT = {"low": "thấp", "medium": "trung bình", "high": "cao"}


@dataclass(frozen=True)
class Message:
    subject: str
    title: str
    text: str
    link: str

    @property
    def html(self) -> str:
        lines = "".join(f"<p>{html.escape(line)}</p>" for line in self.text.splitlines() if line)
        return f'<html><body>{lines}<p><a href="{html.escape(self.link)}">Mở ứng dụng</a></p></body></html>'


def hitl_card(*, title: str, kind: str, risk: str, app_url: str) -> Message:
    link = f"{app_url.rstrip('/')}/needs-you"
    text = (
        f"{title}\n"
        f"Mức rủi ro: {RISK_TEXT.get(risk, risk)}.\n"
        "Việc này cần bạn duyệt hoặc trả lời trong mục Cần bạn của ứng dụng.\n"
        f"{link}"
    )
    return Message(f"[Cần bạn] {title}", title, text, link)


def verification_card(code: str, *, app_url: str) -> Message:
    link = app_url.rstrip("/")
    text = (
        f"Mã xác nhận liên kết kênh của bạn: {code}\n"
        "Nhập mã này trong ứng dụng trong vòng 15 phút. Nếu bạn không yêu cầu, hãy bỏ qua tin này."
    )
    return Message("Mã xác nhận liên kết kênh", "Xác nhận liên kết kênh", text, link)


def probe_card(*, app_url: str) -> Message:
    link = app_url.rstrip("/")
    text = "Đây là thông báo thử từ Data Agent: kênh này nhận được thông báo của bạn."
    return Message("Thông báo thử", "Thông báo thử", text, link)
