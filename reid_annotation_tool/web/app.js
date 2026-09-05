"use strict";

const KIND_LABEL = { cross_track: "跨轨迹", track_purity: "轨迹纯净度" };
const VERDICTS = {
  cross_track: [
    ["same", "同一目标", "1"],
    ["different", "不同目标", "2"],
    ["unclear", "无法判断", "3"],
  ],
  track_purity: [
    ["same", "轨迹干净", "1"],
    ["different", "混入其他目标", "2"],
    ["unclear", "无法判断", "3"],
  ],
};
const CAPTION = { same: "同一目标", different: "不同目标", unclear: "无法判断" };

const state = {
  rows: [], index: -1, total: 0, offset: 0, limit: 60,
  auto: true, busy: false, loading: false, appState: null,
  openConflict: null, openConflictKind: null, openConflictIdentities: [],
};

const $ = (selector) => document.querySelector(selector);
const el = (tag, attributes = {}, children = []) => {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
};

function status(message, failed = false) {
  const node = $("#status");
  node.textContent = message;
  node.title = message;          // the header truncates; the tooltip does not
  node.style.color = failed ? "var(--error)" : "var(--muted)";
}

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json();
}

/* ---------------------------------------------------------------- state */

async function loadState() {
  const value = await api("/api/state");
  state.appState = value;
  $("#total").textContent = value.total;
  $("#labelled").textContent = value.labelled;
  $("#bar").style.width = value.total ? `${(100 * value.labelled) / value.total}%` : "0";

  const kinds = Object.keys(value.kinds);
  fillSelect($("#filter-kind"), kinds, (kind) => KIND_LABEL[kind] || kind, "全部类型");
  fillSelect($("#filter-split"), value.splits, (split) => split, "全部划分");

  paintBadge(value.conflicts);
}

function paintBadge(summary) {
  const badge = $("#conflict-badge");
  const errors = summary.errors || 0;
  badge.textContent = summary.stale ? `${errors}?` : `${errors}`;
  badge.classList.toggle("hot", errors > 0);
  badge.title = summary.stale
    ? "标注已改变，冲突结果待重新检测（打开“冲突”页即可刷新）"
    : "当前冲突数量";
}

function fillSelect(node, values, caption, placeholder) {
  if (node.dataset.filled === JSON.stringify(values)) return;
  node.dataset.filled = JSON.stringify(values);
  const current = node.value;
  node.replaceChildren(el("option", { value: "", text: placeholder }));
  for (const value of values) node.append(el("option", { value, text: caption(value) }));
  node.value = current;
}

/* ---------------------------------------------------------------- queue */

function filters() {
  return {
    kind: $("#filter-kind").value,
    split: $("#filter-split").value,
    status: $("#filter-status").value,
    q: $("#filter-query").value.trim(),
  };
}

async function loadQueue(reset = true) {
  if (state.loading) return;
  state.loading = true;
  if (reset) { state.rows = []; state.offset = 0; state.index = -1; }
  // A status-filtered result set shrinks as labels are saved. Drop rows that no
  // longer match before deriving the next offset, otherwise unseen rows are skipped.
  if (!reset) {
    const activeId = state.rows[state.index] && state.rows[state.index].candidate_id;
    state.rows = state.rows.filter(rowMatchesFilters);
    state.index = activeId ? state.rows.findIndex((row) => row.candidate_id === activeId) : -1;
  }
  state.offset = state.rows.length;
  const query = new URLSearchParams({ ...filters(), offset: state.offset, limit: state.limit });
  let value;
  try {
    value = await api(`/api/candidates?${query}`);
  } finally {
    state.loading = false;
  }
  state.total = value.total;
  const seen = new Set(state.rows.map((row) => row.candidate_id));
  state.rows = state.rows.concat(value.rows.filter((row) => !seen.has(row.candidate_id)));
  state.offset += value.rows.length;
  renderQueue();
  $("#load-more").hidden = state.rows.length >= state.total;
  $("#load-more").textContent = `加载更多（${state.rows.length}/${state.total}）`;
  if (reset && state.rows.length) select(0);
  if (!state.rows.length) $("#card").replaceChildren(el("div", { class: "empty", text: "没有符合条件的候选。" }));
}

function rowMatchesFilters(row) {
  const selected = filters();
  const label = row.review_label || "";
  if (selected.kind && row.kind !== selected.kind) return false;
  if (selected.split && row.split !== selected.split) return false;
  if (selected.status === "pending" && label) return false;
  if (selected.status && selected.status !== "pending" && label !== selected.status) return false;
  return !selected.q || JSON.stringify(row).toLowerCase().includes(selected.q.toLowerCase());
}

function renderQueue() {
  const list = $("#queue");
  list.replaceChildren(...state.rows.map((row, index) => el("li", {
    class: index === state.index ? "active" : "",
    "data-index": index,
    onclick: () => select(index),
  }, [
    el("span", { class: "who", text: row.kind === "track_purity"
      ? row.person_id1 : `${row.person_id1} ↔ ${row.person_id2}` }),
    el("span", { class: `dot ${row.review_label || ""}` }),
    el("span", { class: "meta", text:
      `${KIND_LABEL[row.kind] || row.kind} · ${row.split} · distance ${fmt(row.cosine ?? row.distance)}`
      + (row.review_label ? ` · ${CAPTION[row.review_label]}` : "") }),
  ])));
  const active = list.querySelector("li.active");
  if (active) active.scrollIntoView({ block: "nearest" });
}

const fmt = (value) => (value === undefined || value === "" ? "-" : Number(value).toFixed(3));

/* ----------------------------------------------------------------- card */

function select(index) {
  if (index < 0 || index >= state.rows.length) return;
  state.index = index;
  renderQueue();
  renderCard(state.rows[index]);
  if (index >= state.rows.length - 5 && state.rows.length < state.total) loadQueue(false);
}

