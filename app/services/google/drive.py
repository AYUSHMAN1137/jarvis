import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

from googleapiclient.http import MediaFileUpload

from config import GOOGLE_DRIVE_SCOPES
from app.services.google.oauth import GoogleOAuthService

logger = logging.getLogger("J.A.R.V.I.S")

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


class DriveService:
    def __init__(self):
        self._oauth = GoogleOAuthService()
        self._service = None

    def is_configured(self) -> bool:
        return self._oauth.is_configured()

    def search_files_summary(self, query: str, max_results: int = 10) -> str:
        clean_query = (query or "").strip()
        if not clean_query:
            return "Tell me the Drive file name you want to search for."

        escaped = clean_query.replace("'", r"\'")
        items = self._list_files(
            query=f"name contains '{escaped}' and trashed=false",
            max_results=max_results,
        )
        if not items:
            return f"I couldn't find any Drive files matching '{clean_query}'."
        return self._format_items(
            items,
            heading=f"I found these Drive items for '{clean_query}':",
        )

    def list_items_summary(self, query: str = "", max_results: int = 15) -> str:
        clean_query = (query or "").strip()
        if not clean_query or clean_query.lower() in {"my drive", "root", "drive"}:
            items = self._list_files(
                query="'root' in parents and trashed=false",
                max_results=max_results,
            )
            if not items:
                return "Your Google Drive root folder is empty right now."
            return self._format_items(items, heading="Here are the items in your Drive root folder:")

        folder_id, folder_name, conflict_text = self._resolve_folder(clean_query)
        if conflict_text:
            return conflict_text

        if folder_id:
            items = self._list_files(
                query=f"'{folder_id}' in parents and trashed=false",
                max_results=max_results,
            )
            if not items:
                return f"The Drive folder '{folder_name}' is empty right now."
            return self._format_items(
                items,
                heading=f"Here are the items inside '{folder_name}':",
            )

        return self.search_files_summary(clean_query, max_results=max_results)

    def get_root_item_count(self, allow_interactive: bool = True) -> int:
        result = self._get_service(allow_interactive=allow_interactive).files().list(
            q="'root' in parents and trashed=false",
            pageSize=100,
            fields="files(id)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora="user",
        ).execute()
        return len(result.get("files", []))

    def upload_from_text(self, query: str) -> str:
        local_path, folder_query = self._parse_upload_request(query)
        if local_path is None:
            return (
                "Tell me the local file path to upload, like "
                "'upload C:\\Users\\me\\Desktop\\notes.pdf to Drive folder Docs'."
            )
        if not local_path.exists() or not local_path.is_file():
            return f"I couldn't find the local file '{local_path}'."

        folder_id = None
        folder_name = ""
        if folder_query:
            folder_id, folder_name, conflict_text = self._resolve_folder(folder_query)
            if conflict_text:
                return conflict_text
            if folder_id is None:
                return f"I couldn't find any Drive folder matching '{folder_query}'."

        metadata = {"name": local_path.name}
        if folder_id:
            metadata["parents"] = [folder_id]

        media = MediaFileUpload(str(local_path), resumable=True)
        created = self._get_service().files().create(
            body=metadata,
            media_body=media,
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        ).execute()
        destination = f" into '{folder_name}'" if folder_name else " into your Drive root folder"
        link = created.get("webViewLink")
        link_part = f" Link: {link}" if link else ""
        return f"I uploaded '{created.get('name', local_path.name)}'{destination}.{link_part}"

    def _get_service(self, allow_interactive: bool = True):
        if self._service is None:
            self._service = self._oauth.build_service(
                "drive",
                "v3",
                allow_interactive=allow_interactive,
                scopes=GOOGLE_DRIVE_SCOPES,
            )
        return self._service

    def _list_files(self, query: str, max_results: int) -> List[dict]:
        result = self._get_service().files().list(
            q=query,
            pageSize=max_results,
            fields="files(id,name,mimeType,modifiedTime,webViewLink)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            corpora="user",
            orderBy="folder,name",
        ).execute()
        return result.get("files", [])

    def _resolve_folder(self, query: str) -> Tuple[Optional[str], str, Optional[str]]:
        clean_query = re.sub(r"\bfolder\b", " ", query, flags=re.I)
        clean_query = re.sub(r"\s+", " ", clean_query).strip()
        escaped = clean_query.replace("'", r"\'")
        matches = self._list_files(
            query=(
                f"mimeType='{FOLDER_MIME_TYPE}' and trashed=false and "
                f"name contains '{escaped}'"
            ),
            max_results=10,
        )
        if not matches:
            return (None, clean_query, None)

        exact = [item for item in matches if (item.get("name") or "").lower() == clean_query.lower()]
        if len(exact) == 1:
            return (exact[0]["id"], exact[0].get("name", clean_query), None)

        if len(matches) > 1 and not exact:
            preview = self._format_items(matches[:3], heading="I found multiple Drive folders with similar names:")
            return (None, clean_query, preview + "\nPlease tell me the exact folder name.")

        chosen = (exact or matches)[0]
        return (chosen["id"], chosen.get("name", clean_query), None)

    def _format_items(self, items: List[dict], heading: str) -> str:
        lines = [heading]
        for idx, item in enumerate(items, 1):
            name = item.get("name", "Unnamed item")
            kind = "Folder" if item.get("mimeType") == FOLDER_MIME_TYPE else "File"
            modified = item.get("modifiedTime", "")
            modified_part = f" | Modified: {modified[:19].replace('T', ' ')}" if modified else ""
            lines.append(f"{idx}. {name} | Type: {kind}{modified_part}")
        return "\n".join(lines)

    def _parse_upload_request(self, query: str) -> Tuple[Optional[Path], str]:
        quoted_match = re.search(r'["\']([A-Za-z]:\\[^"\']+)["\']', query)
        path_text = quoted_match.group(1) if quoted_match else ""

        if not path_text:
            raw_match = re.search(r"([A-Za-z]:\\[^\n\r]+?\.[A-Za-z0-9]{1,10})", query)
            if raw_match:
                path_text = raw_match.group(1).strip().rstrip(".,")

        if not path_text:
            return (None, "")

        folder_match = re.search(
            r"\b(?:to|into|in)\s+(?:drive\s+)?folder\s+(.+)$",
            query,
            flags=re.I,
        )
        folder_query = folder_match.group(1).strip().rstrip(".") if folder_match else ""
        return (Path(path_text), folder_query)
