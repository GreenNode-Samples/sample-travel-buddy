/* ============================================================
   Travel Buddy - a travel assistant with memory
   Vanilla JS frontend: no build step, no CDN, no framework.
   The backend serves it at GET / (same origin, so fetches are relative).
   ============================================================ */

"use strict";

/* ------------------- Constants and helpers ------------------- */

const $ = (id) => document.getElementById(id);

const API = {
  INVOCATIONS: "/invocations",
  INFO: "/api/info",
  MEMORY: "/api/memory",
  HISTORY: "/api/history",
  ACTORS: "/api/actors",
  STREAM: "/api/chat/stream",
};

// Headers required by the chat endpoints (they partition memory per user and session)
const HDR_USER = "X-GreenNode-AgentBase-User-Id";
const HDR_SESSION = "X-GreenNode-AgentBase-Session-Id";
const HDR_KEY = "X-API-Key";
const KEY_STORAGE = "travelBuddyApiKey";

const state = {
  actors: [],     // [{ actorId, sessions: [string] }]
  actor: null,    // selected actorId
  session: null,  // selected sessionId
  info: null,     // result of GET /api/info
  sending: false, // waiting for the agent's reply
  viewToken: 0,   // bumped whenever the session changes, so stale responses are ignored
};

let memoryToken = 0; // avoids races while loading /api/memory

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function truncate(s, n) {
  s = String(s);
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

// New user name -> lowercase slug (Vietnamese diacritics removed)
function slugify(s) {
  return String(s)
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/đ/g, "d")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
}

// Relative time ("5 minutes ago" in Vietnamese); falls back to the raw string
function relativeTime(raw) {
  if (!raw) return "";
  const t = new Date(raw).getTime();
  if (Number.isNaN(t)) return String(raw);
  const min = Math.floor((Date.now() - t) / 60000);
  if (min < 1) return "vừa xong";
  if (min < 60) return `${min} phút trước`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} giờ trước`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d} ngày trước`;
  return String(raw);
}

/* ------------------- Minimal markdown -------------------
   HTML is escaped FIRST, then: **bold**, `code`, ```code blocks```,
   links [text](url) / bare URLs (target=_blank), headings rendered bold. */

function renderInlineMd(s) {
  const stash = [];
  const keep = (html) => { stash.push(html); return `\u0001${stash.length - 1}\u0001`; };

  // Inline code is stashed behind a token so bold/link rules never touch its content
  s = s.replace(/`([^`\n]+)`/g, (m, code) => keep(`<code class="md-code">${code}</code>`));
  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g,
    '<a class="md-link" href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)"]+)/g,
    (m, p, url) => `${p}<a class="md-link" href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);
  s = s.replace(/\u0001(\d+)\u0001/g, (m, i) => stash[Number(i)]);
  return s;
}

function renderMarkdown(raw) {
  const esc = escapeHtml(raw ?? "");
  const stash = [];
  const keep = (html) => { stash.push(html); return `\u0000${stash.length - 1}\u0000`; };

  // 1) Code blocks ```...``` -> token (content already escaped, kept as is)
  let text = esc.replace(/```[^\n]*\n?([\s\S]*?)```/g, (m, code) =>
    keep(`<pre class="md-pre"><code>${code.replace(/\n$/, "")}</code></pre>`));

  // 2) Line by line: bold headings, bullet lists, paragraphs
  const out = [];
  let list = null;
  const flushList = () => {
    if (list) {
      out.push(`<ul class="md-ul">${list.map((li) => `<li>${li}</li>`).join("")}</ul>`);
      list = null;
    }
  };

  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();

    // A line that is a code-block token is emitted as is, not wrapped in <p>
    if (/^\u0000\d+\u0000$/.test(line)) { flushList(); out.push(line); continue; }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushList();
      out.push(`<div class="md-h md-h${heading[1].length}"><strong>${renderInlineMd(heading[2])}</strong></div>`);
      continue;
    }

    const item = line.match(/^(?:[-*•]|\d+[.)])\s+(.+)$/);
    if (item) { (list ??= []).push(renderInlineMd(item[1])); continue; }

    if (!line) { flushList(); continue; }

    flushList();
    out.push(`<p class="md-p">${renderInlineMd(line)}</p>`);
  }
  flushList();

  // 3) Restore the code blocks from their tokens
  return out.join("\n").replace(/\u0000(\d+)\u0000/g, (m, i) => stash[Number(i)]);
}