function gallery(row, side) {
  const identity = side === 1 ? row.person_id1 : row.person_id2;
  const meta = (side === 1 ? row.meta1 : row.meta2) || {};
  const images = (side === 1 ? row.gallery1 : row.gallery2) || [];
  const evidence = side === 1 ? row.img1 : row.img2;
  return el("div", { class: "side" }, [
    el("h3", { text: identity }),
    el("div", { class: "sub", text:
      `${meta.crops || images.length} 张 · ${meta.video || ""} · ${fmtSpan(meta)}` }),
    el("div", { class: "pics" }, images.map((path) => el("img", {
      src: `/files/${path}`, loading: "lazy", alt: path,
      class: path === evidence ? "evidence" : "",
      onclick: () => zoom(path),
    }))),
  ]);
}

function fmtSpan(meta) {
  if (!meta.start) return "";
  const start = Number(meta.start), end = Number(meta.end);
  return `${(end - start).toFixed(1)}s`;
}

function renderCard(row) {
  const purity = row.kind === "track_purity";
  const head = el("div", { class: "card-head" }, [
    el("h2", { text: purity ? row.person_id1 : `${row.person_id1}  ↔  ${row.person_id2}` }),
    el("span", { class: "chip" }, [document.createTextNode("类型 "), el("b", { text: KIND_LABEL[row.kind] || row.kind })]),
    el("span", { class: "chip" }, [document.createTextNode("划分 "), el("b", { text: row.split })]),
    el("span", { class: "chip" }, [document.createTextNode("距离/余弦 "), el("b", { text: fmt(row.cosine ?? row.distance) })]),
    el("span", { class: "chip" }, [document.createTextNode("间隔 "), el("b", { text: `${row.time_gap_sec || "0"}s` })]),
    el("span", { class: "chip", text: row.candidate_id }),
  ]);

  const question = el("p", { class: "question", text: purity
    ? "这两张裁剪来自同一条轨迹。它们是同一个目标吗？若不是，说明跟踪中途换了目标，整条轨迹会被丢弃。"
    : "模型认为这两条轨迹相似。它们是同一个目标吗？只有确信时才选“同一目标”。" });

  const evidence = el("div", { class: "evidence-row" }, [row.img1, row.img2]
    .filter(Boolean).map((path) => el("img", { src: `/files/${path}`, alt: path,
      onclick: () => zoom(path) })));

  const sides = purity
    ? el("div", { class: "sides single" }, [gallery(row, 1)])
    : el("div", { class: "sides" }, [gallery(row, 1), gallery(row, 2)]);

  const buttons = VERDICTS[row.kind] || VERDICTS.cross_track;
  const review = el("div", { class: "review" }, [
    ...buttons.map(([value, caption, key]) => el("button", {
      class: `verdict ${value} ${row.review_label === value ? "active" : ""}`,
      "data-value": value,
      onclick: () => setLabel(value),
      text: `${caption} (${key})`,
    })),
    el("span", { class: "saved", id: "saved",
      text: row.review_label ? `已保存：${CAPTION[row.review_label]}` : "" }),
    el("label", { class: "auto" }, [
      el("input", { type: "checkbox", id: "auto", ...(state.auto ? { checked: "checked" } : {}),
        onchange: (event) => { state.auto = event.target.checked; } }),
      document.createTextNode("标注后自动跳到下一个"),
    ]),
  ]);

  const notes = el("textarea", { id: "notes", placeholder: "备注（可选，按 Ctrl+Enter 保存）",
    onkeydown: (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) saveNotes();
    },
    onblur: saveNotes,
  });
  notes.value = row.review_notes || "";

  $("#card").replaceChildren(head, question, evidence, sides, review, notes);
}

async function setLabel(value) {
  const row = state.rows[state.index];
  if (!row || state.busy) return;
  state.busy = true;
  const saved = $("#saved");
  saved.classList.remove("failed");
  saved.textContent = "保存中…";
  try {
    const result = await api("/api/label", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidate_id: row.candidate_id, review_label: value,
        review_notes: $("#notes") ? $("#notes").value : undefined }),
    });
    Object.assign(row, result.row);
    applyState(result.state);
    saved.textContent = `已保存：${CAPTION[value]}`;
    for (const button of document.querySelectorAll(".verdict")) {
      button.classList.toggle("active", button.dataset.value === value);
    }
    renderQueue();
    if (state.auto) {
      state.busy = false;
      next();
      return;
    }
  } catch (error) {
    saved.textContent = `保存失败：${error.message}`;
    saved.classList.add("failed");
    status("保存失败", true);
  }
  state.busy = false;
}

function applyState(value) {
  state.appState = value;
  $("#labelled").textContent = value.labelled;
  $("#total").textContent = value.total;
  $("#bar").style.width = value.total ? `${(100 * value.labelled) / value.total}%` : "0";
  paintBadge(value.conflicts);
  status(`待审核 ${value.pending}`);
}

async function saveNotes() {
  const row = state.rows[state.index];
  const notes = $("#notes");
  if (!row || !notes || (row.review_notes || "") === notes.value) return;
  await api("/api/label", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ candidate_id: row.candidate_id,
      review_label: row.review_label || "", review_notes: notes.value }),
  }).then((result) => { Object.assign(row, result.row); status("备注已保存"); })
    .catch((error) => status(`备注保存失败：${error.message}`, true));
}

const next = () => select(Math.min(state.index + 1, state.rows.length - 1));
const previous = () => select(Math.max(state.index - 1, 0));

/* ------------------------------------------------------------ conflicts */

// Distinct hues for chain nodes. Colour marks identity but is never the only
// marker: endpoints also carry a textual ⚠ label, so the page stays readable
// without colour vision.
const PALETTE = ["#ff5c7a", "#4fc36b", "#ffd23f", "#6c8cff", "#ff9046", "#c77dff",
  "#4fd8e8", "#ff7ae0", "#b9e34f", "#ffb3c1", "#4fb3a5", "#d6c2ff", "#c98f4e"];

