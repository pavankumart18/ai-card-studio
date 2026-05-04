import asyncio
import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

# Support both CLAUDE_API_KEY and ANTHROPIC_API_KEY in .env
if not os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("CLAUDE_API_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = os.environ["CLAUDE_API_KEY"]

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = "cards.db"
MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """You are an AI Card Studio agent. For every user prompt you produce ONE card artifact.

## Card types
- chart   → data visualization (bar, line, scatter, pie…)
- widget  → interactive HTML/JS tool (calculator, timer, converter…)
- data    → tabular analysis (fetch data, run pandas, return table + insight)
- research→ web research with citations
- code    → write + run a script, capture output

---

## GOLDEN RULE — write /tmp/card.json with Python, then print it

For EVERY card type your LAST two steps are always:

  Step A — write the card spec to /tmp/card.json using json.dump (Python handles all escaping):
  ```python
  import json
  spec = {"type": "...", "title": "...", "payload": {...}}
  json.dump(spec, open("/tmp/card.json", "w"))
  ```

  Step B — bash: `python3 -c "import json,sys; d=json.load(open('/tmp/card.json')); print('<card_spec>'); print(json.dumps(d)); print('</card_spec>')"`

  Copy the ENTIRE output of Step B as the last content of your response.

---

## Instructions per card type

### chart
1. bash: `pip install -q matplotlib`
2. write: /tmp/chart.py  — use this exact template:
```python
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import base64, io, json

fig, ax = plt.subplots(figsize=(6, 4), dpi=80)

# --- YOUR PLOT CODE HERE ---

plt.tight_layout()
buf = io.BytesIO()
plt.savefig(buf, format='png', bbox_inches='tight', dpi=80)
buf.seek(0)
b64 = base64.b64encode(buf.read()).decode()

json.dump(
    {"type": "chart", "title": "YOUR TITLE", "payload": {"image_b64": b64, "description": "YOUR DESCRIPTION"}},
    open("/tmp/card.json", "w")
)
print("chart written, b64 length:", len(b64))
```
3. bash: `python3 /tmp/chart.py`
4. bash (Step B above): print the card_spec

### widget
1. write: /tmp/widget.html — complete self-contained HTML with inline CSS/JS
2. bash: write card.json:
```bash
python3 -c "
import base64, json
html = open('/tmp/widget.html','rb').read()
b64 = base64.b64encode(html).decode()
json.dump({'type':'widget','title':'TITLE','payload':{'html_b64':b64}}, open('/tmp/card.json','w'))
print('widget written, b64 length:', len(b64))
"
```
3. bash (Step B above): print the card_spec

### data
1. bash: fetch or compute the data with python/curl
2. bash: write card.json:
```python
import json
spec = {
  "type": "data",
  "title": "TITLE",
  "payload": {
    "columns": ["Col1", "Col2"],
    "rows": [["v1", "v2"]],
    "insight": "KEY FINDING"
  }
}
json.dump(spec, open("/tmp/card.json", "w"))
```
3. bash (Step B above): print the card_spec

### research
1. web_search: 1-2 targeted queries
2. web_fetch: read the top 2 sources
3. bash: write card.json with summary + citations using json.dump
4. bash (Step B above): print the card_spec

### code
1. write: /tmp/script.py
2. bash: `python3 /tmp/script.py 2>&1 | tee /tmp/stdout.txt`
3. bash: write card.json:
```python
import json
code = open('/tmp/script.py').read()
stdout = open('/tmp/stdout.txt').read()
json.dump({"type":"code","title":"TITLE","payload":{"language":"python","code":code,"stdout":stdout}}, open("/tmp/card.json","w"))
```
4. bash (Step B above): print the card_spec

---

## Rules
- ALWAYS actually run bash commands
- NEVER manually type base64 strings — always let Python encode them
- /tmp/card.json MUST be written before Step B
- The <card_spec> block MUST be the very last content in your response
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
            created_at TEXT NOT NULL
        );
    """)
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

# session_id -> asyncio.Queue for SSE events
session_queues: dict[str, asyncio.Queue] = {}
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def ensure_agent_and_env() -> tuple[str, str]:
    agent_id = get_meta("agent_id")
    env_id = get_meta("env_id")

    if not agent_id:
        logger.info("Creating Managed Agent...")
        agent = client.beta.agents.create(
            name="AI Card Studio",
            model=MODEL,
            system=SYSTEM_PROMPT,
            tools=[{"type": "agent_toolset_20260401"}],
        )
        agent_id = agent.id
        set_meta("agent_id", agent_id)
        logger.info(f"Agent created: {agent_id}")
    else:
        logger.info(f"Reusing agent: {agent_id}")

    if not env_id:
        logger.info("Creating cloud environment...")
        env = client.beta.environments.create(
            name="card-studio-env",
            config={"type": "cloud", "networking": {"type": "unrestricted"}},
        )
        env_id = env.id
        set_meta("env_id", env_id)
        logger.info(f"Environment created: {env_id}")
    else:
        logger.info(f"Reusing environment: {env_id}")

    return agent_id, env_id


