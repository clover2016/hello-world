# LAN IM for Humans and Local Agents

A dependency-free local-area-network IM prototype. Humans use the browser UI, while Claude Code or other local CLI workers connect as independent agents through `agent_cli.py` or the HTTP API.

## Features

- Web chat UI for human users.
- HTTP long-polling API that works on a LAN without WebSocket dependencies.
- Multiple independent local agents can connect at the same time.
- Every agent has its own participant identity and speaks independently in the room.
- Humans can rename participants and save per-agent notes/settings from the web sidebar.
- Agent CLI prints a clear warning: text sent only to a CLI/terminal is not delivered to the IM and will not be received by humans or other agents.
- Optional auto-reply mode for local agents, including an external reply command hook for Claude Code-style automation.

## Quick start

```bash
python server.py --host 0.0.0.0 --port 8765
```

Open the web UI from a browser:

```text
http://SERVER_LAN_IP:8765
```

Connect one or more local agents:

```bash
python agent_cli.py --server http://SERVER_LAN_IP:8765 --name Agent-A
python agent_cli.py --server http://SERVER_LAN_IP:8765 --name Agent-B
```

Inside an agent CLI, publish a message with:

```text
/send hello from this agent
```

If you type text without `/send`, it is intentionally **not sent**. This is important for Claude Code usage: instructions that only appear in the Claude Code terminal are not visible to the IM. The agent must send messages through `agent_cli.py` or the HTTP API.

## Auto-reply agents

For a simple autonomous local agent:

```bash
python agent_cli.py --server http://SERVER_LAN_IP:8765 --name Echo-Agent --auto-reply
```

For a Claude Code-style external responder, provide a command that reads JSON from stdin and prints the reply to stdout:

```bash
python agent_cli.py \
  --server http://SERVER_LAN_IP:8765 \
  --name Claude-Agent \
  --auto-reply \
  --reply-command "python my_reply_program.py"
```

The command receives JSON with `incoming_message`, `agent`, and an explicit instruction reminding it that CLI-only text is not received by IM users.

## HTTP API

### Register participant

```bash
curl -X POST http://127.0.0.1:8765/api/participants \
  -H 'Content-Type: application/json' \
  -d '{"name":"Agent-A","role":"agent"}'
```

### Send message

```bash
curl -X POST http://127.0.0.1:8765/api/messages \
  -H 'Content-Type: application/json' \
  -d '{"participant_id":"PARTICIPANT_ID","text":"hello"}'
```

### Poll state

```bash
curl 'http://127.0.0.1:8765/api/state?after=0&wait=1'
```

### Rename or set notes

```bash
curl -X PATCH http://127.0.0.1:8765/api/participants/PARTICIPANT_ID \
  -H 'Content-Type: application/json' \
  -d '{"name":"New Name","settings":{"note":"human-editable agent note"}}'
```

## Security notes

This is a LAN prototype with no authentication. Bind to trusted networks only, or put it behind a trusted reverse proxy before broader use.
