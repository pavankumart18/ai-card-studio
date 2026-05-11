import Anthropic from "@anthropic-ai/sdk";
import hljs from "highlight.js";
import { html, render } from "lit-html";
import { unsafeHTML } from "lit-html/directives/unsafe-html.js";
import { Marked } from "marked";
import saveform from "saveform";

// ── Helpers ──────────────────────────────────────────────────────────────────

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function fmtDate(iso) {
  try {
    const s = iso.endsWith("Z") ? iso : iso + "Z";
    return new Date(s).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}

// ── Markdown renderer ────────────────────────────────────────────────────────

const marked = new Marked();
marked.use({
  renderer: {
    code({ text, lang }) {
      const language = hljs.getLanguage(lang) ? lang : "plaintext";
      return `<pre class="hljs rounded-2"><code class="hljs language-${language}">${hljs.highlight(text, { language }).value.trim()}</code></pre>`;
    },
  },
});

// ── Settings form persistence ────────────────────────────────────────────────

const settingsForm = saveform("#settings-form");
$("#settings-form [type=reset]").addEventListener("click", () => {
  settingsForm.clear();
  setTimeout(updateKeyWarning, 50);
});

$("#api-key-input").addEventListener("input", () => setTimeout(updateKeyWarning, 50));

function getApiKey() {
  return ($("#api-key-input").value || "").trim();
}

function getModel() {
  return ($("#model-input").value || "").trim() || "claude-haiku-4-5-20251001";
}

const PROXY_URL = "https://lucky-scene-c441.pavankumart7052.workers.dev";

function updateKeyWarning() {
  const hasKey = getApiKey().startsWith("sk-");
  $("#key-warning").classList.toggle("d-none", hasKey);
  if (hasKey) {
    $("#nav-model-label").textContent = getModel().replace(/-\d{8}$/, "");
  }
}

setTimeout(updateKeyWarning, 100);

// ── localStorage card storage ────────────────────────────────────────────────

const CARDS_KEY = "ai_card_studio_cards";

function loadCardsFromStorage() {
  try { return JSON.parse(localStorage.getItem(CARDS_KEY) || "[]"); }
  catch { return []; }
}

function saveCardsToStorage(cards) {
  try { localStorage.setItem(CARDS_KEY, JSON.stringify(cards)); } catch {}
}

function addCardToStorage(card) {
  const cards = loadCardsFromStorage();
  cards.unshift(card);
  saveCardsToStorage(cards);
}

function removeCardFromStorage(cardId) {
  saveCardsToStorage(loadCardsFromStorage().filter(c => c.id !== cardId));
}

// ── Agent config ─────────────────────────────────────────────────────────────

const SYSTEM_PROMPT = `You are an AI Card Studio agent. For every user prompt produce ONE artifact, then call \`submit_card\` as your very last action.

## Card types
- chart   → data visualization (bar, line, scatter, pie…)
- widget  → interactive HTML/JS tool (calculator, converter, timer…)
- data    → tabular analysis with key insight
- research→ web research with citations
- code    → write + run a Python script

---

## Per-type instructions

### chart
1. bash: \`pip install -q matplotlib\`
2. write /tmp/chart.py using this EXACT template — do not change the print lines:
\`\`\`python
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
\`\`\`
3. bash: \`python3 /tmp/chart.py\`
4. call \`submit_card\` with type="chart", title="…", payload={
     "image_b64": "<the base64 string>",
     "description": "one sentence describing the chart"
   }

### widget
1. write /tmp/widget.html — complete self-contained HTML/CSS/JS (no external URLs)
2. bash:
\`\`\`bash
python3 -c "
import base64
b = base64.b64encode(open('/tmp/widget.html','rb').read()).decode()
print('__CARD_HTML_B64__')
print(b)
"
\`\`\`
3. call \`submit_card\` with type="widget", title="…", payload={
     "html_b64": "<the base64 string>"
   }

### data
1. bash: fetch / compute data with Python
2. bash: write and print payload:
\`\`\`python
import json
payload = {
  "columns": ["Col1", "Col2"],
  "rows": [["v1", "v2"]],
  "insight": "ONE-SENTENCE KEY FINDING"
}
print(json.dumps(payload))
\`\`\`
3. call \`submit_card\` with type="data", title="…", payload=<the exact dict above>

### research
1. web_search: 1-2 targeted queries
2. web_fetch: read top 2-3 sources
3. call \`submit_card\` with type="research", title="…", payload={
     "summary": "3-5 sentence summary",
     "citations": [{"title": "Page Title", "url": "https://…"}]
   }

### code
1. write /tmp/script.py
2. bash: \`python3 /tmp/script.py 2>&1 | tee /tmp/stdout.txt\`
3. bash: \`cat /tmp/script.py\` — read the code text
4. bash: \`cat /tmp/stdout.txt\` — read the output
5. call \`submit_card\` with type="code", title="…", payload={
     "language": "python",
     "code": "<contents of /tmp/script.py>",
     "stdout": "<contents of /tmp/stdout.txt>"
   }

---

## Rules
- ALWAYS actually run bash commands — never simulate output
- NEVER manually type base64 strings — always let Python encode them
- Call \`submit_card\` EXACTLY ONCE as your very last action
- For factual or conversational questions, use type="research",
  put the answer in payload.summary, and leave citations as []
- Keep titles to 5-8 words
`;

const SUBMIT_CARD_TOOL = {
  type: "custom",
  name: "submit_card",
  description: "Submit the completed card artifact. Call this EXACTLY ONCE as your very last action after producing the artifact. The payload schema varies by card type.",
  input_schema: {
    type: "object",
    properties: {
      type: {
        type: "string",
        enum: ["chart", "widget", "data", "research", "code"],
      },
      title: {
        type: "string",
        description: "Descriptive title, 5-8 words",
      },
      payload: {
        type: "object",
        description: "chart → {image_b64, description} | widget → {html_b64} | data → {columns, rows, insight} | research → {summary, citations:[{title,url}]} | code → {language, code, stdout}",
      },
    },
    required: ["type", "title", "payload"],
  },
};

// ── Agent/env cache (localStorage) ───────────────────────────────────────────

const AGENT_CACHE_KEY = "ai_card_studio_agents_v1";

function getAgentCache() {
  try { return JSON.parse(localStorage.getItem(AGENT_CACHE_KEY) || "{}"); }
  catch { return {}; }
}

function saveAgentCache(cache) {
  try { localStorage.setItem(AGENT_CACHE_KEY, JSON.stringify(cache)); } catch {}
}

function clearAgentCacheEntry(apiKey) {
  const suffix = apiKey.slice(-8);
  const cache = getAgentCache();
  delete cache[suffix];
  saveAgentCache(cache);
}

async function getOrCreateAgent(apiKey, model) {
  const suffix = apiKey.slice(-8);
  const cache = getAgentCache();
  const clientOptions = { apiKey, dangerouslyAllowBrowser: true, baseURL: PROXY_URL };
  const client = new Anthropic(clientOptions);

  if (cache[suffix]?.model === model) {
    return { client, agentId: cache[suffix].agentId, envId: cache[suffix].envId };
  }

  appendStreamTool("initializing", "Creating agent…");
  const agent = await client.beta.agents.create({
    name: "AI Card Studio",
    model,
    system: SYSTEM_PROMPT,
    tools: [
      { type: "agent_toolset_20260401" },
      SUBMIT_CARD_TOOL,
    ],
  });

  appendStreamTool("initializing", "Creating cloud environment…");
  const env = await client.beta.environments.create({
    name: "card-studio-env",
    config: { type: "cloud", networking: { type: "unrestricted" } },
  });

  cache[suffix] = { model, agentId: agent.id, envId: env.id };
  saveAgentCache(cache);
  return { client, agentId: agent.id, envId: env.id };
}

// ── Tool input summary ────────────────────────────────────────────────────────

function toolSummary(name, inp) {
  if (typeof inp !== "object" || inp === null) return String(inp).slice(0, 200);
  if (name === "bash") {
    const cmd = inp.command || "";
    return cmd.slice(0, 200) + (cmd.length > 200 ? "…" : "");
  }
  if (name === "write" || name === "read" || name === "edit") return inp.path || inp.file_path || "";
  if (name === "glob" || name === "grep") return inp.pattern || inp.path || "";
  if (name === "web_search") return inp.query || "";
  if (name === "web_fetch") return inp.url || "";
  if (name === "submit_card") return `type=${inp.type || "?"}, title="${inp.title || "?"}"`;
  return JSON.stringify(inp).slice(0, 200);
}

// ── Streaming session runner ──────────────────────────────────────────────────

async function runStreamingSession(client, sessionId, prompt) {
  const captured = {};
  let cardSpecFromTool = null;
  let fullMessage = "";
  const toolTrace = [];
  const deadline = Date.now() + 300_000;

  // Open stream before sending message so we don't miss early events
  const stream = await client.beta.sessions.events.stream(sessionId);

  await client.beta.sessions.events.send(sessionId, {
    events: [{
      type: "user.message",
      content: [{ type: "text", text: prompt }],
    }],
  });

  for await (const event of stream) {
    if (Date.now() > deadline) {
      showError("Stream timeout after 300 seconds.");
      setRunning(false);
      hideStream();
      return;
    }

    const t = event.type;

    if (t === "agent.message") {
      let text = "";
      for (const block of (event.content || [])) {
        if (block.text) text += block.text;
      }
      if (text) {
        fullMessage += text;
        appendStreamText(text);
      }
    }

    else if (t === "agent.tool_use" || t === "agent.custom_tool_use") {
      const name = event.name || "tool";
      let inp = event.input || {};
      if (typeof inp === "string") {
        try { inp = JSON.parse(inp); } catch { inp = { value: inp }; }
      }
      const toolId = event.custom_tool_use_id || event.id || event.tool_use_id || event.tool_call_id;

      if (name === "submit_card") {
        cardSpecFromTool = inp;
        try {
          await client.beta.sessions.events.send(sessionId, {
            events: [{
              type: "user.custom_tool_result",
              custom_tool_use_id: toolId,
              content: [{ type: "text", text: "Card submitted successfully." }],
            }],
          });
        } catch (e) {
          console.warn("Could not send custom_tool_result:", e);
        }
        appendStreamSuccess(inp.type || "?", inp.title || "?");
      }

      const summary = toolSummary(name, inp);
      toolTrace.push({ tool: name, input: summary });
      appendStreamTool(name, summary);
    }

    else if (t === "agent.tool_result") {
      const raw = event.content || event.output || event.text || event.result || "";
      let contentStr = typeof raw === "string" ? raw :
        (Array.isArray(raw) ? raw.map(b => b.text || String(b)).join("\n") : String(raw));

      for (const [marker, key] of [["__CARD_IMAGE_B64__", "image_b64"], ["__CARD_HTML_B64__", "html_b64"]]) {
        if (contentStr.includes(marker)) {
          const after = contentStr.slice(contentStr.indexOf(marker) + marker.length);
          const b64 = after.trim().split("\n")[0].trim();
          if (b64) {
            captured[key] = b64;
            console.log(`[CardStudio] Captured ${key}: ${b64.length} chars`);
          }
        }
      }
    }

    else if (t === "session.status_idle") {
      let card = null;

      if (cardSpecFromTool) {
        let payload = cardSpecFromTool.payload || {};
        if (typeof payload === "string") {
          try { payload = JSON.parse(payload); } catch { payload = {}; }
        }
        for (const key of ["image_b64", "html_b64"]) {
          if (captured[key]) payload[key] = captured[key];
        }
        card = {
          id: crypto.randomUUID(),
          type: cardSpecFromTool.type || "unknown",
          title: cardSpecFromTool.title || "Untitled",
          prompt,
          payload,
          tool_trace: toolTrace,
          created_at: new Date().toISOString(),
        };
      } else if (fullMessage.trim()) {
        card = {
          id: crypto.randomUUID(),
          type: "research",
          title: prompt.trim().slice(0, 60),
          prompt,
          payload: { summary: fullMessage.trim(), citations: [] },
          tool_trace: toolTrace,
          created_at: new Date().toISOString(),
        };
      }

      if (card) {
        onCardCreated(card);
      } else {
        showError("Agent produced no response.");
        setRunning(false);
        hideStream();
      }
      break;
    }

    else if (t === "session.status_terminated" || t === "session.error") {
      const err = event.error?.message || String(event.error || t);
      showError(err);
      setRunning(false);
      hideStream();
      break;
    }
  }
}

// ── State ────────────────────────────────────────────────────────────────────

let running = false;
let streamTimer = null;
let modalCardId = null;
const cardsMap = {};

// ── Demo cards ───────────────────────────────────────────────────────────────

const DEMOS = [
  {
    icon: "bi-bar-chart-fill",
    title: "Chart",
    body: "Generate a data visualization",
    prompt: "Plot the Fibonacci sequence up to 100 as a bar chart",
    color: "primary",
  },
  {
    icon: "bi-calculator",
    title: "Widget",
    body: "Build an interactive HTML tool",
    prompt: "Build me a tip calculator widget",
    color: "purple",
  },
  {
    icon: "bi-table",
    title: "Data",
    body: "Fetch & analyze live data",
    prompt: "Fetch the current Bitcoin price from CoinGecko and show a 7-day analysis",
    color: "success",
  },
  {
    icon: "bi-search",
    title: "Research",
    body: "Search the web & summarize",
    prompt: "Research Anthropic's Managed Agents and summarize",
    color: "warning",
  },
  {
    icon: "bi-code-square",
    title: "Code",
    body: "Write & execute Python scripts",
    prompt: "Write a Python script that finds all primes under 1000 and run it",
    color: "danger",
  },
];

render(
  DEMOS.map((d, i) => html`
    <div class="col-6 col-md-4 col-lg">
      <div class="card demo-card h-100 text-center shadow-sm border-0" @click=${() => runDemo(i)}>
        <div class="card-body d-flex flex-column py-4">
          <div class="mb-2"><i class="display-5 text-${d.color} ${d.icon}"></i></div>
          <h6 class="card-title fw-bold mb-1">${d.title}</h6>
          <p class="card-text small text-body-secondary flex-grow-1">${d.body}</p>
          <button class="btn btn-sm btn-outline-${d.color} mt-2" @click=${(e) => { e.stopPropagation(); runDemo(i); }}>
            <i class="bi bi-play-fill me-1"></i>Run
          </button>
        </div>
      </div>
    </div>
  `),
  $("#demo-cards"),
);

function runDemo(index) {
  const demo = DEMOS[index];
  if (!demo || running) return;
  $("#prompt-input").value = demo.prompt;
  submitPrompt();
}

// ── Boot: load existing cards from localStorage ──────────────────────────────

(function loadCards() {
  const cards = loadCardsFromStorage();
  const grid = $("#cards-grid");
  if (!cards.length) {
    $("#empty-state").classList.remove("d-none");
  } else {
    $("#empty-state").classList.add("d-none");
    cards.forEach(c => {
      cardsMap[c.id] = c;
      grid.appendChild(buildCardElement(c));
    });
    highlightAll();
  }
})();

// ── Prompt submission ────────────────────────────────────────────────────────

$("#prompt-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !running) submitPrompt();
});