/* ------------------- API layer (same origin) ------------------- */

// The API key (AGENT_API_KEY on the server) is kept in sessionStorage only: it is
// forgotten when the tab closes and never written to disk by this page.
function getApiKey() {
  try { return sessionStorage.getItem(KEY_STORAGE) || ""; } catch { return ""; }
}

function setApiKey(key) {
  try {
    if (key) sessionStorage.setItem(KEY_STORAGE, key);
    else sessionStorage.removeItem(KEY_STORAGE);
  } catch { /* storage unavailable: the key is asked again on the next 401 */ }
}

// Asks for the key once; returns false if the user cancels.
function askApiKey(message) {
  const key = (window.prompt(message) || "").trim();
  if (!key) return false;
  setApiKey(key);
  return true;
}

// fetch() with the API key attached. A 401 means the key is missing or wrong: ask once and retry.
async function apiFetch(url, options = {}) {
  const send = () => {
    const headers = { ...(options.headers || {}) };
    const key = getApiKey();
    if (key) headers[HDR_KEY] = key;
    return fetch(url, { ...options, headers });
  };
  let res = await send();
  if (res.status === 401) {
    setApiKey("");
    if (askApiKey("Máy chủ yêu cầu API key (header X-API-Key). Nhập API key:")) res = await send();
  }
  return res;
}

// Error text of a failed response; includes the request id the server logged, if any.
function errorText(data, res) {
  const text = (data && (data.error || data.message)) || `HTTP ${res.status}`;
  const requestId = data && ((data.details && data.details.request_id) || data.request_id);
  return requestId ? `${text} (${requestId})` : text;
}

async function requestJson(url, options = {}) {
  let res;
  try {
    res = await apiFetch(url, options);
  } catch (e) {
    throw new Error(`không kết nối được server (${e.message})`);
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty body or not JSON */ }
  if (!res.ok) throw new Error(errorText(data, res));
  if (data && data.status === "error") throw new Error(errorText(data, res));
  return data;
}

const getJson = (url) => requestJson(url);

function postInvocation(message) {
  return requestJson(API.INVOCATIONS, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      [HDR_USER]: state.actor,
      [HDR_SESSION]: state.session,
    },
    body: JSON.stringify({ message }),
  });
}

/* ---------- SSE streaming: POST /api/chat/stream ---------- */

// Live bubble for the token stream (plain text while streaming)
function appendLiveBotBubble() {
  const wrap = document.createElement("div");
  wrap.className = "msg bot";
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = "🧭";
  const body = document.createElement("div");
  body.className = "msg-body";
  const name = document.createElement("div");
  name.className = "msg-name";
  name.textContent = "Travel Buddy";
  const bubble = document.createElement("div");
  bubble.className = "bubble md streaming";
  const cursor = document.createElement("span");
  cursor.className = "stream-cursor";
  cursor.textContent = "▍";
  bubble.appendChild(cursor);
  body.appendChild(name);
  body.appendChild(bubble);
  wrap.appendChild(avatar);
  wrap.appendChild(body);
  $("chatMessages").appendChild(wrap);
  scrollToBottom();
  return { wrap: wrap, bubble: bubble, body: body };
}

// The streaming endpoint cannot be used at all (missing route, or a proxy that does not
// pass SSE). Only then does the caller fall back to POST /invocations. Any other failure
// is shown as is: the server may already have processed the message, and a retry would run it twice.
class StreamUnavailable extends Error {}

