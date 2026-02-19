import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from config import SESSIONS_DIR

logger = logging.getLogger(__name__)


@dataclass
class SessionData:
    session_id: str
    title: str
    created_at: float
    updated_at: float
    conversation_history: list[dict] = field(default_factory=list)
    rag_enabled: bool = True


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, SessionData] = {}
        self._load_all()

    def _load_all(self):
        """Scan sessions directory on startup and load all JSON files."""
        for path in SESSIONS_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                session = SessionData(**data)
                self._sessions[session.session_id] = session
            except Exception as e:
                logger.warning("Failed to load session %s: %s", path.name, e)
        logger.info("Loaded %d sessions from disk", len(self._sessions))

    def _persist(self, session_id: str):
        """Write a single session to disk."""
        session = self._sessions.get(session_id)
        if not session:
            return
        path = SESSIONS_DIR / f"{session_id}.json"
        path.write_text(json.dumps(asdict(session), indent=2))

    def create(self, title: str = "New chat") -> SessionData:
        """Create a new session."""
        now = time.time()
        session = SessionData(
            session_id=str(uuid.uuid4()),
            title=title,
            created_at=now,
            updated_at=now,
        )
        self._sessions[session.session_id] = session
        self._persist(session.session_id)
        return session

    def get(self, session_id: str) -> SessionData | None:
        return self._sessions.get(session_id)

    def get_or_create(self, session_id: str | None) -> SessionData:
        """Get existing session or create a new one if id is None or not found."""
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        return self.create()

    def list_all(self) -> list[dict]:
        """List all sessions sorted by updated_at descending."""
        sessions = sorted(
            self._sessions.values(),
            key=lambda s: s.updated_at,
            reverse=True,
        )
        return [
            {
                "session_id": s.session_id,
                "title": s.title,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "message_count": len(s.conversation_history),
            }
            for s in sessions
        ]

    def delete(self, session_id: str) -> bool:
        """Delete a session from memory and disk."""
        if session_id not in self._sessions:
            return False
        del self._sessions[session_id]
        path = SESSIONS_DIR / f"{session_id}.json"
        if path.exists():
            path.unlink()
        return True

    def update_title(self, session_id: str, title: str) -> bool:
        session = self._sessions.get(session_id)
        if not session:
            return False
        session.title = title
        session.updated_at = time.time()
        self._persist(session_id)
        return True

    def append_history(self, session_id: str, role: str, content: str):
        """Append a message to session history. Auto-titles from first user message."""
        session = self._sessions.get(session_id)
        if not session:
            return
        session.conversation_history.append({"role": role, "content": content})
        session.updated_at = time.time()

        # Auto-title from first user message
        if role == "user" and session.title == "New chat":
            session.title = content[:60].strip()
            if len(content) > 60:
                session.title += "..."

        self._persist(session_id)

    def get_history(self, session_id: str, max_entries: int = 20) -> list[dict]:
        """Get recent conversation history for a session."""
        session = self._sessions.get(session_id)
        if not session:
            return []
        return session.conversation_history[-max_entries:]

    def set_rag_enabled(self, session_id: str, enabled: bool):
        session = self._sessions.get(session_id)
        if session:
            session.rag_enabled = enabled
            self._persist(session_id)


session_manager = SessionManager()