const METRIC_NOTES = {
  missing_metrics: "部分边没有任何相似度记录 —— 无法排序，仅显示各边原始数值。",
  mixed_metrics: "各边的相似度来自不同轮次/不同模型，量纲互不可比 —— 跨轮次不可排序，仅显示原始数值。",
  ambiguous_metrics: "各边同时存在多个可比度量，无法唯一确定最弱边 —— 仅显示原始数值。",
};

// One request per identity for the whole page: 12 edges share 13 identities,
// so the gallery fetches are memoised as promises.
const identityCache = new Map();
function fetchIdentity(identity) {
  if (!identityCache.has(identity)) {
    identityCache.set(identity,
      api(`/api/identity/${encodeURIComponent(identity)}`)
        .catch(() => ({ images: [], meta: {} })));
  }
  return identityCache.get(identity);
}

// Even sampling, mirroring the server's spread(): 4 crops must cover the whole
// track, not just its first frames.
function sampleEven(values, maximum) {
  if (values.length <= maximum) return values;
  const step = (values.length - 1) / (maximum - 1);
  return Array.from({ length: maximum }, (_, index) => values[Math.round(index * step)]);
}

// A chain is now edited in place, so the page keeps a handle on whichever
// conflict is open: after a verdict the report is recomputed and the SAME
// chain re-rendered from the fresh data, instead of dumping the reviewer back
// at the top of the list halfway through a 12-edge review.
const conflictKey = (item) => [item.kind, (item.conflict_detail
  && item.conflict_detail.group_key) || ((item.conflict_detail
  && item.conflict_detail.endpoints) || item.identities || []).join(">")].join("|");

async function fetchConflicts(refresh) {
  const value = await api(`/api/conflicts${refresh ? "?refresh=1" : ""}`);
  $("#conflict-summary").textContent =
    `错误 ${value.errors} · 警告 ${value.warnings} · 待审核 ${value.pending_reviews}`
    + (value.conflicts.length ? "" : " · 数据集逻辑自洽");
  $("#conflict-list").replaceChildren(...value.conflicts.map(renderConflict));
  paintBadge({ errors: value.errors, stale: false });
  return value;
}

async function loadConflicts(refresh = false) {
  closeConflictDetail();
  $("#conflict-summary").textContent = "检测中…";
  try {
    await fetchConflicts(refresh);
  } catch (error) {
    $("#conflict-summary").textContent = `检测失败：${error.message}`;
  }
}

// A chain that vanished after a verdict is the success case, not an empty
// page: say so and go back to the list. But a grouped conflict's group_key is
// the identity union of its member paths, so resolving one member re-shapes
// the key even though the rest of the group is still unresolved -- an exact
// key miss must fall back to identity overlap before it is read as "solved".
async function refreshOpenConflict() {
  const key = state.openConflict;
  const kind = state.openConflictKind;
  const priorIdentities = state.openConflictIdentities;
  const value = await fetchConflicts(true);
  const exact = key && value.conflicts.find((item) => conflictKey(item) === key);
  if (exact) {
    showConflictDetail(exact, true);
    return;
  }
  const remaining = priorIdentities.length ? value.conflicts.filter((item) =>
    item.kind === kind
    && (item.identities || []).some((identity) => priorIdentities.includes(identity))) : [];
  if (remaining.length) {
    showConflictDetail(remaining[0], false);
    if (remaining.length > 1) {
      status(`该组已拆分为 ${remaining.length} 个待审冲突，已跳转到其中一个`);
    }
  } else {
    closeConflictDetail();
    status("该冲突链已消除");
  }
}

// The list stays a summary: message, kind and the one-line chain. Everything
// actionable (crops, provenance, verdict buttons) lives on the detail page, so
// a card click cannot collide with inner button clicks.
function renderConflict(item) {
  const paths = (item.detail && item.detail.same_paths) || [];
  const chain = paths.length > 1
    ? el("div", { class: "chain", text: `同一冲突链：${paths.length} 条 different 矛盾 · `
      + `${item.identities.length} 个相关身份（重复 same 边已合并）` })
    : item.detail && item.detail.same_path
      ? el("div", { class: "chain", text: `同一链：${item.detail.same_path.join(" → ")}` })
      : null;
  const detail = item.conflict_detail;
  if (!detail) {
    // No chain semantics (e.g. split_leakage): flat witness summary, not clickable.
    const links = (item.chain || []).map((link) => el("div", { class: `link ${link.type}` }, [
      el("span", { class: "witness locked", text: link.witness }),
      el("span", { class: "link-detail", text: link.detail || "" }),
    ]));
    return el("div", { class: `conflict ${item.severity}` }, [
      el("h3", { text: item.message }),
      el("div", { class: "kind", text: `${item.kind} · ${item.severity}` }),
      chain,
      el("div", { class: "witnesses" }, links),
    ]);
  }
  return el("div", {
    class: `conflict ${item.severity} clickable`,
    onclick: () => showConflictDetail(item),
  }, [
    el("h3", { text: item.message }),
    el("div", { class: "kind", text: `${item.kind} · ${item.severity}` }),
    chain,
    el("div", { class: "hint", text: `链上 ${detail.edges.length} 条 same 边 · `
      + `${detail.edges.filter((edge) => edge.decision.verdict).length} 条已有判定 · 点击逐边复核并直接改判` }),
  ]);
}

/* -------------------------------------------------- conflict detail page */

function chipFor(identity, colors, endpoints) {
  const chip = el("span", { class: "id-chip", text: identity });
  const color = colors.get(identity);
  if (color) { chip.style.borderColor = color; chip.style.color = color; }
  if (endpoints.has(identity)) {
    chip.classList.add("endpoint");
    chip.append(el("b", { class: "endpoint-mark", text: " ⚠冲突端点" }));
  }
  return chip;
}

