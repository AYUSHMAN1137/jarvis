import logging
import threading
from typing import Optional, Sequence

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config import (
    GOOGLE_CREDENTIALS_PATH,
    GOOGLE_SCOPES,
    GOOGLE_TOKEN_PATH,
    LEGACY_GMAIL_TOKEN_PATH,
)

logger = logging.getLogger("J.A.R.V.I.S")


class GoogleOAuthService:
    _lock = threading.Lock()
    _cached_credentials: Optional[Credentials] = None

    def __init__(self):
        self.credentials_path = GOOGLE_CREDENTIALS_PATH
        self.token_path = GOOGLE_TOKEN_PATH
        self.scopes = list(GOOGLE_SCOPES)

    def is_configured(self) -> bool:
        return self.credentials_path.exists()

    def build_service(
        self,
        api_name: str,
        version: str,
        allow_interactive: bool = True,
        scopes: Optional[Sequence[str]] = None,
    ):
        credentials = self.get_credentials(
            allow_interactive=allow_interactive,
            scopes=scopes,
        )
        return build(api_name, version, credentials=credentials, cache_discovery=False)

    def get_credentials(
        self,
        allow_interactive: bool = True,
        scopes: Optional[Sequence[str]] = None,
    ) -> Credentials:
        required_scopes = list(dict.fromkeys(scopes or self.scopes))

        with self._lock:
            creds = self._cached_credentials

            if creds and creds.valid and self._has_required_scopes(creds, required_scopes):
                return creds

            if creds is None:
                creds = self._load_saved_credentials()

            if creds and not self._has_required_scopes(creds, required_scopes):
                logger.info("[GOOGLE] Saved token is missing required scopes. Re-authentication is needed.")
                creds = None

            if creds and not creds.valid:
                if creds.expired and creds.refresh_token:
                    try:
                        creds.refresh(Request())
                    except Exception as exc:
                        logger.warning("[GOOGLE] Token refresh failed: %s", exc)
                        creds = None
                else:
                    creds = None

            if creds is None:
                if not allow_interactive:
                    raise RuntimeError("Google account is not authenticated yet.")

                if not self.credentials_path.exists():
                    raise RuntimeError(
                        f"Google credentials file not found at '{self.credentials_path}'. "
                        "Keep your downloaded OAuth JSON there before using Google integrations."
                    )

                logger.info("[GOOGLE] Starting local OAuth flow with combined Google scopes...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path),
                    required_scopes,
                )
                creds = flow.run_local_server(host="localhost", port=0, open_browser=True)

            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(creds.to_json(), encoding="utf-8")
            self._cached_credentials = creds
            logger.info("[GOOGLE] OAuth token saved to %s", self.token_path)
            return creds

    def _load_saved_credentials(self) -> Optional[Credentials]:
        for path in (self.token_path, LEGACY_GMAIL_TOKEN_PATH):
            if not path.exists():
                continue

            try:
                creds = Credentials.from_authorized_user_file(str(path))
                logger.info("[GOOGLE] Loaded saved token from %s", path)
                return creds
            except Exception as exc:
                logger.warning("[GOOGLE] Failed to load saved token from %s: %s", path, exc)

        return None

    @staticmethod
    def _has_required_scopes(creds: Credentials, required_scopes: Sequence[str]) -> bool:
        try:
            return creds.has_scopes(list(required_scopes))
        except Exception:
            token_scopes = set((creds.scopes or []))
            return set(required_scopes).issubset(token_scopes)