$("#submit-btn").addEventListener("click", () => submitPrompt());

async function submitPrompt() {
  if (running) return;
  const prompt = $("#prompt-input").value.trim();
  if (!prompt) return;

  const apiKey = getApiKey();
  if (!apiKey || !apiKey.startsWith("sk-")) {
    showError("Please enter your Anthropic API key in Settings first.");
    $("#settings-details").open = true;
    $("#api-key-input").focus();
    return;
  }

  setRunning(true);
  clearStream();
  showStream();

  try {
    const model = getModel();
    const { client, agentId, envId } = await getOrCreateAgent(apiKey, model);

    const session = await client.beta.sessions.create({
      agent: agentId,
      environment_id: envId,
      title: prompt.slice(0, 100),
    });

    await runStreamingSession(client, session.id, prompt);
  } catch (e) {
    const msg = e?.message || String(e);
    // If the error looks like a stale cache (agent/env not found), clear it and let user retry
    if (/not.?found|404|does not exist/i.test(msg)) {
      clearAgentCacheEntry(getApiKey());
    }
    showError(msg);
    setRunning(false);
    hideStream();
  }
}

// ── Card created ─────────────────────────────────────────────────────────────

function onCardCreated(card) {
  setRunning(false);
  $("#prompt-input").value = "";

  addCardToStorage(card);
  cardsMap[card.id] = card;

  setTimeout(() => {
    hideStream();
    $("#empty-state").classList.add("d-none");
    const el = buildCardElement(card);
    el.classList.add("card-enter");
    $("#cards-grid").prepend(el);
    highlightAll();
  }, 600);
}