function galleryBlock(identity, colors, endpoints) {
  const block = el("div", { class: "edge-side" }, [
    chipFor(identity, colors, endpoints),
    el("div", { class: "pics" }),
    el("div", { class: "sub" }),
  ]);
  fetchIdentity(identity).then((value) => {
    const pics = block.querySelector(".pics");
    for (const imagePath of sampleEven(value.images || [], 4)) {
      pics.append(el("img", { src: `/files/${imagePath}`, loading: "lazy", alt: imagePath,
        onclick: () => zoom(imagePath) }));
    }
    const meta = value.meta || {};
    if (meta.video) {
      block.querySelector(".sub").textContent =
        `${meta.crops || (value.images || []).length} 张 · ${meta.video} · ${meta.split || ""}`;
    }
  });
  return block;
}

function answerLine(answer) {
  const parts = [`${answer.round} · ${answer.candidate_id} → ${CAPTION[answer.label] || answer.label}`];
  if (answer.source) parts.push(`来源 ${answer.source}`);
  if (answer.metric_name !== undefined) {
    parts.push(`${answer.metric_name}=${Number(answer.metric_value).toFixed(4)}`);
  }
  parts.push(answer.editable ? "当前轮（上方按钮可直接改判）"
    : `${answer.file}（历史轮次，该文件不会被重写）`);
  const line = el("div", { class: `prov answer answer-${answer.label}` }, [
    el("span", { class: "answer-text", text: parts.join(" · ") }),
  ]);
  if (answer.notes) line.append(el("span", { class: "note", text: `备注：${answer.notes}` }));
  return line;
}

/* ------------------------------------------- judging a relation in place */

// The chain IS a list of relations, so each one is answered where it is read.
// Short captions on purpose: these buttons sit twelve to a page and the long
// queue wording ("同一目标") stops being scannable at that density.
const RELATIONS = [["same", "相同"], ["different", "不同"], ["unclear", "不确定"]];
const SHORT = { same: "相同", different: "不同", unclear: "不确定" };

function decisionBar(left, right, decision, caption) {
  return el("div", { class: `decision${decision.conflicting ? " conflicting" : ""}` }, [
    el("span", { class: "decision-caption", text: caption }),
    ...RELATIONS.map(([value, label]) => el("button", {
      class: `verdict ${value} ${decision.verdict === value ? "active" : ""}`,
      "data-verdict": value,
      onclick: (event) => applyVerdict(event.currentTarget, left, right, value, decision),
      text: label,
    })),
    el("span", { class: "decision-origin", text: decisionOrigin(decision) }),
  ]);
}

const evidenceNames = (rows) => [...new Set(rows.map((row) => row.evidence))].join("、");

// Where the highlighted button comes from, and what a click would cost. A
// reviewer who cannot see this cannot tell an untouched edge from one a past
// round already answered — which is the whole reason the chain is on screen.
function decisionOrigin(decision) {
  const parts = [];
  if (decision.conflicting) parts.push(`⚠ 判定不唯一：${decision.origin}`);
  else if (decision.verdict) parts.push(`已判定「${SHORT[decision.verdict]}」· ${decision.origin}`);
  else parts.push(`未判定 · ${decision.origin}`);
  if (decision.archives.length) {
    parts.push(`改判会归档 ${decision.archives.length} 行已烤进 pairs.csv 的旧人判`);
  }
  if (decision.physical.length) parts.push(`物理证据 ${evidenceNames(decision.physical)} 不可推翻`);
  return parts.join(" · ");
}

// Only the irreversible and the ineffective cases interrupt: archiving baked
// rows cannot be undone from the page, and a verdict fighting physical
// evidence will not make the conflict go away. Ordinary re-judgement just saves.
function confirmVerdict(left, right, verdict, decision) {
  if (decision.archives.length) {
    const lines = decision.archives
      .map((row) => `行 ${row.line}（${row.evidence}，label=${row.label}）`).join("\n");
    return confirm(`${left} ↔ ${right}\n\n`
      + `将从 pairs.csv 移除并归档 ${decision.archives.length} 行旧人判：\n${lines}\n\n`
      + `新判定「${SHORT[verdict]}」作为当前轮候选入队，走正常复核管线。\n`
      + `物理证据行不受影响。确定改判吗？`);
  }
  if (decision.physical.length) {
    return confirm(`${left} ↔ ${right} 的 ${decision.physical.length} 行证据`
      + `（${evidenceNames(decision.physical)}）来自抽取阶段，网页无法推翻。\n\n`
      + `判定「${SHORT[verdict]}」会被记录，但这些行仍留在 pairs.csv 里，`
      + `冲突可能不会因此消失。\n\n继续吗？`);
  }
  return true;
}

async function applyVerdict(button, left, right, verdict, decision) {
  if (state.busy || !confirmVerdict(left, right, verdict, decision)) return;
  const bar = button.parentElement;
  state.busy = true;
  bar.classList.add("busy");
  status(`保存 ${left} ↔ ${right} …`);
  let saved = false;
  try {
    const result = await api("/api/relation", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ person_id1: left, person_id2: right, verdict }),
    });
    saved = true;
    applyState(result.state);
    status(describeEvent(result.event));
    await refreshOpenConflict();     // re-render this chain from fresh data
  } catch (error) {
    // the reviewer has to know whether the verdict landed: a stale view is a
    // very different problem from a lost judgement
    status(`${saved ? "已保存，但冲突刷新失败" : "保存失败"}：${error.message}`, true);
  } finally {
    bar.classList.remove("busy");   // detached after a successful re-render
    state.busy = false;
  }
}

function describeEvent(event) {
  const parts = [`${event.pair.join(" ↔ ")} → ${SHORT[event.verdict]}`];
  if (event.removed.length) parts.push(`归档 ${event.removed.length} 行旧人判`);
  parts.push(event.injected ? `新候选 ${event.candidate_id} 入队` : `候选 ${event.candidate_id} 已更新`);
  return parts.join(" · ");
}

const SVG_NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attributes = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

