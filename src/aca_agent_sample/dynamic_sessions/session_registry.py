"""In-process ownership registry; not a multi-tenant identity service."""
from __future__ import annotations

import re
import secrets
import threading

_ID = re.compile(r"^[A-Za-z0-9_-]{4,128}$")


def validate_session_id(session_id: str) -> str:
    if not isinstance(session_id, str) or not _ID.fullmatch(session_id):
        raise ValueError("Invalid session identifier")
    return session_id


def mask_session_id(session_id: str | None) -> str:
    return "[REDACTED]" if session_id else "[NOT SET]"


class SessionRegistry:
    def __init__(self) -> None:
        self._owners: dict[str, tuple[str, str]] = {}
        self._sessions: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def get_or_create(self, owner: str, conversation_id: str, session_id: str | None = None) -> str:
        if not isinstance(owner, str) or not owner or not isinstance(conversation_id, str) or not conversation_id:
            raise ValueError("Owner and conversation are required")
        key = (owner, conversation_id)
        with self._lock:
            existing = self._sessions.get(key)
            if session_id is not None:
                validate_session_id(session_id)
                assigned = self._owners.get(session_id)
                if assigned is not None and assigned != key:
                    raise PermissionError("Session belongs to another owner or conversation")
                if existing is not None and existing != session_id:
                    raise PermissionError("Conversation already has a different session")
                # Explicit IDs may only be claimed in this local single-user registry.
                chosen = session_id
            elif existing:
                return existing
            else:
                chosen = secrets.token_urlsafe(32)
                while chosen in self._owners:
                    chosen = secrets.token_urlsafe(32)
            self._owners[chosen] = key
            self._sessions[key] = chosen
            return chosen

    def require_owner(self, owner: str, conversation_id: str, session_id: str) -> None:
        validate_session_id(session_id)
        with self._lock:
            if self._owners.get(session_id) != (owner, conversation_id):
                raise PermissionError("Session ownership not established")
