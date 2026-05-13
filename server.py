#!/usr/bin/env python3
"""A dependency-free LAN IM server for humans and local CLI/Claude Code agents."""
from __future__ import annotations

import json
import mimetypes
import posixpath
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).parent.resolve()
WEB_ROOT = ROOT / "web"
MAX_BODY_BYTES = 128 * 1024
LONG_POLL_SECONDS = 25


@dataclass
class Participant:
    id: str
    name: str
    role: str
    created_at: float
    last_seen_at: float
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    id: int
    sender_id: str
    sender_name: str
    sender_role: str
    text: str
    created_at: float
    kind: str = "chat"


class ChatStore:
    def __init__(self) -> None:
        self._lock = threading.Condition()
        self._participants: dict[str, Participant] = {}
        self._messages: list[Message] = []
        self._next_message_id = 1

    def register(self, name: str, role: str, settings: dict[str, Any] | None = None) -> Participant:
        role = role if role in {"human", "agent", "system"} else "human"
        now = time.time()
        participant = Participant(
            id=uuid.uuid4().hex,
            name=(name or ("Agent" if role == "agent" else "Human")).strip()[:80],
            role=role,
            created_at=now,
            last_seen_at=now,
            settings=settings or {},
        )
        with self._lock:
            self._participants[participant.id] = participant
            self._append_system_locked(f"{participant.name} joined as {participant.role}.")
            self._lock.notify_all()
        return participant

    def update_participant(self, participant_id: str, patch: dict[str, Any]) -> Participant | None:
        with self._lock:
            participant = self._participants.get(participant_id)
            if not participant:
                return None
            old_name = participant.name
            if "name" in patch:
                name = str(patch["name"]).strip()
                if name:
                    participant.name = name[:80]
            if "settings" in patch and isinstance(patch["settings"], dict):
                participant.settings.update(patch["settings"])
            participant.last_seen_at = time.time()
            if old_name != participant.name:
                self._append_system_locked(f"{old_name} is now {participant.name}.")
            self._lock.notify_all()
            return participant

    def heartbeat(self, participant_id: str) -> bool:
        with self._lock:
            participant = self._participants.get(participant_id)
            if not participant:
                return False
            participant.last_seen_at = time.time()
            self._lock.notify_all()
            return True

    def add_message(self, participant_id: str, text: str, kind: str = "chat") -> Message | None:
        text = text.strip()
        if not text:
            return None
        with self._lock:
            participant = self._participants.get(participant_id)
            if not participant:
                return None
            participant.last_seen_at = time.time()
            msg = Message(
                id=self._next_message_id,
                sender_id=participant.id,
                sender_name=participant.name,
                sender_role=participant.role,
                text=text[:8000],
                created_at=time.time(),
                kind=kind[:40] or "chat",
            )
            self._next_message_id += 1
            self._messages.append(msg)
            self._lock.notify_all()
            return msg

    def snapshot(self, after: int = 0, wait: bool = False) -> dict[str, Any]:
        deadline = time.time() + LONG_POLL_SECONDS
        with self._lock:
            while wait and self._messages and self._messages[-1].id <= after and time.time() < deadline:
                self._lock.wait(timeout=min(1.0, deadline - time.time()))
            if wait and not self._messages:
                self._lock.wait(timeout=LONG_POLL_SECONDS)
            return {
                "participants": [self._participant_view(p) for p in self._participants.values()],
                "messages": [asdict(m) for m in self._messages if m.id > after],
                "latest_message_id": self._messages[-1].id if self._messages else 0,
                "server_time": time.time(),
            }

    def _append_system_locked(self, text: str) -> None:
        self._messages.append(
            Message(
                id=self._next_message_id,
                sender_id="system",
                sender_name="System",
                sender_role="system",
                text=text,
                created_at=time.time(),
                kind="system",
            )
        )
        self._next_message_id += 1

    @staticmethod
    def _participant_view(participant: Participant) -> dict[str, Any]:
        view = asdict(participant)
        view["online"] = time.time() - participant.last_seen_at < 45
        return view


STORE = ChatStore()


class Handler(BaseHTTPRequestHandler):
    server_version = "LanIM/1.0"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            query = parse_qs(parsed.query)
            after = self._int_query(query, "after", 0)
            wait = query.get("wait", ["0"])[0] in {"1", "true", "yes"}
            self._send_json(STORE.snapshot(after=after, wait=wait))
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/participants":
            body = self._read_json()
            participant = STORE.register(
                name=str(body.get("name") or ""),
                role=str(body.get("role") or "human"),
                settings=body.get("settings") if isinstance(body.get("settings"), dict) else {},
            )
            self._send_json({"participant": asdict(participant)}, HTTPStatus.CREATED)
            return
        if parsed.path == "/api/messages":
            body = self._read_json()
            msg = STORE.add_message(
                participant_id=str(body.get("participant_id") or ""),
                text=str(body.get("text") or ""),
                kind=str(body.get("kind") or "chat"),
            )
            if not msg:
                self._send_json({"error": "unknown participant_id or empty text"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"message": asdict(msg)}, HTTPStatus.CREATED)
            return
        if parsed.path.endswith("/heartbeat") and parsed.path.startswith("/api/participants/"):
            participant_id = parsed.path.split("/")[3]
            if not STORE.heartbeat(participant_id):
                self._send_json({"error": "unknown participant_id"}, HTTPStatus.NOT_FOUND)
                return
            self._send_json({"ok": True})
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_PATCH(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/participants/"):
            participant_id = parsed.path.split("/")[3]
            participant = STORE.update_participant(participant_id, self._read_json())
            if not participant:
                self._send_json({"error": "unknown participant_id"}, HTTPStatus.NOT_FOUND)
                return
            self._send_json({"participant": asdict(participant)})
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def _read_json(self) -> dict[str, Any]:
        length = min(int(self.headers.get("Content-Length", "0") or 0), MAX_BODY_BYTES)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, request_path: str) -> None:
        safe_path = posixpath.normpath(unquote(request_path)).lstrip("/")
        file_path = WEB_ROOT / (safe_path or "index.html")
        if file_path.is_dir():
            file_path = file_path / "index.html"
        if not str(file_path.resolve()).startswith(str(WEB_ROOT)) or not file_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _int_query(query: dict[str, list[str]], key: str, default: int) -> int:
        try:
            return int(query.get(key, [str(default)])[0])
        except ValueError:
            return default


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Start a dependency-free LAN IM server.")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address. Use 0.0.0.0 for LAN access.")
    parser.add_argument("--port", default=8765, type=int, help="HTTP port.")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"LAN IM listening on http://{args.host}:{args.port}")
    print("Browser users open the URL. Claude Code/CLI agents must send messages through agent_cli.py or the HTTP API.")
    server.serve_forever()


if __name__ == "__main__":
    main()
