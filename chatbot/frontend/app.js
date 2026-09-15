/* G4F Model Router — frontend logic */
const $ = (id) => document.getElementById(id);
const messagesEl = $("messages"), promptEl = $("prompt"), sendBtn = $("send"), stopBtn = $("stop");
const modelListEl = $("model-list"), searchEl = $("search"), topName = document.querySelector(".cm-name");
const topDesc = document.querySelector(".cm-desc"), usageChip = $("usage-chip"), modelMeta = $("model-meta");

let MODELS = [];           // [{id,name,family,tags,context,description,requests,tokens}]
let currentModel = null;   // model object
let history = [];          // [{role, content}]
let aborter = null;
let busy = false;

const FAMILY_COLORS = {
  "openai-z": "#4f8cff", "zai-z": "#2ecc71", "xai-z": "#9b59b6", "logfare": "#f5a623",
  "poolside": "#1abc9c", "osaii": "#e74c3c", "venice-z": "#e91e8c", "qwen-z": "#f1c40f",
  "fireworks-z": "#ff7f50", "groq-z": "#7ed321", "deepseek-z": "#48dbfb", "mimo-z": "#ff6b81",
  "gemini-z": "#54a0ff", "minimax-z": "#5f27cd", "mistral-z": "#fd79a8", "openrouter-z": "#00d2d3", "inception-z": "#c56cf0",
  "stealth": "#576574", "meta-z": "#34ace0", "microsoft": "#4aa3ff",
};

/* ---------------- helpers ---------------- */
function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
/* tiny markdown: fenced code + inline code + bold (text is escaped first) */
function md(s) {
  let out = esc(s);
  out = out.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) =>
    `<pre><code>${code.replace(/\n$/, "")}</code></pre>`);
  out = out.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  out = out.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  return out;
}
function fmt(n) { return n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n); }

/* ---------------- server status ---------------- */
async function ping() {
  try {
    const r = await fetch("/api/health");
    const d = await r.json();
    $("status-dot").className = "dot ok";
    $("mode-badge").textContent = `server online · ${d.models} models · mode: ${d.mode}`;
  } catch {
    $("status-dot").className = "dot err";
    $("mode-badge").textContent = "server offline";
  }
}

/* ---------------- model list ---------------- */
async function loadModels() {
  const r = await fetch("/api/models");
  const d = await r.json();
  MODELS = d.models;
  renderModels(searchEl.value.trim().toLowerCase());
  $("side-foot").textContent =
    `${MODELS.length} models · ${MODELS.reduce((a, m) => a + m.requests, 0)} total requests · ` +
    `${MODELS.reduce((a, m) => a + m.tokens, 0)} tokens`;
}

function renderModels(filter = "") {
  const groups = {};
  for (const m of MODELS) {
    if (filter && !(m.name + " " + m.id + " " + m.family + " " + m.tags.join(" ")).toLowerCase().includes(filter)) continue;
    (groups[m.family] ||= []).push(m);
  }
  modelListEl.innerHTML = "";
  for (const [fam, list] of Object.entries(groups)) {
    const title = document.createElement("div");
    title.className = "fam-title";
    title.innerHTML = `<span>${esc(fam)}</span><span>${list.length}</span>`;
    modelListEl.appendChild(title);
    for (const m of list) {
      const row = document.createElement("div");
      row.className = "model-row" + (currentModel?.id === m.id ? " active" : "");
      row.innerHTML = `
        <span class="swatch" style="background:${FAMILY_COLORS[m.family] || "#888"}"></span>
        <div style="flex:1;min-width:0">
          <div class="m-name" title="${esc(m.id)}">${esc(m.name)}</div>
          <div class="m-tags">${m.tags.slice(0, 3).map(t => `<span class="tag">${esc(t)}</span>`).join("")}</div>
        </div>
        <span class="m-count">${m.requests ? fmt(m.requests) : ""}</span>`;
      row.onclick = () => selectModel(m);
      modelListEl.appendChild(row);
    }
  }
  if (!modelListEl.children.length) {
    modelListEl.innerHTML = '<div style="color:var(--dim);padding:20px;text-align:center;font-size:12px">no models match</div>';
  }
}