// A compact causal overview before the evidence cards. Solid edges are the
// assumptions that merge tracks; dashed red edges are the constraints those
// assumptions violate. The layered BFS layout keeps a long chain readable and
// still handles branches without requiring a graph library.
function relationGraph(detail, colors) {
  const nodes = detail.path || [];
  if (nodes.length < 2) return null;
  const endpointPairs = detail.endpoint_pairs || [detail.endpoints];
  const adjacency = new Map(nodes.map((identity) => [identity, []]));
  detail.edges.forEach((edge, index) => {
    adjacency.get(edge.left).push([edge.right, index]);
    adjacency.get(edge.right).push([edge.left, index]);
  });
  const root = endpointPairs[0][0];
  const level = new Map([[root, 0]]);
  const queue = [root];
  while (queue.length) {
    const current = queue.shift();
    for (const [next] of adjacency.get(current) || []) {
      if (!level.has(next)) {
        level.set(next, level.get(current) + 1);
        queue.push(next);
      }
    }
  }
  let lastLevel = Math.max(0, ...level.values());
  for (const identity of nodes) if (!level.has(identity)) level.set(identity, ++lastLevel);
  const columns = new Map();
  for (const identity of nodes) {
    const value = level.get(identity);
    if (!columns.has(value)) columns.set(value, []);
    columns.get(value).push(identity);
  }
  const maxRows = Math.max(...[...columns.values()].map((values) => values.length));
  const nodeWidth = 250, nodeHeight = 42, xGap = 305, yGap = 82, padding = 34;
  // Different-edges are routed through a dedicated lane strip above every
  // node row (see below) instead of arcing over the row itself: a smooth arc
  // only clears node height near its own midpoint, so for a chain longer than
  // a couple of columns most of it is painted over by intermediate nodes.
  const laneCount = Math.min(endpointPairs.length, 4);
  const laneGap = 14;
  const laneTop = 10;
  const gridTop = laneTop + laneCount * laneGap + 22;
  const width = Math.max(620, (lastLevel + 1) * xGap + padding * 2);
  const height = Math.max(150, maxRows * yGap + gridTop + padding);
  const positions = new Map();
  for (const [column, identities] of columns) {
    identities.forEach((identity, row) => positions.set(identity, {
      x: padding + column * xGap,
      y: gridTop + (row + (maxRows - identities.length) / 2) * yGap,
    }));
  }

  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, width, height,
    role: "img", "aria-label": "same 关系与 different 矛盾图" });
  const title = svgEl("title");
  title.textContent = "实线表示 same，红色虚线表示 proven different";
  svg.append(title);
  for (const [index, edge] of detail.edges.entries()) {
    const first = positions.get(edge.left), second = positions.get(edge.right);
    const line = svgEl("line", { x1: first.x + nodeWidth / 2, y1: first.y + nodeHeight / 2,
      x2: second.x + nodeWidth / 2, y2: second.y + nodeHeight / 2,
      class: "graph-same-edge" });
    const hit = svgEl("line", { x1: first.x + nodeWidth / 2, y1: first.y + nodeHeight / 2,
      x2: second.x + nodeWidth / 2, y2: second.y + nodeHeight / 2,
      class: "graph-edge-hit", tabindex: "0", role: "button",
      "aria-label": `${edge.left} 与 ${edge.right} 的 same 边；点击查看证据` });
    const jump = () => {
      const card = document.getElementById(`conflict-edge-${index}`);
      if (!card) return;
      card.scrollIntoView({ behavior: "smooth", block: "center" });
      card.classList.add("graph-target");
      setTimeout(() => card.classList.remove("graph-target"), 1600);
    };
    hit.addEventListener("click", jump);
    hit.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); jump(); }
    });
    svg.append(line, hit);
  }
  for (const [index, pair] of endpointPairs.entries()) {
    const first = positions.get(pair[0]), second = positions.get(pair[1]);
    if (!first || !second) continue;
    const x1 = first.x + nodeWidth / 2, x2 = second.x + nodeWidth / 2;
    // The lane sits above gridTop, i.e. above every node's top edge in the
    // whole graph, so the horizontal run cannot be hidden behind any node
    // regardless of how many columns it spans.
    const lane = laneTop + (index % laneCount) * laneGap;
    const path = svgEl("path", {
      d: `M ${x1} ${first.y} L ${x1} ${lane} L ${x2} ${lane} L ${x2} ${second.y}`,
      class: "graph-different-edge",
    });
    svg.append(path);
  }
  for (const identity of nodes) {
    const position = positions.get(identity);
    const group = svgEl("g", { class: "graph-node" });
    const rect = svgEl("rect", { x: position.x, y: position.y,
      width: nodeWidth, height: nodeHeight, rx: 9,
      style: `--node-color:${colors.get(identity) || "#8992a3"}` });
    const text = svgEl("text", { x: position.x + 12, y: position.y + 26 });
    text.textContent = identity;
    const exact = svgEl("title");
    exact.textContent = identity;
    group.append(rect, text, exact);
    svg.append(group);
  }
  return el("section", { class: "relation-graph" }, [
    el("div", { class: "graph-head" }, [
      el("b", { text: "关系因果图" }),
      el("span", { class: "graph-legend same", text: "━ same（点击线段跳到复核）" }),
      el("span", { class: "graph-legend different", text: "┄ proven different" }),
    ]),
    el("p", { text: "沿实线可从任一红色虚线的一端走到另一端，就是本组冲突的因果链。" }),
    el("div", { class: "graph-scroll" }, [svg]),
  ]);
}

