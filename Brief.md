# AI Card Studio — Project Brief

> A client-facing demo of Anthropic's **Claude Managed Agents** (public beta, April 2026).
> Built to showcase capabilities that a plain model API cannot deliver: tool use, container execution, and persistent state.

---

## 1. Overview

| Item | Detail |
|------|--------|
| **Product** | AI Card Studio |
| **Purpose** | Enterprise demo for Straive — show Claude Managed Agents doing real work |
| **Audience** | Senior leadership, enterprise clients |
| **Key differentiator** | Every card is produced by a real agent running in a cloud container — not generated text |

---

## 2. How It Works

```
User enters prompt
       ↓
Claude Managed Agent runs in cloud container
  • streams tool calls live (bash, file ops, web search)
  • produces a real artifact (PNG chart, HTML widget, data table, etc.)
       ↓
Artifact saved as a "card" → added to persistent grid
       ↓
Cards survive page reload (SQLite-backed)
```

---

## 3. Card Types

The agent decides which type to produce based on the prompt.

| Type | What the agent does | Payload |
|------|---------------------|---------|
| **chart** | Writes Python, runs matplotlib in container, saves PNG | `image_b64`, `description` |
| **widget** | Writes self-contained HTML/JS snippet | `html_b64` |
| **data** | Ingests CSV/URL, runs pandas analysis | `columns`, `rows`, `insight` |
| **research** | Uses web_search + web_fetch, summarizes | `summary`, `citations[]` |
| **code** | Writes script, executes it, captures stdout | `language`, `code`, `stdout` |

Each card stores: `id` · `type` · `title` · `prompt` · `payload` · `tool_trace` · `created_at`

---

## 4. Tech Stack

| Layer | Choice |
|-------|--------|
| **Backend** | Python 3.11+, FastAPI, Uvicorn |
| **AI SDK** | `anthropic` (latest) — Managed Agents beta |
| **Database** | SQLite via stdlib `sqlite3` |
| **Frontend** | Single `index.html` — vanilla JS + Tailwind CDN, no build step |
| **Streaming** | Server-Sent Events (SSE) from FastAPI to browser |

---

## 5. Anthropic API Configuration

| Setting | Value |
|---------|-------|
| **Beta header** | `managed-agents-2026-04-01` (SDK sets automatically) |
| **Model** | `claude-sonnet-4-5` (token-efficient; stakeholder requirement) |
| **Agent lifecycle** | One agent + one environment created at startup, reused on restart (IDs cached in SQLite) |
| **Session lifecycle** | One session per user prompt |
| **Toolset** | `agent_toolset_20260401` (bash, file ops, web_search, web_fetch) |
| **Environment** | `{"type": "cloud", "networking": {"type": "unrestricted"}}` |

> If SDK method signatures have changed, check **platform.claude.com/docs/en/managed-agents/** before coding.

---

## 6. Architecture

```
Browser (index.html)
  │
  ├─ POST /api/prompt      → create session, start background thread
  ├─ GET  /api/stream/{id} → SSE (asyncio.Queue fed by thread)
  ├─ GET  /api/cards        → list all cards
  └─ DELETE /api/cards/{id} → delete a card
         │
    FastAPI (app.py)
         │
    Anthropic SDK — Managed Agents beta
         │
    Cloud Container (bash · file ops · web_search · web_fetch)
         │
    SQLite (cards.db) — cards + agent/env IDs
```

---

## 7. Backend Requirements (`app.py`)

### Startup
- Create one Agent + one Environment; cache IDs in SQLite
- On restart, reuse existing IDs

### System Prompt (agent instructions)
The agent must:
1. Decide on a card type from the 5 above
2. Execute real tools to produce the artifact
3. Write the final card spec to `/tmp/card.json` using `json.dump`
4. Print `<card_spec>` block as its last output

### Endpoints

| Method | Path | Body / Response |
|--------|------|-----------------|
| `POST` | `/api/prompt` | `{prompt}` → `{session_id}` |
| `GET` | `/api/stream/{session_id}` | SSE: `agent.message`, `agent.tool_use`, `agent.tool_result`, `card.created`, `error` |
| `GET` | `/api/cards` | `{cards: [...]}` newest first |
| `DELETE` | `/api/cards/{id}` | `{ok: true}` |

### Error Handling
- Invalid card spec → send `error` SSE event, do not crash
- Session terminated → send `error` SSE event

### CORS
- Allow all origins (`*`) for local dev

---

## 8. Frontend Requirements (`index.html`)

### Layout
- **Header** — app name + model badge + live status dot
- **Prompt bar** — text input + submit button + example chips
- **Streaming pane** — appears when agent is running, shows live tool calls
- **Cards grid** — responsive (1 col mobile / 2–3 col desktop)

### Streaming Pane
- Agent text in normal style
- Tool calls in amber monospace box: `▶ bash: pip install matplotlib`
- Auto-scroll; collapses when card is created

### Card Rendering

| Type | Rendered as |
|------|-------------|
| chart | `<img>` with base64 data URL |
| widget | `<iframe sandbox="allow-scripts">` with `srcdoc` |
| data | HTML table + insight paragraph |
| research | Summary text + clickable citation links |
| code | Syntax-highlighted code block + stdout box |

### Card Detail Modal
- Click any card → modal showing full tool trace
- Every bash/tool call the agent made, in order
- This is the "real container" proof for clients

### Persistence
- On page load, fetch `/api/cards` and render all existing cards

---

## 9. Demo Script

Run these 5 prompts in order. Each must produce a visibly different, working card.

| # | Prompt | Expected type |
|---|--------|---------------|
| 1 | Plot the Fibonacci sequence up to 100 as a bar chart | chart |
| 2 | Build me a tip calculator widget | widget |
| 3 | Fetch the current Bitcoin price from CoinGecko and show a 7-day analysis | data |
| 4 | Research the latest on Anthropic's Managed Agents and summarize | research |
| 5 | Write a Python script that finds all primes under 1000 and run it | code |

**After all 5:** reload the browser — all cards must still be present.

---

## 10. Deliverables

| File | Status |
|------|--------|
| `app.py` | ✅ FastAPI backend |
| `index.html` | ✅ Frontend (served at `/`) |
| `requirements.txt` | ✅ Dependencies |
| `README.md` | ✅ Setup + run instructions |
| `cards.db` | Auto-created, gitignored |
| `.env` | API key (gitignored) |

---

## 11. Constraints

- **Single-file frontend** — no React, no build step
- **No mocking** — every card must come from a real agent session
- **API key** — set `ANTHROPIC_API_KEY` or `CLAUDE_API_KEY` in `.env`

---

## 12. Implementation Order

1. `[DONE]` Agent + environment + session + stream round-trip
2. `[DONE]` SQLite schema and persistence
3. `[DONE]` System prompt → reliable `/tmp/card.json` output
4. `[DONE]` `POST /api/prompt` and `GET /api/stream/{id}`
5. `[DONE]` Frontend — prompt bar, streaming pane, cards grid, modal
6. `[ ]` Run 5-prompt demo script end-to-end
7. `[ ]` Polish, error handling, final README
