# AI Card Studio — Claude Managed Agents Demo

A single-page demo that shows Claude Managed Agents running real work in a cloud container and persisting the results as interactive "cards".

## Prerequisites

- Python 3.11+
- An Anthropic API key with Managed Agents access

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your API key
export ANTHROPIC_API_KEY="sk-ant-..."   # Linux / macOS
# or
set ANTHROPIC_API_KEY=sk-ant-...        # Windows CMD
# or
$env:ANTHROPIC_API_KEY="sk-ant-..."     # Windows PowerShell

# 3. Start the server
python app.py
```

Then open **http://localhost:8000** in your browser.

On first run the server creates one Agent and one Environment via the Managed Agents API and stores their IDs in `cards.db`. Subsequent restarts reuse them.

## Demo script

Run these 5 prompts in order to prove every card type works:

1. **Chart** — `Plot the Fibonacci sequence up to 100 as a bar chart`
2. **Widget** — `Build me a tip calculator widget`
3. **Data** — `Fetch the current Bitcoin price from CoinGecko and show a 7-day analysis`
4. **Research** — `Research the latest on Anthropic's Managed Agents and summarize`
5. **Code** — `Write a Python script that finds all primes under 1000 and run it`

After all 5, reload the page — all cards persist.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `401 Unauthorized` | Check `ANTHROPIC_API_KEY` is set and valid |
| `Agent did not produce a valid card_spec` | The agent response didn't include `<card_spec>` tags — retry; the system prompt is designed to prevent this but LLM outputs are non-deterministic |
| Model not found | Change `MODEL = "claude-sonnet-4-5"` in `app.py` to `"claude-sonnet-4-6"` or `"claude-opus-4-7"` |
| Widget blank | Some CDN resources may be blocked; the widget HTML is sandboxed in an iframe |
| Chart shows placeholder | The agent failed to base64-encode the PNG — retry the prompt |

## Architecture

```
Browser (index.html served by FastAPI)
   │
   ├─ POST /api/prompt  ──► creates session ──► starts background thread
   ├─ GET  /api/stream/{id}  ◄── SSE (asyncio.Queue fed by thread)
   ├─ GET  /api/cards   ──► SQLite cards.db
   └─ DELETE /api/cards/{id}
                         │
                   Anthropic SDK (Managed Agents beta)
                         │
                   Cloud Container (bash, file ops, web_search, web_fetch)
```

## Files

| File | Purpose |
|------|---------|
| `app.py` | FastAPI backend |
| `index.html` | Frontend (served at `/`) |
| `requirements.txt` | Python dependencies |
| `cards.db` | Auto-created SQLite database (gitignored) |