// ── Build card DOM element ───────────────────────────────────────────────────

function buildCardElement(card) {
  const wrapper = document.createElement("div");
  wrapper.className = "col-md-6 col-xl-4";
  wrapper.dataset.cardId = card.id;

  const badgeClass = `badge-${card.type}`;
  const typeIcons = {
    chart: "bi-bar-chart-fill",
    widget: "bi-window-stack",
    data: "bi-table",
    research: "bi-search",
    code: "bi-code-square",
  };
  const icon = typeIcons[card.type] || "bi-card-text";

  wrapper.innerHTML = `
    <div class="card gen-card h-100 shadow-sm">
      <div class="card-header d-flex align-items-center justify-content-between bg-transparent">
        <div class="d-flex align-items-center gap-2">
          <span class="badge rounded-pill ${badgeClass}"><i class="bi ${icon} me-1"></i>${esc(card.type)}</span>
          <span class="small fw-semibold text-truncate" style="max-width:180px;" title="${esc(card.title)}">${esc(card.title)}</span>
        </div>
        <button class="btn btn-sm btn-link text-body-tertiary p-0 card-delete-btn" title="Delete">
          <i class="bi bi-x-lg"></i>
        </button>
      </div>
      <div class="card-body card-payload p-3"></div>
      <div class="card-footer bg-transparent d-flex align-items-center justify-content-between">
        <span class="small mono text-body-tertiary">${esc(fmtDate(card.created_at))}</span>
        <div class="d-flex gap-2">
          <button class="btn btn-sm btn-outline-secondary card-download-btn">
            <i class="bi bi-download me-1"></i>Download
          </button>
          <button class="btn btn-sm btn-outline-primary card-trace-btn">
            <i class="bi bi-terminal me-1"></i>Trace
          </button>
        </div>
      </div>
    </div>
  `;

  const payloadSlot = wrapper.querySelector(".card-payload");
  try {
    payloadSlot.innerHTML = renderPayload(card.type, card.payload);
  } catch (e) {
    payloadSlot.innerHTML = `<p class="small text-danger">Render error: ${esc(e.message)}</p>`;
  }

  wrapper.querySelector(".card-delete-btn").addEventListener("click", () => deleteCard(card.id));
  wrapper.querySelector(".card-trace-btn").addEventListener("click", () => openCardModal(card.id));
  wrapper.querySelector(".card-download-btn").addEventListener("click", () => downloadCard(card));

  return wrapper;
}