function edgeCard(edge, index, detail, colors, endpoints) {
  const weakest = detail.metric_status === "sortable" && detail.weakest_index === index;
  const decision = edge.decision;
  const head = el("div", { class: "edge-head" }, [
    el("span", { class: "edge-index", text: `边 ${index + 1}/${detail.edges.length}` }),
    chipFor(edge.left, colors, endpoints),
    el("span", { class: "edge-eq", text: "=same=" }),
    chipFor(edge.right, colors, endpoints),
    el("span", { class: `edge-state ${decision.conflicting ? "conflicting" : decision.verdict}`,
      text: decision.conflicting ? "判定不唯一"
        : decision.verdict ? `已判定 ${SHORT[decision.verdict]}` : "未判定" }),
    weakest ? el("span", { class: "weak-badge",
      text: "⚠ 同模型同度量下的最弱边，建议优先复查" }) : null,
  ]);
  const sides = el("div", { class: "edge-sides" }, [
    galleryBlock(edge.left, colors, endpoints),
    galleryBlock(edge.right, colors, endpoints),
  ]);
  const provenance = el("div", { class: "provenance" });
  const positives = edge.base_rows.filter((row) => Number(row.label) === 1);
  const negatives = edge.base_rows.filter((row) => Number(row.label) === 0);
  for (const row of positives) {
    provenance.append(el("div", { class: "prov base",
      text: `pairs.csv 行 ${row.line} · label=1 · evidence ${row.evidence} · batch ${row.batch || "-"}` }));
  }
  for (const row of negatives) {
    provenance.append(el("div", { class: "prov base neg",
      text: `pairs.csv 行 ${row.line} · label=0（与本边直接矛盾）· evidence ${row.evidence} · batch ${row.batch || "-"}` }));
  }
  for (const answer of edge.answers) provenance.append(answerLine(answer));
  if (!edge.base_rows.length && !edge.answers.length) {
    provenance.append(el("div", { class: "prov", text: "未找到该边的任何出处记录。" }));
  }
  return el("div", { id: `conflict-edge-${index}`,
    class: `edge-card ${weakest ? "weakest" : ""}` },
    [head, sides, decisionBar(edge.left, edge.right, decision, "这两条轨迹是："), provenance]);
}

// Merge every physical evidence source known for the two chain ends, whatever
// check produced the conflict: overlap seconds, covisible frames, negative
// manifest rows and human "different" verdicts all say the same thing.
function contradictionBanner(detail, contradiction, colors, endpoints, index, total) {
  const c = contradiction || {};
  const pair = c.endpoints || detail.endpoints;
  const facts = [];
  if (c.temporal) {
    facts.push(`两端轨迹在 ${c.temporal.video} 中同时出现，时间重叠 ${c.temporal.overlap_sec}s —— 同一个人不可能同时是两条轨迹`);
  }
  if (c.covisible) {
    facts.push(`covisibility.csv：两端在 ${c.covisible.video} 同帧共现 ${c.covisible.frames} 帧`);
  }
  if (c.negative_row_count) {
    const byEvidence = {};
    for (const row of c.negative_rows || []) {
      (byEvidence[row.evidence] = byEvidence[row.evidence] || []).push(row.line);
    }
    const parts = Object.entries(byEvidence).map(([evidence, lines]) =>
      `${evidence} ×${lines.length}（行 ${lines.slice(0, 12).join(", ")}${lines.length > 12 ? " …" : ""}）`);
    facts.push(`pairs.csv 共 ${c.negative_row_count} 行 label=0 负样本：${parts.join("；")}`);
  }
  for (const answer of c.different_answers || []) {
    facts.push(`人工判定不同：${answer.round} · ${answer.candidate_id}`);
  }
  if (!facts.length) facts.push("未在数据里找到两端的直接负证据（矛盾仅由冲突检测规则推得）。");
  return el("div", { class: "contradiction" }, [
    el("h3", {}, [
      document.createTextNode("⚠ 矛盾端点："),
      chipFor(pair[0], colors, endpoints),
      document.createTextNode(" ↔ "),
      chipFor(pair[1], colors, endpoints),
      total > 1 ? document.createTextNode(`　(${index + 1}/${total})`) : null,
    ]),
    el("ul", {}, facts.map((fact) => el("li", { text: fact }))),
    el("p", { class: "verdict-line",
      text: `以上证据证明两端不同，而下方 ${detail.edges.length} 条 same 边把它们连成了同一身份 —— 至少有一条 same 边是错的。同色 chip = 同一条轨迹，可据此追踪 A=B、B=C…的传递路径。` }),
    decisionBar(pair[0], pair[1], c.decision, "这两个端点是："),
  ]);
}

function showConflictDetail(item, keepScroll = false) {
  const detail = item.conflict_detail;
  if (!detail) return;
  state.openConflict = conflictKey(item);
  state.openConflictKind = item.kind;
  state.openConflictIdentities = item.identities || [];
  const panel = $("#tab-conflicts");
  const scroll = panel.scrollTop;
  const colors = new Map(detail.path.map((identity, index) =>
    [identity, PALETTE[index % PALETTE.length]]));
  const endpointPairs = detail.endpoint_pairs || [detail.endpoints];
  const endpoints = new Set(endpointPairs.flat());
  const contradictions = detail.contradictions || [detail.contradiction];
  let metricNote = METRIC_NOTES[detail.metric_status] || "";
  if (detail.metric_status === "sortable" && detail.metric_key) {
    const key = detail.metric_key;
    // "same model" is proven by content hash when the recorded file is still
    // readable; a bare path match is announced as the weaker claim it is.
    const byHash = key.model.startsWith("sha256:");
    metricNote = `全链 ${detail.edges.length} 条边由同一已记录模型、同一度量（${key.name}）衡量`
      + `（${key.model_path || key.model}，${byHash ? "按模型文件内容 hash 核对" : "仅按记录路径比较，模型文件不可读、未能核对内容"}），已标出最弱边（仅按支持该边的 same 记录排序）。`;
  }
  const nodes = [
    el("div", { class: "detail-toolbar" }, [
      el("button", { text: "← 返回冲突列表", onclick: closeConflictDetail }),
      el("span", { class: "kind", text: `${item.kind} · ${item.severity}` }),
    ]),
    el("h3", { class: "detail-title", text: item.message }),
    relationGraph(detail, colors),
    ...contradictions.map((contradiction, index) =>
      contradictionBanner(detail, contradiction, colors, endpoints,
        index, contradictions.length)),
    metricNote ? el("p", { class: "metric-note", text: metricNote }) : null,
    ...detail.edges.map((edge, index) => edgeCard(edge, index, detail, colors, endpoints)),
  ];
  $("#conflict-detail").replaceChildren(...nodes.filter(Boolean));
  $("#conflict-list").hidden = true;
  $("#conflict-detail").hidden = false;
  // a re-render after a verdict must not throw the reviewer back to the top
  panel.scrollTop = keepScroll ? scroll : 0;
}

