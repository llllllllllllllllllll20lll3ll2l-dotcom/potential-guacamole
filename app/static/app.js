/* НейроКоманда — клиент интерфейса: WebSocket, лента, глаз браузера, уведомления. */
let ws = null;
let soundOn = true;
let audioCtx = null;
let running = false;
const liveBubbles = {};   // роль -> элемент пузыря «мышления»

const $ = (id) => document.getElementById(id);
const feed = $("feed"), logEl = $("log");

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    handle(m);
  };
  ws.onclose = () => setTimeout(connect, 2000);
}

function handle(m) {
  switch (m.type) {
    case "hello":
      applyHealth(m.health);
      break;
    case "log":
      addLog(m.ts, m.line, m.kind);
      break;
    case "token": {
      let el = liveBubbles[m.role];
      if (!el) {
        addFeed(`<div class="msg live" id="live-${esc(m.role)}">🤔 <b>${esc(m.role)}</b> думает<span class="caret"></span><div class="t"></div></div>`);
        el = liveBubbles[m.role] = document.getElementById("live-" + m.role);
      }
      el.querySelector(".t").textContent += m.text;
      feed.scrollTop = feed.scrollHeight;
      break;
    }
    case "token_end": {
      const el = liveBubbles[m.role];
      if (el) { el.classList.add("done"); const c = el.querySelector(".caret"); if (c) c.remove(); }
      delete liveBubbles[m.role];
      break;
    }
    case "task_started":
      running = true; setSend();
      addFeed(`<div class="msg user">📨 <b>Твоя задача:</b> ${esc(m.task)}</div>`);
      break;
    case "plan": {
      if (!m.plan || !m.plan.length) {
        addFeed(`<div class="msg orch">🧠 <b>Оркестратор:</b> ${esc(m.comment)}</div>`);
        break;
      }
      const items = m.plan.map((s, i) =>
        `<li><span class="badge">${esc(roleName(s.agent))}</span> ${esc(s.task)}</li>`).join("");
      addFeed(`<div class="msg plan">🧠 <b>План Оркестратора:</b> ${esc(m.comment || "")}<ol>${items}</ol></div>`);
      break;
    }
    case "step_start":
      addFeed(`<div class="msg step" id="step-${m.i}">▶ Шаг ${m.i}: <span class="badge">${esc(m.role)}</span> ${esc(m.task)} <span class="dots">…</span></div>`);
      break;
    case "step_done": {
      const el = $("step-" + m.i);
      if (el) {
        el.classList.add(m.status === "done" ? "ok" : "fail");
        el.innerHTML = el.innerHTML.replace('<span class="dots">…</span>',
          `<div class="result">${m.status === "done" ? "✔" : "⚠"} ${esc(m.result)}</div>`);
      }
      break;
    }
    case "action":
      addLog("", `🔧 ${esc(m.role)} → ${esc(m.tool)}: ${esc(m.obs)}`, "action");
      if (m.shot) {
        $("eye").src = "data:image/png;base64," + m.shot;
        $("eye").classList.remove("hidden");
        $("eye-placeholder").classList.add("hidden");
        $("eye-caption").textContent = `${m.role}: ${m.tool}`;
      }
      break;
    case "approval":
      addFeed(`<div class="msg approval" id="appr-${m.id}">
        🔒 <b>Нужно твоё подтверждение</b><br>${esc(m.desc)}
        <pre>${esc(m.args)}</pre>
        <div class="appr-btns">
          <button data-id="${m.id}" data-ok="1">✅ Разрешить</button>
          <button data-id="${m.id}" data-ok="0">🚫 Запретить</button>
        </div></div>`);
      beep(440, 0.12);
      break;
    case "approval_done": {
      const el = $("appr-" + m.id);
      if (el) {
        el.classList.add(m.ok ? "ok" : "fail");
        const b = el.querySelector(".appr-btns");
        if (b) b.innerHTML = m.ok ? "<i>— разрешено</i>" : "<i>— запрещено</i>";
      }
      break;
    }
    case "report":
      addFeed(`<div class="msg report ${m.status}">📄 <b>Отчёт Оркестратора</b> (${esc(m.status)})<pre>${esc(m.text)}</pre>
        <div class="muted">Итог: ${esc(m.result)} · файл: ${esc(m.file)}</div></div>`);
      break;
    case "notify":
      notify(m.title, m.body);
      break;
    case "task_done":
      running = false; setSend();
      break;
    case "memory_cleared":
      $("chip-memory").textContent = "Память: 0 записей";
      break;
  }
}

function roleName(a) {
  return { coder: "Программист", researcher: "Исследователь", files: "Файловый",
           executor: "Исполнитель", orchestrator: "Оркестратор" }[a] || a;
}

function addFeed(html) {
  feed.insertAdjacentHTML("beforeend", html);
  feed.scrollTop = feed.scrollHeight;
}

function addLog(ts, line, kind) {
  const div = document.createElement("div");
  div.className = "logline " + (kind || "");
  div.textContent = (ts ? ts + "  " : "") + line;
  logEl.appendChild(div);
  while (logEl.children.length > 400) logEl.removeChild(logEl.firstChild);
  logEl.scrollTop = logEl.scrollHeight;
}

function setSend() {
  const b = $("btn-send");
  b.disabled = running;
  b.textContent = running ? "Выполняется…" : "Отправить ➤";
}

function sendTask() {
  const t = $("task").value.trim();
  if (!t || running || !ws) return;
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission();
  }
  ws.send(JSON.stringify({ type: "task", text: t }));
  $("task").value = "";
}

function notify(title, body) {
  if ("Notification" in window && Notification.permission === "granted") {
    try { new Notification(title, { body }); } catch (e) {}
  }
  if (soundOn) { beep(880, 0.12); setTimeout(() => beep(1175, 0.18), 140); }
}

function beep(freq, dur) {
  if (!soundOn) return;
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.frequency.value = freq; o.type = "sine";
    g.gain.setValueAtTime(0.15, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + dur);
    o.connect(g); g.connect(audioCtx.destination);
    o.start(); o.stop(audioCtx.currentTime + dur);
  } catch (e) {}
}

async function refreshHealth() {
  try {
    const r = await fetch("/api/health");
    applyHealth(await r.json());
  } catch (e) {}
}

function applyHealth(h) {
  if (!h) return;
  $("chip-ollama").textContent = "Ollama: " + (h.ollama ? "на связи ✅" : "нет связи ⛔");
  $("chip-ollama").classList.toggle("bad", !h.ollama);
  $("chip-memory").textContent = `Память: ${h.memory} записей`;
  $("chip-wl").textContent = "📁 " + (h.whitelist || []).join("; ");
  $("chip-demo").classList.toggle("hidden", !h.demo);
}

document.addEventListener("DOMContentLoaded", () => {
  connect();
  setInterval(refreshHealth, 15000);
  $("btn-send").onclick = sendTask;
  $("task").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendTask(); }
  });
  $("btn-memory").onclick = async () => {
    if (!confirm("Стереть все воспоминания системы?")) return;
    await fetch("/api/memory/clear", { method: "POST" });
  };
  $("btn-xls").onclick = () => window.open("/api/reports/xls", "_blank");
  $("btn-sound").onclick = () => {
    soundOn = !soundOn;
    $("btn-sound").textContent = soundOn ? "🔔 Звук: вкл" : "🔕 Звук: выкл";
  };
  feed.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-id]");
    if (!b || !ws) return;
    ws.send(JSON.stringify({ type: "approve", id: +b.dataset.id, ok: b.dataset.ok === "1" }));
  });
});