// ── Payload renderers ────────────────────────────────────────────────────────

function renderPayload(type, p) {
  switch (type) {
    case "chart":    return renderChart(p);
    case "widget":   return renderWidget(p);
    case "data":     return renderData(p);
    case "research": return renderResearch(p);
    case "code":     return renderCode(p);
    default: return `<pre class="small mono text-body-secondary">${esc(JSON.stringify(p, null, 2))}</pre>`;
  }
}

function renderChart(p) {
  const b64 = (p.image_b64 || "").replace(/\s/g, "");
  if (!b64) return '<p class="text-body-secondary small">No image produced.</p>';
  return `
    <img src="data:image/png;base64,${b64}" class="img-fluid rounded-3" alt="Chart" style="max-height:240px;width:100%;object-fit:contain;">
    ${p.description ? `<p class="small text-body-secondary mt-2 mb-0">${esc(p.description)}</p>` : ""}
  `;
}

function renderWidget(p) {
  const b64 = p.html_b64 || "";
  const raw = p.html || "";
  if (!b64 && !raw) return '<p class="text-body-secondary small">No widget data.</p>';
  let htmlContent;
  try { htmlContent = b64 ? atob(b64.replace(/\s/g, "")) : raw; }
  catch (e) { return `<p class="small text-danger">Base64 decode error: ${esc(e.message)}</p>`; }
  const isDoc = /^\s*<!doctype|^\s*<html/i.test(htmlContent);
  const srcdoc = isDoc ? htmlContent :
    `<!DOCTYPE html><html><head><meta charset="UTF-8"><style>body{margin:0;padding:8px;font-family:system-ui,sans-serif;font-size:14px}</style></head><body>${htmlContent}</body></html>`;
  return `<iframe class="widget-frame w-100" sandbox="allow-scripts allow-forms" srcdoc="${esc(srcdoc)}" style="height:260px;"></iframe>`;
}

