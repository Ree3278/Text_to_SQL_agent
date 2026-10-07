"use strict";

// Everything the model or the database returns is put on the page with textContent, never innerHTML,
// so a hostile answer (or a hostile row of data) cannot inject markup or script.

const $ = (id) => document.getElementById(id);
const els = {
  form: $("ask-form"), question: $("question"), button: $("ask-button"),
  resting: $("resting"), result: $("result"), answer: $("answer"),
  route: $("route"), routeNote: $("route-note"),
  sqlBlock: $("sql-block"), sqlTitle: $("sql-title"), sql: $("sql"), copy: $("copy-sql"),
  tableBlock: $("table-block"), table: $("table"), tableNote: $("table-note"),
  meta: $("meta"), error: $("error"),
};

const STEP_LABELS = { write: "Wrote SQL", check: "Checked it", run: "Ran it", explain: "Explained" };
const numberFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
let busy = false;

/* ---------- the route line ---------- */

function setRoute(states, labels = {}) {
  for (const li of els.route.children) {
    const step = li.dataset.step;
    li.dataset.state = states[step];
    li.firstElementChild.textContent = labels[step] || STEP_LABELS[step];
  }
}

function isDeclined(d) {
  return d.columns.length === 1 && d.columns[0] === "note" && d.rows.length === 1 &&
    String(d.rows[0][0]).toLowerCase() === "unanswerable";
}

function outcome(d) {
  const allDone = { write: "done", check: "done", run: "done", explain: "done" };
  if (d.blocked) {
    return { kind: "blocked", states: { write: "done", check: "stopped", run: "skipped", explain: "skipped" }, labels: { check: "Blocked" } };
  }
  if (d.failed) {
    return { kind: "failed", states: { write: "done", check: "done", run: "stopped", explain: "skipped" }, labels: { run: "Failed" } };
  }
  if (isDeclined(d)) {
    return { kind: "declined", states: { ...allDone, run: "skipped" }, labels: { run: "No query" } };
  }
  return { kind: "ok", states: allDone, labels: {} };
}

/* ---------- rendering ---------- */

function formatCell(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return numberFormat.format(v);
  return String(v);
}

function renderTable(d) {
  const table = els.table;
  table.replaceChildren();
  const numeric = d.columns.map((_, i) => d.rows.length > 0 && d.rows.every((r) => typeof r[i] === "number"));

  const head = table.createTHead().insertRow();
  d.columns.forEach((name, i) => {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = name;
    if (numeric[i]) th.className = "num";
    head.appendChild(th);
  });

  const body = table.createTBody();
  for (const row of d.rows) {
    const tr = body.insertRow();
    row.forEach((v, i) => {
      const td = tr.insertCell();
      td.textContent = formatCell(v);
      if (numeric[i]) td.className = "num";
    });
  }

  const n = d.rows.length;
  els.tableNote.textContent = d.truncated
    ? `Showing the first ${n} rows. There are more.`
    : `${n} ${n === 1 ? "row" : "rows"}.`;
}

function dbError(answer) {
  // The agent's failure message ends with the database error; show only its first line.
  const msg = String(answer).replace(/^I couldn't run that query\.\s*/, "");
  return msg.split("\n")[0].slice(0, 160);
}

function renderResult(d) {
  const o = outcome(d);

  els.answer.dataset.kind = o.kind;
  els.answer.textContent = o.kind === "failed"
    ? "I couldn't get a working query for that. Try rephrasing the question."
    : d.answer;
  setRoute(o.states, o.labels);
  els.route.removeAttribute("data-loading");

  let note = "";
  if (o.kind === "declined") note = "The data has nothing on this, so no query ran.";
  else if (o.kind === "failed" && d.attempts > 1) note = "The model tried twice and the database rejected both queries. " + dbError(d.answer);
  else if (o.kind === "ok" && d.attempts > 1) note = "The first query hit a database error, so the model rewrote it once.";
  els.routeNote.textContent = note;
  els.routeNote.hidden = note === "";

  const showSql = o.kind !== "declined";
  els.sqlBlock.hidden = !showSql;
  els.sqlTitle.textContent = o.kind === "blocked" ? "The SQL that was blocked"
    : o.kind === "failed" ? "The SQL that failed" : "The SQL";
  els.sql.textContent = d.sql;

  const showTable = o.kind === "ok";
  els.tableBlock.hidden = !showTable;
  if (showTable) renderTable(d);

  const seconds = (d.latency_ms / 1000).toFixed(1);
  const tokens = numberFormat.format(d.input_tokens + d.output_tokens);
  els.meta.textContent = `Took ${seconds} seconds and ${tokens} tokens.`;
}

function renderWorking() {
  els.result.hidden = false;
  els.answer.dataset.kind = "working";
  els.answer.textContent = "Working on it…";
  setRoute({ write: "pending", check: "pending", run: "pending", explain: "pending" });
  els.route.setAttribute("data-loading", "");
  els.routeNote.hidden = true;
  els.sqlBlock.hidden = true;
  els.tableBlock.hidden = true;
  els.meta.textContent = "";
}

/* ---------- talking to the API ---------- */

function errorMessage(status, detail) {
  if (status === 0) return "Can't reach the server. Check your connection and try again.";
  if (status === 422) return "Questions need to be between 3 and 300 characters.";
  if (status === 429) return "You're asking too fast. Wait a minute and try again.";
  if (typeof detail === "string" && detail) return detail;
  return "Something went wrong. Please try again.";
}

function showError(message) {
  els.error.textContent = message;
  els.error.hidden = false;
}

function setBusy(on) {
  busy = on;
  els.button.disabled = on || els.resting.hidden === false;
  els.button.textContent = on ? "Working…" : "Ask";
}

async function ask(question) {
  question = question.trim();
  if (busy || question.length < 3) return;

  els.error.hidden = true;
  setBusy(true);
  renderWorking();

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 70000);
  try {
    const res = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
      signal: controller.signal,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      els.result.hidden = true;
      showError(errorMessage(res.status, data.detail));
      if (res.status === 503) checkStatus();
      return;
    }
    renderResult(data);
  } catch (err) {
    els.result.hidden = true;
    showError(err.name === "AbortError" ? "That took too long. Please try again." : errorMessage(0));
  } finally {
    clearTimeout(timer);
    setBusy(false);
  }
}

async function checkStatus() {
  try {
    const res = await fetch("/status");
    const s = await res.json();
    els.resting.hidden = s.demo_available !== false;
    els.button.disabled = !els.resting.hidden;
  } catch {
    /* the status banner is a nicety; ask() reports real failures */
  }
}

/* ---------- wiring ---------- */

els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  ask(els.question.value);
});

els.question.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    els.form.requestSubmit();
  }
});

for (const btn of document.querySelectorAll(".example")) {
  btn.addEventListener("click", () => {
    els.question.value = btn.textContent;
    ask(btn.textContent);
  });
}

els.copy.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(els.sql.textContent);
    els.copy.textContent = "Copied";
  } catch {
    els.copy.textContent = "Copy failed";
  }
  setTimeout(() => { els.copy.textContent = "Copy SQL"; }, 1500);
});

checkStatus();