// Calls the stream: each token is rendered as it arrives; when done, markdown + memory callout like a normal bubble
async function postStream(message) {
  let res;
  try {
    res = await apiFetch(API.STREAM, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [HDR_USER]: state.actor,
        [HDR_SESSION]: state.session,
      },
      body: JSON.stringify({ message }),
    });
  } catch (e) {
    throw new StreamUnavailable(e.message);
  }
  const ct = res.headers.get("content-type") || "";
  if (!res.ok || !ct.includes("text/event-stream")) {
    let data = null;
    try { data = await res.json(); } catch { /* not JSON */ }
    const message = errorText(data, res);
    if (res.ok || res.status === 404 || res.status === 405) throw new StreamUnavailable(message);
    throw new Error(message);
  }

  const live = appendLiveBotBubble();
  let full = "";
  let memoriesUsed = [];

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop() || "";
    for (const part of parts) {
      const line = part.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      let ev = null;
      try { ev = JSON.parse(line.slice(5).trim()); } catch { continue; }
      if (ev.type === "token") {
        full += ev.text || "";
        live.bubble.textContent = full;
        const cursor = document.createElement("span");
        cursor.className = "stream-cursor";
        cursor.textContent = "▍";
        live.bubble.appendChild(cursor);
        scrollToBottom();
      } else if (ev.type === "done") {
        full = ev.response || full;
        memoriesUsed = ev.memories_used || [];
        live.bubble.classList.remove("streaming");
        live.bubble.innerHTML = renderMarkdown(full);
        if (memoriesUsed.length) live.body.appendChild(buildMemoryCallout(memoriesUsed));
        scrollToBottom();
      } else if (ev.type === "error") {
        throw new Error(errorText(ev, res));
      }
    }
  }
  if (!full) throw new Error("Stream kết thúc mà không có nội dung.");
  return { status: "success", response: full, memories_used: memoriesUsed };
}

/* ------------------- Chat: message bubbles ------------------- */

function scrollToBottom() {
  const box = $("chatMessages");
  box.scrollTop = box.scrollHeight;
}

function appendBubble(role, text, memoriesUsed) {
  const wrap = document.createElement("div");
  wrap.className = "msg " + (role === "user" ? "user" : "bot");

  if (role === "user") {
    // User message: on the right, plain text (textContent is safe)
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;
    wrap.appendChild(bubble);
  } else {
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = "🧭";
    avatar.title = "Travel Buddy";

    const body = document.createElement("div");
    body.className = "msg-body";

    const name = document.createElement("div");
    name.className = "msg-name";
    name.textContent = "Travel Buddy";

    const bubble = document.createElement("div");
    bubble.className = "bubble md";
    bubble.innerHTML = renderMarkdown(text); // renderMarkdown escapes first

    body.appendChild(name);
    body.appendChild(bubble);
    if (Array.isArray(memoriesUsed) && memoriesUsed.length > 0) {
      body.appendChild(buildMemoryCallout(memoriesUsed));
    }

    wrap.appendChild(avatar);
    wrap.appendChild(body);
  }

  $("chatMessages").appendChild(wrap);
  scrollToBottom();
  return wrap;
}

function buildMemoryCallout(facts) {
  const box = document.createElement("div");
  box.className = "mem-callout";
  const title = document.createElement("div");
  title.className = "mem-callout-title";
  title.textContent = "✨ Agent vừa nhớ lại:";
  const ul = document.createElement("ul");
  for (const fact of facts) {
    const li = document.createElement("li");
    li.textContent = fact;
    ul.appendChild(li);
  }
  box.appendChild(title);
  box.appendChild(ul);
  return box;
}

function renderEmptyChat() {
  const hasTarget = state.actor && state.session;
  $("chatMessages").innerHTML = `
    <div class="chat-empty">
      <div class="chat-empty-icon">🧭</div>
      <div class="chat-empty-title">${hasTarget
        ? "Bắt đầu trò chuyện với Travel Buddy"
        : "Chọn người dùng &amp; phiên để bắt đầu"}</div>
      <div class="chat-empty-sub">${hasTarget
        ? "Hỏi về lịch trình, chỗ ở, ẩm thực… Agent sẽ dần ghi nhớ sở thích và chi tiết chuyến đi của bạn."
        : "Chọn một người dùng và phiên hội thoại ở cột bên trái, hoặc bấm “+ Phiên mới”."}</div>
    </div>`;
}

