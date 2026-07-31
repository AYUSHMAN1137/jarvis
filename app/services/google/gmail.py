import base64
import logging
import re
import threading
from typing import Dict, List

from config import GMAIL_SCOPES
from app.services.google.oauth import GoogleOAuthService

logger = logging.getLogger("J.A.R.V.I.S")


class GmailService:
    def __init__(self):
        self._service = None
        self._lock = threading.Lock()
        self._oauth = GoogleOAuthService()

    def is_configured(self) -> bool:
        return self._oauth.is_configured()

    def send_message(self, to: str, subject: str, body: str,
                     cc: str = "") -> str:
        """Send a plain-text email. Returns a confirmation containing the id.

        Raises on failure so the tool layer can turn it into an ERROR string --
        a silent "sent" for a message that never left would be the worst
        possible outcome here.
        """
        from email.message import EmailMessage

        recipients = [a.strip() for a in re.split(r"[,;]", to or "") if a.strip()]
        if not recipients:
            raise ValueError("no recipient given")
        for address in recipients:
            if "@" not in address or " " in address:
                raise ValueError(f"'{address}' is not a valid email address")

        message = EmailMessage()
        message["To"] = ", ".join(recipients)
        if cc.strip():
            message["Cc"] = cc.strip()
        message["Subject"] = (subject or "").strip()
        message.set_content(body or "")

        encoded = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service = self._get_service()
        sent = service.users().messages().send(
            userId="me", body={"raw": encoded}).execute()

        message_id = sent.get("id", "")
        logger.info("[GMAIL] Sent message %s to %d recipient(s)",
                    message_id, len(recipients))
        return f"Sent to {', '.join(recipients)} (id {message_id})."

    def find_sent_message(self, subject: str, max_results: int = 5) -> bool:
        """True when a message with this subject is in Sent. Used to verify."""
        needle = (subject or "").strip().lower()
        if not needle:
            return False
        service = self._get_service(allow_interactive=False)
        result = service.users().messages().list(
            userId="me", labelIds=["SENT"], maxResults=max_results).execute()
        for entry in result.get("messages", []):
            details = self._get_message_details(service, entry.get("id", ""))
            if needle in str(details.get("subject", "")).lower():
                return True
        return False

    def get_inbox_summary(self, max_results: int = 5) -> str:
        service = self._get_service()
        result = service.users().messages().list(
            userId="me",
            labelIds=["INBOX"],
            maxResults=max_results,
        ).execute()

        message_ids = result.get("messages", [])
        if not message_ids:
            return "Your inbox is empty right now."

        emails = [self._get_message_details(service, item["id"]) for item in message_ids]
        lines = [f"Here are your latest {len(emails)} inbox emails:"]

        for idx, email in enumerate(emails, 1):
            lines.append(
                f"{idx}. From: {email['from']} | Subject: {email['subject']} | "
                f"Date: {email['date']} | Preview: {email['preview']}"
            )

        return "\n".join(lines)

    def get_unread_summary(self, max_results: int = 5) -> str:
        service = self._get_service()
        result = service.users().messages().list(
            userId="me",
            labelIds=["INBOX", "UNREAD"],
            maxResults=max_results,
        ).execute()

        message_ids = result.get("messages", [])
        unread_count = result.get("resultSizeEstimate", len(message_ids))

        if unread_count == 0 or not message_ids:
            return "You have no unread emails right now."

        emails = [self._get_message_details(service, item["id"]) for item in message_ids]
        lines = [f"You have about {unread_count} unread email(s). Latest unread messages:"]

        for idx, email in enumerate(emails, 1):
            body = email["body"]
            body_line = f" | Body: {body}" if body else ""
            lines.append(
                f"{idx}. From: {email['from']} | Subject: {email['subject']} | "
                f"Date: {email['date']} | Preview: {email['preview']}{body_line}"
            )

        return "\n".join(lines)

    def get_unread_count(self, allow_interactive: bool = True) -> int:
        service = self._get_service(allow_interactive=allow_interactive)
        result = service.users().messages().list(
            userId="me",
            labelIds=["INBOX", "UNREAD"],
            maxResults=1,
        ).execute()
        return int(result.get("resultSizeEstimate", 0) or 0)

    def _get_service(self, allow_interactive: bool = True):
        with self._lock:
            if self._service is not None:
                return self._service
            self._service = self._oauth.build_service(
                "gmail",
                "v1",
                allow_interactive=allow_interactive,
                scopes=GMAIL_SCOPES,
            )
            return self._service

    def _get_message_details(self, service, message_id: str) -> Dict[str, str]:
        detail = service.users().messages().get(
            userId="me",
            id=message_id,
            format="full",
        ).execute()

        payload = detail.get("payload", {})
        headers = self._headers_to_dict(payload.get("headers", []))
        preview = self._clean_text(detail.get("snippet", ""))
        body = self._extract_body(payload)

        return {
            "from": headers.get("From", "Unknown sender"),
            "subject": headers.get("Subject", "No subject"),
            "date": headers.get("Date", "Unknown date"),
            "preview": preview or "No preview available",
            "body": body,
        }

    def _headers_to_dict(self, headers: List[dict]) -> Dict[str, str]:
        return {
            str(item.get("name", "")): str(item.get("value", ""))
            for item in headers
            if item.get("name")
        }

    def _extract_body(self, payload: dict) -> str:
        plain_text = self._find_body_part(payload, preferred_mime="text/plain")
        if plain_text:
            return plain_text

        html_text = self._find_body_part(payload, preferred_mime="text/html")
        if html_text:
            cleaned = re.sub(r"<[^>]+>", " ", html_text)
            return self._clean_text(cleaned)

        return ""

    def _find_body_part(self, payload: dict, preferred_mime: str) -> str:
        if not payload:
            return ""

        mime_type = payload.get("mimeType", "")
        body_data = (payload.get("body") or {}).get("data")
        if mime_type == preferred_mime and body_data:
            return self._decode_body(body_data)

        for part in payload.get("parts", []) or []:
            text = self._find_body_part(part, preferred_mime)
            if text:
                return text

        if body_data and preferred_mime == "text/plain":
            return self._decode_body(body_data)

        return ""

    def _decode_body(self, data: str) -> str:
        try:
            missing_padding = (-len(data)) % 4
            decoded = base64.urlsafe_b64decode(data + ("=" * missing_padding))
            return self._clean_text(decoded.decode("utf-8", errors="ignore"))
        except Exception as exc:
            logger.warning("[GMAIL] Failed to decode message body: %s", exc)
            return ""

    def _clean_text(self, text: str, max_len: int = 280) -> str:
        cleaned = re.sub(r"\s+", " ", (text or "")).strip()
        if len(cleaned) <= max_len:
            return cleaned
        return cleaned[: max_len - 3].rstrip() + "..."