function closeConflictDetail() {
  const container = $("#conflict-detail");
  if (!container) return;
  state.openConflict = null;
  state.openConflictKind = null;
  state.openConflictIdentities = [];
  container.hidden = true;
  container.replaceChildren();
  $("#conflict-list").hidden = false;
}

/* --------------------------------------------------- config / models / jobs */

// List-typed config values are edited as a comma-separated line rather than
// raw JSON -- every list in the closed sections is short and either numeric
// or short strings (class ids, split ratios, calibration points).
function parseListInput(text) {
  return text.split(",").map((token) => token.trim()).filter((token) => token !== "")
    .map((token) => (token !== "" && Number.isFinite(Number(token)) ? Number(token) : token));
}
const formatListValue = (value) => (value || []).join(", ");

function collectTypedValues(fields, inputs) {
  const patch = {};
  for (const [key, meta] of Object.entries(fields)) {
    const input = inputs[key];
    if (meta.type === "bool") patch[key] = input.checked;
    else if (meta.type === "int") patch[key] = parseInt(input.value, 10) || 0;
    else if (meta.type === "float") patch[key] = parseFloat(input.value) || 0;
    else if (meta.type === "list") patch[key] = parseListInput(input.value);
    else patch[key] = input.value;
  }
  return patch;
}

async function saveConfigPatch(section, patch, note) {
  note.textContent = "保存中…";
  note.className = "save-note";
  try {
    await api(`/api/config/${section}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    note.textContent = "已保存";
    note.className = "save-note ok";
  } catch (error) {
    note.textContent = `保存失败：${error.message}`;
    note.className = "save-note failed";
  }
}

function typedSectionForm(section, fields, values) {
  const inputs = {};
  const grid = el("div", { class: "grid" });
  for (const [key, meta] of Object.entries(fields)) {
    const current = values[key] !== undefined ? values[key] : meta.default;
    let input;
    if (meta.type === "bool") {
      input = el("input", { type: "checkbox", ...(current ? { checked: "checked" } : {}) });
    } else if (meta.type === "int" || meta.type === "float") {
      input = el("input", { type: "number", step: meta.type === "int" ? "1" : "any", value: current });
    } else if (meta.type === "list") {
      input = el("input", { type: "text", value: formatListValue(current) });
    } else {
      input = el("input", { type: "text", value: current ?? "" });
    }
    inputs[key] = input;
    grid.append(el("div", { class: "config-field" }, [el("label", { text: key }), input]));
  }
  const note = el("span", { class: "save-note" });
  return el("form", {
    class: "config-section",
    onsubmit: (event) => { event.preventDefault(); saveConfigPatch(section, collectTypedValues(fields, inputs), note); },
  }, [
    el("h3", { text: section }), grid,
    el("div", { class: "save-row" }, [el("button", { type: "submit", text: "保存" }), note]),
  ]);
}

// `pipeline` (and any future open section besides `models`, which gets its
// own tab): the key set is private to the user's script, so this tool has no
// schema to build a typed form from -- raw JSON is the fallback the README
// already documents for custom pipelines.
function rawSectionForm(section, values) {
  const textarea = el("textarea", { class: "raw-json", rows: "5" });
  textarea.value = JSON.stringify(values, null, 2);
  const note = el("span", { class: "save-note" });
  return el("form", {
    class: "config-section",
    onsubmit: (event) => {
      event.preventDefault();
      let patch;
      try {
        patch = JSON.parse(textarea.value);
      } catch (error) {
        note.textContent = `JSON 格式错误：${error.message}`;
        note.className = "save-note failed";
        return;
      }
      saveConfigPatch(section, patch, note);
    },
  }, [
    el("h3", { text: `${section}（原始 JSON，本工具不校验这些键）` }), textarea,
    el("div", { class: "save-row" }, [el("button", { type: "submit", text: "保存" }), note]),
  ]);
}

async function loadConfig() {
  try {
    const [config, schema] = await Promise.all([api("/api/config"), api("/api/config/schema")]);
    const container = $("#config-forms");
    container.replaceChildren(...Object.entries(schema)
      .filter(([section]) => section !== "models")
      .map(([section, fields]) => (config.open_sections.includes(section)
        ? rawSectionForm(section, config.sections[section] || {})
        : typedSectionForm(section, fields, config.sections[section] || {}))));
  } catch (error) {
    $("#config-forms").replaceChildren(el("div", { class: "empty", text: `加载失败：${error.message}` }));
  }
}

async function loadModels() {
  try {
    renderModels(await api("/api/models"));
  } catch (error) {
    $("#model-list").replaceChildren(el("div", { class: "empty", text: `加载失败：${error.message}` }));
  }
}

function renderModels(models) {
  const list = $("#model-list");
  const names = Object.keys(models);
  if (!names.length) {
    list.replaceChildren(el("div", { class: "empty", text: "还没有命名模型。" }));
    return;
  }
  list.replaceChildren(...names.map((name) => {
    const entry = models[name];
    return el("div", { class: "model-row" }, [
      el("span", { class: "name", text: name }),
      el("span", { class: "path", text: `${entry.path} · ${entry.framework} · ${entry.device}` }),
      el("button", { text: "删除", onclick: () => removeModel(name) }),
    ]);
  }));
}

async function removeModel(name) {
  if (!confirm(`删除模型 ${name}？只删除 models: 节里的这条记录，不会删除权重文件。`)) return;
  try {
    await api(`/api/models/${encodeURIComponent(name)}`, { method: "DELETE" });
    await loadModels();
  } catch (error) {
    status(`删除失败：${error.message}`, true);
  }
}

async function submitModel(event) {
  event.preventDefault();
  const name = $("#model-name").value.trim();
  const path = $("#model-path").value.trim();
  if (!name || !path) return;
  try {
    await api(`/api/models/${encodeURIComponent(name)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, device: $("#model-device").value }),
    });
    $("#model-add").reset();
    await loadModels();
  } catch (error) {
    status(`保存失败：${error.message}`, true);
  }
}