/* ------------------- "Typing" indicator (3 dots + timer) ------------------- */

function showTypingIndicator() {
  const wrap = document.createElement("div");
  wrap.className = "msg bot typing";

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = "🧭";

  const body = document.createElement("div");
  body.className = "msg-body";

  const name = document.createElement("div");
  name.className = "msg-name";
  name.textContent = "Travel Buddy";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML =
    '<span class="typing-dots"><span></span><span></span><span></span></span>' +
    '<span class="typing-timer">0.0 giây</span>';

  body.appendChild(name);
  body.appendChild(bubble);
  wrap.appendChild(avatar);
  wrap.appendChild(body);
  $("chatMessages").appendChild(wrap);
  scrollToBottom();

  const startedAt = Date.now();
  const timerEl = bubble.querySelector(".typing-timer");
  const timer = setInterval(() => {
    timerEl.textContent = ((Date.now() - startedAt) / 1000).toFixed(1) + " giây";
  }, 100);

  return { wrap, stop: () => { clearInterval(timer); wrap.remove(); } };
}

/* ------------------- Chat state and status dot ------------------- */

function clearChat() {
  state.viewToken += 1;
  $("chatMessages").innerHTML = "";
}

function setDot(kind) {
  const dot = $("statusDot");
  dot.classList.remove("ok", "err");
  if (kind) dot.classList.add(kind);
}

/* ------------------- GET /api/info ------------------- */

async function loadInfo() {
  try {
    let data = await getJson(API.INFO);
    // GET /api/info is public and only reports auth_required; with the key it also returns the details.
    if (data.auth_required && !getApiKey()
        && askApiKey("Máy chủ yêu cầu API key (header X-API-Key). Nhập API key:")) {
      data = await getJson(API.INFO);
    }
    state.info = data;
    $("infoAgent").textContent = data.agent || "—";
    $("infoMemory").textContent = String(data.memory_id || "—").slice(0, 8); // memory id shortened to 8 characters
    $("infoModel").textContent = data.llm_model || "—";
    $("infoGateway").textContent = truncate(data.gateway || data.mcp_url || "—", 30);
    setDot("ok");
  } catch (e) {
    setDot("err");
    showToast("Không tải được /api/info: " + e.message);
  }
}

/* ------------------- Users and sessions (GET /api/actors) ------------------- */

// Merge the server's actor list with actors/sessions created locally (not synced yet)
function mergeActors(remoteActors) {
  const byId = new Map();
  for (const a of remoteActors) {
    byId.set(a.actorId, {
      actorId: a.actorId,
      sessions: Array.isArray(a.sessions) ? [...a.sessions] : [],
    });
  }
  for (const local of state.actors) {
    const remote = byId.get(local.actorId);
    if (!remote) {
      byId.set(local.actorId, { actorId: local.actorId, sessions: [...(local.sessions || [])] });
    } else {
      for (const s of local.sessions || []) {
        if (!remote.sessions.includes(s)) remote.sessions.push(s);
      }
    }
  }
  state.actors = [...byId.values()];
}

function renderUsers() {
  const box = $("usersList");
  box.innerHTML = "";
  if (!state.actors.length) {
    box.innerHTML = '<div class="list-empty">Chưa có người dùng nào.</div>';
    return;
  }
  for (const actor of state.actors) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "list-item" + (actor.actorId === state.actor ? " active" : "");

    const icon = document.createElement("span");
    icon.className = "list-item-icon";
    icon.textContent = (actor.actorId || "?").charAt(0);

    const main = document.createElement("span");
    main.className = "list-item-main";
    main.textContent = actor.actorId;
    const sub = document.createElement("span");
    sub.className = "list-item-sub";
    sub.textContent = `${(actor.sessions || []).length} phiên`;

    btn.appendChild(icon);
    btn.appendChild(main);
    btn.appendChild(sub);
    btn.addEventListener("click", () => selectUser(actor.actorId));
    box.appendChild(btn);
  }
}