function selectModel(m) {
  currentModel = m;
  topName.textContent = m.name;
  topDesc.textContent = m.description;
  usageChip.textContent = `${m.requests} req · ${fmt(m.tokens)} tok`;
  modelMeta.textContent = `${m.family} · ${m.context.toLocaleString()} ctx · ${m.tags.join(", ")}`;
  document.querySelectorAll(".model-row").forEach(r => r.classList.remove("active"));
  renderModels(searchEl.value.trim().toLowerCase());
  promptEl.focus();
  if (window.innerWidth <= 760) $("sidebar").classList.add("hidden");
}

/* ---------------- messages ---------------- */
function addMsg(role, modelLabel, text, streaming = false) {
  $("empty-state")?.remove();
  const div = document.createElement("div");
  div.className = "msg " + (role === "user" ? "user" : "ai");
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = role === "user" ? "you" : modelLabel;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = md(text) + (streaming ? '<span class="caret"></span>' : "");
  div.appendChild(role === "user" ? bubble : meta);
  div.appendChild(role === "user" ? meta : bubble);
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return bubble;
}

async function send(text) {
  if (!text) return;
  if (!currentModel) {
    addMsg("ai", "router", "Pick a model from the list on the left first — there are 45 of them. 🙂");
    return;
  }
  if (busy) return;
  busy = true; sendBtn.classList.add("hidden"); stopBtn.classList.remove("hidden");
  promptEl.value = ""; autoresize();

  history.push({ role: "user", content: text });
  addMsg("user", "", text);

  const bubble = addMsg("ai", `${currentModel.name} · ${currentModel.id}`, "", true);
  let acc = "";
  aborter = new AbortController();
  try {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: currentModel.id, messages: history, stream: true }),
      signal: aborter.signal,
    });
    if (!r.ok) throw new Error("HTTP " + r.status + " " + (await r.text()).slice(0, 200));
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const p = line.slice(6);
        if (p === "[DONE]") continue;
        try {
          const chunk = JSON.parse(p);
          const delta = chunk.choices?.[0]?.delta?.content;
          if (delta) {
            acc += delta;
            bubble.innerHTML = md(acc) + '<span class="caret"></span>';
            messagesEl.scrollTop = messagesEl.scrollHeight;
          }
        } catch {}
      }
    }
    history.push({ role: "assistant", content: acc || "(empty)" });
  } catch (e) {
    if (e.name === "AbortError") acc += " _[stopped]_";
    else acc = "⚠️ " + e.message;
    bubble.innerHTML = md(acc);
  } finally {
    bubble.innerHTML = md(acc || "(stopped)");
    busy = false; aborter = null;
    sendBtn.classList.remove("hidden"); stopBtn.classList.add("hidden");
    const fresh = MODELS.find(m => m.id === currentModel?.id);
    if (fresh) { fresh.requests++; fresh.tokens += Math.max(1, Math.round(acc.length / 4)); }
    selectModel(currentModel);
    renderModels(searchEl.value.trim().toLowerCase());
  }
}

/* ---------------- events ---------------- */
sendBtn.onclick = () => send(promptEl.value.trim());
stopBtn.onclick = () => aborter && aborter.abort();
promptEl.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(promptEl.value.trim()); }
});
function autoresize() {
  promptEl.style.height = "auto";
  promptEl.style.height = Math.min(promptEl.scrollHeight, 160) + "px";
}
promptEl.addEventListener("input", autoresize);
$("new-chat").onclick = () => {
  history = [];
  messagesEl.innerHTML = "";
  addMsg("ai", currentModel ? currentModel.name : "router",
    currentModel ? `Fresh chat with ${currentModel.name}. What's up?` : "Fresh chat. Pick a model on the left.");
  promptEl.focus();
};
$("side-toggle").onclick = () => $("sidebar").classList.toggle("hidden");
searchEl.addEventListener("input", () => renderModels(searchEl.value.trim().toLowerCase()));
document.querySelectorAll(".hint").forEach(h => h.onclick = () => send(h.dataset.prompt));

/* ---------------- boot ---------------- */
(async function boot() {
  await Promise.allSettled([ping(), loadModels()]);
  // preselect the most-used model (on the live server it's Laguna XS 2.1)
  const ranked = [...MODELS].sort((a, b) => b.requests - a.requests);
  selectModel(ranked[0] || MODELS[0]);
  ping();
  setInterval(ping, 30000);
})();
