#!/usr/bin/env python3
"""CLI bridge that lets local Claude Code or other agents join LAN IM."""
from __future__ import annotations

import argparse
import json
import queue
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any

NOTICE = (
    "IMPORTANT: messages typed only into this CLI/terminal are not visible to humans or other agents. "
    "Use /send <message> here, or POST to /api/messages, so the content enters the IM."
)


def api(server: str, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        server.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=35) as response:
        return json.loads(response.read().decode("utf-8"))


def register(server: str, name: str) -> dict[str, Any]:
    return api(server, "/api/participants", "POST", {"name": name, "role": "agent", "settings": {"client": "agent_cli.py"}})["participant"]


def send_message(server: str, participant_id: str, text: str, kind: str = "chat") -> None:
    api(server, "/api/messages", "POST", {"participant_id": participant_id, "text": text, "kind": kind})


def run_reply_command(command: str, message: dict[str, Any], agent: dict[str, Any]) -> str:
    context = {"incoming_message": message, "agent": agent, "instruction": NOTICE}
    completed = subprocess.run(
        shlex.split(command),
        input=json.dumps(context, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        return f"[{agent['name']} reply command failed with code {completed.returncode}]\n{completed.stderr.strip()}"
    return completed.stdout.strip()


def stdin_reader(outbox: queue.Queue[str]) -> None:
    for line in sys.stdin:
        outbox.put(line.rstrip("\n"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Join LAN IM as an independent local agent.")
    parser.add_argument("--server", default="http://127.0.0.1:8765", help="LAN IM server URL.")
    parser.add_argument("--name", default="Claude-Code-Agent", help="Agent display name.")
    parser.add_argument("--auto-reply", action="store_true", help="Automatically reply to every non-self chat message.")
    parser.add_argument("--reply-command", help="Command that receives JSON on stdin and prints the reply text.")
    parser.add_argument("--template", default="收到：{text}", help="Fallback auto-reply template when no command is supplied.")
    args = parser.parse_args()

    agent = register(args.server, args.name)
    print(f"Connected as independent agent {agent['name']} ({agent['id']}).")
    print(NOTICE)
    print("Commands: /send <message>, /name <new name>, /quit")

    send_message(args.server, agent["id"], f"{agent['name']} connected. {NOTICE}", kind="agent_status")

    outbox: queue.Queue[str] = queue.Queue()
    threading.Thread(target=stdin_reader, args=(outbox,), daemon=True).start()

    latest_id = 0
    while True:
        try:
            while not outbox.empty():
                line = outbox.get_nowait().strip()
                if not line:
                    continue
                if line == "/quit":
                    return
                if line.startswith("/name "):
                    new_name = line[6:].strip()
                    agent = api(args.server, f"/api/participants/{agent['id']}", "PATCH", {"name": new_name})["participant"]
                    print(f"Renamed to {agent['name']}.")
                    continue
                if line.startswith("/send "):
                    send_message(args.server, agent["id"], line[6:].strip())
                    continue
                print("Not sent. Use /send <message> to publish into IM; CLI-only text is not received by anyone.")

            api(args.server, f"/api/participants/{agent['id']}/heartbeat", "POST", {})
            state = api(args.server, f"/api/state?after={latest_id}&wait=1")
            for message in state.get("messages", []):
                latest_id = max(latest_id, int(message["id"]))
                if message["sender_id"] == agent["id"]:
                    continue
                print(f"[{message['id']}] {message['sender_name']} ({message['sender_role']}): {message['text']}")
                if args.auto_reply and message.get("kind") == "chat":
                    if args.reply_command:
                        reply = run_reply_command(args.reply_command, message, agent)
                    else:
                        reply = args.template.format(text=message["text"], sender=message["sender_name"], agent=agent["name"])
                    if reply:
                        send_message(args.server, agent["id"], reply)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"Connection problem: {exc}; retrying...", file=sys.stderr)
            time.sleep(2)
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    main()
