import asyncio
import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional

from dotenv import load_dotenv
load_dotenv()

if not os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("CLAUDE_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = os.environ["CLAUDE_API_KEY"]

import anthropic
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = "cards.db"
MODEL = "claude-haiku-4-5-20251001"
FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="14" fill="#0f172a"/>
<rect x="8" y="8" width="48" height="48" rx="10" fill="#6366f1"/>
<text x="32" y="41" text-anchor="middle" font-family="Arial,sans-serif" font-size="22" font-weight="700" fill="white">CS</text>
</svg>"""

# Bump this string whenever the tool list or system prompt changes to force agent recreation.
TOOLS_VERSION = "v4-dual-path"

SUBMIT_CARD_TOOL = {
    "type": "custom",
    "name": "submit_card",
    "description": (
        "Submit the completed card artifact. Call this EXACTLY ONCE as your very last "
        "action after producing the artifact. The payload schema varies by card type."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["chart", "widget", "data", "research", "code"],
            },
            "title": {
                "type": "string",
                "description": "Descriptive title, 5-8 words",
            },
            "payload": {
                "type": "object",
                "description": (
                    "chart → {image_b64, description} | "
                    "widget → {html_b64} | "
                    "data → {columns, rows, insight} | "
                    "research → {summary, citations:[{title,url}]} | "
                    "code → {language, code, stdout}"
                ),
            },
        },
        "required": ["type", "title", "payload"],
    },
}

SYSTEM_PROMPT = """You are an AI Card Studio agent. For every user prompt produce ONE artifact, then call `submit_card` as your very last action.

## Card types
- chart   → data visualization (bar, line, scatter, pie…)
- widget  → interactive HTML/JS tool (calculator, converter, timer…)
- data    → tabular analysis with key insight
- research→ web research with citations
- code    → write + run a Python script

---

## Per-type instructions

### chart
1. bash: `pip install -q matplotlib`
2. write /tmp/chart.py using this EXACT template — do not change the print lines:
```python
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
import base64, io

fig, ax = plt.subplots(figsize=(2.5, 2), dpi=40)

# --- YOUR PLOT CODE HERE ---

plt.tight_layout()
buf = io.BytesIO()
plt.savefig(buf, format='png', bbox_inches='tight')
buf.seek(0)
b64 = base64.b64encode(buf.read()).decode()
print("__CARD_IMAGE_B64__")
print(b64)
```
3. bash: `python3 /tmp/chart.py`
   Output: first line is `__CARD_IMAGE_B64__`, second line is the base64 string.
4. call `submit_card` with type="chart", title="…", payload={
     "image_b64": "<the base64 string — the line AFTER __CARD_IMAGE_B64__ in step 3 output>",
     "description": "one sentence describing the chart"
   }

### widget
1. write /tmp/widget.html — complete self-contained HTML/CSS/JS (no external URLs)
2. bash:
```bash
python3 -c "
import base64
b = base64.b64encode(open('/tmp/widget.html','rb').read()).decode()
print('__CARD_HTML_B64__')
print(b)
"
```
   Output: first line is `__CARD_HTML_B64__`, second line is the base64 string.
3. call `submit_card` with type="widget", title="…", payload={
     "html_b64": "<the base64 string — the line AFTER __CARD_HTML_B64__ in step 2 output>"
   }

### data
1. bash: fetch / compute data with Python
2. bash: write and print payload:
```python
import json
payload = {
  "columns": ["Col1", "Col2"],
  "rows": [["v1", "v2"]],
  "insight": "ONE-SENTENCE KEY FINDING"
}
print(json.dumps(payload))
```
3. call `submit_card` with type="data", title="…", payload=<the exact dict above>

### research
1. web_search: 1-2 targeted queries
2. web_fetch: read top 2-3 sources
3. call `submit_card` with type="research", title="…", payload={
     "summary": "3-5 sentence summary",
     "citations": [{"title": "Page Title", "url": "https://…"}]
   }

### code
1. write /tmp/script.py
2. bash: `python3 /tmp/script.py 2>&1 | tee /tmp/stdout.txt`
3. bash: `cat /tmp/script.py` — read the code text
4. bash: `cat /tmp/stdout.txt` — read the output
5. call `submit_card` with type="code", title="…", payload={
     "language": "python",
     "code": "<contents of /tmp/script.py>",
     "stdout": "<contents of /tmp/stdout.txt>"
   }

---

## Rules
- ALWAYS actually run bash commands — never simulate output
- NEVER manually type base64 strings — always let Python encode them
- For chart and widget, do NOT include image_b64/html_b64 in submit_card — the system handles it
- Call `submit_card` EXACTLY ONCE as your very last action
- Keep titles to 5-8 words
"""


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cards (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            prompt TEXT NOT NULL,
            payload TEXT NOT NULL,
            tool_trace TEXT NOT NULL DEFAULT '[]',
            agent_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
    """)
    # Non-destructive migration: add agent_message if the column doesn't exist yet
    try:
        conn.execute("ALTER TABLE cards ADD COLUMN agent_message TEXT NOT NULL DEFAULT ''")
        conn.commit()
        logger.info("Migrated: added agent_message column")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def get_meta(key: str) -> Optional[str]:
    conn = get_db()
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def set_meta(key: str, value: str):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