function renderData(p) {
  const cols = p.columns || [];
  const rows = p.rows || [];
  if (!cols.length && !rows.length) return '<p class="text-body-secondary small">No data.</p>';
  const thead = cols.map(c => `<th class="px-2 py-1">${esc(String(c))}</th>`).join("");
  const tbody = rows.slice(0, 10).map(r =>
    `<tr>${r.map(v => `<td class="px-2 py-1">${esc(String(v))}</td>`).join("")}</tr>`
  ).join("");
  const more = rows.length > 10 ? `<p class="small text-body-tertiary mt-1">…and ${rows.length - 10} more rows</p>` : "";
  return `
    <div class="table-responsive rounded-2 border">
      <table class="table table-sm table-hover data-table mb-0 small">${thead ? `<thead><tr>${thead}</tr></thead>` : ""}<tbody>${tbody}</tbody></table>
    </div>
    ${more}
    ${p.insight ? `<p class="small mt-2 mb-0">💡 ${esc(p.insight)}</p>` : ""}
  `;
}

function renderResearch(p) {
  const cites = (p.citations || []).slice(0, 8);
  const citesHtml = cites.length ? `
    <div class="mt-2 pt-2 border-top">
      ${cites.map(c => `<a href="${esc(c.url)}" target="_blank" rel="noopener" class="d-block small text-primary text-decoration-none mb-1">
        <i class="bi bi-box-arrow-up-right me-1"></i>${esc(c.title || c.url)}</a>`).join("")}
    </div>` : "";
  const summary = esc(p.summary || "").replace(/\n/g, "<br>");
  return `<div class="small lh-lg" style="display:-webkit-box;-webkit-line-clamp:7;-webkit-box-orient:vertical;overflow:hidden;">${summary}</div>${citesHtml}`;
}

