import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from dateparser.search import search_dates

from config import GOOGLE_CALENDAR_SCOPES
from app.services.google.oauth import GoogleOAuthService

logger = logging.getLogger("J.A.R.V.I.S")


class CalendarService:
    def __init__(self):
        self._oauth = GoogleOAuthService()
        self._service = None

    def is_configured(self) -> bool:
        return self._oauth.is_configured()

    def get_today_events_summary(self, max_results: int = 10) -> str:
        now = datetime.now().astimezone()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)
        events = self._list_events(
            time_min=start_of_day,
            time_max=end_of_day,
            max_results=max_results,
        )
        if not events:
            return "You have no events scheduled for today."
        return self._format_events(
            events,
            heading=f"Here are your {len(events)} event(s) for today:",
        )

    def get_today_event_count(self, allow_interactive: bool = True) -> int:
        now = datetime.now().astimezone()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)
        events = self._list_events(
            time_min=start_of_day,
            time_max=end_of_day,
            max_results=50,
            allow_interactive=allow_interactive,
        )
        return len(events)

    def get_upcoming_events_summary(self, max_results: int = 10) -> str:
        now = datetime.now().astimezone()
        events = self._list_events(
            time_min=now,
            max_results=max_results,
        )
        if not events:
            return "You have no upcoming calendar events right now."
        return self._format_events(
            events,
            heading=f"Here are your next {len(events)} upcoming event(s):",
        )

    def search_events_summary(self, query: str, max_results: int = 10) -> str:
        clean_query = (query or "").strip()
        if not clean_query:
            return self.get_upcoming_events_summary(max_results=max_results)

        now = datetime.now().astimezone()
        events = self._list_events(
            time_min=now - timedelta(days=365),
            time_max=now + timedelta(days=365 * 2),
            query=clean_query,
            max_results=max_results,
        )
        if not events:
            return f"I couldn't find any calendar events matching '{clean_query}'."
        return self._format_events(
            events,
            heading=f"I found these calendar matches for '{clean_query}':",
        )

    def create_event_from_text(self, query: str) -> str:
        clean_query = (query or "").strip()
        if not clean_query:
            return "Tell me the event title and time, like 'create a meeting tomorrow at 5 pm'."

        parsed = self._parse_event_request(clean_query)
        if parsed is None:
            return (
                "I couldn't understand the event time. Try something like "
                "'create a doctor appointment tomorrow at 6 pm'."
            )

        title, start_dt, end_dt, is_all_day = parsed
        service = self._get_service()
        body = {
            "summary": title,
            "description": f"Created by Jarvis from: {clean_query}",
        }

        if is_all_day:
            body["start"] = {"date": start_dt.date().isoformat()}
            body["end"] = {"date": end_dt.date().isoformat()}
        else:
            body["start"] = {"dateTime": start_dt.isoformat()}
            body["end"] = {"dateTime": end_dt.isoformat()}

        created = service.events().insert(calendarId="primary", body=body).execute()
        when = self._format_event_time(created)
        return f"I created the event '{title}' for {when}."

    def delete_event_from_text(self, query: str) -> str:
        clean_query = (query or "").strip()
        if not clean_query:
            return "Tell me which event to delete, like 'delete dentist appointment tomorrow'."

        matches = self._list_events(
            time_min=datetime.now().astimezone() - timedelta(days=365),
            time_max=datetime.now().astimezone() + timedelta(days=365 * 2),
            query=clean_query,
            max_results=10,
        )

        if not matches:
            return f"I couldn't find any event matching '{clean_query}' to delete."

        best_matches = self._pick_best_matches(matches, clean_query)
        if len(best_matches) > 1:
            preview = self._format_events(best_matches[:3], heading="I found multiple matching events:")
            return preview + "\nPlease say the exact event title or include the date."

        event = best_matches[0]
        self._get_service().events().delete(calendarId="primary", eventId=event["id"]).execute()
        title = event.get("summary", "Untitled event")
        when = self._format_event_time(event)
        return f"I deleted the event '{title}' scheduled for {when}."

    def _get_service(self, allow_interactive: bool = True):
        if self._service is None:
            self._service = self._oauth.build_service(
                "calendar",
                "v3",
                allow_interactive=allow_interactive,
                scopes=GOOGLE_CALENDAR_SCOPES,
            )
        return self._service

    def _list_events(
        self,
        time_min: datetime,
        time_max: Optional[datetime] = None,
        query: Optional[str] = None,
        max_results: int = 10,
        allow_interactive: bool = True,
    ) -> List[dict]:
        params = {
            "calendarId": "primary",
            "timeMin": time_min.isoformat(),
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": max_results,
        }
        if time_max is not None:
            params["timeMax"] = time_max.isoformat()
        if query:
            params["q"] = query

        result = self._get_service(allow_interactive=allow_interactive).events().list(**params).execute()
        return result.get("items", [])

    def _format_events(self, events: List[dict], heading: str) -> str:
        lines = [heading]
        for idx, event in enumerate(events, 1):
            title = event.get("summary", "Untitled event")
            when = self._format_event_time(event)
            location = (event.get("location") or "").strip()
            location_part = f" | Location: {location}" if location else ""
            lines.append(f"{idx}. {title} | When: {when}{location_part}")
        return "\n".join(lines)

    def _format_event_time(self, event: dict) -> str:
        start = event.get("start", {})
        end = event.get("end", {})
        start_dt = start.get("dateTime")
        end_dt = end.get("dateTime")
        start_date = start.get("date")

        if start_date and not start_dt:
            return f"all day on {self._format_date_string(start_date)}"

        start_text = self._format_datetime_string(start_dt)
        end_text = self._format_datetime_string(end_dt)
        if start_text and end_text:
            return f"{start_text} to {end_text}"
        return start_text or "an unknown time"

    def _parse_event_request(
        self,
        query: str,
    ) -> Optional[Tuple[str, datetime, datetime, bool]]:
        normalized = self._normalize_natural_time_words(query)
        matches = search_dates(
            normalized,
            settings={
                "PREFER_DATES_FROM": "future",
                "RETURN_AS_TIMEZONE_AWARE": True,
            },
        )
        if not matches:
            return None

        clean_matches = [(text.strip(), dt.astimezone()) for text, dt in matches if text and dt]
        if not clean_matches:
            return None

        title = self._extract_event_title(query, clean_matches)
        start_dt = clean_matches[0][1]
        is_all_day = self._looks_all_day(query, clean_matches[0][0])

        if is_all_day:
            day_start = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            return (
                title,
                day_start,
                day_start + timedelta(days=1),
                True,
            )

        end_dt = self._resolve_end_time(clean_matches, start_dt)
        return (title, start_dt, end_dt, False)

    def _resolve_end_time(
        self,
        matches: List[Tuple[str, datetime]],
        start_dt: datetime,
    ) -> datetime:
        if len(matches) >= 2:
            candidate = matches[1][1]
            if candidate > start_dt:
                return candidate
        return start_dt + timedelta(hours=1)

    def _extract_event_title(
        self,
        original_query: str,
        matches: List[Tuple[str, datetime]],
    ) -> str:
        cleaned = original_query
        for matched_text, _ in matches:
            cleaned = re.sub(re.escape(matched_text), " ", cleaned, flags=re.I)

        cleaned = re.sub(
            r"\b(?:create|add|schedule|set|make|new|calendar|event|reminder|appointment|meeting|birthday|for|on|at|from|to|please|jarvis)\b",
            " ",
            cleaned,
            flags=re.I,
        )
        cleaned = re.sub(r"\bto\b", " ", cleaned, flags=re.I)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,-")
        if not cleaned:
            return "Jarvis reminder"
        return cleaned[0].upper() + cleaned[1:]

    def _looks_all_day(self, query: str, matched_text: str) -> bool:
        lowered = query.lower()
        if any(token in lowered for token in ("all day", "birthday", "anniversary")):
            return True
        time_hint = re.search(r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b|\b\d{1,2}:\d{2}\b", matched_text, re.I)
        return time_hint is None

    def _pick_best_matches(self, events: List[dict], query: str) -> List[dict]:
        lowered = query.lower()
        exact = [event for event in events if (event.get("summary") or "").strip().lower() == lowered]
        if exact:
            return exact[:1]

        partial = [event for event in events if lowered in (event.get("summary") or "").lower()]
        if len(partial) == 1:
            return partial
        if partial:
            return partial[:3]
        return events[:3]

    def _normalize_natural_time_words(self, text: str) -> str:
        replacements = {
            "aaj": "today",
            "aj": "today",
            "kal": "tomorrow",
            "parso": "day after tomorrow",
            "subah": "morning",
            "dopahar": "afternoon",
            "shaam": "evening",
            "raat": "night",
        }
        normalized = f" {text} "
        for src, dst in replacements.items():
            normalized = re.sub(rf"\b{re.escape(src)}\b", dst, normalized, flags=re.I)
        return normalized.strip()

    def _format_datetime_string(self, value: Optional[str]) -> str:
        if not value:
            return ""
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
            return dt.strftime("%a, %d %b %Y %I:%M %p")
        except Exception:
            return value

    def _format_date_string(self, value: str) -> str:
        try:
            dt = datetime.fromisoformat(value)
            return dt.strftime("%a, %d %b %Y")
        except Exception:
            return value
