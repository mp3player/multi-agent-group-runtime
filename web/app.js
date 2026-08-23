const state = {
  busy: false,
  messages: new Map(),
  memberCount: 0,
};

const els = {
  groupName: document.querySelector("#groupName"),
  members: document.querySelector("#members"),
  messages: document.querySelector("#messages"),
  statusText: document.querySelector("#statusText"),
  form: document.querySelector("#chatForm"),
  input: document.querySelector("#messageInput"),
  sendBtn: document.querySelector("#sendBtn"),
  clearBtn: document.querySelector("#clearBtn"),
  newBtn: document.querySelector("#newBtn"),
  showPass: document.querySelector("#showPass"),
  memberForm: document.querySelector("#memberForm"),
  memberName: document.querySelector("#memberName"),
  memberDescription: document.querySelector("#memberDescription"),
  memberUrl: document.querySelector("#memberUrl"),
  memberModel: document.querySelector("#memberModel"),
  memberKey: document.querySelector("#memberKey"),
  addMemberBtn: document.querySelector("#addMemberBtn"),
};

function isPass(content) {
  const normalized = content.trim().replace(/^`+|`+$/g, "").trim();
  return normalized.toUpperCase() === "PASS" || normalized === "group_pass()";
}

function setBusy(busy) {
  state.busy = busy;
  els.sendBtn.disabled = busy || state.memberCount === 0;
  els.addMemberBtn.disabled = busy;
  els.statusText.textContent = busy
    ? "Group is running"
    : state.memberCount === 0
      ? "Add a member"
      : "Ready";
}

function renderMembers(members) {
  els.members.innerHTML = "";
  state.memberCount = members.length;
  if (!members.length) {
    els.members.innerHTML = `<div class="empty-members">No members. Add one below.</div>`;
    setBusy(state.busy);
    return;
  }
  for (const member of members) {
    const node = document.createElement("div");
    node.className = "member";
    const tools = member.tools.slice(0, 6).join(", ");
    const queue = `${member.unread}/${member.dispatchable}`;
    node.innerHTML = `
      <div class="member-top">
        <span class="member-avatar">${escapeHtml(senderInitial(member.name))}</span>
        <span class="member-main">
          <strong>${escapeHtml(member.name)}</strong>
          <small>${escapeHtml(member.description || "No description")}</small>
        </span>
        <span class="member-status ${member.status === "running" ? "running" : "idle"}">
          <span class="status-dot"></span>
          ${escapeHtml(member.status)}
        </span>
      </div>
      <div class="member-metrics">
        <span>queue ${escapeHtml(queue)}</span>
        <span>${escapeHtml(member.model || "model unset")}</span>
      </div>
      <details class="member-details">
        <summary>Connection & tools</summary>
        <small>${escapeHtml(member.url || "url unset")}</small>
        <small>${escapeHtml(tools || "no tools")}</small>
      </details>
    `;
    els.members.appendChild(node);
  }
}

function addMessage(message) {
  if (state.messages.has(message.id)) return;
  state.messages.set(message.id, message);
  if (isPass(message.content) && !els.showPass.checked) return;

  const node = renderMessage(message);
  els.messages.appendChild(node);
  els.messages.scrollTop = els.messages.scrollHeight;
}

function rerenderMessages() {
  els.messages.innerHTML = "";
  const messages = [...state.messages.values()].sort((a, b) => a.id - b.id);
  state.messages.clear();
  for (const message of messages) {
    state.messages.set(message.id, message);
    if (isPass(message.content) && !els.showPass.checked) continue;
    els.messages.appendChild(renderMessage(message));
  }
  els.messages.scrollTop = els.messages.scrollHeight;
}

function renderMessage(message) {
  const pass = isPass(message.content);
  if (pass) {
    const node = document.createElement("div");
    node.className = "pass-event";
    node.dataset.id = String(message.id);
    node.dataset.sender = message.sender;
    node.innerHTML = `
      <span class="pass-dot"></span>
      <span>${escapeHtml(message.sender)} passed</span>
      <span class="message-id">#${message.id}</span>
    `;
    return node;
  }

  const node = document.createElement("article");
  node.className = `message ${message.kind}`;
  node.dataset.id = String(message.id);
  node.dataset.sender = message.sender;
  const mode = message.propagate ? "broadcast" : "local";
  const dispatch = message.dispatch_mode && message.dispatch_mode !== "normal"
    ? `<span class="meta-chip">${escapeHtml(message.dispatch_mode)}</span>`
    : "";
  node.innerHTML = `
    <div class="meta">
      <span class="avatar">${escapeHtml(senderInitial(message.sender))}</span>
      <span class="sender">${escapeHtml(message.sender)}</span>
      <span class="message-id">#${message.id}</span>
      <span class="meta-chip ${message.propagate ? "broadcast" : "local"}">${mode}</span>
      ${dispatch}
    </div>
    <div class="content">${renderMarkdown(message.content)}</div>
  `;
  return node;
}

