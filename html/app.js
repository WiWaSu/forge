const $ = (s, r = document) => r.querySelector(s);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const fmt = (v, d = 2) => {
  if (v === null || v === undefined) return "—";
  const s = Number(v).toFixed(d);
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
};

let STATE = null;
const PRIM_LABELS = { cylinder: "Цилиндр", tube: "Труба", box: "Параллелепипед" };

const GOST_LETTERS = { M: "М", C: "С", T: "Т" };

const processRu = s => {
  const n = (STATE && STATE.processes && STATE.processes[s]) || s || "—";
  return n.charAt(0).toUpperCase() + n.slice(1);
};
const gostCode = s => String(s ?? "—").replace(/^[MCT]/, c => GOST_LETTERS[c]);
const unitRu = u => String(u ?? "").replace(/2$/, "²").replace(/3$/, "³");
const equipRu = code => (STATE && STATE.equipment && STATE.equipment[code]) || code || "—";

async function loadState() {
  try {
    STATE = await (await fetch("/api/state")).json();
  } catch (e) {
    $("#pills").append(el("span", "pill bad", "<span class='dot'></span>Движок не отвечает"));
    return;
  }
  renderPills();
  fillSelect($("#f-type"), STATE.part_types.map(t => [t.code, t.description.split("(")[0].trim()]));
  fillSelect($("#f-mat"), STATE.materials.map(m => [m.grade, m.grade]));
  renderSamples();
  renderModes();
  renderModelPicker();
  startOllamaPolling();
  if (!$("#prims").children.length) addPrim("cylinder");
}

function renderPills() {
  const p = $("#pills");
  p.innerHTML = "";
  const o = STATE.ollama;

  const model = effectiveModel();
  const ok = ollamaOk();
  const label = ok ? "Ollama: " + esc(model)
    : !o.app_installed ? "Ollama не установлена"
    : !o.service ? "Ollama не запущена"
    : "Нет модели " + esc(model);
  const pill = el("span", "pill clickable " + (ok ? "ok" : "warn"),
    `<span class="dot"></span>${label}`);
  pill.title = (o.message || "") + ". Нажмите, чтобы скачать модель или запустить Ollama";
  pill.onclick = openModelSetup;
  p.append(pill);

  const g = STATE.gost7505;
  if (!g.verified) {
    p.append(el("span", "pill warn",
      `<span class="dot"></span>ГОСТ 7505: требует сверки`));
    p.lastChild.title = g.note;
  }
}

let FR_REDRAW = null;

const OLLAMA_POLL_MS = 15000;
const PING_MS = 30000;

async function refreshOllama() {
  let fresh;
  try { fresh = (await (await fetch("/api/ollama")).json()).ollama; }
  catch (e) { return false; }
  const before = JSON.stringify([STATE.ollama.ok, STATE.ollama.service,
                                 STATE.ollama.app_installed, STATE.ollama.installed]);
  const after = JSON.stringify([fresh.ok, fresh.service, fresh.app_installed, fresh.installed]);
  STATE.ollama = fresh;
  if (before === after) return false;
  renderPills();
  renderModelPicker();
  if (FR_REDRAW) FR_REDRAW();
  drawModelSetup();
  return true;
}

function startOllamaPolling() {
  setInterval(() => { if (!document.hidden) refreshOllama(); }, OLLAMA_POLL_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshOllama();
  });
}

const STEP_BUSY = { ollama: "ставлю", start: "запускаю", model: "скачиваю" };
const STEP_DONE = { ollama: "Ollama установлена.", start: "Ollama запущена.", model: "Модель скачана." };

async function startStep(step, btn, model = "") {
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>' + (STEP_BUSY[step] || "работаю");
  const log = $("#ms-log");
  log.hidden = false;
  log.textContent = "";
  try {
    await api("/api/setup", { step, model });
  } catch (e) {
    $("#ms-note").textContent = "Не удалось запустить: " + e.message;
    btn.disabled = false;
    btn.textContent = "Повторить";
    return;
  }
  pollSetup(step, btn);
}

async function pollSetup(step, btn) {
  let data;
  try { data = (await (await fetch("/api/setup")).json()).setup; }
  catch (e) { return; }

  const log = $("#ms-log"), note = $("#ms-note");
  if (log && data.lines?.length) { log.textContent = data.lines.join("\n"); log.scrollTop = log.scrollHeight; }
  if (data.running) { setTimeout(() => pollSetup(step, btn), 1500); return; }

  if (data.ok) {
    await refreshOllama();
    if (FR_REDRAW) FR_REDRAW();
    drawModelSetup();
    if ($("#ms-note")) $("#ms-note").textContent = STEP_DONE[step] || "Готово.";
  } else if (note) {
    note.textContent = data.error || "Шаг не завершился";
    btn.textContent = "Повторить";
    btn.disabled = false;
  }
}

function renderModelPicker() {
  const box = $("#model-pick"), pick = $("#f-pick-model"), sel = $("#f-model");
  const models = (STATE.ollama && STATE.ollama.models) || [];
  const fallback = (STATE.ollama && STATE.ollama.model) || "";

  pick.disabled = !models.length;
  pick.parentElement.title = models.length ? "" : "Список появится, когда Ollama будет запущена";
  pick.onchange = () => { box.hidden = !pick.checked; onModelChange(); };
  sel.onchange = onModelChange;

  if (!models.length) { box.hidden = true; return; }

  const keep = sel.value || PREF_MODEL;
  fillSelect(sel, models.map(m => [
    m.name,
    m.name + (m.size_gb ? `  ·  ${fmt(m.size_gb, 1)} ГБ` : "") +
             (m.parameters ? `  ·  ${m.parameters}` : ""),
  ]));
  const has = name => models.some(m => m.name === name);
  sel.value = has(keep) ? keep : has(fallback) ? fallback : models[0].name;
  box.hidden = !pick.checked;
}

function chosenModel() {
  return $("#f-pick-model").checked ? $("#f-model").value : "";
}

function effectiveModel() {
  return chosenModel() || (STATE.ollama && STATE.ollama.model) || "";
}

function modelInstalled(name) {
  const list = (STATE.ollama && STATE.ollama.installed) || [];
  return list.includes(name) || (!name.includes(":") && list.includes(name + ":latest"));
}

function ollamaOk() {
  return !!(STATE.ollama && STATE.ollama.service) && modelInstalled(effectiveModel());
}

function onModelChange() {
  savePrefs();
  renderPills();
}

function fillSelect(sel, pairs) {
  sel.innerHTML = "";
  for (const [v, t] of pairs) {
    const o = document.createElement("option");
    o.value = v; o.textContent = t;
    sel.append(o);
  }
}

function renderSamples() {
  const box = $("#samples");
  box.innerHTML = "";
  STATE.samples.forEach((s, i) => {
    const b = el("button", "chip", esc(s.name));
    b.onclick = () => loadSample(i);
    box.append(b);
  });
}

function renderModes() {
  const box = $("#modes");
  box.innerHTML = "";
  STATE.modes.forEach((m, i) => {
    const lab = el("label", "mode");
    lab.innerHTML = `<input type="radio" name="mode" value="${m.code}" ${i === 1 ? "checked" : ""}>
      <div title="${esc(m.hint)}"><b>${esc(m.name)}</b></div>`;
    box.append(lab);
  });
}

function addPrim(kind, data = {}) {
  const wrap = el("div", "prim");
  wrap.dataset.kind = kind;
  const dims = kind === "box"
    ? `<div class="dims three">
         <div><label>A, мм</label><input data-f="a" type="number" step="0.1" value="${data.a ?? ""}"></div>
         <div><label>B, мм</label><input data-f="b" type="number" step="0.1" value="${data.b ?? ""}"></div>
         <div><label>C, мм</label><input data-f="c" type="number" step="0.1" value="${data.c ?? ""}"></div>
       </div>`
    : kind === "tube"
    ? `<div class="dims three">
         <div><label>D нар., мм</label><input data-f="d" type="number" step="0.1" value="${data.d ?? ""}"></div>
         <div><label>d внутр., мм</label><input data-f="d_inner" type="number" step="0.1" value="${data.d_inner ?? ""}"></div>
         <div><label>Длина, мм</label><input data-f="l" type="number" step="0.1" value="${data.l ?? ""}"></div>
       </div>`
    : `<div class="dims">
         <div><label>Диаметр, мм</label><input data-f="d" type="number" step="0.1" value="${data.d ?? ""}"></div>
         <div><label>Длина, мм</label><input data-f="l" type="number" step="0.1" value="${data.l ?? ""}"></div>
       </div>`;
  wrap.innerHTML = `
    <div class="prim-head">
      <input data-f="name" placeholder="${PRIM_LABELS[kind]}" value="${esc(data.name ?? "")}">
      <button>Удалить</button>
    </div>
    ${dims}
    <div class="dims" style="margin-top:8px">
      <label class="check" title="Отверстие или выточка"><input type="checkbox" data-f="subtract" ${data.subtract ? "checked" : ""}> вычитается</label>
      <label class="check" title="Элемент добавляет длину детали"><input type="checkbox" data-f="along_axis" ${data.along_axis === false ? "" : "checked"}> вдоль оси</label>
    </div>`;
  wrap.querySelector(".prim-head button").onclick = () => wrap.remove();
  $("#prims").append(wrap);
}

function collectPart() {
  const prims = [...$("#prims").children].map(w => {
    const o = { kind: w.dataset.kind };
    w.querySelectorAll("[data-f]").forEach(i => {
      const f = i.dataset.f;
      o[f] = i.type === "checkbox" ? i.checked : (i.type === "number" ? Number(i.value || 0) : i.value);
    });
    return o;
  });
  return {
    name: $("#f-name").value.trim(),
    designation: $("#f-desig").value.trim(),
    part_type: $("#f-type").value,
    material_grade: $("#f-mat").value,
    annual_program: Number($("#f-prog").value),
    roughness_ra_um: Number($("#f-ra").value),
    heating: $("#f-heat").value,
    machined_all_over: $("#f-machined").checked,
    envelope_kind: prims.some(p => p.kind === "box") ? "box" : "cylinder",
    primitives: prims,
  };
}

function loadSample(i) {
  fillForm(STATE.samples[i]);
}

function fillForm(s) {
  if (!s) return;
  $("#f-name").value = s.name || "";
  $("#f-desig").value = s.designation || "";
  $("#f-type").value = s.part_type;
  $("#f-mat").value = s.material_grade;
  $("#f-prog").value = s.annual_program ?? 1000;
  $("#f-ra").value = s.roughness_ra_um ?? 6.3;
  $("#f-heat").value = s.heating || "flame";
  $("#f-machined").checked = s.machined_all_over !== false;
  $("#prims").innerHTML = "";
  (s.primitives || []).forEach(p => addPrim(p.kind, p));
}

function newJob() {
  return "job-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
}

function secText(s) {
  s = Math.max(0, Math.round(s));
  if (s < 60) return s + " с";
  return Math.floor(s / 60) + " мин " + String(s % 60).padStart(2, "0") + " с";
}

const FEED_CLASS = { "идёт": "run", "ок": "ok", "брак": "retry", "ошибка": "bad", "стоп": "stop" };
const FEED_MARK = { "идёт": "…", "ок": "✓", "брак": "↻", "ошибка": "✕", "стоп": "■" };

function requestFeed(reqs) {
  if (!reqs || !reqs.length) return "";
  const longest = Math.max(...reqs.map(r => r.sec), 1);
  const done = reqs.filter(r => r.status !== "идёт");
  const avg = done.length ? done.reduce((sum, r) => sum + r.sec, 0) / done.length : 0;
  const count = s => reqs.filter(r => r.status === s).length;
  return `<div class="pf-head">Запросы к модели: принято ${count("ок")}, брак ${count("брак")}` +
    (count("ошибка") ? `, ошибок ${count("ошибка")}` : "") +
    (done.length ? `, в среднем ${fmt(avg, 1)} с` : "") + `</div>` +
    reqs.map(r => `<div class="pf-row pf-${FEED_CLASS[r.status] || "run"}">
      <span class="pf-mark">${FEED_MARK[r.status] || ""}</span>
      <span class="pf-stage">${esc(r.stage)}${r.attempt > 1 ? ` <i>попытка ${r.attempt}</i>` : ""}</span>
      <span class="pf-bar"><i style="width:${Math.max(2, r.sec / longest * 100).toFixed(1)}%"></i></span>
      <span class="pf-sec">${fmt(r.sec, 1)} с</span></div>`).join("");
}

function watchProgress(job) {
  const started = Date.now();
  let stopped = false;
  const show = html => { const box = $("#pg-text"); if (box) box.innerHTML = html; };
  const feed = html => { const box = $("#pg-feed"); if (box) box.innerHTML = html; };
  const tick = async () => {
    const waited = secText((Date.now() - started) / 1000);
    let step = "Подготовка", stage = "", reqs = null;
    try {
      const r = await fetch("/api/progress", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job }),
      });
      const p = (await r.json()).progress || {};
      if (p.total) step = `Запрос ${p.done} из ${p.total}`;
      stage = p.stage || "";
      reqs = p.requests;
    } catch (e) {
      step = "Движок занят ответом";
    }
    if (stopped) return;
    show(`<b>${esc(step)}</b>${stage ? " — " + esc(stage) : ""}<br>` +
         `<span class="hint">идёт ${esc(waited)}</span>`);
    if (reqs) feed(requestFeed(reqs));
  };
  tick();
  const timer = setInterval(tick, 1000);
  return () => { stopped = true; clearInterval(timer); };
}

async function stopBuild(job) {
  const b = $("#btn-stop");
  if (b) { b.disabled = true; b.textContent = "останавливаю…"; }
  try {
    await fetch("/api/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job }),
    });
  } catch (e) {
    if (b) { b.disabled = false; b.textContent = "Остановить"; }
  }
}

async function send(url, body, btn) {
  const out = $("#out");
  const old = btn.innerHTML;
  const withModel = url.endsWith("route") && $('input[name=mode]:checked').value !== "baseline";
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>считаю';

  const canStop = withModel && body.job;
  out.innerHTML = `<div class="card"><div class="empty"><span class="spinner"></span>
    <span id="pg-text">${withModel
      ? "Запрос к локальной модели. Первый запрос дольше: модель загружается в память."
      : "Расчёт…"}</span>
    ${canStop ? '<div class="stop-row"><button id="btn-stop" type="button">Остановить</button></div>' : ""}
    ${canStop && SETTINGS.show_requests !== false ? '<div class="pg-feed" id="pg-feed"></div>' : ""}
    </div></div>`;
  const stopWatch = canStop ? watchProgress(body.job) : null;
  if (canStop) $("#btn-stop").onclick = () => stopBuild(body.job);
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) { out.innerHTML = ""; out.append(note("bad", esc(data.error || "Ошибка"))); return; }
    render(data);
    recordResult(url, body, data);

    if (data.mode && data.mode !== "baseline" && data.source === "baseline") refreshOllama();
  } catch (e) {
    out.innerHTML = "";
    out.append(note("bad", "Движок не отвечает: " + esc(e.message)));
  } finally {
    if (stopWatch) stopWatch();
    btn.disabled = false;
    btn.innerHTML = old;
  }
}