client = anthropic.Anthropic()

session_queues: dict[str, asyncio.Queue] = {}
# Binary data captured from bash tool results keyed by session_id.
# Avoids the LLM having to relay large base64 strings through submit_card.
session_captured: dict[str, dict] = {}
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def ensure_agent_and_env() -> tuple[str, str]:
    agent_id = get_meta("agent_id")
    env_id = get_meta("env_id")
    tools_ver = get_meta("tools_version")

    if not agent_id or tools_ver != TOOLS_VERSION:
        if agent_id:
            logger.info("Tools version changed (%s → %s); recreating agent...", tools_ver, TOOLS_VERSION)
        else:
            logger.info("Creating Managed Agent...")
        agent = client.beta.agents.create(
            name="AI Card Studio",
            model=MODEL,
            system=SYSTEM_PROMPT,
            tools=[
                {"type": "agent_toolset_20260401"},
                SUBMIT_CARD_TOOL,
            ],
        )
        agent_id = agent.id
        set_meta("agent_id", agent_id)
        set_meta("tools_version", TOOLS_VERSION)
        logger.info("Agent created: %s", agent_id)
    else:
        logger.info("Reusing agent: %s", agent_id)

    if not env_id:
        logger.info("Creating cloud environment...")
        env = client.beta.environments.create(
            name="card-studio-env",
            config={"type": "cloud", "networking": {"type": "unrestricted"}},
        )
        env_id = env.id
        set_meta("env_id", env_id)
        logger.info("Environment created: %s", env_id)
    else:
        logger.info("Reusing environment: %s", env_id)

    return agent_id, env_id


def _tool_summary(name: str, inp) -> str:
    if not isinstance(inp, dict):
        return str(inp)[:200]
    if name == "bash":
        cmd = inp.get("command", "")
        return cmd[:200] + ("…" if len(cmd) > 200 else "")
    if name in ("write", "read", "edit"):
        return inp.get("path", inp.get("file_path", ""))
    if name in ("glob", "grep"):
        return inp.get("pattern", inp.get("path", ""))
    if name == "web_search":
        return inp.get("query", "")
    if name == "web_fetch":
        return inp.get("url", "")
    if name == "submit_card":
        return f'type={inp.get("type","?")}, title="{inp.get("title","?")}"'
    return json.dumps(inp)[:200]