def _tool_summary(name: str, inp) -> str:
    if not isinstance(inp, dict):
        return str(inp)[:120]
    if name == "bash":
        cmd = inp.get("command", "")
        return cmd[:120] + ("…" if len(cmd) > 120 else "")
    if name in ("write", "read", "edit"):
        return inp.get("path", inp.get("file_path", ""))
    if name in ("glob", "grep"):
        return inp.get("pattern", inp.get("path", ""))
    if name == "web_search":
        return inp.get("query", "")
    if name == "web_fetch":
        return inp.get("url", "")
    return json.dumps(inp)[:100]


def run_agent_session(session_id: str, prompt: str):
    """Runs in a background thread. Streams from Anthropic and feeds the asyncio queue."""
    tool_trace: list[dict] = []
    full_message = ""

    def push(evt: dict):
        if _main_loop and session_id in session_queues:
            asyncio.run_coroutine_threadsafe(
                session_queues[session_id].put(evt), _main_loop
            )

    try:
        with client.beta.sessions.events.stream(session_id) as stream:
            # Open stream first, then send message (prevents race condition)
            client.beta.sessions.events.send(
                session_id,
                events=[{
                    "type": "user.message",
                    "content": [{"type": "text", "text": prompt}],
                }],
            )

            for event in stream:
                t = event.type

                if t == "agent.message":
                    text = ""
                    for block in getattr(event, "content", []):
                        if hasattr(block, "text") and block.text:
                            text += block.text
                    if text:
                        full_message += text
                        push({"type": "agent.message", "text": text})

                elif t == "agent.tool_use":
                    name = getattr(event, "name", "tool")
                    inp = getattr(event, "input", {})
                    if isinstance(inp, str):
                        try:
                            inp = json.loads(inp)
                        except Exception:
                            inp = {"value": inp}
                    summary = _tool_summary(name, inp)
                    tool_trace.append({"tool": name, "input": summary})
                    push({"type": "agent.tool_use", "name": name, "summary": summary})

                elif t == "agent.tool_result":
                    content = getattr(event, "content", "")
                    if isinstance(content, list):
                        content = " ".join(
                            str(getattr(b, "text", b)) for b in content
                        )
                    content_str = str(content)
                    # If the bash Step-B command printed the card_spec, grab it here
                    # so we don't depend on the agent copying it into its message
                    if "<card_spec>" in content_str:
                        full_message += "\n" + content_str
                    push({"type": "agent.tool_result", "preview": content_str[:300]})

                elif t == "session.status_idle":
                    card = _parse_and_save_card(prompt, full_message, tool_trace)
                    if card:
                        push({"type": "card.created", "card": card})
                    else:
                        push({
                            "type": "error",
                            "message": (
                                "Agent did not produce a valid <card_spec>. "
                                "Check the server logs for the full agent message."
                            ),
                        })
                    break

                elif t in ("session.status_terminated", "session.error"):
                    err = getattr(event, "error", {})
                    if isinstance(err, dict):
                        err = err.get("message", str(err))
                    push({"type": "error", "message": f"{t}: {err}"})
                    break

    except Exception as e:
        logger.error(f"Agent session error for {session_id}: {e}", exc_info=True)
        push({"type": "error", "message": str(e)})
    finally:
        push({"type": "__done__"})


def _parse_and_save_card(
    prompt: str, message: str, tool_trace: list
) -> Optional[dict]:
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

    card_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    card = {
        "id": card_id,
        "type": spec.get("type", "unknown"),
        "title": spec.get("title", "Untitled"),
        "prompt": prompt,
        "payload": spec.get("payload", {}),
        "tool_trace": tool_trace,
        "created_at": now,
    }

    conn = get_db()
    conn.execute(
        "INSERT INTO cards (id, type, title, prompt, payload, tool_trace, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            card["id"], card["type"], card["title"], card["prompt"],
            json.dumps(card["payload"]), json.dumps(card["tool_trace"]), card["created_at"],
        ),
    )
    conn.commit()
    conn.close()
    logger.info("Card saved: %s (%s) — %s", card["id"], card["type"], card["title"])
    return card


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


class PromptRequest(BaseModel):
    prompt: str


@app.post("/api/prompt")
async def api_prompt(req: PromptRequest):
    agent_id = get_meta("agent_id")
    env_id = get_meta("env_id")
    if not agent_id or not env_id:
        raise HTTPException(500, "Agent/environment not initialized — check server logs")

    loop = asyncio.get_running_loop()
    session = await loop.run_in_executor(
        None,
        lambda: client.beta.sessions.create(
            agent=agent_id,
            environment_id=env_id,
            title=req.prompt[:100],
        ),
    )
    session_id = session.id
    logger.info("Session created: %s", session_id)

    # Queue must exist before the thread starts pushing
    session_queues[session_id] = asyncio.Queue()

    threading.Thread(
        target=run_agent_session,
        args=(session_id, req.prompt),
        daemon=True,
    ).start()

    return {"session_id": session_id}


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
                    yield 'data: {"type":"error","message":"stream timeout"}\n\n'
                    break
                if event.get("type") == "__done__":
                    break
                # Send heartbeat comments every ~30s handled by timeout retry
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
    rows = conn.execute(
        "SELECT * FROM cards ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return {
        "cards": [
            {
                "id": r["id"],
                "type": r["type"],
                "title": r["title"],
                "prompt": r["prompt"],
                "payload": json.loads(r["payload"]),
                "tool_trace": json.loads(r["tool_trace"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    }


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