$("#btn-calc").onclick = e => send("/api/calc", { part: collectPart() }, e.currentTarget);
$("#btn-route").onclick = e => send("/api/route", {
  part: collectPart(),
  mode: $('input[name=mode]:checked').value,
  transitions: $("#f-transitions").checked,
  model: chosenModel(),
  job: newJob(),
}, e.currentTarget);
document.querySelectorAll("[data-add]").forEach(b => b.onclick = () => addPrim(b.dataset.add));

function note(kind, html) { return el("div", "note " + kind, html); }
function card(title, node) {
  const c = el("div", "card");
  c.append(el("h2", null, esc(title)));
  const b = el("div", "body");
  b.append(node);
  c.append(b);
  return c;
}
function q(v, d = 2) {
  if (!v) return "—";
  const mark = v.standard === false ? '<span class="badge nogost">не ГОСТ</span>' : "";
  return `${fmt(v.value, d)} ${esc(unitRu(v.unit))}${mark}`;
}

let SVG_SEQ = 0;

function stackProfile(prims) {
  let x = 0;
  const steps = [], holes = [], bosses = [];
  for (const p of prims || []) {
    const round = p.kind === "cylinder" || p.kind === "tube";
    const len = round ? p.l : Math.max(p.a, p.b, p.c);
    let r;
    if (round) {
      r = p.d / 2;
    } else {
      const s = [p.a, p.b, p.c].sort((u, v) => u - v);
      r = Math.hypot(s[0], s[1]) / 2;
    }
    if (!len || !r) continue;
    if (p.subtract) { holes.push({ x0: 0, x1: len, r, name: p.name }); continue; }
    if (p.along_axis === false) { bosses.push({ len, r, name: p.name }); continue; }
    steps.push({ x0: x, x1: x + len, r, ri: p.kind === "tube" ? p.d_inner / 2 : 0,
                 name: p.name, round });
    x += len;
  }
  const maxR = Math.max(...steps.map(s => s.r), ...bosses.map(b => b.r), 1);
  return { steps, holes, bosses, length: x, maxR };
}

function profilePath(pr, sx, sy, dx, cy) {
  if (!pr.steps.length) return { outline: "", holes: "" };
  const X = v => dx + v * sx;
  const Y = v => cy + v * sy;
  let top = "", bottom = "";
  pr.steps.forEach((s, i) => {
    top += (i === 0 ? `M ${X(s.x0)} ${Y(-s.r)}` : ` L ${X(s.x0)} ${Y(-s.r)}`);
    top += ` L ${X(s.x1)} ${Y(-s.r)}`;
  });
  for (let i = pr.steps.length - 1; i >= 0; i--) {
    const s = pr.steps[i];
    bottom += ` L ${X(s.x1)} ${Y(s.r)} L ${X(s.x0)} ${Y(s.r)}`;
  }
  const outline = top + bottom + " Z";

  let holes = "";
  for (const s of pr.steps) {
    if (s.ri > 0) holes += ` M ${X(s.x0)} ${Y(-s.ri)} L ${X(s.x1)} ${Y(-s.ri)}` +
                           ` L ${X(s.x1)} ${Y(s.ri)} L ${X(s.x0)} ${Y(s.ri)} Z`;
  }
  for (const h of pr.holes) {
    holes += ` M ${X(h.x0)} ${Y(-h.r)} L ${X(h.x1)} ${Y(-h.r)}` +
             ` L ${X(h.x1)} ${Y(h.r)} L ${X(h.x0)} ${Y(h.r)} Z`;
  }
  return { outline, holes };
}

function dimH(x1, x2, y, text) {

  return `<line class="dim" x1="${x1}" y1="${y}" x2="${x2}" y2="${y}"
            marker-start="url(#ar)" marker-end="url(#ar)"/>
          <line class="ext" x1="${x1}" y1="${y - 6}" x2="${x1}" y2="${y + 6}"/>
          <line class="ext" x1="${x2}" y1="${y - 6}" x2="${x2}" y2="${y + 6}"/>
          <text class="dimtext" x="${(x1 + x2) / 2}" y="${y - 5}" text-anchor="middle">${text}</text>`;
}

function dimV(y1, y2, x, text) {
  const my = (y1 + y2) / 2, tx = x - 5;
  return `<line class="dim" x1="${x}" y1="${y1}" x2="${x}" y2="${y2}"
            marker-start="url(#ar)" marker-end="url(#ar)"/>
          <text class="dimtext" x="${tx}" y="${my}" text-anchor="middle"
            transform="rotate(-90 ${tx} ${my})">${text}</text>`;
}

function sketchSVG(calc) {
  const partPr = stackProfile(calc.part?.primitives);
  const forgePr = stackProfile(calc.forging?.primitives);
  if (!partPr.steps.length) return "";

  const id = "h" + (++SVG_SEQ);

  const dimSteps = [];
  const seenR = new Set();
  partPr.steps.forEach((st, i) => {
    if (seenR.has(st.r)) return;
    seenR.add(st.r);
    dimSteps.push({ st, forge: forgePr.steps[i] });
  });

  const L = Math.max(partPr.length, forgePr.length);
  const R = Math.max(partPr.maxR, forgePr.maxR);
  const s = Math.min(430 / L, 230 / (2 * R));

  const padL = 28 + dimSteps.length * 24, padR = 58, padT = 24, padB = 52;
  const W = Math.round(padL + L * s + padR);
  const H = Math.round(2 * R * s + padT + padB);
  const cy = padT + R * s;
  const dxPart = padL + (L - partPr.length) * s / 2;
  const dxForge = padL + (L - forgePr.length) * s / 2;

  const part = profilePath(partPr, s, s, dxPart, cy);
  const forge = profilePath(forgePr, s, s, dxForge, cy);

  const o = [];
  o.push(`<svg class="sketch" viewBox="0 0 ${W} ${H}" style="max-width:${W}px" role="img"
    aria-label="Эскиз детали и поковки в осевом разрезе">`);
  o.push(`<defs>
    <pattern id="${id}" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="7" stroke="currentColor" stroke-width="0.6" opacity="0.4"/>
    </pattern>
    <marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
            orient="auto-start-reverse">
      <path d="M 0 1 L 10 5 L 0 9 z" fill="currentColor"/>
    </marker>
  </defs>`);

  o.push(`<path d="${part.outline}${part.holes}" fill="url(#${id})" fill-rule="evenodd" stroke="none"/>`);

  o.push(`<line class="axis" x1="${padL - 26}" y1="${cy}" x2="${W - padR + 26}" y2="${cy}"/>`);

  o.push(`<path class="forge" d="${forge.outline}" fill="none"/>`);
  if (forge.holes) o.push(`<path class="forge" d="${forge.holes}" fill="none"/>`);

  o.push(`<path class="part" d="${part.outline}" fill="none"/>`);
  if (part.holes) o.push(`<path class="part" d="${part.holes}" fill="none"/>`);

  dimSteps.forEach(({ st, forge: fs }, slot) => {
    const x = padL - 14 - slot * 24;
    const y1 = cy - st.r * s, y2 = cy + st.r * s;
    const label = (st.round ? "⌀" : "") + fmt(st.r * 2, 1) +
                  (fs ? ` (${fmt(fs.r * 2, 1)})` : "");
    o.push(`<line class="ext" x1="${x - 3}" y1="${y1}" x2="${dxPart + st.x0 * s}" y2="${y1}"/>`);
    o.push(`<line class="ext" x1="${x - 3}" y1="${y2}" x2="${dxPart + st.x0 * s}" y2="${y2}"/>`);
    o.push(dimV(y1, y2, x, label));
  });

  const bore = partPr.steps.find(st => st.ri > 0) || partPr.holes[0];
  if (bore) {
    const r = bore.ri || bore.r;
    const fb = forgePr.steps.find(st => st.ri > 0) || forgePr.holes[0];
    const fr = fb ? (fb.ri || fb.r) : 0;
    const x = padL + L * s + 30;
    const y1 = cy - r * s, y2 = cy + r * s;
    o.push(`<line class="ext" x1="${dxPart + partPr.length * s}" y1="${y1}" x2="${x + 3}" y2="${y1}"/>`);
    o.push(`<line class="ext" x1="${dxPart + partPr.length * s}" y1="${y2}" x2="${x + 3}" y2="${y2}"/>`);
    o.push(dimV(y1, y2, x, `⌀${fmt(r * 2, 1)}${fr ? ` (${fmt(fr * 2, 1)})` : ""}`));
  }

  o.push(dimH(dxPart, dxPart + partPr.length * s, cy + R * s + 32,
    `${fmt(partPr.length, 1)} (${fmt(forgePr.length, 1)})`));
  o.push("</svg>");

  if (partPr.bosses.length)
    o.push(`<div class="hint">Вне разреза остались элементы поперёк оси: ${partPr.bosses.map(b => esc(b.name || "элемент")).join(", ")}.</div>`);
  return o.join("\n");
}

const HOT_OPS = {
  "015": ["Осадка", ""], "020": ["Протяжка", ""], "025": ["Штамповка", "предв."],
  "030": ["Штамповка", "оконч."], "035": ["Прошивка", ""], "040": ["Гибка", ""],
  "045": ["Обрезка", ""], "050": ["Правка", ""],
};

function hotSteps(ops) {
  const steps = (ops || [])
    .map(op => [op, HOT_OPS[String(op.code || "").slice(0, 3)]])
    .filter(([, n]) => n)
    .map(([op, [name, sub]]) => ({ name, sub: [sub, op.number].filter(Boolean).join(" · ") }));
  return steps.length ? steps : [{ name: "Ковка", sub: "" }];
}

function tempSVG(calc, ops) {
  const h = calc.heating;
  if (!h?.temp_start || !h?.temp_end) return "";
  const tS = h.temp_start.value, tE = h.temp_end.value;
  const steps = hotSteps(ops);

  const W = 720, H = 236, padL = 150, padR = 20, yTop = 30, yBot = 170;

  const yS = 62, yE = 128, yRoom = yBot - 4;
  const Y = t => yS + (tS - t) / (tS - tE) * (yE - yS);

  const cols = 2 + steps.length, cw = (W - padL - padR) / cols;
  const xc = i => padL + i * cw;

  const o = [`<svg class="tempband" viewBox="0 0 ${W} ${H}" role="img"
    aria-label="Схема изменения температуры по операциям">`];
  const pid = "tb" + (++SVG_SEQ);
  o.push(`<defs><pattern id="${pid}" width="7" height="7" patternUnits="userSpaceOnUse"
      patternTransform="rotate(45)">
      <line x1="0" y1="0" x2="0" y2="7" stroke="currentColor" stroke-width="0.6" opacity="0.3"/>
    </pattern>
    <marker id="${pid}a" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7"
      orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" fill="currentColor"/></marker></defs>`);

  o.push(`<rect x="${padL}" y="${yS}" width="${W - padL - padR}" height="${yE - yS}"
    fill="url(#${pid})" stroke="none"/>`);
  for (const y of [yS, yE])
    o.push(`<line class="ext" x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke-dasharray="5 3"/>`);
  o.push(`<text class="dimtext" x="${padL - 8}" y="${yS + 4}" text-anchor="end">начало ковки ${fmt(tS, 0)} °C</text>`);
  o.push(`<text class="dimtext" x="${padL - 8}" y="${yE + 4}" text-anchor="end">конец ковки ${fmt(tE, 0)} °C</text>`);
  o.push(`<text class="cap" x="${padL - 8}" y="${yRoom + 3}" text-anchor="end">температура цеха</text>`);

  o.push(`<line class="ext" x1="${padL}" y1="${yBot}" x2="${padL}" y2="${yTop - 14}" marker-end="url(#${pid}a)"/>`);
  o.push(`<line class="ext" x1="${padL}" y1="${yBot}" x2="${W - padR}" y2="${yBot}" marker-end="url(#${pid}a)"/>`);
  o.push(`<text class="cap" x="${padL + 8}" y="${yTop - 8}">t, °C</text>`);
  o.push(`<text class="cap" x="${W - padR}" y="${yBot + 29}" text-anchor="end">τ</text>`);

  const pts = [[xc(0) + 4, yRoom], [xc(0) + cw * 0.55, yS], [xc(1) - cw * 0.1, yS]];

  const n = steps.length, span = (tS - tE) * 0.85, d = span / n;
  steps.forEach((s, i) => {
    const a = tS - d * i, x0 = xc(1 + i);
    pts.push([x0 + cw * 0.5, Y(a - d * 0.25)]);
    pts.push([x0 + cw * 0.8, Y(a - d)]);
  });

  const xl = xc(cols - 1);
  pts.push([xl + cw * 0.4, yRoom - 34], [W - padR - 8, yRoom]);
  o.push(`<polyline class="band" fill="none"
    points="${pts.map(p => p.map(v => v.toFixed(1)).join(",")).join(" ")}"/>`);

  const label = (i, top, bottom) => {
    const x = xc(i) + cw / 2;
    if (i) o.push(`<line class="ext" x1="${xc(i)}" y1="${yBot}" x2="${xc(i)}" y2="${yBot + 4}"/>`);
    o.push(`<text class="dimtext" x="${x}" y="${yBot + 16}" text-anchor="middle">${esc(top)}</text>`);
    if (bottom) o.push(`<text class="cap" x="${x}" y="${yBot + 29}" text-anchor="middle">${esc(bottom)}</text>`);
  };
  label(0, "Нагрев", "печь");
  steps.forEach((s, i) => label(1 + i, s.name, s.sub));
  label(cols - 1, "Охлаждение", "");
  o.push(`<text class="cap" x="4" y="${H - 4}">Схема: из расчёта взяты только температуры
    начала и конца ковки; форма линии условная, время не в масштабе.</text>`);
  o.push("</svg>");
  return o.join("\n");
}

async function fetchCards(data) {
  return (await api("/api/cards", { data })).sheets;
}