function renderSessions() {
  const box = $("sessionsList");
  box.innerHTML = "";
  if (!state.actor) {
    box.innerHTML = '<div class="list-empty">Chọn người dùng trước.</div>';
    return;
  }
  const actor = state.actors.find((a) => a.actorId === state.actor);
  const sessions = actor ? actor.sessions || [] : [];
  if (!sessions.length) {
    box.innerHTML = '<div class="list-empty">Chưa có phiên nào.</div>';
    return;
  }
  for (const sessionId of sessions) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "list-item" + (sessionId === state.session ? " active" : "");
    const icon = document.createElement("span");
    icon.className = "list-item-icon";
    icon.textContent = "#";
    const main = document.createElement("span");
    main.className = "list-item-main";
    main.textContent = sessionId;
    btn.appendChild(icon);
    btn.appendChild(main);
    btn.addEventListener("click", () => selectSession(sessionId));
    box.appendChild(btn);
  }
}

function updateChips() {
  $("chipUser").textContent = state.actor ? "@" + state.actor : "@—";
  $("chipSession").textContent = state.session || "—";
  $("memoryTitle").textContent = state.actor ? `🧠 Bộ nhớ của ${state.actor}` : "🧠 Bộ nhớ";
}

function selectUser(actorId) {
  state.actor = actorId;
  const actor = state.actors.find((a) => a.actorId === actorId);
  const sessions = actor ? actor.sessions || [] : [];
  if (!state.session || !sessions.includes(state.session)) {
    state.session = sessions[0] || null;
  }
  renderUsers();
  renderSessions();
  updateChips();
  clearChat();
  loadHistory();
  loadMemory(); // refresh the memory panel when the user changes
}

function selectSession(sessionId) {
  state.session = sessionId;
  renderSessions();
  updateChips();
  clearChat();
  loadHistory();
  loadMemory();
}

// "New user": the input becomes a lowercase slug and is selected right away
function createUser() {
  const input = $("newUserInput");
  const slug = slugify(input.value);
  if (!slug) {
    showToast("Nhập tên người dùng (chữ/số/gạch ngang, không dấu) trước đã!");
    return;
  }
  if (!state.actors.some((a) => a.actorId === slug)) {
    state.actors.push({ actorId: slug, sessions: [] });
  }
  input.value = "";
  selectUser(slug);
}

// "New session": id = "s-" + timestamp
function createSession() {
  if (!state.actor) {
    showToast("Hãy chọn hoặc tạo người dùng trước.");
    return;
  }
  const sessionId = "s-" + Date.now();
  const actor = state.actors.find((a) => a.actorId === state.actor);
  if (actor && !actor.sessions.includes(sessionId)) actor.sessions.unshift(sessionId);
  selectSession(sessionId);
}

async function loadActors() {
  try {
    const data = await getJson(API.ACTORS);
    mergeActors(Array.isArray(data.actors) ? data.actors : []);
  } catch (e) {
    showToast("Không tải được danh sách người dùng: " + e.message);
  }
  if (!state.actor && state.actors.length) {
    selectUser(state.actors[0].actorId);
  } else {
    renderUsers();
    renderSessions();
    updateChips();
  }
}

// Refresh the actor list after the agent replied (the selection does not change)
async function refreshActorsSilently() {
  try {
    const data = await getJson(API.ACTORS);
    mergeActors(Array.isArray(data.actors) ? data.actors : []);
    renderUsers();
    renderSessions();
  } catch { /* silent: not worth bothering the user */ }
}

/* ------------------- GET /api/history ------------------- */

