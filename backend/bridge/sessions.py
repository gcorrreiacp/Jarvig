"""In-memory conversation history. Swap for Redis/Postgres in production."""
from collections import defaultdict, deque

from agent.base import Message


class SessionStore:
    def __init__(self, limit: int):
        self._sessions: dict[str, deque[Message]] = defaultdict(lambda: deque(maxlen=limit))

    def history(self, session_id: str) -> list[Message]:
        return list(self._sessions[session_id])

    def append(self, session_id: str, message: Message) -> None:
        self._sessions[session_id].append(message)

    def reset(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def turns(self, session_id: str) -> int:
        return sum(1 for m in self._sessions.get(session_id, []) if m.role == "user")