function mkPage(rows) {
  const mm = x => Math.round(x * 1000) / 1000;
  const edges = new Set([0]);
  rows.forEach(r => r.cells.reduce((x, [w]) => { const e = mm(x + w); edges.add(e); return e; }, 0));
  const xs = [...edges].sort((a, b) => a - b);
  const col = x => xs.indexOf(mm(x)) + 1;
  let cells = "";
  rows.forEach((r, i) => {
    let x = 0;
    for (const [w, text, kind, span] of r.cells) {
      const from = col(x);
      x = mm(x + w);
      if (kind !== "under") cells += `<div class="mk-cell${kind ? " mk-" + kind : ""}" style="grid-column:${from}/${col(x)};grid-row:${i + 1}/span ${span || 1}">${esc(text ?? "")}</div>`;
    }
  });
  const columns = xs.slice(1).map((e, i) => `${mm(e - xs[i])}mm`).join(" ");
  return `<div class="mk-sheet"><div class="mk-frame" style="grid-template-columns:${columns};grid-template-rows:${rows.map(r => r.height_mm + "mm").join(" ")}">${cells}</div></div>`;
}

function sheetHtml(sheet) {
  return sheet.pages.map(mkPage).join("");
}

let LAST = null;

const PRINT_PARTS = [
  ["calc", "расчёт"],
  ["sketch", "эскиз"],
  ["route", "маршрут"],
  ["transitions", "переходы"],
  ["sources", "источники"],
  ["card", "карта ГОСТ 3.1118"],
  ["forging_card", "карта ГОСТ 3.1403"],
];

function printAvailable(data, key) {
  if (key === "sketch") return !!(data.calc.forging?.primitives || []).length;
  if (key === "route") return !!data.route;
  if (key === "transitions") return !!(data.transitions || []).length;
  if (key === "sources") return !!(data.calc.non_standard_values || []).length;
  if (key === "card" || key === "forging_card") return !!data.route;
  return true;
}

function printBar(data) {
  const bar = el("div", "printbar");
  bar.append(el("b", null, "Печать"));
  const boxes = el("div", "pb-boxes");
  for (const [key, label] of PRINT_PARTS) {
    const has = printAvailable(data, key);
    const lab = el("label", has ? null : "off");

    const on = has && key !== "card" && key !== "forging_card";
    lab.innerHTML = `<input type="checkbox" data-pp="${key}"${on ? " checked" : ""}${has ? "" : " disabled"}> ${label}`;
    boxes.append(lab);
  }
  bar.append(boxes);
  const btn = el("button", "primary", "Печать / PDF");
  btn.onclick = doPrint;
  bar.append(btn);
  if (printAvailable(data, "card")) {
    const xl = el("button", null, "Карты в Excel");
    xl.title = "Маршрутная карта ГОСТ 3.1118 и карта техпроцесса ГОСТ 3.1403 в файл .xlsx, папка exports";
    xl.onclick = () => exportCards(xl);
    bar.append(xl);
  }
  return bar;
}

async function exportCards(btn) {
  btn.disabled = true;
  try {
    const r = await api("/api/export", { data: LAST });
    addEvent("calc", `Карты выгружены: ${r.path}`);
    location.href = r.url;
  } catch (e) {
    addEvent("err", "Карты не выгружены: " + e.message);
  } finally {
    btn.disabled = false;
  }
}