def _json_loads_or_default(value: str, default):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _normalize_jsonish(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped[0] in "[{" and stripped[-1] in "]}":
            try:
                return _normalize_jsonish(json.loads(stripped))
            except json.JSONDecodeError:
                pass
        return value

    if isinstance(value, Mapping):
        return {str(k): _normalize_jsonish(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_normalize_jsonish(v) for v in value]

    for attr in ("model_dump", "to_dict", "dict"):
        fn = getattr(value, attr, None)
        if callable(fn):
            try:
                return _normalize_jsonish(fn())
            except TypeError:
                continue

    if hasattr(value, "__dict__"):
        try:
            return {
                str(k): _normalize_jsonish(v)
                for k, v in vars(value).items()
                if not k.startswith("_")
            }
        except TypeError:
            pass

    return value


def run_agent_session(session_id: str, prompt: str):
    """Runs in a background thread; feeds events into the asyncio queue."""
    tool_trace: list[dict] = []
    full_message = ""
    card_spec_from_tool: Optional[dict] = None

    def push(evt: dict):
        if _main_loop and session_id in session_queues:
            asyncio.run_coroutine_threadsafe(
                session_queues[session_id].put(evt), _main_loop
            )

    try:
        with client.beta.sessions.events.stream(session_id) as stream:
            # Open stream first, then send — prevents race condition
            client.beta.sessions.events.send(
                session_id,
                events=[{
                    "type": "user.message",
                    "content": [{"type": "text", "text": prompt}],
                }],
            )

            for event in stream:
                t = event.type
                logger.debug("Event: %s", t)

                if t == "agent.message":
                    text = ""
                    for block in getattr(event, "content", []):
                        if hasattr(block, "text") and block.text:
                            text += block.text
                    if text:
                        full_message += text
                        push({"type": "agent.message", "text": text})

                elif t in ("agent.tool_use", "agent.custom_tool_use"):
                    name = getattr(event, "name", "tool")
                    inp = _normalize_jsonish(getattr(event, "input", {}))
                    tool_id = (
                        getattr(event, "custom_tool_use_id", None)
                        or getattr(event, "id", None)
                        or getattr(event, "tool_use_id", None)
                        or getattr(event, "tool_call_id", None)
                    )
                    if isinstance(inp, str):
                        inp = _json_loads_or_default(inp, {"value": inp})
                    elif not isinstance(inp, dict):
                        inp = {"value": inp}

                    if name == "submit_card":
                        card_spec_from_tool = inp
                        logger.info("submit_card called: type=%s title=%s", inp.get("type"), inp.get("title"))
                        try:
                            client.beta.sessions.events.send(
                                session_id,
                                events=[{
                                    "type": "user.custom_tool_result",
                                    "custom_tool_use_id": tool_id,
                                    "content": [{"type": "text", "text": "Card submitted successfully."}],
                                }],
                            )
                        except Exception as e:
                            logger.warning("Could not send custom_tool_result: %s", e)
                        # Send a special event so the frontend can show a success indicator
                        push({
                            "type": "agent.submit_card",
                            "card_type": inp.get("type", "?"),
                            "title": inp.get("title", "?"),
                        })

                    summary = _tool_summary(name, inp)
                    tool_trace.append({"tool": name, "input": summary})
                    push({"type": "agent.tool_use", "name": name, "summary": summary})

                elif t == "agent.tool_result":
                    # Try all known attribute names — SDK versions differ
                    raw = (
                        getattr(event, "content", None)
                        or getattr(event, "output", None)
                        or getattr(event, "text", None)
                        or getattr(event, "result", None)
                        or ""
                    )
                    if isinstance(raw, list):
                        raw = "\n".join(str(getattr(b, "text", b)) for b in raw)
                    content_str = str(raw)
                    logger.info("agent.tool_result (%d chars): %s", len(content_str), repr(content_str[:300]))

                    # Primary capture path: intercept binary markers so the LLM
                    # doesn't have to relay large base64 strings.
                    for marker, key in (("__CARD_IMAGE_B64__", "image_b64"),
                                        ("__CARD_HTML_B64__",  "html_b64")):
                        if marker in content_str:
                            after = content_str[content_str.index(marker) + len(marker):]
                            # Take everything up to the first whitespace-only line (end of b64 block)
                            b64 = after.strip().split("\n")[0].strip()
                            if b64:
                                session_captured.setdefault(session_id, {})[key] = b64
                                logger.info("Captured %s via tool_result: %d chars", key, len(b64))

                    # Legacy fallback
                    if "<card_spec>" in content_str:
                        full_message += "\n" + content_str
                    push({"type": "agent.tool_result", "preview": content_str[:300]})

                elif t == "session.status_idle":
                    captured = session_captured.pop(session_id, {})
                    if card_spec_from_tool:
                        payload = card_spec_from_tool.get("payload", {})
                        if not isinstance(payload, dict):
                            payload = {}
                        for key in ("image_b64", "html_b64"):
                            if captured.get(key):
                                # Prefer backend-captured (direct from tool output) over
                                # whatever the agent put in submit_card
                                payload[key] = captured[key]
                                logger.info("Used captured %s (%d chars)", key, len(captured[key]))
                            elif not payload.get(key):
                                logger.warning("No %s found in payload or captured data", key)
                        card_spec_from_tool["payload"] = payload
                        card = _save_card(prompt, card_spec_from_tool, tool_trace, full_message)
                    else:
                        logger.warning("submit_card not called; falling back to <card_spec> regex")
                        card = _parse_and_save_card(prompt, full_message, tool_trace)
                    if card:
                        push({"type": "card.created", "card": card})
                    else:
                        push({
                            "type": "error",
                            "message": (
                                "Agent did not submit a card. "
                                "It may not have called submit_card, or the payload was invalid. "
                                "Check server logs for details."
                            ),
                        })
                    break

                elif t in ("session.status_terminated", "session.error"):
                    err = getattr(event, "error", {})
                    if isinstance(err, dict):
                        err = err.get("message", str(err))
                    push({"type": "error", "message": f"{t}: {err}"})
                    break

                else:
                    # Log unrecognised event types — helps diagnose SDK differences
                    attrs = {a: str(getattr(event, a, ""))[:80]
                             for a in dir(event) if not a.startswith("_")}
                    logger.info("Unknown event %s: %s", t, attrs)

    except Exception as e:
        logger.error("Agent session error %s: %s", session_id, e, exc_info=True)
        push({"type": "error", "message": str(e)})
    finally:
        push({"type": "__done__"})


def _save_card(
    prompt: str, spec: dict, tool_trace: list, agent_message: str = ""
) -> Optional[dict]:
    spec = _normalize_jsonish(spec)
    if not isinstance(spec, dict):
        logger.error("submit_card spec was not a dict: %r", spec)
        return None
    payload = spec.get("payload", {})
    payload = _normalize_jsonish(payload)
    if not isinstance(payload, dict):
        payload = {}
    card_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    card = {
        "id": card_id,
        "type": spec.get("type", "unknown"),
        "title": spec.get("title", "Untitled"),
        "prompt": prompt,
        "payload": payload,
        "tool_trace": tool_trace,
        "agent_message": agent_message,
        "created_at": now,
    }
    conn = get_db()
    conn.execute(
        "INSERT INTO cards (id, type, title, prompt, payload, tool_trace, agent_message, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            card["id"], card["type"], card["title"], card["prompt"],
            json.dumps(card["payload"]), json.dumps(card["tool_trace"]),
            agent_message, card["created_at"],
        ),
    )
    conn.commit()
    conn.close()
    logger.info("Card saved: %s (%s) — %s", card["id"], card["type"], card["title"])
    return card


def _parse_and_save_card(
    prompt: str, message: str, tool_trace: list
) -> Optional[dict]:
    """Fallback: extract card spec from <card_spec>…</card_spec> tags in the agent message."""
    match = re.search(r"<card_spec>(.*?)</card_spec>", message, re.DOTALL)
    if not match:
        logger.error("No <card_spec> block found. Message tail:\n%s", message[-800:])
        return None
    raw = match.group(1).strip()
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in card_spec: %s\nRaw: %s", e, raw[:400])
        return None
    return _save_card(prompt, spec, tool_trace, message)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    init_db()
    try:
        ensure_agent_and_env()
        logger.info("AI Card Studio ready at http://localhost:8080")
    except Exception as e:
        logger.error("Startup failed: %s", e)
    yield


app = FastAPI(title="AI Card Studio", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def serve_frontend():
    return FileResponse("index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=FAVICON_SVG, media_type="image/svg+xml")


class PromptRequest(BaseModel):
    prompt: str
    file_ids: List[str] = []


@app.post("/api/prompt")
async def api_prompt(req: PromptRequest):
    agent_id = get_meta("agent_id")
    env_id = get_meta("env_id")
    if not agent_id or not env_id:
        raise HTTPException(500, "Agent/environment not initialized — check server logs")

    loop = asyncio.get_running_loop()
    session_kwargs: dict = dict(agent=agent_id, environment_id=env_id, title=req.prompt[:100])
    if req.file_ids:
        session_kwargs["resources"] = [{"type": "file", "file_id": fid} for fid in req.file_ids]

    session = await loop.run_in_executor(
        None,
        lambda: client.beta.sessions.create(**session_kwargs),
    )
    session_id = session.id
    logger.info("Session created: %s (files: %s)", session_id, req.file_ids or "none")

    session_queues[session_id] = asyncio.Queue()
    threading.Thread(
        target=run_agent_session,
        args=(session_id, req.prompt),
        daemon=True,
    ).start()

    return {"session_id": session_id}


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    content = await file.read()
    filename = file.filename or "upload"
    content_type = file.content_type or "application/octet-stream"
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: client.beta.files.upload(
                file=(filename, content, content_type),
            ),
        )
        logger.info("File uploaded: %s → %s", filename, result.id)
        return {"file_id": result.id, "filename": filename, "size": len(content)}
    except Exception as e:
        logger.error("File upload failed: %s", e, exc_info=True)
        raise HTTPException(500, f"Upload failed: {e}")


@app.get("/api/stream/{session_id}")
async def api_stream(session_id: str):
    if session_id not in session_queues:
        raise HTTPException(404, "Session not found")
    queue = session_queues[session_id]

    async def generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=300.0)
                except asyncio.TimeoutError:
                    yield 'data: {"type":"error","message":"stream timeout after 300s"}\n\n'
                    break
                if event.get("type") == "__done__":
                    break
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            session_queues.pop(session_id, None)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/cards")
async def api_cards():
    conn = get_db()
    rows = conn.execute("SELECT * FROM cards ORDER BY created_at DESC").fetchall()
    conn.close()
    cards = []
    for row in rows:
        row_keys = set(row.keys()) if hasattr(row, "keys") else set()
        cards.append(
            {
                "id": row["id"],
                "type": row["type"],
                "title": row["title"],
                "prompt": row["prompt"],
                "payload": _json_loads_or_default(row["payload"], {}),
                "tool_trace": _json_loads_or_default(row["tool_trace"], []),
                "agent_message": row["agent_message"] if "agent_message" in row_keys else "",
                "created_at": row["created_at"],
            }
        )
    return {"cards": cards}


@app.delete("/api/cards/{card_id}")
async def api_delete_card(card_id: str):
    conn = get_db()
    conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