async function loadHistory() {
  const token = state.viewToken;
  if (!state.actor || !state.session) {
    renderEmptyChat();
    return;
  }
  try {
    const data = await getJson(
      `${API.HISTORY}?actor=${encodeURIComponent(state.actor)}&session=${encodeURIComponent(state.session)}`
    );
    if (token !== state.viewToken) return; // the session changed while waiting
    const events = Array.isArray(data.events) ? data.events : [];
    if (!events.length) {
      renderEmptyChat();
      return;
    }
    for (const ev of events) {
      appendBubble(ev.role === "user" ? "user" : "bot", ev.message || "", []);
    }
  } catch (e) {
    if (token !== state.viewToken) return;
    renderEmptyChat();
    showToast("Không tải được lịch sử hội thoại: " + e.message);
  }
}

/* ------------------- Sending a message ------------------- */

async function sendMessage() {
  const input = $("composerInput");
  const text = input.value.trim();

  if (!text || state.sending) return;
  if (!state.actor || !state.session) {
    showToast("Chưa chọn người dùng / phiên hội thoại — hãy chọn ở cột bên trái.");
    return;
  }

  state.sending = true;
  $("sendBtn").disabled = true;

  appendBubble("user", text, []);
  input.value = "";
  autosizeComposer();

  const typing = showTypingIndicator();

  try {
    // 1) Try SSE streaming: tokens render as they arrive in a live bubble
    let data;
    try {
      data = await postStream(text);
    } catch (streamErr) {
      if (!(streamErr instanceof StreamUnavailable)) throw streamErr;
      // 2) Streaming is not available: fall back to POST /invocations
      const fallback = await postInvocation(text);
      if (!fallback || fallback.status !== "success") {
        throw new Error((fallback && fallback.error) || streamErr.message || "Agent trả về lỗi.");
      }
      data = fallback;
    }
    typing.stop();
    if (!data || data.status === "error") {
      throw new Error((data && data.error) || "Agent trả về lỗi.");
    }
    setDot("ok");
    loadMemory();            // refresh the memory panel after every bot reply
    refreshActorsSilently(); // new actors/sessions may now exist on the server
  } catch (e) {
    typing.stop();
    setDot("err");
    appendBubble("bot", `⚠️ **Không gọi được agent:** ${e.message}`, []);
    showToast("Gọi agent thất bại: " + e.message);
  } finally {
    state.sending = false;
    $("sendBtn").disabled = false;
    $("composerInput").focus();
  }
}

/* ------------------- "Memory" panel (GET /api/memory) ------------------- */

// Group title: maps strategy id/name to a Vietnamese title, falling back to the raw name
function strategyTitle(group) {
  const sid = String(group.strategy_id || "").toLowerCase();
  const sname = String(group.strategy || "");
  const lower = sname.toLowerCase();
  if (sid.includes("preference") || lower.includes("preference")) return "⭐ Sở thích";
  if (sid.includes("trip") || lower.includes("trip")) return "📌 Sự kiện chuyến đi";
  return sname || group.strategy_id || "Nhóm khác";
}

function buildGroupCard(group) {
  const sec = document.createElement("div");
  sec.className = "mem-group";

  const title = document.createElement("div");
  title.className = "mem-group-title";
  title.textContent = strategyTitle(group);
  sec.appendChild(title);

  // A group error is shown as a red note
  if (group.error) {
    const err = document.createElement("div");
    err.className = "mem-group-error";
    err.textContent = "⚠️ " + group.error;
    sec.appendChild(err);
    return sec;
  }

  const records = Array.isArray(group.records) ? group.records : [];
  if (!records.length) {
    const empty = document.createElement("div");
    empty.className = "mem-group-empty";
    empty.textContent = "Chưa có bản ghi trong nhóm này.";
    sec.appendChild(empty);
    return sec;
  }

  for (const rec of records) {
    const card = document.createElement("div");
    card.className = "mem-card";
    const fact = document.createElement("div");
    fact.className = "mem-card-fact";
    fact.textContent = rec.memory || "";
    const time = document.createElement("div");
    time.className = "mem-card-time";
    time.textContent = relativeTime(rec.createdAt);
    card.appendChild(fact);
    card.appendChild(time);
    sec.appendChild(card);
  }
  return sec;
}