function renderCode(p) {
  const lang = p.language || "python";
  const code = p.code || "";
  const out = p.stdout || "";
  const highlighted = hljs.getLanguage(lang) ? hljs.highlight(code, { language: lang }).value : esc(code);
  return `
    <div class="rounded-2 overflow-hidden border">
      <div class="d-flex align-items-center gap-2 px-3 py-1 bg-dark">
        <span class="d-inline-block rounded-circle bg-danger" style="width:8px;height:8px;opacity:.6"></span>
        <span class="d-inline-block rounded-circle bg-warning" style="width:8px;height:8px;opacity:.6"></span>
        <span class="d-inline-block rounded-circle bg-success" style="width:8px;height:8px;opacity:.6"></span>
        <span class="small mono text-secondary ms-1">${esc(lang)}</span>
      </div>
      <div class="overflow-auto" style="max-height:180px;"><pre class="hljs mb-0"><code class="hljs language-${esc(lang)}">${highlighted}</code></pre></div>
    </div>
    ${out ? `<div class="mt-2 rounded-2 bg-dark p-2 overflow-auto" style="max-height:100px;">
      <div class="small mono text-secondary mb-1">stdout</div>
      <pre class="small mono text-success mb-0" style="white-space:pre-wrap;">${esc(out)}</pre>
    </div>` : ""}
  `;
}