function stamp() {
  const d = new Date(), two = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}`
       + ` ${two(d.getHours())}-${two(d.getMinutes())}`;
}

async function doPrint() {
  if (!LAST) return;
  const opts = {};
  document.querySelectorAll("#out [data-pp]").forEach(i => {
    opts[i.dataset.pp] = i.checked && !i.disabled;
  });

  const sheets = (opts.card || opts.forging_card) ? await fetchCards(LAST) : null;
  $("#print-doc").innerHTML = buildPrintDoc(LAST, opts)
    + (sheets && opts.card ? sheetHtml(sheets[0]) : "")
    + (sheets && opts.forging_card ? sheetHtml(sheets[1]) : "");

  const prev = document.title;
  const name = String(LAST.calc?.part?.name || "деталь").replace(/[\\/:*?"<>|]/g, " ").trim();
  document.title = `FORGE — ${name} — ${stamp()}`;
  window.addEventListener("afterprint", () => { document.title = prev; }, { once: true });
  window.print();
}

function steelName(grade) {
  const g = String(grade || "").trim();
  if (!g) return "";
  return /^ст/i.test(g) ? g : "Сталь " + g;
}

function materialLine(calc) {
  const grade = steelName(calc.material?.grade || calc.part?.material_grade);
  return [grade, calc.material?.gost || ""].filter(Boolean).join(" ") || "—";
}

function pq(v, d = 2) {
  if (!v) return "—";
  return `${fmt(v.value, d)} ${esc(unitRu(v.unit))}${v.standard === false ? " (не ГОСТ)" : ""}`;
}

function buildPrintDoc(data, opts) {
  const c = data.calc, f = c.forging, b = c.blank, h = c.heating, fr = c.force;
  const p = c.part || {};
  const o = [];

  o.push(`<div class="pd-title">
    <b>Расчёт и маршрут технологического процесса</b>
    <span>ковка и горячая объёмная штамповка · рабочий документ</span>
  </div>`);

  o.push(`<table><tbody>
    <tr><td class="k">Наименование детали</td><td>${esc(p.name || "—")}</td></tr>
    <tr><td class="k">Обозначение</td><td>${esc(p.designation || "—")}</td></tr>
    <tr><td class="k">Материал</td><td>${esc(materialLine(c))}</td></tr>
    <tr><td class="k">Программа выпуска</td><td>${esc(p.annual_program ?? "—")} шт./год</td></tr>
    <tr><td class="k">Вид технологического процесса</td><td>${esc(processRu(c.process))}</td></tr>
    <tr><td class="k">Дата формирования</td><td>${new Date().toLocaleDateString("ru-RU")}</td></tr>
    <tr><td class="k">Версия системы</td><td>${esc(STATE?.version || "—")}</td></tr>
  </tbody></table>`);

  o.push(`<div class="pd-note">Лист сформирован расчётным модулем системы.
    Формы маршрутной карты по ГОСТ 3.1118-82 и карты технологического процесса
    ковки и штамповки по ГОСТ 3.1403-85 не применялись — настоящий документ
    является рабочим и технологическим документом по ЕСТД не является.</div>`);

  if (opts.calc) {
    o.push("<h2>Поковка</h2>");
    o.push(`<table><tbody>
      <tr><td class="k">Масса детали</td><td class="num">${pq(f.mass_part)}</td></tr>
      <tr><td class="k">Масса поковки</td><td class="num">${pq(f.mass_actual)}</td></tr>
      <tr><td class="k">Припуски и допуски</td><td>${esc(f.norm || "ГОСТ 7505-89")}</td></tr>
      ${f.forging_kind ? `<tr><td class="k">Тип поковки</td><td>${esc(f.forging_kind)}</td></tr>` : `
      <tr><td class="k">Группа стали</td><td>${esc(gostCode(f.steel_group))}</td></tr>
      <tr><td class="k">Степень сложности</td><td>${esc(gostCode(f.complexity))}</td></tr>
      <tr><td class="k">Класс точности</td><td>${esc(gostCode(f.accuracy_class))}</td></tr>
      <tr><td class="k">Исходный индекс</td><td class="num">${esc(f.initial_index)}</td></tr>`}
      ${fr.projection_area ? `<tr><td class="k">Площадь проекции</td><td class="num">${pq(fr.projection_area, 0)}</td></tr>` : ""}
    </tbody></table>`);
  o.push(f.forging_kind
    ? `<div class="pd-note">Свободная ковка на молотах: тип поковки — по таблице 1 ГОСТ 7829-70,
       припуск и отклонение на каждый размер — по таблице этого типа. Масса поковки —
       по её номинальным размерам (п. 12).</div>`
    : `<div class="pd-note">Масса поковки — по её номинальным размерам (ГОСТ 7505-89, п. 2.6).
    Исходный индекс определён по ней же${f.index_passes > 1
      ? "; первый проход шёл по ориентировочной массе Мд·Кр, индекс уточнён вторым проходом"
      : ""}.</div>`);
    if (c.process_reason) o.push(`<div class="pd-note">${esc(c.process_reason)}</div>`);

    if (f.dimensions?.length) {
      o.push("<h2>Размеры поковки, мм</h2>");
      const rows = f.dimensions.map(d => `<tr>
        <td>${esc(d.label || d.element + " " + d.parameter)}</td>
        <td class="num">${fmt(d.part_mm, 1)}</td>
        <td class="num">${fmt(d.allowance_mm, 1)}</td>
        <td class="num">${fmt(d.forging_mm, 1)}</td>
        <td class="num">${fmt(d.tolerance.value, 1)}</td>
        <td class="num">+${fmt(d.upper.value, 1)} / ${fmt(d.lower.value, 1)}</td></tr>`).join("");
      o.push(`<table><thead><tr><th>Размер</th><th>Деталь</th>
        <th>Припуск</th><th>Поковка</th><th>Допуск</th><th>Предельные</th></tr></thead>
        <tbody>${rows}</tbody></table>`);
    }

    o.push("<h2>Заготовка и нагрев</h2>");
    o.push(`<table><tbody>
      <tr><td class="k">Заготовка</td><td>⌀${fmt(b.diameter?.value, 0)} × ${fmt(b.length?.value, 0)} мм</td></tr>
      <tr><td class="k">Масса заготовки</td><td class="num">${pq(b.mass_blank)}</td></tr>
      ${b.consumption_ratio ? `<tr><td class="k">Коэффициент использования металла (Мзаг / Мдет)</td><td class="num">${pq(b.consumption_ratio)}</td></tr>` : ""}
      <tr><td class="k">Уковка</td><td class="num">${pq(b.forge_ratio)}</td></tr>
      ${b.upset_ratio ? `<tr><td class="k">L/D при осадке</td><td class="num">${fmt(b.upset_ratio.value)}</td></tr>` : ""}
      <tr><td class="k">Облой</td><td class="num">${pq(b.mass_flash)}</td></tr>
      ${b.mass_web?.value > 0 ? `<tr><td class="k">Выдра (перемычка)</td><td class="num">${pq(b.mass_web)}</td></tr>` : ""}
      <tr><td class="k">Угар</td><td class="num">${pq(b.mass_scale)}</td></tr>
      <tr><td class="k">Потери при резке</td><td class="num">${pq(b.mass_cut_loss)}</td></tr>
      ${h.hold_time ? `<tr><td class="k">Выдержка при нагреве под ковку</td><td class="num">${pq(h.hold_time, 0)}</td></tr>` : ""}
      ${h.treat_hold_time ? `<tr><td class="k">Выдержка при термообработке</td><td class="num">${pq(h.treat_hold_time, 0)}</td></tr>` : ""}
      ${h.temp_start ? `<tr><td class="k">Температура нагрева под ковку</td><td class="num">${pq(h.temp_start, 0)}</td></tr>` : ""}
      <tr><td class="k">Интервал ковки</td><td>${fmt(h.temp_start?.value, 0)} — ${pq(h.temp_end, 0)}</td></tr>
      <tr><td class="k">Нагревательное устройство</td><td>${esc(h.heater_name)}</td></tr>
    </tbody></table>`);

    const eq = [];
    if (fr.force_required) eq.push(`<tr><td class="k">Требуемое усилие</td><td class="num">${pq(fr.force_required, 1)}</td></tr>`);
    if (fr.falling_mass_required) eq.push(`<tr><td class="k">Требуемая масса падающих частей</td><td class="num">${pq(fr.falling_mass_required, 0)}</td></tr>`);
    if (fr.main_equipment) eq.push(`<tr><td class="k">Основное оборудование</td><td>${esc(fr.main_equipment.name_ru)} — ${fmt(fr.main_equipment.value)} ${esc(fr.main_equipment.unit)}</td></tr>`);
    if (fr.trim_equipment) eq.push(`<tr><td class="k">Обрезное оборудование</td><td>${esc(fr.trim_equipment.name_ru)} — ${fmt(fr.trim_equipment.value)} ${esc(fr.trim_equipment.unit)}</td></tr>`);
    if (eq.length) {
      o.push("<h2>Оборудование</h2>");
      o.push(`<table><tbody>${eq.join("")}</tbody></table>`);
      const why = [fr.main_equipment?.reason, fr.trim_equipment?.reason].filter(Boolean);
      if (why.length) o.push(`<div class="pd-note">${why.map(esc).join("<br>")}</div>`);
    }

  }

  if (opts.sketch) {
    const sk = sketchSVG(c);
    if (sk) {
      o.push("<h2>Эскиз детали и поковки</h2>");
      o.push(`<div class="pd-fig">${sk}</div>`);
    }
    const tb = tempSVG(c, data.route?.operations);
    if (tb) {
      o.push("<h2>Температура по операциям (схема)</h2>");
      o.push(`<div class="pd-fig">${tb}</div>`);
    }
  }

  if (opts.calc) {
    if (c.warnings?.length) {
      o.push("<h2>Предупреждения</h2>");
      o.push(`<div class="pd-note">${c.warnings.map(w => "— " + esc(w)).join("<br>")}</div>`);
    }
  }

  if (opts.route && data.route) {
    const label = { llm: "нейросеть", stepwise: "нейросеть, по частям", baseline: "правила" }[data.source] || data.source;
    o.push("<h2>Технологический маршрут</h2>");
    o.push(`<div class="pd-note">Маршрут построен: ${esc(label)}${
      data.model ? ", модель " + esc(data.model) : ""}.
      Запросов к модели: ${esc(data.attempts ?? 0)}.
      ${data.stopped ? "Построение остановлено вручную: часть решений и переходов — по правилам." : ""}
      ${data.fallback_reason ? "Откат на правила: " + esc(data.fallback_reason) : ""}</div>`);

    const byCode = {};
    if (opts.transitions) (data.transitions || []).forEach(r => byCode[r.code] = r);

    const rows = data.route.operations.map(op => {
      let html = `<tr><td class="num">${esc(op.number)}</td>
        <td>${esc(op.name)}<br>${esc(op.code)}</td>
        <td>${esc(equipRu(op.equipment))}</td>
        <td>${esc(op.rationale)}</td></tr>`;
      const blk = byCode[op.code];
      if (blk && blk.transitions.length) {
        const list = blk.transitions
          .map(x => `<span class="n">${esc(x.number)}.</span> ${esc(x.text)}`).join("<br>");
        html += `<tr class="pd-sub"><td></td><td colspan="3">${list}</td></tr>`;
      }
      return html;
    }).join("");

    o.push(`<table><thead><tr><th>№</th><th>Операция</th><th>Оборудование</th>
      <th>Обоснование</th></tr></thead><tbody>${rows}</tbody></table>`);

    if (data.route.summary) o.push(`<div class="pd-note">${esc(data.route.summary)}</div>`);
  }

  if (opts.sources && (c.non_standard || []).length) {
    o.push("<h2>Значения не из ГОСТ — проверить перед подписью</h2>");
    o.push(`<div class="pd-note">${SRC_INTRO} Строки с пометкой «сверить» — в первую очередь.</div>`);
    o.push(sourcesTable(c.non_standard, true));
  } else if (opts.sources && c.non_standard_values?.length) {

    o.push("<h2>Значения без нормативного источника</h2>");
    o.push(`<div class="pd-note">Числа ниже взяты из справочной литературы и в ГОСТ 7505-89
      не нормируются. Перед применением подлежат проверке по первоисточнику.</div>`);
    o.push(`<div class="pd-note">${c.non_standard_values.map(v => "— " + esc(v)).join("<br>")}</div>`);
  }

  o.push(`<div class="pd-sign"><table><tbody><tr>
    <td class="lbl">Разработал</td><td></td>
    <td class="lbl">Проверил</td><td></td>
  </tr></tbody></table></div>`);

  return o.join("\n");
}

function renderResult(data) {
  const out = $("#out");
  out.innerHTML = "";
  LAST = data;
  const c = data.calc, f = c.forging, b = c.blank, h = c.heating, fr = c.force;

  const tiles = el("div", "tiles");
  const tile = (k, v, u) => {
    const t = el("div", "tile");
    t.innerHTML = `<div class="k">${esc(k)}</div><div class="v">${v}<span class="u"> ${esc(u || "")}</span></div>`;
    return t;
  };
  tiles.append(
    tile("Масса детали", fmt(f.mass_part?.value), "кг"),
    tile("Масса поковки", fmt(f.mass_actual?.value), "кг"),
    tile("Масса заготовки", fmt(b.mass_blank?.value), "кг"),
    tile("Заготовка", `⌀${fmt(b.diameter?.value, 0)}×${fmt(b.length?.value, 0)}`, "мм"),
    tile("КИМ", fmt(b.consumption_ratio?.value, 2), ""),
    tile("Уковка", fmt(b.forge_ratio?.value), ""),
  );
  out.append(tiles);
  out.append(printBar(data));

  if (data.route) out.append(routeCard(data));

  const forge = el("div");
  forge.append(el("dl", "kv", `
    <dt>Техпроцесс</dt><dd>${esc(processRu(c.process))}</dd>
    <dt>Припуски и допуски</dt><dd>${esc(f.norm || "ГОСТ 7505-89")}</dd>
    ${f.forging_kind ? `<dt>Тип поковки</dt><dd>${esc(f.forging_kind)}</dd>` : `
    <dt>Группа стали</dt><dd>${esc(gostCode(f.steel_group))}</dd>
    <dt>Степень сложности</dt><dd>${esc(gostCode(f.complexity))}</dd>
    <dt>Класс точности</dt><dd>${esc(gostCode(f.accuracy_class))}</dd>
    <dt>Исходный индекс</dt><dd>${f.initial_index}</dd>`}
    ${fr.projection_area ? `<dt>Площадь проекции</dt><dd>${q(fr.projection_area, 0)}</dd>` : ""}`));
  forge.append(el("div", "hint", esc(c.process_reason)));
  out.append(card("Поковка", forge));

  if (f.dimensions?.length) {
    const t = el("table");
    t.innerHTML = "<thead><tr><th>Размер</th><th>Деталь</th>" +
      "<th>Припуск</th><th>Поковка</th><th>Допуск</th></tr></thead>";
    const tb = el("tbody");
    for (const d of f.dimensions) {
      tb.append(el("tr", null,
        `<td>${esc(d.label || d.element + " " + d.parameter)}</td>` +
        `<td class="num">${fmt(d.part_mm, 1)}</td>` +
        `<td class="num">${fmt(d.allowance_mm, 1)}${d.tolerance.standard === false ? '<span class="badge nogost">!</span>' : ""}</td>` +
        `<td class="num"><b>${fmt(d.forging_mm, 1)}</b></td>` +
        `<td class="num">${fmt(d.tolerance.value, 1)} <span style="color:var(--ink-3)">+${fmt(d.upper.value, 1)}/${fmt(d.lower.value, 1)}</span></td>`));
    }
    t.append(tb);
    const wrap = el("div", "scroll"); wrap.append(t);
    out.append(card("Размеры поковки, мм", wrap));
  }

  const sketch = sketchSVG(c);
  if (sketch) out.append(card("Эскиз детали и поковки", el("div", null, sketch)));

  const two = el("div");
  two.append(el("dl", "kv", `
    <dt>Заготовка</dt><dd>⌀${fmt(b.diameter?.value, 0)} × ${fmt(b.length?.value, 0)} мм</dd>
    <dt>Коэф. использования металла</dt><dd>${q(b.consumption_ratio)}</dd>
    ${b.upset_ratio ? `<dt>L/D при осадке</dt><dd>${fmt(b.upset_ratio.value)}</dd>` : ""}
    <dt>Облой</dt><dd>${q(b.mass_flash)}</dd>
    ${b.mass_web?.value > 0 ? `<dt>Выдра (перемычка)</dt><dd>${q(b.mass_web)}</dd>` : ""}
    <dt>Угар</dt><dd>${q(b.mass_scale)}</dd>
    <dt>Потери при резке</dt><dd>${q(b.mass_cut_loss)}</dd>
    ${h.hold_time ? `<dt>Выдержка под ковку</dt><dd>${q(h.hold_time, 0)}</dd>` : ""}
    ${h.treat_hold_time ? `<dt>Выдержка при ТО</dt><dd>${q(h.treat_hold_time, 0)}</dd>` : ""}
    ${h.temp_start ? `<dt>Нагрев под ковку</dt><dd>${q(h.temp_start, 0)}</dd>` : ""}
    <dt>Интервал ковки</dt><dd>${fmt(h.temp_start?.value, 0)} — ${q(h.temp_end, 0)}</dd>
    <dt>Нагреватель</dt><dd style="font-family:var(--sans)">${esc(h.heater_name)}</dd>`));
  const band = tempSVG(c, data.route?.operations);
  if (band) two.append(el("div", null, band));
  out.append(card("Заготовка и нагрев", two));

  const eq = el("div");
  const rows = [];
  if (fr.force_required) rows.push(`<dt>Требуемое усилие</dt><dd>${q(fr.force_required, 1)}</dd>`);
  if (fr.falling_mass_required) rows.push(`<dt>Требуемая масса п.ч.</dt><dd>${q(fr.falling_mass_required, 0)}</dd>`);
  if (fr.main_equipment) rows.push(`<dt>Основное</dt><dd style="font-family:var(--sans)">${esc(fr.main_equipment.name_ru)} — ${fmt(fr.main_equipment.value)} ${esc(fr.main_equipment.unit)}</dd>`);
  if (fr.trim_equipment) rows.push(`<dt>Обрезное</dt><dd style="font-family:var(--sans)">${esc(fr.trim_equipment.name_ru)} — ${fmt(fr.trim_equipment.value)} ${esc(fr.trim_equipment.unit)}</dd>`);
  eq.append(el("dl", "kv", rows.join("")));
  for (const why of [fr.main_equipment?.reason, fr.trim_equipment?.reason]) {
    if (why) eq.append(el("div", "hint", esc(why)));
  }
  out.append(card("Оборудование", eq));

  if (c.warnings?.length) {
    out.append(card("Предупреждения",
      note("warn", "<ul>" + c.warnings.map(w => `<li>${esc(w)}</li>`).join("") + "</ul>")));
  }
  if ((c.non_standard || []).length || (c.normative || []).length) {
    out.append(card("Источники", sourcesView(c)));
  } else if (c.non_standard_values?.length) {

    const d = el("details");
    d.append(el("summary", null,
      `Значений без нормативного источника: <b>${c.non_standard_values.length}</b>. Показать`));
    d.append(el("div", "mono-list", c.non_standard_values.map(esc).join("<br>")));
    out.append(card("Источники", d));
  }
}

const SRC_INTRO = "ГОСТ 7505-89 задаёт припуски и допуски поковки, но не задаёт потери металла, " +
  "температуры, выдержки и усилия. Эти числа программа взяла из справочников, заводских норм " +
  "или посчитала сама. Перед подписью карты их нужно сверить с первоисточником.";
const SRC_KINDS = {
  approx: ["сверить", "nogost", "приближённая формула, значение оценочное"],
  expert: ["указание технолога", "stepwise", "норма со слов практикующего технолога"],
  factory: ["заводская норма", "stepwise", "так считают на заводе, стандарт величину не задаёт"],
  book: ["справочник", "baseline", "из справочной литературы"],
  calc: ["расчёт", "baseline", "посчитано программой из величин выше"],
};

function sourcesTable(rows, forPrint) {
  const books = [];
  const refOf = t => { let i = books.indexOf(t); if (i < 0) { books.push(t); i = books.length - 1; } return i + 1; };
  let group = "";
  const body = rows.map(r => {
    const head = r.group !== group ? `<tr class="grp"><td colspan="3">${esc(r.group)}</td></tr>` : "";
    group = r.group;
    const kind = SRC_KINDS[r.kind] || [r.kind_name || "", "baseline", ""];
    const mark = forPrint
      ? `<span class="kd${r.kind === "approx" ? " chk" : ""}">${esc(kind[0])}</span>`
      : `<span class="badge ${kind[1]}">${esc(kind[0])}</span>`;
    const refs = (r.books || []).map(refOf);
    const refText = refs.length ? ` <span class="ref"${forPrint ? "" : ` title="${esc((r.books || []).join("\n"))}"`}>[${refs.join(", ")}]</span>` : "";
    const note = r.source_text ? (forPrint ? ": " : " ") + esc(r.source_text) : "";
    return head + `<tr><td>${esc(r.name)}${r.how ? `<span class="how">${esc(r.how)}</span>` : ""}</td>` +
      `<td class="num">${esc(r.value_text)}</td><td>${mark}${note}${refText}</td></tr>`;
  }).join("");
  const lit = books.length
    ? `<div class="srclit"><b>Литература</b><ol>${books.map(b => `<li>${esc(b)}</li>`).join("")}</ol></div>` : "";
  return `<table class="pd-src srctab"><thead><tr><th>Величина</th><th>Значение</th>` +
    `<th>Откуда взято</th></tr></thead><tbody>${body}</tbody></table>${lit}`;
}

function sourcesView(c) {
  const box = el("div", "srcs");
  if ((c.normative || []).length) {
    box.append(el("h3", null, "Из стандартов"));
    box.append(el("div", "scroll", `<table class="srctab"><thead><tr><th>Стандарт</th><th>Что из него взято</th></tr></thead><tbody>` +
      c.normative.map(n => `<tr><td><b>${esc(n.standard)}</b><span class="how">${esc(n.title)}</span></td>` +
        `<td>${esc(n.what)}</td></tr>`).join("") + "</tbody></table>"));
  }
  const rows = c.non_standard || [];
  if (rows.length) {
    const check = rows.filter(r => r.kind === "approx").length;
    box.append(el("h3", null, `Не из ГОСТ — сверить перед подписью: ${rows.length}` +
      (check ? ` <span class="badge nogost">из них оценочных: ${check}</span>` : "")));
    box.append(el("div", "scroll", sourcesTable(rows, false)));
  } else {
    box.append(el("div", "hint", "Все числа расчёта взяты из стандартов."));
  }
  return box;
}

function routeCard(data) {
  const wrap = el("div");
  const src = data.source;
  const label = { llm: "нейросеть", stepwise: "нейросеть, по частям", baseline: "правила" }[src] || src;

  const asked = src === "llm" || src === "stepwise";

  const spent = data.elapsed_sec ? ` · построено за ${esc(secText(data.elapsed_sec))}` : "";
  const head = el("div", null,
    `<span class="badge ${esc(src)}">${esc(label)}</span>` +
    (asked ? `<span class="hint" style="margin-left:8px">${esc(data.model || "")} · ` +
             `запросов: ${data.attempts ?? 0}${spent}</span>`
           : (spent ? `<span class="hint" style="margin-left:8px">${spent.slice(3)}</span>` : "")));
  wrap.append(head);

  if (data.stopped) wrap.append(note("warn",
    "Построение остановлено кнопкой. Что модель успела, осталось в маршруте, " +
    "остальные решения и переходы поставлены по правилам: смотрите метки «модель» и «правила»."));
  if (data.fallback_reason) wrap.append(note("warn", esc(data.fallback_reason)));
  if (data.errors?.length) {
    const d = el("details");
    d.append(el("summary", null, `Модель переспрашивали ${data.errors.length} раз. Показать ошибки`));
    d.append(el("div", "mono-list",
      data.errors.map((b, i) => `попытка ${i + 1}:<br>` + b.map(e => "&nbsp;&nbsp;" + esc(e)).join("<br>")).join("<br>")));
    wrap.append(d);
  }

  const byCode = {};
  (data.transitions || []).forEach(r => byCode[r.code] = r);

  const t = el("table");
  t.style.marginTop = "10px";
  t.innerHTML = "<thead><tr><th>№</th><th>Операция</th><th>Оборудование</th><th>Обоснование</th></tr></thead>";
  const tb = el("tbody");
  for (const op of data.route.operations) {
    tb.append(el("tr", null,
      `<td class="code">${esc(op.number)}</td>` +
      `<td><b>${esc(op.name)}</b><br><span class="code">${esc(op.code)}</span>` +
      (asked ? `<br><button type="button" class="flag" data-flag="${esc(op.code)}" title="Модель ошиблась в этой операции">Ошибка</button>` : "") + `</td>` +
      `<td>${esc(equipRu(op.equipment))}</td>` +
      `<td class="rationale">${esc(op.rationale)}</td>`));

    const block = byCode[op.code];
    if (block && block.transitions.length) {
      const list = block.transitions
        .map(x => `<div class="tr-line"><span class="tr-num">${x.number}</span>${esc(x.text)}</div>`)
        .join("");
      const mark = block.source === "llm"
        ? '<span class="badge llm">модель</span>' : '<span class="badge baseline">правила</span>';
      tb.append(el("tr", "tr-sub", `<td></td><td colspan="3">${mark}${list}</td>`));
    }
  }
  t.append(tb);
  tb.querySelectorAll("[data-flag]").forEach(b => b.onclick = () =>
    openMistake({ where: "route", op: data.route.operations.find(o => o.code === b.dataset.flag), data }));
  const sc = el("div", "scroll"); sc.append(t);
  wrap.append(sc);

  if (data.decisions?.length) {
    const d = el("details");
    d.append(el("summary", null, "Решения модели по операциям"));
    const dt = el("table");
    dt.innerHTML = "<thead><tr><th>Операция</th><th>Решение</th><th>Кто решил</th><th>Обоснование</th></tr></thead>";
    const dtb = el("tbody");
    for (const dec of data.decisions) {
      dtb.append(el("tr", null,
        `<td class="code">${esc(dec.code)}</td>` +
        `<td>${dec.needed ? "нужна" : "не нужна"}</td>` +
        `<td><span class="badge ${dec.source === "llm" ? "llm" : "baseline"}">${dec.source === "llm" ? "модель" : "правила"}</span></td>` +
        `<td class="rationale">${esc(dec.rationale)}</td>`));
    }
    dt.append(dtb);
    d.append(dt);
    wrap.append(d);
  }

  if (data.compare) {
    const cm = data.compare;
    wrap.append(note("info",
      `Сравнение с маршрутом по правилам: совпадение состава <b>${cm.match_percent}%</b>, ` +
      `операций ${cm.operations_llm} против ${cm.operations_baseline}, порядок ` +
      (cm.same_order ? "совпадает" : "<b>не совпадает</b>") +
      (cm.extra_in_llm.length ? `<br>модель добавила: ${cm.extra_in_llm.map(esc).join(", ")}` : "") +
      (cm.missing_in_llm.length ? `<br>модель пропустила: ${cm.missing_in_llm.map(esc).join(", ")}` : "")));
  }

  if (data.route.summary) wrap.append(el("div", "hint", esc(data.route.summary)));
  if ((data.lessons || []).length) wrap.append(lessonsUsedBox(data.lessons));
  return card("Технологический маршрут", wrap);
}

async function api(path, payload) {
  const r = await fetch(path, payload === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || r.status);
  return data;
}

const RESULT_TABS = [
  ["route", "Маршрут"], ["calc", "Расчёт"], ["sketch", "Эскиз"],
  ["cards", "Карты"], ["sources", "Источники"], ["print", "Печать"],
];
const TAB_OF_CARD = {
  "Технологический маршрут": "route",
  "Поковка": "calc", "Размеры поковки, мм": "calc", "Заготовка и нагрев": "calc",
  "Оборудование": "calc", "Предупреждения": "calc",
  "Эскиз детали и поковки": "sketch",
  "Источники": "sources",
};
let ACTIVE_TAB = "route";

function render(data) {
  renderResult(data);
  if (isLayoutB()) { sheetify(data); return; }
  if (isLayoutC() && data.route && ACTIVE_TAB === "route") ACTIVE_TAB = "calc";
  tabify(data);
  if (isLayoutC()) chainify(data);
}

function tabify(data) {
  const out = $("#out");
  const panes = {};
  const bar = el("div", "rtabs");
  for (const [key, label] of RESULT_TABS) {
    panes[key] = el("div", "rpane");
    panes[key].dataset.tab = key;
    const b = el("button", "rtab", esc(label));
    b.dataset.tab = key;
    b.onclick = () => showTab(key);
    bar.append(b);
  }
  for (const node of [...out.children]) {
    if (node.classList.contains("tiles") || node.classList.contains("note")) continue;
    if (node.classList.contains("printbar")) { panes.print.append(node); continue; }
    const title = node.querySelector(":scope > h2")?.textContent || "";
    panes[TAB_OF_CARD[title] || "calc"].append(node);
  }
  panes.cards.append(el("div", "hint", "Карты собираются по справочнику форм…"));
  const tiles = out.querySelector(":scope > .tiles");
  if (tiles) tiles.after(bar); else out.prepend(bar);
  let after = bar;
  for (const [key] of RESULT_TABS) { after.after(panes[key]); after = panes[key]; }

  for (const b of bar.children) {
    const k = b.dataset.tab;
    if (k === "cards") b.hidden = !data.route;
    else if (k !== "print") b.hidden = !panes[k].children.length;
  }
  const visible = [...bar.children].filter(b => !b.hidden).map(b => b.dataset.tab);
  showTab(visible.includes(ACTIVE_TAB) ? ACTIVE_TAB : (data.route ? "route" : "calc"));
  if (data.route) fillCardsPreview(data, panes.cards);
  if ((data.edits || []).length) {
    panes.route.prepend(note("info", "Маршрут изменён вручную через чат: " + esc(changesText(data.edits)) +
      ". Обоснования добавленных операций дала модель, программа их проверила."));
  }
}

function showTab(key) {
  ACTIVE_TAB = key;
  document.querySelectorAll("#out .rtab").forEach(b => b.classList.toggle("on", b.dataset.tab === key));
  document.querySelectorAll("#out .rpane").forEach(p => { p.hidden = p.dataset.tab !== key; });
}

async function fillCardsPreview(data, pane) {
  try {
    const sheets = await fetchCards(data);
    pane.innerHTML = "";
    const box = el("div", "cards-preview");
    box.innerHTML = sheets.map(sheetHtml).join("");
    pane.append(box);
  } catch (e) {
    pane.innerHTML = "";
    pane.append(note("warn", "Карты не собраны: " + esc(e.message)));
  }
}

function showEmpty() {
  $("#out").innerHTML = `<div class="card"><div class="empty">
    <b>Новый чат</b>
  </div></div>`;
}

let SETTINGS = null, LAYOUTS_READY = ["A"], PREF_MODEL = "";

let ACCENT = "";
const DEFAULT_ACCENT_PICK = "#2b2f33";

function hexRgb(hex) { return [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)); }
function rgbHex(c) { return "#" + c.map(v => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0")).join(""); }
function luminance(c) {
  const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
}
function darkActive() {
  const t = document.documentElement.dataset.theme;
  return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
}

function applyAccent(hex) {
  ACCENT = hex || "";
  const root = document.documentElement.style;
  if (!ACCENT) { root.removeProperty("--accent"); root.removeProperty("--accent-ink"); return; }
  let c = hexRgb(ACCENT);
  const dark = darkActive();
  for (let i = 0; i < 24 && (dark ? luminance(c) < 0.25 : luminance(c) > 0.3); i++)
    c = c.map(v => dark ? v + (255 - v) * 0.12 : v * 0.88);
  root.setProperty("--accent", rgbHex(c));
  const l = luminance(c);
  root.setProperty("--accent-ink", (l + 0.05) / 0.05 > 1.05 / (l + 0.05) ? "#111315" : "#ffffff");
}

function applyTheme(theme) {
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  applyAccent(ACCENT);
}

matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyAccent(ACCENT));

let UPDATE = null;

async function checkUpdate(fresh) {
  try { UPDATE = (await api("/api/update", fresh ? { fresh: true } : undefined)).update; }
  catch (e) { UPDATE = { error: e.message }; }
  const b = $("#btn-update");
  b.hidden = !UPDATE.newer;
  if (UPDATE.newer) b.textContent = `Обновить до ${UPDATE.latest}`;
  return UPDATE;
}

function openUpdate() {
  const u = UPDATE || {};
  const box = $("#dlg");
  box.innerHTML = `<div class="dlg" role="dialog" aria-label="Обновление FORGE">
    <div class="dlg-head"><h2>FORGE ${esc(u.latest || "")}</h2><button type="button" id="up-x">Закрыть</button></div>
    <div class="dlg-body">
      <div class="hint">Сейчас стоит ${esc(u.current || "")}</div>
      <div class="up-notes">${esc(u.notes || "Описание изменений — на странице релиза.")}</div>
      <div class="fr-row">${u.can_install
        ? '<button type="button" class="primary" id="up-go">Обновить сейчас</button><span class="hint">FORGE скачает новую версию, закроется и откроется заново</span>'
        : `<a href="${esc(u.page || "")}" target="_blank" rel="noopener">Открыть релиз на GitHub</a><span class="hint">из исходников обновление ставится вручную</span>`}</div>
      <div class="hint" id="up-state"></div>
    </div></div>`;
  box.hidden = false;
  $("#up-x").onclick = closeDlg;
  if ($("#up-go")) $("#up-go").onclick = async () => {
    $("#up-go").disabled = true;
    $("#up-state").textContent = "Скачиваю обновление…";
    try {
      await api("/api/update/install", {});
      $("#up-state").textContent = "Готово. FORGE перезапускается — это окно можно закрыть.";
    } catch (e) {
      $("#up-state").textContent = e.message;
      $("#up-go").disabled = false;
    }
  };
}

function applyPrefs() {
  const s = SETTINGS;
  ACCENT = s.accent || "";
  applyTheme(s.theme);
  PREF_MODEL = s.model || "";
  const mode = s.use_llm ? (s.mode === "baseline" ? "stepwise" : s.mode) : "baseline";
  const radio = document.querySelector(`input[name=mode][value=${mode}]`);
  if (radio) radio.checked = true;
  $("#f-transitions").checked = !!s.transitions;
  if (PREF_MODEL) {
    $("#f-pick-model").checked = true;
    renderModelPicker();

    const sel = $("#f-model");
    if ([...sel.options].some(o => o.value === PREF_MODEL)) sel.value = PREF_MODEL;
    renderPills();
  }
  updateComposer();
  applyLayout();
}

let SAVE_TIMER = null;

function savePrefs() {
  if (!SETTINGS) return;
  const mode = document.querySelector("input[name=mode]:checked")?.value || SETTINGS.mode;
  SETTINGS.use_llm = mode !== "baseline";
  if (mode !== "baseline") SETTINGS.mode = mode;
  SETTINGS.transitions = $("#f-transitions").checked;
  if ($("#f-pick-model").checked && $("#f-model").value) SETTINGS.model = $("#f-model").value;
  clearTimeout(SAVE_TIMER);
  SAVE_TIMER = setTimeout(() => api("/api/settings", { settings: SETTINGS }).catch(() => {}), 300);
}

function shortModel(name) {
  return String(name || "").split("/").pop().replace(/-GGUF/i, "");
}

const LAYOUT_INFO = [
  ["A", "A. Рабочее место",
   '<div class="pv" style="grid-template-columns:1fr 1.3fr 2.6fr 1.6fr"><i></i><i></i><i></i><i class="c"></i></div>'],
  ["B", "B. Лист + ассистент",
   '<div class="pv" style="grid-template-columns:1fr 3fr 1.6fr"><i></i><i style="margin:6px 12px"></i><i class="c"></i></div>'],
  ["C", "C. Пульт технолога",
   '<div class="pv" style="grid-template-rows:1.6fr 1fr;grid-template-columns:3fr 1fr;background:#141412"><i style="background:#22211e"></i><i style="background:#22211e"></i><i style="grid-column:span 2;background:#2d2b27"></i></div>'],
];

function openModelSetup() {
  const box = $("#dlg");
  box.innerHTML = `<div class="dlg" id="ms" role="dialog" aria-label="Скачать модель">
    <div class="dlg-head"><h2>Скачать модель</h2>
      <button type="button" class="chip" id="ms-recheck">проверить снова</button>
      <button type="button" id="ms-x">Закрыть</button></div>
    <div class="dlg-body" id="ms-body"></div></div>`;
  box.hidden = false;
  $("#ms-x").onclick = closeDlg;
  $("#ms-recheck").onclick = async () => { await refreshOllama(); drawModelSetup(); };
  drawModelSetup();
}

function drawModelSetup() {
  const body = $("#ms-body");
  if (!body) return;
  const o = STATE.ollama || {};
  const installed = !!o.app_installed, running = !!o.service;
  const row = m => `<div class="cat-row">
      <div><span class="mn">${esc(shortModel(m.name))}</span> <span class="md">${fmt(m.size_gb, 1)} ГБ · ${esc(m.about)}</span></div>
      ${m.tested ? '<span class="mtag ok">испытана</span>' : "<span></span>"}
      ${modelInstalled(m.name) ? '<span class="mtag ok">скачана</span>'
        : `<button type="button" data-pull="${esc(m.name)}" ${running ? "" : "disabled"}>Скачать</button>`}
    </div>`;
  const groups = (STATE.catalog || []).map(g => `<div class="cat-group">
      <div class="cat-head"><b>${esc(g.title)}</b><span>${esc(g.hint)}</span></div>
      ${g.models.map(row).join("")}</div>`).join("");

  body.innerHTML = `
    <div class="step ${installed ? "done" : ""}">
      <span class="num">1</span>
      <div>
        <b>Ollama${installed ? " — установлена" : ""}</b>
        ${installed ? "" : `<div class="hint">Windows спросит права администратора.</div>
        <div class="setup-act">
          <button class="primary" data-step="ollama">Установить Ollama</button>
          <span class="hint mono">winget install Ollama.Ollama</span>
        </div>`}
      </div>
    </div>
    <div class="step ${running ? "done" : ""}">
      <span class="num">2</span>
      <div>
        <b>Сервис Ollama${running ? " — работает" : ""}</b>
        ${running || !installed ? "" : `<div class="setup-act">
          <button class="primary" data-step="start">Запустить Ollama</button>
          <span class="hint mono">ollama app.exe</span>
        </div>`}
      </div>
    </div>
    <div class="step">
      <span class="num">3</span>
      <div class="step-wide">
        <b>Модель</b>
        ${running ? "" : '<div class="hint">Сначала шаги 1 и 2</div>'}
        ${groups}
        <div class="setup-act">
          <input id="ms-name" placeholder="другая модель, имя с ollama.com/library" ${running ? "" : "disabled"}>
          <button type="button" id="ms-custom" ${running ? "" : "disabled"}>Скачать</button>
        </div>
      </div>
    </div>
    <div class="hint" id="ms-note"></div>
    <pre class="pull-log" id="ms-log" hidden></pre>`;

  body.querySelectorAll("[data-step]").forEach(b => b.onclick = () => startStep(b.dataset.step, b));
  body.querySelectorAll("[data-pull]").forEach(b => b.onclick = () => startStep("model", b, b.dataset.pull));
  $("#ms-custom").onclick = () => {
    const name = $("#ms-name").value.trim();
    if (name) startStep("model", $("#ms-custom"), name);
  };
}

function openFirstRun(first) {
  const s = { ...SETTINGS };
  const box = $("#firstrun");
  const fallback = (STATE && STATE.ollama && STATE.ollama.model) || "";
  const installedModels = () => (STATE && STATE.ollama && STATE.ollama.models) || [];
  let choice = !s.use_llm ? "" : (s.model || fallback || (installedModels()[0] && installedModels()[0].name) || "");

  const layouts = LAYOUT_INFO.map(([code, name, pv]) => {
    const ready = LAYOUTS_READY.includes(code);
    return `<button type="button" class="lay${ready ? "" : " soon"}"
      data-lay="${code}" ${ready ? "" : "disabled"}>${pv}<b>${name}</b>
      </button>`;
  }).join("");

  box.innerHTML = `
    <div class="fr-bar"><b>FORGE</b><span class="ver">${esc(STATE?.version || "")}</span></div>
    <div class="fr-grid">
      <div class="fr-step">
        <h2>Как вам удобнее работать?</h2>
        <div class="lays">${layouts}</div>
        <div class="fr-row"><span>Тема</span><div class="themes" id="fr-theme" role="radiogroup">
          <label class="th"><input type="radio" name="fr-theme" value="system"> Как в системе</label>
          <label class="th"><input type="radio" name="fr-theme" value="light"> Светлая</label>
          <label class="th"><input type="radio" name="fr-theme" value="dark"> Тёмная</label></div></div>
        <div class="fr-row"><span>Цвет</span><input type="color" id="fr-accent" class="accent-pick">
          <button type="button" id="fr-accent-reset">Как в пульте</button></div>
      </div>
      <div class="fr-step">
        <h2>Какой моделью строить маршрут?</h2>
        <div id="fr-models"></div>
        <div class="fr-row" id="fr-think-row" hidden><span>Рассуждение</span>
          <select id="fr-think"></select></div>
        <label class="fr-row fr-check"><input type="checkbox" id="fr-requests"> Показывать запросы к модели во время построения</label>
        <label class="fr-row fr-check"><input type="checkbox" id="fr-updates"> Проверять обновления при запуске</label>
        <div class="fr-row"><button type="button" id="fr-check-update">Проверить обновления</button><span class="hint" id="fr-update-state"></span></div>
        <div class="fr-row"><button type="button" id="fr-setup">Скачать модель…</button></div>
        <div class="fr-foot">
          ${first ? "" : '<button type="button" id="fr-cancel">Отмена</button>'}
          <button type="button" class="primary" id="fr-ok">${first ? "Начать работу" : "Сохранить"}</button>
        </div>
      </div>
    </div>`;

  const pickLayout = code => box.querySelectorAll(".lay").forEach(b => b.classList.toggle("on", b.dataset.lay === code));
  const pickTheme = th => { s.theme = th; applyTheme(th);
    box.querySelectorAll("#fr-theme .th").forEach(l => {
      const input = l.querySelector("input");
      input.checked = input.value === th;
      l.classList.toggle("on", input.checked);
    }); };
  s.think = { ...(s.think || {}) };
  const thinkValue = v => v === true ? "on" : v === false ? "off" : v || "";
  const showThink = () => {
    const m = installedModels().find(x => x.name === choice);
    $("#fr-think-row").hidden = !(m && m.thinking);
    if (m && m.thinking) {
      const names = { low: "низкий", medium: "средний", high: "высокий", on: "включено", off: "выключено" };
      const own = m.think_profile === null || m.think_profile === undefined ? "" : ` (${names[thinkValue(m.think_profile)]})`;
      const kinds = /gpt-oss/i.test(choice) ? ["low", "medium", "high"] : ["on", "off"];
      fillSelect($("#fr-think"), [["", "как в профиле модели" + own], ...kinds.map(k => [k, names[k]])]);
      const want = thinkValue(s.think[choice]);
      $("#fr-think").value = kinds.includes(want) ? want : "";
    }
  };
  const pickModel = name => { choice = name;
    box.querySelectorAll(".mrow").forEach(r => {
      const on = r.dataset.m === name; r.classList.toggle("on", on); r.querySelector("input").checked = on; });
    showThink(); };

  const drawModels = () => {
    const models = installedModels();
    const tag = m => m.tested ? `<span class="mtag ok">испытана</span>` : `<span class="mtag warn">не испытана</span>`;
    const rows = models.map(m => `<label class="mrow" data-m="${esc(m.name)}">
      <input type="radio" name="fr-model" value="${esc(m.name)}">
      <div><div class="mn">${esc(shortModel(m.name))}</div>
      <div class="md">${esc(m.name)}${m.size_gb ? " · " + fmt(m.size_gb, 1) + " ГБ" : ""}${m.parameters ? " · " + esc(m.parameters) : ""}</div></div>
      ${tag(m)}</label>`).join("");
    $("#fr-models").innerHTML = `
      ${models.length ? "" : '<div class="note warn">Скачанных моделей пока нет. Можно начать без нейросети и скачать модель ниже.</div>'}
      <div class="mlist">${rows}
        <label class="mrow" data-m=""><input type="radio" name="fr-model" value="">
          <div><div class="mn">Без нейросети</div></div></label>
      </div>`;
    box.querySelectorAll(".mrow").forEach(r => r.onclick = () => pickModel(r.dataset.m));
    pickModel(choice === "" || models.some(m => m.name === choice) ? choice : (models[0] ? models[0].name : ""));
  };

  FR_REDRAW = drawModels;
  const close = () => { FR_REDRAW = null; box.hidden = true; };

  pickLayout(LAYOUTS_READY.includes(s.layout) ? s.layout : "A");
  pickTheme(s.theme);
  FR_REDRAW();

  box.querySelectorAll(".lay:not(.soon)").forEach(b => b.onclick = () => { s.layout = b.dataset.lay; pickLayout(s.layout); });
  box.querySelectorAll("#fr-theme input").forEach(i => i.addEventListener("change", () => pickTheme(i.value)));
  $("#fr-setup").onclick = openModelSetup;
  $("#fr-requests").checked = s.show_requests !== false;
  $("#fr-requests").onchange = () => { s.show_requests = $("#fr-requests").checked; };
  $("#fr-updates").checked = s.check_updates !== false;
  $("#fr-updates").onchange = () => { s.check_updates = $("#fr-updates").checked; };
  $("#fr-check-update").onclick = async () => {
    $("#fr-update-state").textContent = "проверяю…";
    const u = await checkUpdate(true);
    $("#fr-update-state").textContent = u.error ? u.error
      : u.newer ? `вышла ${u.latest} — кнопка «Обновить» в шапке`
      : u.latest ? `стоит последняя версия (${u.latest})` : "релизов на GitHub пока нет";
  };
  $("#fr-accent").value = s.accent || DEFAULT_ACCENT_PICK;
  $("#fr-accent").oninput = () => { s.accent = $("#fr-accent").value; applyAccent(s.accent); };
  $("#fr-accent-reset").onclick = () => { s.accent = ""; $("#fr-accent").value = DEFAULT_ACCENT_PICK; applyAccent(""); };
  $("#fr-think").onchange = () => {
    const v = $("#fr-think").value;
    if (v === "") delete s.think[choice];
    else s.think[choice] = v === "on" ? true : v === "off" ? false : v;
  };
  if ($("#fr-cancel")) $("#fr-cancel").onclick = () => { ACCENT = SETTINGS.accent || ""; applyTheme(SETTINGS.theme); close(); };
  $("#fr-ok").onclick = async () => {
    s.use_llm = choice !== "";
    s.model = choice;
    try {
      const res = await api("/api/settings", { settings: s });
      SETTINGS = res.settings;
    } catch (e) {
      SETTINGS = s;
    }
    close();
    applyPrefs();
    renderPills();
  };
  box.hidden = false;
}

let CHAT = null, CHATS = [];

function blankChat() {
  return { id: null, title: "Новый чат", model: "", part: null, result: null, events: [], messages: [] };
}

function newChat() {
  CHAT = blankChat();
  showEmpty();
  renderChatLog();
  renderHistory();
}

async function refreshHistory() {
  try { CHATS = (await api("/api/history")).chats; } catch (e) { CHATS = []; }
  renderHistory();
}

function dayGroup(stamp) {
  const d = new Date(String(stamp || "").replace(" ", "T"));
  if (isNaN(d)) return "Ранее";
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const diff = (today - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 864e5;
  return diff <= 0 ? "Сегодня" : diff <= 1 ? "Вчера" : diff <= 7 ? "На этой неделе" : "Ранее";
}

function renderHistory() {
  const list = $("#hist-list");
  const q = ($("#hist-q").value || "").trim().toLowerCase();
  const items = CHATS.filter(c => !q ||
    [c.title, c.designation, c.part_name, c.model].join(" ").toLowerCase().includes(q));
  list.innerHTML = "";
  if (!items.length) {
    list.append(el("div", "hempty", q ? "Ничего не нашлось" : "Чатов пока нет"));
    return;
  }
  let group = "";
  for (const c of items) {
    const g = dayGroup(c.updated);
    if (g !== group) { list.append(el("div", "hgroup", g)); group = g; }
    const it = el("div", "hitem" + (CHAT && CHAT.id === c.id ? " on" : ""));
    const time = String(c.updated || "").slice(g === "Сегодня" || g === "Вчера" ? 11 : 0, 16);
    it.innerHTML = `<div class="ht">${esc(c.title)}</div>
      <div class="hm">${esc([c.designation, shortModel(c.model) || (c.has_result ? "правила" : ""), time]
        .filter(Boolean).join(" · "))}</div>
      <div class="hact"><button title="Переименовать" data-a="ren">Имя</button>
        <button title="Удалить" data-a="del">×</button></div>`;
    it.onclick = e => {
      const a = e.target.closest("button")?.dataset.a;
      if (a === "ren") return renameChat(c);
      if (a === "del") return deleteChat(c);
      openChat(c.id);
    };
    list.append(it);
  }
}

async function openChat(id) {
  try {
    CHAT = (await api("/api/history/get", { id })).chat;
  } catch (e) {
    refreshHistory();
    return;
  }
  CHAT.events = CHAT.events || [];
  if (CHAT.part) fillForm(CHAT.part);
  if (CHAT.result) render(CHAT.result); else showEmpty();
  renderChatLog();
  renderHistory();
  if (document.body.classList.contains("hist-on") && innerWidth <= 1400) toggleHistory();
}

async function renameChat(c) {
  const title = prompt("Название чата", c.title);
  if (!title || !title.trim()) return;
  try { await api("/api/history/rename", { id: c.id, title }); } catch (e) { return; }
  if (CHAT && CHAT.id === c.id) CHAT.title = title.trim();
  refreshHistory();
}

async function deleteChat(c) {
  if (!confirm(`Удалить чат «${c.title}»? Это нельзя отменить.`)) return;
  try { await api("/api/history/delete", { id: c.id }); } catch (e) { return; }
  if (CHAT && CHAT.id === c.id) newChat();
  refreshHistory();
}

async function saveChat() {
  try {
    const saved = (await api("/api/history/save", { chat: CHAT })).chat;
    CHAT.id = saved.id;
  } catch (e) {
    CHAT.events.push({ at: nowHM(), kind: "err", text: "Чат не сохранён: " + e.message });
    renderChatLog();
  }
  refreshHistory();
}

function nowHM() {
  const d = new Date(), two = n => String(n).padStart(2, "0");
  return `${two(d.getHours())}:${two(d.getMinutes())}`;
}

function addEvent(kind, text) {
  if (!CHAT) CHAT = blankChat();
  CHAT.events.push({ at: nowHM(), kind, text });
  renderChatLog();
  if (CHAT.id) saveChat();
}

function renderChatLog() {
  const log = $("#chat-log");
  log.innerHTML = "";
  $("#chat-model").textContent = shortModel(CHAT && CHAT.model) || "";
  $("#chat-model").hidden = !(CHAT && CHAT.model);
  const events = (CHAT && CHAT.events) || [];
  if (!events.length && !CHAT_BUSY) {
    updateComposer();
    return;
  }
  updateComposer();
  events.forEach((e, i) => {
    if (e.kind === "me") { log.append(el("div", "msg me", esc(e.text))); return; }
    if (e.kind === "ai") { log.append(aiBubble(e, i)); return; }
    log.append(el("div", "ev " + esc(e.kind || ""), `<span class="at">${esc(e.at || "")}</span>${esc(e.text || "")}`));
  });
  if (CHAT_BUSY) log.append(CHAT_BUSY.node);
  log.scrollTop = log.scrollHeight;
}

function recordResult(url, body, data) {
  if (!CHAT) CHAT = blankChat();
  const isRoute = url.endsWith("route");
  CHAT.part = body.part;
  CHAT.result = data;
  if (data.model) CHAT.model = data.model;
  const name = (body.part && body.part.name) || "Деталь";
  if (CHAT.title === "Новый чат") {
    CHAT.title = isRoute ? `${name} — маршрут, ${shortModel(data.model) || "правила"}` : `${name} — расчёт`;
  }
  const f = data.calc && data.calc.forging, b = data.calc && data.calc.blank;
  let text;
  if (isRoute) {
    const ops = (data.route && data.route.operations || []).length;
    const who = data.source === "baseline" ? "правила" : "модель " + shortModel(data.model);
    text = `Маршрут: ${ops} операций, построили ${who}` +
      (data.elapsed_sec !== undefined ? `, ${fmt(data.elapsed_sec, 1)} с` : "") +
      (data.stopped ? " — остановлено кнопкой" : "");
  } else {
    text = `Расчёт: поковка ${fmt(f && f.mass_actual && f.mass_actual.value)} кг, заготовка ⌀` +
      `${fmt(b && b.diameter && b.diameter.value, 0)}×${fmt(b && b.length && b.length.value, 0)} мм`;
  }
  CHAT.events.push({ at: nowHM(), kind: isRoute ? "route" : "calc", text });
  renderChatLog();
  saveChat();
}

function toggleHistory() {
  if (innerWidth <= 1400) {
    const on = document.body.classList.toggle("hist-on");
    if (on) document.body.classList.remove("form-open");
  } else document.body.classList.toggle("hist-off");
}

function syncHeaderHeight() {
  const h = document.querySelector("header");
  if (h) document.documentElement.style.setProperty("--hdr-h", h.getBoundingClientRect().height + "px");
}
addEventListener("resize", syncHeaderHeight);

let CHAT_MODE = "explain", CHAT_BUSY = null;

function aiBubble(e, index) {
  const b = el("div", "msg ai", esc(e.text || ""));
  if (e.proposal) b.append(proposalBox(e, index));
  if ((e.rejected || []).length) {
    b.append(el("div", "rejected", "Правка не прошла проверку маршрута и не предложена:<ul>" +
      e.rejected.map(r => `<li>${esc(r)}</li>`).join("") + "</ul>"));
  }
  const meta = el("div", "meta");
  for (const s of e.sources || []) meta.append(el("span", "src", esc(s)));
  if (e.mode === "consult") meta.append(el("span", "vwarn", "не из расчёта, проверить по справочнику"));
  else if (e.verified) meta.append(el("span", "vok", "числа сверены с расчётом"));
  else meta.append(el("span", "vwarn", "не из расчёта: " + esc((e.invented || []).map(v => fmt(v, 3)).join(", "))));
  if (e.mode === "consult" && (e.invented || []).length)
    meta.append(el("span", null, "числа вне расчёта: " + esc(e.invented.map(v => fmt(v, 3)).join(", "))));
  meta.append(el("span", "mtagc", esc(e.mode_name || "")));
  meta.append(el("span", null, esc([shortModel(e.model), e.seconds !== undefined ? fmt(e.seconds, 1) + " с" : ""]
    .filter(Boolean).join(" · "))));
  if ((e.lessons || []).length) {
    const u = el("span", "src", `уроков: ${e.lessons.length}`);
    u.title = e.lessons.map(x => "• " + x.rule).join("\n");
    meta.append(u);
  }
  const flag = el("button", "flag", "Ошибка");
  flag.type = "button";
  flag.title = "Модель ошиблась в этом ответе: записать, как правильно";
  flag.onclick = () => openMistake({ where: "chat", wrong: e.text || "", model: e.model || "" });
  meta.append(flag);
  b.append(meta);
  return b;
}

function chatModel() {
  return chosenModel() || (STATE && STATE.ollama && STATE.ollama.model) || "";
}

function updateComposer() {
  const q = $("#chat-q"), send = $("#chat-send"), note = $("#chat-note");
  if (!q) return;
  const noLlm = SETTINGS && SETTINGS.use_llm === false;
  const needCalc = ["explain", "edit", "whatif"].includes(CHAT_MODE) && !(CHAT && CHAT.result && CHAT.result.calc);
  const needRoute = CHAT_MODE === "edit" && !(CHAT && CHAT.result && CHAT.result.route);
  let why = "";
  if (noLlm) why = "Чат недоступен: в настройках выбрано «Без нейросети».";
  else if (needCalc) why = "Сначала посчитайте деталь или постройте маршрут.";
  else if (needRoute) why = "Править нечего: сначала постройте маршрут.";
  q.disabled = !!why || !!CHAT_BUSY;
  send.disabled = !!why || !!CHAT_BUSY;
  q.placeholder = { explain: "Вопрос о расчёте или маршруте…", consult: "Вопрос о технологии…",
                    edit: "Что изменить в маршруте? Например: «убери правку»",
                    whatif: "Например: «а если сталь 45?» или «а если 2000 шт. в год?»" }[CHAT_MODE] || "";
  note.textContent = why;
}

function setChatMode(mode) {
  CHAT_MODE = mode;
  document.querySelectorAll("#chat-modes .cmode").forEach(b => b.classList.toggle("on", b.dataset.mode === mode));
  updateComposer();
}

function chatHistory() {
  return ((CHAT && CHAT.events) || []).filter(e => e.kind === "me" || e.kind === "ai")
    .slice(-6).map(e => ({ role: e.kind, text: e.text }));
}

async function sendChat() {
  const q = $("#chat-q");
  const question = (q.value || "").trim();
  if (!question || CHAT_BUSY) return;
  if (!CHAT) CHAT = blankChat();
  const history = chatHistory();
  CHAT.events.push({ at: nowHM(), kind: "me", text: question, mode: CHAT_MODE });
  if (CHAT.title === "Новый чат") CHAT.title = question.slice(0, 60);
  q.value = "";

  const job = newJob();
  const node = el("div", "thinking", '<span class="spinner"></span><span>модель думает…</span>');
  const stop = el("button", null, "Остановить");
  stop.onclick = () => stopBuild(job);
  node.append(stop);
  CHAT_BUSY = { node, job };
  renderChatLog();

  let reply;
  try {
    reply = (await api("/api/chat", {
      mode: CHAT_MODE, question, history, job,
      model: chatModel(),
      part: CHAT.part || null,
      result: CHAT.result ? { route: CHAT.result.route, transitions: CHAT.result.transitions } : null,
    })).chat;
  } catch (e) {
    reply = { error: e.message };
  }
  CHAT_BUSY = null;
  if (reply.answer) {
    CHAT.events.push({ at: nowHM(), kind: "ai", text: reply.answer, sources: reply.sources,
      verified: reply.verified, invented: reply.invented, mode: reply.mode, mode_name: reply.mode_name,
      model: reply.model, seconds: reply.seconds, proposal: reply.proposal || null,
      rejected: reply.rejected || [], status: reply.proposal ? "pending" : "", lessons: reply.lessons || [] });
    if (reply.model) CHAT.model = CHAT.model || reply.model;
  } else {
    CHAT.events.push({ at: nowHM(), kind: "err",
      text: reply.stopped ? "Остановлено." : "Модель не ответила: " + (reply.error || "нет ответа") });
  }
  renderChatLog();
  saveChat();
}

function initChat() {
  document.querySelectorAll("#chat-modes .cmode:not(:disabled)").forEach(b =>
    b.onclick = () => setChatMode(b.dataset.mode));
  $("#chat-send").onclick = sendChat;
  $("#chat-q").addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
  });
  setChatMode("explain");
}

function proposalBox(e, index) {
  if (e.proposal.kind === "whatif") return whatifBox(e, index);
  const box = el("div", "proposal");
  box.append(el("div", "ph", "Предлагаемая правка маршрута"));
  for (const c of e.proposal.changes || []) {
    box.append(el("div", "row " + (c.action === "remove" ? "del" : "add"),
      `${c.action === "remove" ? "−" : "+"} ${esc(c.number || "")} ${esc(c.name || c.code)}`));
  }
  const foot = el("div", "pf");
  if (e.status === "accepted") foot.append(el("span", "vok", "принята, маршрут и карты обновлены"));
  else if (e.status === "declined") foot.append(el("span", null, "отклонена"));
  else {
    foot.append(el("span", "vok", "проверки маршрута пройдены"), el("span", "grow"));
    const no = el("button", null, "Отклонить"), yes = el("button", "primary", "Принять");
    no.onclick = () => decideProposal(index, false);
    yes.onclick = () => decideProposal(index, true);
    foot.append(no, yes);
  }
  box.append(foot);
  return box;
}

function changesText(changes) {
  return (changes || []).map(c => `${c.action === "remove" ? "−" : "+"}${c.number || ""} ${c.name || c.code}`).join("; ");
}

function decideProposal(index, accept) {
  const e = CHAT && CHAT.events[index];
  if (!e || !e.proposal || e.status !== "pending") return;
  e.status = accept ? "accepted" : "declined";
  if (accept && CHAT.result) {
    CHAT.result.route = e.proposal.route;
    if (e.proposal.transitions) CHAT.result.transitions = e.proposal.transitions;
    CHAT.result.edits = (CHAT.result.edits || []).concat(e.proposal.changes || []);
    render(CHAT.result);
    CHAT.events.push({ at: nowHM(), kind: "route", text: "Маршрут изменён через чат: " + changesText(e.proposal.changes) });
  }
  renderChatLog();
  saveChat();
}

function whatifBox(e, index) {
  const p = e.proposal;
  const box = el("div", "proposal");
  box.append(el("div", "ph", "Вариант: " + esc((p.changes || []).map(c => `${c.label}: ${c.old} → ${c.new}`).join("; "))));
  if ((p.compare || []).length) {
    const rows = p.compare.map(r => `<tr class="${r.changed ? "chg" : ""}"><td>${esc(r.label)}</td>` +
      `<td>${esc(r.old)}</td><td>${esc(r.new)}</td></tr>`).join("");
    box.append(el("div", null, `<table class="cmp"><tr><th></th><th>Было</th><th>Вариант</th></tr>${rows}</table>`));
  }
  const foot = el("div", "pf");
  if (e.status === "accepted") foot.append(el("span", "vok", "вариант открыт, форма и расчёт заменены"));
  else if (e.status === "declined") foot.append(el("span", null, "оставлено как было"));
  else {
    foot.append(el("span", "vok", "пересчитано программой"), el("span", "grow"));
    const no = el("button", null, "Оставить как было"), yes = el("button", "primary", "Открыть вариант");
    no.onclick = () => decideWhatif(index, false);
    yes.onclick = () => decideWhatif(index, true);
    foot.append(no, yes);
  }
  box.append(foot);
  return box;
}

function decideWhatif(index, accept) {
  const e = CHAT && CHAT.events[index];
  if (!e || !e.proposal || e.status !== "pending") return;
  e.status = accept ? "accepted" : "declined";
  if (accept && e.proposal.result) {
    fillForm(e.proposal.part);
    CHAT.part = e.proposal.part;
    CHAT.result = e.proposal.result;
    render(CHAT.result);
    CHAT.events.push({ at: nowHM(), kind: "calc", text: "Открыт вариант: " +
      (e.proposal.changes || []).map(c => `${c.label} ${c.new}`).join(", ") + ". Маршрут — по правилам; " +
      "чтобы маршрут построила модель, нажмите «Построить маршрут»." });
  }

  delete e.proposal.result;
  renderChatLog();
  saveChat();
}

let SHEET_TAB = "doc";

function clonePrintStyles() {
  if (document.getElementById("paper-css")) return;
  const out = [];
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch (e) { continue; }
    for (const rule of rules) {
      if (!(rule instanceof CSSMediaRule) || !/print/.test(rule.conditionText || rule.media.mediaText)) continue;
      for (const inner of rule.cssRules) {
        if (inner instanceof CSSStyleRule && inner.selectorText.includes("#print-doc")) {
          out.push(inner.cssText.replaceAll("#print-doc", ".paper"));
        }
      }
    }
  }
  const style = document.createElement("style");
  style.id = "paper-css";
  style.textContent = out.join("\n") + "\n.paper { display: block !important; }";
  document.head.append(style);
}

function isLayoutB() { return document.body.classList.contains("layout-B"); }

function sheetify(data) {
  clonePrintStyles();
  const out = $("#out");
  const notes = [...out.querySelectorAll(":scope > .note")];
  const bar = printBar(data);
  out.innerHTML = "";
  const tabs = el("div", "btabs");
  const panes = {};
  const list = [["doc", "Лист"], ["card", "МК ГОСТ 3.1118"], ["fcard", "КТП ГОСТ 3.1403"], ["print", "Печать"]];
  for (const [key, label] of list) {
    if ((key === "card" || key === "fcard") && !data.route) continue;
    const b = el("button", null, esc(label));
    b.dataset.tab = key;
    b.onclick = () => showSheetTab(key);
    tabs.append(b);
    panes[key] = el("div", "desk");
    panes[key].dataset.tab = key;
  }
  out.append(tabs);
  for (const k in panes) out.append(panes[k]);
  notes.forEach(n => panes.doc.append(n));

  const opts = {};
  for (const [key] of PRINT_PARTS) opts[key] = printAvailable(data, key) && key !== "card" && key !== "forging_card";
  const paper = el("div", "paper");
  paper.innerHTML = buildPrintDoc(data, opts);
  panes.doc.append(el("div", "note info", "Щёлкните строку таблицы на листе, и вопрос о ней появится в чате."), paper);
  if ((data.edits || []).length) panes.doc.prepend(note("info", "Маршрут изменён вручную через чат: " + esc(changesText(data.edits))));
  if ((data.lessons || []).length) {
    const lu = lessonsUsedBox(data.lessons);
    lu.classList.add("note", "info");
    panes.doc.prepend(lu);
  }
  paper.querySelectorAll("tr").forEach(tr => {
    if (tr.querySelector("th")) return;
    tr.classList.add("ask");
    tr.onclick = () => askAboutRow(tr);
  });
  if (data.route) {
    fetchCards(data).then(sheets => {
      panes.card.innerHTML = sheetHtml(sheets[0]);
      panes.fcard.innerHTML = sheetHtml(sheets[1]);
    }).catch(e => { panes.card.append(note("warn", "Карты не собраны: " + esc(e.message))); });
  }
  panes.print.append(bar);
  showSheetTab(panes[SHEET_TAB] ? SHEET_TAB : "doc");
}

function showSheetTab(key) {
  SHEET_TAB = key;
  document.querySelectorAll("#out .btabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === key));
  document.querySelectorAll("#out .desk").forEach(d => { d.hidden = d.dataset.tab !== key; });
}

function askAboutRow(tr) {
  document.querySelectorAll(".paper tr.asked").forEach(r => r.classList.remove("asked"));
  tr.classList.add("asked");
  const cells = [...tr.children].map(c => c.textContent.trim().replace(/\s+/g, " ")).filter(Boolean);
  const table = tr.closest("table");
  const head = table && table.querySelector("tr th") ?
    [...table.querySelector("tr").children].map(c => c.textContent.trim()) : [];
  const text = cells.map((c, i) => head[i] ? `${head[i].toLowerCase()} ${c}` : c).join("; ").slice(0, 220);
  setChatMode("explain");
  const q = $("#chat-q");
  q.value = `Объясни строку листа: «${text}»`;
  q.focus();
}

function toggleForm(force) {
  const on = force === undefined ? !document.body.classList.contains("form-open") : force;
  document.body.classList.toggle("form-open", on);
  if (on) document.body.classList.remove("hist-on");
}

function applyLayout() {
  const want = SETTINGS && LAYOUTS_READY.includes(SETTINGS.layout) ? SETTINGS.layout : "A";
  const before = document.body.className;
  document.body.classList.toggle("layout-B", want === "B");
  document.body.classList.toggle("layout-C", want === "C");

  applyTheme(want === "C" && SETTINGS.theme === "system" ? "dark" : SETTINGS.theme);
  const drawer = want === "B" || want === "C";
  if (!drawer) toggleForm(false);
  if (before !== document.body.className && CHAT && CHAT.result) render(CHAT.result);
  if (drawer && !(CHAT && CHAT.result)) toggleForm(true);
}

let CHAIN_SEL = null;

function isLayoutC() { return document.body.classList.contains("layout-C"); }

function chainify(data) {
  if (!data.route || !(data.route.operations || []).length) return;
  const out = $("#out");
  const ops = data.route.operations;
  const rows = Object.fromEntries((data.transitions || []).map(r => [r.code, r]));
  const byModel = new Set((data.decisions || []).filter(d => d.source === "llm" && d.needed).map(d => d.code));
  if (!ops.some(o => o.code === CHAIN_SEL)) CHAIN_SEL = (ops.find(o => HOT_OPS[o.code.slice(0, 3)]) || ops[0]).code;

  const box = el("div", "chainbox");
  const chain = el("div", "chain");
  chain.append(el("div", "lbl", "Маршрут: цепочка операций и температура"));
  chain.insertAdjacentHTML("beforeend", tempSVG(data.calc, ops));
  const line = el("div", "ops");
  for (const op of ops) {
    const b = el("button", "op" + (HOT_OPS[op.code.slice(0, 3)] ? " hot" : "") + (op.code === CHAIN_SEL ? " sel" : ""));
    b.innerHTML = `<div class="no">${esc(op.number)}</div><div class="nm">${esc(op.name)}</div>
      <div class="eq">${esc(equipRu(op.equipment) === "—" ? "—" : equipRu(op.equipment))}</div>
      ${byModel.has(op.code) ? '<span class="tag">модель</span>' : ""}`;
    b.onclick = () => { CHAIN_SEL = op.code; chainify(data); };
    line.append(b);
  }
  chain.append(line);
  const sel = ops.find(o => o.code === CHAIN_SEL);
  const row = rows[CHAIN_SEL];
  const det = el("div", "opdet");
  det.innerHTML = `<h4>${esc(sel.number)} · ${esc(sel.name)}</h4>
    <div class="kv"><span>Оборудование</span><span>${esc(equipRu(sel.equipment))}</span></div>
    <div class="kv"><span>Решение</span><span>${byModel.has(sel.code) ? "модель" : "правила"}</span></div>
    <div class="hint" style="margin:6px 0">${esc(sel.rationale || "")}</div>` +
    (row ? `<div class="kv"><span>Переходы</span><span>${row.source === "llm" ? "модель" : "справочник"}</span></div>
      <ol>${(row.transitions || []).map(tr => `<li>${esc(tr.text)}</li>`).join("")}</ol>` +
      (row.errors || []).slice(0, 3).map(e => `<div class="brak">брак: ${esc(e)}</div>`).join("")
      : `<div class="hint">Переходы не раскрывались.</div>`);
  const ask = el("button", null, "Спросить об операции");
  ask.style.marginTop = "8px";
  ask.onclick = () => { setChatMode("explain"); $("#chat-q").value = `Объясни операцию ${sel.number} «${sel.name}»: зачем она и почему такое оборудование?`; $("#chat-q").focus(); };
  det.append(ask);
  if (data.source === "llm" || data.source === "stepwise") {
    const fl = el("button", "flag", "Ошибка в операции");
    fl.style.margin = "8px 0 0 6px";
    fl.onclick = () => openMistake({ where: "route", op: sel, data });
    det.append(fl);
  }
  box.append(chain, det);

  const old = out.querySelector(":scope > .chainbox");
  if (old) old.replaceWith(box);
  else {
    const tiles = out.querySelector(":scope > .tiles");
    if (tiles) tiles.after(box); else out.prepend(box);
  }
}

let LESSONS = { lessons: [], counts: {}, stats: [] }, LESSON_TAB = "candidate";
const LESSON_TABS = [["candidate", "На проверке"], ["accepted", "Уроки"], ["rejected", "Отклонённые"], ["stats", "По моделям"]];

async function loadLessons() {
  try { LESSONS = await api("/api/lessons"); } catch (e) { return; }
  const n = (LESSONS.counts || {}).candidate || 0;
  const badge = $("#lessons-badge");
  badge.textContent = n;
  badge.hidden = !n;
}

function partTypeName(code) {
  const t = ((STATE && STATE.part_types) || []).find(x => x.code === code);
  return t ? t.description.split("(")[0].trim() : (code || "");
}

function lessonsUsedBox(list) {
  const d = el("details", "lused");
  d.append(el("summary", null, `Модель получила уроков из журнала ошибок: ${list.length}`));
  d.append(el("ul", null, list.map(x =>
    `<li>${x.op_code ? `<span class="code">${esc(x.op_code)}</span> ` : ""}${esc(x.rule)}</li>`).join("")));
  return d;
}

function closeDlg() { const d = $("#dlg"); d.hidden = true; d.innerHTML = ""; }

function opText(op, data) {
  if (!op) return "";
  const row = ((data && data.transitions) || []).find(r => r.code === op.code);
  let t = `${op.number} ${op.name}; оборудование — ${equipRu(op.equipment)}.`;
  if (op.rationale) t += ` Обоснование: ${op.rationale}`;
  if (row && (row.transitions || []).length) t += "\nПереходы: " + row.transitions.map(x => x.text).join("; ");
  return t;
}

function openMistake(ctx = {}) {
  const data = ctx.data || (CHAT && CHAT.result) || null;
  const part = (CHAT && CHAT.part) || collectPart();
  const ops = (data && data.route && data.route.operations) || [];
  const model = ctx.model || (data && data.model) || chatModel();
  const box = $("#dlg");
  const opOptions = `<option value="">весь маршрут / ответ в целом</option>` +
    ops.map(o => `<option value="${esc(o.code)}"${ctx.op && ctx.op.code === o.code ? " selected" : ""}>${esc(o.number)} · ${esc(o.name)}</option>`).join("");
  const wrong = ctx.wrong !== undefined ? ctx.wrong.slice(0, 600) : opText(ctx.op, data);
  box.innerHTML = `<div class="dlg narrow" role="dialog" aria-label="Ошибка модели">
    <div class="dlg-head"><h2>Ошибка модели</h2><button type="button" id="mk-x">Закрыть</button></div>
    <div class="dlg-body">
      <label class="f">Операция</label>
      <select id="mk-op"${ops.length ? "" : " disabled"}>${opOptions}</select>
      <div id="mk-sim"></div>
      <label class="f">Что сделала модель</label>
      <textarea id="mk-wrong">${esc(wrong)}</textarea>
      <label class="f">Как правильно (это и станет уроком)</label>
      <textarea id="mk-right" placeholder="Например: обрезку облоя ставят сразу после окончательной штамповки, до термообработки"></textarea>
      <label class="f" style="display:flex;gap:8px;align-items:center;color:var(--ink)">
        <input type="checkbox" id="mk-type" checked style="width:auto">
        только для деталей типа «${esc(partTypeName(part.part_type))}»</label>
      <div class="hint">Модель: ${esc(model || "—")}${part.designation ? " · деталь " + esc(part.designation) : ""}</div>
    </div>
    <div class="dlg-foot"><span class="hint" id="mk-err" style="margin-right:auto;color:var(--warn)"></span>
      <button type="button" id="mk-cancel">Отмена</button>
      <button type="button" class="primary" id="mk-ok">В журнал</button></div></div>`;
  box.hidden = false;

  const similar = () => {
    const code = $("#mk-op").value;
    const same = (LESSONS.lessons || []).filter(x => x.status === "accepted" && code && x.op_code === code &&
      (!x.part_type || x.part_type === part.part_type));
    $("#mk-sim").innerHTML = "";
    for (const x of same.slice(0, 3)) {
      const s = el("div", "lsim", `<b>Урок уже есть:</b> ${esc(x.rule)}`);
      const b = el("button", null, "Это та же ошибка, отметить повтор");
      b.onclick = async () => {
        try { await api("/api/lessons/repeat", { id: x.id, model }); } catch (e) { $("#mk-err").textContent = e.message; return; }
        addEvent("calc", `Журнал ошибок: модель ${shortModel(model)} повторила ошибку, на которую уже есть урок`);
        closeDlg(); loadLessons();
      };
      s.append(el("br"), b);
      $("#mk-sim").append(s);
    }
  };
  similar();
  $("#mk-op").onchange = () => {
    similar();
    const op = ops.find(o => o.code === $("#mk-op").value);
    if (op && ctx.where !== "chat") $("#mk-wrong").value = opText(op, data);
  };
  $("#mk-x").onclick = $("#mk-cancel").onclick = closeDlg;
  $("#mk-ok").onclick = async () => {
    const code = $("#mk-op").value;
    const op = ops.find(o => o.code === code);
    const lesson = {
      where: ctx.where || "route", op_code: code, op_name: op ? op.name : "",
      part_type: $("#mk-type").checked ? part.part_type : "",
      part_type_name: $("#mk-type").checked ? partTypeName(part.part_type) : "",
      wrong: $("#mk-wrong").value, right: $("#mk-right").value, model,
      designation: part.designation || "", chat_id: (CHAT && CHAT.id) || "",
    };
    try { await api("/api/lessons/add", { lesson }); }
    catch (e) { $("#mk-err").textContent = e.message; return; }
    addEvent("calc", `Журнал ошибок: записана ошибка${op ? " в операции " + op.number + " «" + op.name + "»" : ""} — ждёт проверки`);
    closeDlg();
    loadLessons();
  };
  setTimeout(() => $("#mk-right").focus(), 30);
}

function lessonCard(x) {
  const c = el("div", "lcard");
  const scope = [x.op_code ? `«${x.op_name || x.op_code}» (${x.op_code})` : "весь маршрут",
    x.part_type ? `тип: ${x.part_type_name || partTypeName(x.part_type)}` : "любые детали"].join(" · ");
  const reps = (x.repeats || []).filter(r => r.after_accept).length;
  c.innerHTML = `<div class="lh"><b>${esc(scope)}</b>
      <span class="ctag">${esc(x.starter ? "замечание технолога" : (shortModel(x.model) || "—"))}</span>
      <span>${esc(x.starter ? "пришёл с программой" : (x.where === "chat" ? "ответ в чате" : "маршрут"))}${x.designation ? " · " + esc(x.designation) : ""}</span>
      <span style="margin-left:auto">${esc(x.created || "")}</span></div>` +
    (x.wrong ? `<div class="lw"><span>было</span>${esc(x.wrong)}</div>` : "") +
    `<div class="lr"><span>как правильно</span>${esc(x.right)}</div>` +
    `<label class="f" style="font-size:11px;color:var(--ink-3);margin-top:8px;display:block">урок, как его прочитает модель</label>`;
  const rule = el("textarea");
  rule.value = x.rule || "";
  c.append(rule);
  const act = el("div", "lact");
  const btn = (text, fn, cls) => { const b = el("button", cls || null, text); b.type = "button"; b.onclick = fn; act.append(b); };
  const change = async changes => {
    try { await api("/api/lessons/update", { id: x.id, changes }); }
    catch (e) { alertIn(c, e.message); return; }
    await loadLessons(); renderLessons();
  };
  if (x.status !== "accepted") btn("Принять", () => change({ status: "accepted", rule: rule.value }), "primary");
  if (x.status === "accepted") btn("Сохранить урок", () => change({ rule: rule.value }));
  if (x.status !== "rejected") btn(x.status === "accepted" ? "Отключить" : "Отклонить", () => change({ status: "rejected" }));
  if (x.status === "rejected") btn("Вернуть на проверку", () => change({ status: "candidate" }));
  btn("Удалить", async () => {
    if (!confirmIn(act)) return;
    try { await api("/api/lessons/delete", { id: x.id }); } catch (e) { alertIn(c, e.message); return; }
    await loadLessons(); renderLessons();
  });
  act.append(el("span", "hint", x.status === "accepted"
    ? `ушёл модели: ${x.used || 0} раз${reps ? ` · ошибка повторилась после урока: ${reps}` : ""}`
    : (x.status === "candidate" ? "модель этот урок пока не видит" : "в запросы не идёт")));
  c.append(act);
  return c;
}

function confirmIn(act) {
  if (act.dataset.sure) return true;
  act.dataset.sure = "1";
  const b = [...act.querySelectorAll("button")].find(x => x.textContent === "Удалить");
  if (b) { b.textContent = "Точно удалить?"; b.classList.add("primary"); }
  return false;
}

function alertIn(node, text) {
  let n = node.querySelector(".lerr");
  if (!n) { n = el("div", "note warn lerr"); node.append(n); }
  n.textContent = text;
}

function renderLessons() {
  const body = $("#dlg .dlg-body");
  if (!body) return;
  const counts = LESSONS.counts || {};
  body.innerHTML = "";
  const tabs = el("div", "ltabs");
  for (const [key, name] of LESSON_TABS) {
    const n = key === "stats" ? "" : ` (${counts[key] || 0})`;
    const b = el("button", key === LESSON_TAB ? "on" : null, esc(name + n));
    b.type = "button";
    b.onclick = () => { LESSON_TAB = key; renderLessons(); };
    tabs.append(b);
  }
  body.append(tabs);
  if (LESSON_TAB === "stats") {
    const rows = LESSONS.stats || [];
    if (!rows.length) { body.append(el("div", "lempty", "Ошибок пока не отмечали.")); return; }
    const t = el("table", "lstat");
    t.innerHTML = "<thead><tr><th>Модель</th><th>Отмечено ошибок</th><th>Из них уроков</th><th>Повторила после урока</th></tr></thead><tbody>" +
      rows.map(r => `<tr><td>${esc(r.model)}</td><td>${r.marked}</td><td>${r.accepted}</td><td>${r.repeats_after}</td></tr>`).join("") +
      "</tbody>";
    body.append(t);
    return;
  }
  const list = (LESSONS.lessons || []).filter(x => x.status === LESSON_TAB);
  if (!list.length) {
    body.append(el("div", "lempty", {
      candidate: "Новых ошибок нет. Отметить ошибку можно кнопкой «Ошибка» у операции маршрута, у ответа в чате или в шапке чата.",
      accepted: "Принятых уроков пока нет.", rejected: "Отклонённых записей нет." }[LESSON_TAB]));
    return;
  }
  list.forEach(x => body.append(lessonCard(x)));
}

async function openLessons() {
  await loadLessons();
  if (!(LESSONS.counts || {}).candidate && LESSON_TAB === "candidate" && (LESSONS.counts || {}).accepted) LESSON_TAB = "accepted";
  const box = $("#dlg");
  box.innerHTML = `<div class="dlg" role="dialog" aria-label="Журнал ошибок">
    <div class="dlg-head"><h2>Журнал ошибок</h2><button type="button" id="lj-x">Закрыть</button></div>
    <div class="dlg-body"></div></div>`;
  box.hidden = false;
  $("#lj-x").onclick = closeDlg;
  renderLessons();
}

async function boot() {
  await loadState();
  let res = null;
  try { res = await api("/api/settings"); } catch (e) { res = null; }
  SETTINGS = (res && res.settings) || { layout: "A", theme: "system", model: "", use_llm: true,
                                        mode: "stepwise", transitions: true };
  LAYOUTS_READY = (res && res.layouts_ready) || ["A"];
  applyPrefs();
  document.querySelectorAll("#modes input").forEach(i => i.addEventListener("change", savePrefs));
  $("#f-transitions").addEventListener("change", savePrefs);
  $("#btn-settings").onclick = () => openFirstRun(false);
  $("#btn-update").onclick = openUpdate;
  if (SETTINGS.check_updates !== false) checkUpdate(false);
  $("#btn-hist").onclick = toggleHistory;
  syncHeaderHeight();
  $("#btn-part").onclick = () => toggleForm();
  $("#btn-lessons").onclick = openLessons;
  $("#chat-flag").onclick = () => openMistake({ where: "route" });
  $("#dlg").addEventListener("mousedown", e => { if (e.target.id === "dlg") closeDlg(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#dlg").hidden) closeDlg(); });
  loadLessons();

  ["#btn-route", "#btn-calc"].forEach(s => $(s).addEventListener("click", () => {
    if (isLayoutB() || isLayoutC()) toggleForm(false);
  }));
  $("#btn-newchat").onclick = newChat;
  $("#hist-q").oninput = renderHistory;
  initChat();
  newChat();
  refreshHistory();
  if (res && res.first_run) openFirstRun(true);

  setInterval(() => { fetch("/api/ping").catch(() => {}); }, PING_MS);
}

boot();