async function loadMemory() {
  const token = ++memoryToken;
  const body = $("memoryBody");

  if (!state.actor) {
    body.innerHTML = '<div class="mem-empty">Chưa có dữ liệu — hãy trò chuyện để agent học về bạn</div>';
    renderRecent([]);
    return;
  }

  body.innerHTML = '<div class="mem-loading">Đang tải bộ nhớ…</div>';
  try {
    const data = await getJson(`${API.MEMORY}?actor=${encodeURIComponent(state.actor)}`);
    if (token !== memoryToken) return; // the user changed while waiting
    const groups = Array.isArray(data.groups) ? data.groups : [];
    body.innerHTML = "";
    if (!groups.length) {
      body.innerHTML = '<div class="mem-empty">Chưa có dữ liệu — hãy trò chuyện để agent học về bạn</div>';
    }
    for (const group of groups) body.appendChild(buildGroupCard(group));
  } catch (e) {
    if (token !== memoryToken) return;
    body.innerHTML = `<div class="mem-error">Không tải được bộ nhớ: ${escapeHtml(e.message)}</div>`;
  }
  loadRecent();
}

/* ------------------- "Recent conversation" (last 5 events) ------------------- */

function renderRecent(events) {
  const box = $("recentList");
  box.innerHTML = "";
  if (!events.length) {
    box.innerHTML = '<div class="recent-empty">Chưa có hội thoại nào.</div>';
    return;
  }
  for (const ev of events) {
    const item = document.createElement("div");
    item.className = "recent-item" + (ev.role === "user" ? " from-user" : "");
    const role = document.createElement("span");
    role.className = "recent-role";
    role.textContent = ev.role === "user" ? "Bạn" : "Travel Buddy";
    const msg = document.createElement("span");
    msg.className = "recent-msg";
    msg.textContent = truncate(ev.message || "", 80); // cut at 80 characters
    item.appendChild(role);
    item.appendChild(msg);
    box.appendChild(item);
  }
}

async function loadRecent() {
  if (!state.actor || !state.session) {
    renderRecent([]);
    return;
  }
  try {
    const data = await getJson(
      `${API.HISTORY}?actor=${encodeURIComponent(state.actor)}&session=${encodeURIComponent(state.session)}`
    );
    const events = Array.isArray(data.events) ? data.events : [];
    renderRecent(events.slice(-5).reverse()); // newest first
  } catch {
    $("recentList").innerHTML = '<div class="recent-empty">Không tải được hội thoại gần đây.</div>';
  }
}

/* ------------------- Error toast (auto-hides, dismissible) ------------------- */

let toastTimer = null;

function showToast(message) {
  const toast = $("toast");
  $("toastMsg").textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 6000);
}

/* ------------------- Composer (auto-growing textarea) ------------------- */

function autosizeComposer() {
  const el = $("composerInput");
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 160) + "px";
}

/* ------------------- Event binding and startup ------------------- */

function bindEvents() {
  $("sendBtn").addEventListener("click", sendMessage);

  const composer = $("composerInput");
  composer.addEventListener("input", autosizeComposer);
  composer.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { // Enter sends, Shift+Enter inserts a newline
      e.preventDefault();
      sendMessage();
    }
  });

  $("newUserBtn").addEventListener("click", createUser);
  $("newUserInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); createUser(); }
  });

  $("newSessionBtn").addEventListener("click", createSession);
  $("memoryRefreshBtn").addEventListener("click", loadMemory);

  // The memory panel opens/closes on narrow screens
  $("memoryToggle").addEventListener("click", () => $("layout").classList.toggle("show-memory"));
  $("panelClose").addEventListener("click", () => $("layout").classList.remove("show-memory"));

  $("toastClose").addEventListener("click", () => {
    clearTimeout(toastTimer);
    $("toast").classList.remove("show");
  });
}

async function init() {
  bindEvents();
  await loadInfo();
  await loadActors();
}

document.addEventListener("DOMContentLoaded", init);