// ── Download ─────────────────────────────────────────────────────────────────

function downloadCard(card) {
  const slug = card.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const p = card.payload;
  switch (card.type) {
    case "chart":
      if (p.image_b64) downloadBlob(Uint8Array.from(atob(p.image_b64.replace(/\s/g, "")), c => c.charCodeAt(0)), `${slug}.png`, "image/png");
      break;
    case "widget":
      if (p.html_b64) downloadBlob(Uint8Array.from(atob(p.html_b64.replace(/\s/g, "")), c => c.charCodeAt(0)), `${slug}.html`, "text/html");
      break;
    case "code":
      downloadBlob(new TextEncoder().encode(p.code || ""), `${slug}.py`, "text/x-python");
      break;
    case "data": {
      const cols = (p.columns || []).map(c => `"${String(c).replace(/"/g, '""')}"`);
      const rowsStr = (p.rows || []).map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(","));
      downloadBlob(new TextEncoder().encode([cols.join(","), ...rowsStr].join("\n")), `${slug}.csv`, "text/csv");
      break;
    }
    case "research": {
      const cites = (p.citations || []).map(c => `- [${c.title || c.url}](${c.url})`).join("\n");
      downloadBlob(new TextEncoder().encode(`# ${card.title}\n\n${p.summary || ""}\n\n## Sources\n${cites}`), `${slug}.md`, "text/markdown");
      break;
    }
  }
}