function senderInitial(sender) {
  const trimmed = String(sender || "?").trim();
  return trimmed ? trimmed[0].toUpperCase() : "?";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderMarkdown(value) {
  const lines = String(value).replaceAll("\r\n", "\n").split("\n");
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }

    const fence = line.match(/^```(\w+)?\s*$/);
    if (fence) {
      const code = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith("```")) {
        code.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1;
      const lang = fence[1] ? ` data-lang="${escapeHtml(fence[1])}"` : "";
      blocks.push(`<pre${lang}><code>${escapeHtml(code.join("\n"))}</code></pre>`);
      continue;
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length + 2;
      blocks.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }

    if (isTableStart(lines, i)) {
      const header = splitTableRow(lines[i]);
      const align = splitTableRow(lines[i + 1]);
      const rows = [];
      i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      blocks.push(renderTable(header, align, rows));
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i += 1;
      }
      blocks.push(`<ul>${items.map((item) => `<li>${renderInline(item)}</li>`).join("")}</ul>`);
      continue;
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i += 1;
      }
      blocks.push(`<ol>${items.map((item) => `<li>${renderInline(item)}</li>`).join("")}</ol>`);
      continue;
    }

    const paragraph = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^```/.test(lines[i]) &&
      !/^(#{1,3})\s+/.test(lines[i]) &&
      !/^\s*[-*]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i]) &&
      !isTableStart(lines, i)
    ) {
      paragraph.push(lines[i]);
      i += 1;
    }
    blocks.push(`<p>${paragraph.map(renderInline).join("<br>")}</p>`);
  }

  return blocks.join("");
}

function renderInline(value) {
  const tokens = [];
  let html = escapeHtml(value).replace(/`([^`]+)`/g, (_, code) => {
    const key = `\u0000${tokens.length}\u0000`;
    tokens.push(`<code>${code}</code>`);
    return key;
  });
  html = html
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
  for (let index = 0; index < tokens.length; index += 1) {
    html = html.replaceAll(`\u0000${index}\u0000`, tokens[index]);
  }
  return html;
}

function isTableStart(lines, index) {
  return (
    index + 1 < lines.length &&
    /^\s*\|.*\|\s*$/.test(lines[index]) &&
    /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index + 1])
  );
}

function splitTableRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function renderTable(header, align, rows) {
  const aligns = align.map((cell) => {
    if (cell.startsWith(":") && cell.endsWith(":")) return "center";
    if (cell.endsWith(":")) return "right";
    return "left";
  });
  const th = header.map((cell, idx) => `<th style="text-align:${aligns[idx] || "left"}">${renderInline(cell)}</th>`).join("");
  const trs = rows.map((row) => {
    const tds = header.map((_, idx) => `<td style="text-align:${aligns[idx] || "left"}">${renderInline(row[idx] || "")}</td>`).join("");
    return `<tr>${tds}</tr>`;
  }).join("");
  return `<div class="table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table></div>`;
}

async function loadState() {
  const response = await fetch("/api/state");
  const data = await response.json();
  els.groupName.textContent = data.group;
  renderMembers(data.members);
  setBusy(data.busy);
}

async function loadHistory() {
  const response = await fetch("/api/history");
  const data = await response.json();
  state.messages.clear();
  els.messages.innerHTML = "";
  for (const message of data.messages) {
    addMessage(message);
  }
}

async function postJson(path, payload = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

els.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = els.input.value.trim();
  if (!message) return;
  try {
    setBusy(true);
    await postJson("/api/chat", { message });
    els.input.value = "";
  } catch (error) {
    setBusy(false);
    alert(error.message);
  }
});

els.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    els.form.requestSubmit();
  }
});

els.clearBtn.addEventListener("click", async () => {
  await postJson("/api/clear");
  state.messages.clear();
  els.messages.innerHTML = "";
  await loadState();
});

els.newBtn.addEventListener("click", async () => {
  await postJson("/api/new");
  state.messages.clear();
  els.messages.innerHTML = "";
  await loadState();
});

els.showPass.addEventListener("change", () => {
  rerenderMessages();
});

els.memberForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    name: els.memberName.value.trim(),
    description: els.memberDescription.value.trim(),
    base_url: els.memberUrl.value.trim(),
    model: els.memberModel.value.trim(),
    api_key: els.memberKey.value.trim(),
  };
  if (!payload.name) {
    alert("Name is required");
    return;
  }
  try {
    els.addMemberBtn.disabled = true;
    await postJson("/api/members", payload);
    els.memberName.value = "";
    els.memberDescription.value = "";
    els.memberUrl.value = "";
    els.memberModel.value = "";
    els.memberKey.value = "";
    await loadState();
  } catch (error) {
    alert(error.message);
  } finally {
    els.addMemberBtn.disabled = state.busy;
  }
});

function connectEvents() {
  const source = new EventSource("/api/events");
  source.onopen = () => {
    els.statusText.textContent = state.busy ? "Group is running" : "Ready";
  };
  source.onmessage = async (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "message") {
      addMessage(payload.message);
      await loadState();
      return;
    }
    if (payload.type === "member_start") {
      els.statusText.textContent = `${payload.member} is running`;
      await loadState();
      return;
    }
    if (payload.type === "done") {
      setBusy(false);
      await loadState();
      return;
    }
    if (payload.type === "clear" || payload.type === "reset") {
      state.messages.clear();
      els.messages.innerHTML = "";
      await loadState();
      return;
    }
    if (payload.type === "members_changed") {
      await loadState();
      return;
    }
    if (payload.type === "error") {
      setBusy(false);
      alert(payload.error);
    }
  };
  source.onerror = () => {
    els.statusText.textContent = "Reconnecting...";
  };
}

loadState();
loadHistory();
connectEvents();