const JOB_STATUS_LABEL = { queued: "排队中", running: "运行中", done: "完成", failed: "失败" };
let jobsPollTimer = null;
let selectedJobId = null;
let lastJobs = [];

async function loadJobs() {
  try {
    const value = await api("/api/jobs");
    lastJobs = value.jobs;
    renderJobStageButtons(value.stages);
    renderJobList(lastJobs);
    const running = lastJobs.some((job) => job.status === "running" || job.status === "queued");
    $("#job-badge").hidden = !running;
    // The one deliberate polling loop in this app: a job's completion is an
    // external async event with no user action to hang a refetch off, unlike
    // every other interaction here (label save, verdict, config save).
    if (running && !jobsPollTimer) jobsPollTimer = setInterval(loadJobs, 2000);
    if (!running && jobsPollTimer) { clearInterval(jobsPollTimer); jobsPollTimer = null; }
    if (selectedJobId) {
      const job = lastJobs.find((item) => item.id === selectedJobId);
      if (job) renderJobLog(job);
    }
  } catch (error) {
    $("#job-list").replaceChildren(el("li", { text: `加载失败：${error.message}` }));
  }
}

function renderJobStageButtons(stages) {
  const container = $("#job-stage-buttons");
  if (container.dataset.filled === JSON.stringify(stages)) return;
  container.dataset.filled = JSON.stringify(stages);
  container.replaceChildren(...stages.map((stageName) => el("button", {
    text: `运行 ${stageName}`, onclick: () => startJob(stageName),
  })));
}

function renderJobList(jobs) {
  $("#job-list").replaceChildren(...jobs.slice().reverse().map((job) => el("li", {
    class: job.id === selectedJobId ? "active" : "",
    onclick: () => selectJob(job),
  }, [
    el("span", { class: "job-stage", text: job.stage }),
    el("span", { class: `job-status ${job.status}`, text: JOB_STATUS_LABEL[job.status] || job.status }),
    el("span", { class: "job-time", text: job.created_at || "" }),
  ])));
}

function selectJob(job) {
  selectedJobId = job.id;
  renderJobList(lastJobs);
  renderJobLog(job);
}

function renderJobLog(job) {
  const lines = [
    `# ${job.stage} · ${JOB_STATUS_LABEL[job.status] || job.status}`,
    `created  ${job.created_at || "-"}`, `started  ${job.started_at || "-"}`,
    `finished ${job.finished_at || "-"}`, "",
  ];
  if (job.error) lines.push(`错误：${job.error}`, "");
  lines.push(...(job.log || []));
  if (job.result) lines.push("", `结果：${JSON.stringify(job.result)}`);
  $("#job-log").textContent = lines.join("\n");
}

async function startJob(stageName) {
  const payload = {
    dry_run: $("#job-dry-run").checked, apply: $("#job-apply").checked,
    strict: $("#job-strict").checked,
  };
  try {
    const job = await api(`/api/jobs/${stageName}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    selectedJobId = job.id;
    status(`已启动任务 ${stageName} (${job.id})`);
    await loadJobs();
  } catch (error) {
    status(`启动失败：${error.message}`, true);
  }
}

/* ----------------------------------------------------------------- shell */

function showTab(name) {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("active", tab.dataset.tab === name);
  }
  for (const panel of document.querySelectorAll(".tab-panel")) {
    panel.classList.toggle("active", panel.id === `tab-${name}`);
  }
  if (name === "conflicts") loadConflicts();
  else if (name === "config") loadConfig();
  else if (name === "models") loadModels();
  else if (name === "jobs") loadJobs();
}

function zoom(path) {
  $("#zoom-image").src = `/files/${path}`;
  $("#zoom").hidden = false;
}

function bind() {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => showTab(tab.dataset.tab));
  }
  for (const id of ["#filter-kind", "#filter-split", "#filter-status"]) {
    $(id).addEventListener("change", () => loadQueue(true));
  }
  let timer = null;
  $("#filter-query").addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => loadQueue(true), 250);
  });
  $("#load-more").addEventListener("click", () => loadQueue(false));
  $("#recheck").addEventListener("click", () => loadConflicts(true));
  $("#model-add").addEventListener("submit", submitModel);
  $("#zoom").addEventListener("click", () => { $("#zoom").hidden = true; });

  document.addEventListener("keydown", (event) => {
    if (event.target.matches("input, textarea, select")) {
      if (event.key === "Escape") event.target.blur();
      return;
    }
    const key = event.key.toLowerCase();
    if (key === "escape") { $("#zoom").hidden = true; return; }
    // The conflict page is now an editing surface of its own; a stray "2"
    // there used to label whatever the review queue happened to have selected.
    if (!$("#tab-review").classList.contains("active")) return;
    if (key === "j" || event.key === "ArrowDown") { event.preventDefault(); next(); }
    else if (key === "k" || event.key === "ArrowUp") { event.preventDefault(); previous(); }
    else if (key === "1" || key === "s") setLabel("same");
    else if (key === "2" || key === "d") setLabel("different");
    else if (key === "3" || key === "u") setLabel("unclear");
    else if (key === "n" && $("#notes")) { event.preventDefault(); $("#notes").focus(); }
    else if (key === "z") {
      const row = state.rows[state.index];
      if (row && row.img1) zoom(row.img1);
    }
  });
}

async function main() {
  bind();
  try {
    await loadState();
    await loadQueue(true);
    status(`待审核 ${state.appState.pending}`);
  } catch (error) {
    status(`加载失败：${error.message}`, true);
  }
  // So the 任务 badge is accurate even before the reviewer ever opens that
  // tab -- e.g. a training job kicked off earlier is still visibly running.
  loadJobs().catch(() => {});
}

main();