function downloadBlob(data, filename, mimeType) {
  const blob = new Blob([data], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

// ── Card modal ───────────────────────────────────────────────────────────────

const cardModalEl = document.getElementById("card-modal");
const cardModal = new bootstrap.Modal(cardModalEl);

function openCardModal(cardId) {
  const card = cardsMap[cardId];
  if (!card) return;
  modalCardId = cardId;

  const badgeClass = `badge-${card.type}`;
  $("#modal-badge").className = `badge rounded-pill ${badgeClass}`;
  $("#modal-badge").textContent = card.type;
  $("#modal-title").textContent = card.title;
  $("#modal-prompt").textContent = `"${card.prompt}"`;
  $("#modal-date").textContent = fmtDate(card.created_at);

  const trace = card.tool_trace || [];
  const traceEl = $("#modal-trace");
  const emptyEl = $("#modal-empty-trace");

  if (!trace.length) {
    traceEl.innerHTML = "";
    emptyEl.classList.remove("d-none");
  } else {
    emptyEl.classList.add("d-none");
    traceEl.innerHTML = trace.map(t => {
      const isSubmit = t.tool === "submit_card";
      const borderClass = isSubmit ? "tool-entry-success" : "tool-entry";
      const textClass = isSubmit ? "text-success" : "text-primary";
      return `<div class="${borderClass} ps-3 py-2 mb-1 rounded-end-2 bg-body-tertiary">
        <span class="${textClass} small fw-bold mono">▶ ${esc(t.tool)}</span>
        <span class="text-body-secondary small mono ms-2" style="word-break:break-all;">${esc(t.input)}</span>
      </div>`;
    }).join("");
  }

  cardModal.show();
}

$("#modal-delete-btn").addEventListener("click", () => {
  if (modalCardId) {
    deleteCard(modalCardId);
    cardModal.hide();
  }
});

// ── Card deletion ────────────────────────────────────────────────────────────

function deleteCard(cardId) {
  removeCardFromStorage(cardId);
  delete cardsMap[cardId];
  const el = $(`[data-card-id="${cardId}"]`);
  if (el) el.remove();
  if (!$("#cards-grid").children.length) {
    $("#empty-state").classList.remove("d-none");
  }
}

// ── Stream pane ──────────────────────────────────────────────────────────────

function showStream() {
  $("#stream-wrapper").classList.remove("d-none");
  let t = 0;
  streamTimer = setInterval(() => {
    $("#stream-elapsed").textContent = `${++t}s`;
  }, 1000);
}

function hideStream() {
  clearInterval(streamTimer);
  $("#stream-wrapper").classList.add("d-none");
  $("#stream-elapsed").textContent = "";
}

function clearStream() {
  $("#stream-content").innerHTML = "";
}

function appendStreamText(text) {
  const pane = $("#stream-content");
  let cur = pane.querySelector(".stream-text-active");
  if (!cur) {
    cur = document.createElement("p");
    cur.className = "stream-text-active text-light-emphasis mb-2 small lh-lg";
    pane.appendChild(cur);
  }
  cur.textContent += text;
  pane.scrollTop = pane.scrollHeight;
}

function appendStreamTool(name, summary) {
  if (name === "submit_card") return;
  const pane = $("#stream-content");
  pane.querySelector(".stream-text-active")?.classList.remove("stream-text-active");
  const el = document.createElement("div");
  el.className = "d-flex align-items-start gap-2 bg-black bg-opacity-25 rounded-2 px-3 py-2 mb-1 small";
  el.innerHTML = `<span class="text-warning fw-bold mono flex-shrink-0">▶ ${esc(name)}</span>
    <span class="text-light-emphasis mono" style="word-break:break-all;">${esc(summary)}</span>`;
  pane.appendChild(el);
  pane.scrollTop = pane.scrollHeight;
}

function appendStreamSuccess(cardType, title) {
  const pane = $("#stream-content");
  pane.querySelector(".stream-text-active")?.classList.remove("stream-text-active");
  const el = document.createElement("div");
  el.className = "d-flex align-items-center gap-2 border border-success border-opacity-25 bg-success bg-opacity-10 rounded-2 px-3 py-2 mb-1 small";
  el.innerHTML = `<i class="bi bi-check-circle-fill text-success"></i>
    <span class="text-success fw-bold mono">submit_card</span>
    <span class="text-success-emphasis mono">${esc(cardType)} — "${esc(title)}"</span>`;
  pane.appendChild(el);
  pane.scrollTop = pane.scrollHeight;
}

// ── Error display ────────────────────────────────────────────────────────────

function showError(msg) {
  console.error("[CardStudio]", msg);
  const section = $("#error-section");
  section.classList.remove("d-none");

  const el = document.createElement("div");
  el.className = "alert alert-danger alert-dismissible fade show d-flex align-items-start gap-2";
  el.innerHTML = `<i class="bi bi-exclamation-circle flex-shrink-0 mt-1"></i>
    <div class="flex-grow-1 small">${esc(msg)}</div>
    <button type="button" class="btn-close" data-bs-dismiss="alert"></button>`;
  el.addEventListener("closed.bs.alert", () => {
    if (!section.children.length) section.classList.add("d-none");
  });
  section.appendChild(el);
}

// ── UI state ─────────────────────────────────────────────────────────────────

function setRunning(yes) {
  running = yes;
  $("#submit-btn").disabled = yes;
  $("#btn-text").classList.toggle("d-none", yes);
  $("#btn-spinner").classList.toggle("d-none", !yes);
  $("#prompt-input").disabled = yes;
  $$("#demo-cards .demo-card").forEach(el => {
    el.style.opacity = yes ? "0.5" : "1";
    el.style.pointerEvents = yes ? "none" : "";
  });
}

function highlightAll() {
  requestAnimationFrame(() => {
    document.querySelectorAll("pre code.hljs").forEach(el => {
      if (!el.dataset.highlighted) hljs.highlightElement(el);
    });
  });
}
