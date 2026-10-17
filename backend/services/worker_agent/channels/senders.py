"""Sending a message out: email by SMTP, Teams by a Workflows (Power Automate) webhook.

The address is the one the person bound and verified (notify.channel_binding). A Teams webhook is
called only when it is https on Microsoft's hosts and without redirects: the worker must never be
made to call an address inside the cluster (OpenBao, the database) by a URL someone typed.
Settings: SMTP_HOST, SMTP_PORT, SMTP_FROM, SMTP_USER/SMTP_PASSWORD, SMTP_STARTTLS ("1"). On dev
SMTP is Mailpit, which keeps every message for viewing and sends none out.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlsplit

import httpx

from services.worker_agent.channels.cards import Message

# Hosts a Teams Workflows or incoming webhook is served from.
WEBHOOK_HOSTS = (".logic.azure.com", ".webhook.office.com", ".environment.api.powerplatform.com")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TIMEOUT = 10.0


class SendFailed(RuntimeError):
    """The channel did not take the message; the text is safe to store (no address, no secret)."""


def webhook_allowed(url: str) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443):
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return any(host.endswith(suffix) for suffix in WEBHOOK_HOSTS)


def email_allowed(address: str) -> bool:
    return bool(_EMAIL.match(address)) and len(address) <= 320


def teams_payload(m: Message) -> dict[str, Any]:
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": m.title, "weight": "Bolder", "wrap": True},
            *(
                {"type": "TextBlock", "text": line, "wrap": True}
                for line in m.text.splitlines()[1:]
                if line and line != m.link
            ),
        ],
        "actions": [{"type": "Action.OpenUrl", "title": "Mở ứng dụng", "url": m.link}],
    }
    return {
        "type": "message",
        "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}
        ],
    }


@dataclass(frozen=True)
class Smtp:
    host: str
    port: int
    sender: str
    user: str | None = None
    password: str | None = None
    starttls: bool = False

    @classmethod
    def from_env(cls) -> Smtp | None:
        host = os.environ.get("SMTP_HOST")
        if not host:
            return None
        return cls(
            host,
            int(os.environ.get("SMTP_PORT", "25")),
            os.environ.get("SMTP_FROM", "data-agent@localhost"),
            os.environ.get("SMTP_USER") or None,
            os.environ.get("SMTP_PASSWORD") or None,
            os.environ.get("SMTP_STARTTLS") == "1",
        )

    def _send(self, to: str, m: Message) -> None:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = m.subject, self.sender, to
        msg.set_content(m.text)
        msg.add_alternative(m.html, subtype="html")
        with smtplib.SMTP(self.host, self.port, timeout=TIMEOUT) as smtp:
            if self.starttls:
                smtp.starttls()
            if self.user and self.password:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)

    async def send(self, to: str, m: Message) -> None:
        if not email_allowed(to):
            raise SendFailed("the bound email address is not an address")
        try:
            await asyncio.to_thread(self._send, to, m)
        except (OSError, smtplib.SMTPException) as exc:
            raise SendFailed(f"SMTP: {type(exc).__name__}") from exc


class Teams:
    async def send(self, url: str, m: Message) -> None:
        if not webhook_allowed(url):
            raise SendFailed("the bound Teams address is not a Microsoft https webhook")
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
                r = await client.post(url, json=teams_payload(m))
        except httpx.HTTPError as exc:
            raise SendFailed(f"Teams: {type(exc).__name__}") from exc
        if r.status_code not in (200, 202):
            raise SendFailed(f"Teams answered {r.status_code}")
