// AI 뉴스 대시보드 — 빌드 없는 바닐라 JS
// 데이터: data/articles.json (수집 Lambda가 매시간 갱신)
// 읽음/북마크/메모: localStorage (기기별 저장, 2차에서 Cognito+DynamoDB 동기화 예정)
import { initChat, setChatArticle } from "./chat.js";

const $ = (s) => document.querySelector(s);
const PAGE = 40;
const INDUSTRIES = ["금융", "IT", "제조", "공공", "기타"];

// ── 저장소 ──────────────────────────────────────────────
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { localStorage.setItem(k, JSON.stringify(v)); },
};
const readSet = new Set(store.get("read", []));
const bookmarks = store.get("bookmarks", {}); // id → { memo, savedAt, article }
function saveRead() {
  // 오래된 항목이 무한히 쌓이지 않게 최근 5000개만
  const arr = [...readSet].slice(-5000);
  store.set("read", arr);
}
const saveBookmarks = () => store.set("bookmarks", bookmarks);

// ── 상태 ────────────────────────────────────────────────
let all = [];
let filtered = [];
let shown = 0;
let lastDay = ""; // 날짜 구분선용
let dayCounts = new Map();
let topCompanies = []; // [[이름, 건수]]
const state = Object.assign(
  { industry: "", topic: "", company: "", source: "", period: "7", q: "", unreadOnly: false, bookmarkOnly: false },
  store.get("filters", {}),
);

// ── 유틸 ────────────────────────────────────────────────
function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(e.dataset, v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) e.append(c); // 문자열은 텍스트 노드 → XSS 안전
  return e;
}
const appendAll = (parent, ...children) => { for (const c of children) if (c != null) parent.append(c); };
const safeUrl =(u) => (/^https?:\/\//i.test(u) ? u : "#");
const kstDay = (d) => new Date(d.getTime() + 9 * 3600e3).toISOString().slice(0, 10);

function relTime(iso) {
  const diff = (Date.now() - new Date(iso).getTime()) / 60000;
  if (diff < 1) return "방금";
  if (diff < 60) return `${Math.floor(diff)}분 전`;
  if (diff < 1440) return `${Math.floor(diff / 60)}시간 전`;
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// 1.6px 선 아이콘 (currentColor)
const ICONS = {
  bookmark: "M7 4h10v16l-5-3.6L7 20z",
  memo: "M5 19h3.5L18.5 9 15 5.5 5 15.5zM13.5 7l3.5 3.5",
  read: "M4.5 12.5l4.5 4.5 10-10",
  why: "M5 7h14M5 12h9M5 17h5",
  chat: "M4 5.5h16v10.5H10l-4.5 3.5V16H4z",
  x: "M7 7l10 10M17 7 7 17",
};
function icon(name) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(NS, "path"); p.setAttribute("d", ICONS[name]);
  svg.append(p);
  return svg;
}

const WEEK = ["일", "월", "화", "수", "목", "금", "토"];
const dayOf = (a) => a.day || kstDay(new Date(a.publishedAt));
function dayLabel(day) {
  const [y, m, d] = day.split("-").map(Number);
  const wd = WEEK[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  const md = `${m}월 ${d}일`;
  if (day === kstDay(new Date())) return ["오늘", `${md} ${wd}요일`];
  if (day === kstDay(new Date(Date.now() - 86400e3))) return ["어제", `${md} ${wd}요일`];
  return [md, `${wd}요일`];
}

// ── 필터 ────────────────────────────────────────────────
function inPeriod(a) {
  const today = kstDay(new Date());
  if (state.period === "today") return a.day === today;
  if (state.period === "yesterday") return a.day === kstDay(new Date(Date.now() - 86400e3));
  return Date.now() - new Date(a.publishedAt).getTime() <= Number(state.period) * 86400e3;
}

function matches(a, { skipIndustry = false } = {}) {
  if (!skipIndustry && state.industry && !a.industries.includes(state.industry)) return false;
  if (state.topic && !a.topics.includes(state.topic)) return false;
  if (state.company && !a.companies.includes(state.company)) return false;
  if (state.source && a.source !== state.source) return false;
  if (state.unreadOnly && readSet.has(a.id)) return false;
  if (state.bookmarkOnly && !bookmarks[a.id]) return false;
  if (!state.bookmarkOnly && !inPeriod(a)) return false;
  if (state.q) {
    const hay = `${a.title} ${a.summary} ${a.companies.join(" ")}`.toLowerCase();
    if (!state.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
  }
  return true;
}

function apply() {
  store.set("filters", { ...state, q: "" });
  // 북마크 보기: 스냅샷(7일)에서 빠진 기사도 북마크에 저장된 사본으로 보여준다
  let pool = all;
  if (state.bookmarkOnly) {
    const ids = new Set(all.map((a) => a.id));
    pool = [...all, ...Object.values(bookmarks).map((b) => b.article).filter((a) => a && !ids.has(a.id))];
  }
  filtered = pool.filter((a) => matches(a)).sort((a, b) => b.publishedAt.localeCompare(a.publishedAt));
  // 업종 탭 건수 (업종 외 필터 적용 기준)
  const base = pool.filter((a) => matches(a, { skipIndustry: true }));
  renderTabs(base);
  $("#count").textContent = `${filtered.length.toLocaleString()}건`;
  $("#list").replaceChildren();
  shown = 0;
  lastDay = "";
  dayCounts = new Map();
  for (const a of filtered) { const d = dayOf(a); dayCounts.set(d, (dayCounts.get(d) || 0) + 1); }
  renderMore();
  $("#empty").hidden = filtered.length > 0;
  $("#markAllBtn").disabled = filtered.length === 0;
  renderActiveFilters();
  renderTopCompanies();
}

// ── 렌더링 ──────────────────────────────────────────────
function renderTabs(base) {
  const counts = Object.fromEntries(INDUSTRIES.map((i) => [i, 0]));
  for (const a of base) for (const i of a.industries) if (i in counts) counts[i]++;
  const tab = (value, label, n) =>
    el("button", {
      class: "tab", role: "tab", "aria-selected": String(state.industry === value),
      onclick: () => { state.industry = value; apply(); },
    }, label, el("span", { class: "n" }, n.toLocaleString()));
  $("#industryTabs").replaceChildren(tab("", "전체", base.length), ...INDUSTRIES.map((i) => tab(i, i, counts[i])));
}

function badge(kind, value, onclick) {
  return el("button", { class: `badge ${kind}`, dataset: { v: value }, onclick, title: "이 분류로 필터" }, value);
}
const act = (cls, label, iconName, onclick, extra = {}, text = null) =>
  el("button", { class: `act ${cls}`, "aria-label": label, title: label, onclick, ...extra }, icon(iconName), text ? el("span", {}, text) : null);

function card(a) {
  const isRead = readSet.has(a.id);
  const bm = bookmarks[a.id];
  const li = el("li", { class: `card${isRead ? " read" : ""}`, dataset: { id: a.id } });
  const markRead = () => {
    if (!readSet.has(a.id)) { readSet.add(a.id); saveRead(); }
    li.classList.add("read");
    setChatArticle(a);
  };
  const whyBox = el("div", { class: "why", hidden: true },
    "분류 근거: ",
    ...Object.entries(a.why || {}).flatMap(([k, v]) => [el("code", {}, `${k} ← ${v.join(", ")}`), " "]),
    Object.keys(a.why || {}).length ? null : "매칭된 키워드 없음",
  );
  const sep = () => el("i", { class: "dot-sep", "aria-hidden": "true" });
  appendAll(li,
    el("div", { class: "meta" },
      ...a.industries.map((v) => el("button", { class: "ind", dataset: { v }, title: `${v} 업종만 보기`, onclick: () => { state.industry = v; apply(); } }, v)),
      a.industries.length ? sep() : null,
      el("span", { class: "src" }, a.source),
      sep(),
      el("time", { datetime: a.publishedAt, title: a.publishedAt }, relTime(a.publishedAt)),
      a.dateGuessed ? el("span", { class: "guess", title: "발행 시각이 없어 수집 시각으로 표시" }, "(추정)") : null,
    ),
    el("h2", {}, el("a", { href: safeUrl(a.link), target: "_blank", rel: "noopener noreferrer", onclick: markRead, onauxclick: markRead }, a.title)),
    a.summary ? el("p", { class: "summary" }, a.summary) : null,
    bm?.memo ? el("div", { class: "memo" }, bm.memo) : null,
    whyBox,
    el("div", { class: "card-foot" },
      el("div", { class: "badges" },
        ...a.companies.slice(0, 5).map((v) => badge("co", v, () => { state.company = v; syncControls(); apply(); })),
        ...a.topics.map((v) => badge("tp", v, () => { state.topic = v; syncControls(); apply(); })),
      ),
      el("div", { class: "actions" },
        act(`bm${bm ? " on" : ""}`, bm ? "북마크 해제" : "북마크", "bookmark", () => toggleBookmark(a), { "aria-pressed": String(!!bm) }),
        act("", "메모", "memo", () => editMemo(a)),
        act("", "읽음 전환", "read", () => { readSet.has(a.id) ? readSet.delete(a.id) : readSet.add(a.id); saveRead(); li.classList.toggle("read"); }),
        act("", "분류 근거", "why", () => { whyBox.hidden = !whyBox.hidden; }),
        act("ask", "이 기사 AI에게 묻기", "chat", () => { setChatArticle(a, true); }, {}, "AI에게 묻기"),
      ),
    ),
  );
  return li;
}

function renderMore() {
  const next = filtered.slice(shown, shown + PAGE);
  const nodes = [];
  for (const a of next) {
    const d = dayOf(a);
    if (d !== lastDay) {
      const [title, sub] = dayLabel(d);
      nodes.push(el("li", { class: "day-head", role: "presentation" }, title,
        el("span", {}, sub), el("span", { class: "day-n" }, `${(dayCounts.get(d) || 0).toLocaleString()}건`)));
      lastDay = d;
    }
    nodes.push(card(a));
  }
  $("#list").append(...nodes);
  shown += next.length;
}

// 적용된 필터를 칩으로 보여주고, ×로 하나씩 해제
function renderActiveFilters() {
  const items = [
    ["industry", "업종", state.industry],
    ["topic", "주제", state.topic],
    ["company", "기업", state.company],
    ["source", "매체", state.source],
    ["q", "검색", state.q],
    ["unreadOnly", "보기", state.unreadOnly && "안 읽은 글"],
    ["bookmarkOnly", "보기", state.bookmarkOnly && "북마크"],
  ].filter(([, , v]) => v);
  const box = $("#activeFilters");
  box.hidden = items.length === 0;
  if (!items.length) { box.replaceChildren(); return; }
  box.replaceChildren(
    el("span", { class: "af-label" }, "적용된 필터"),
    ...items.map(([k, label, v]) => el("button", {
      class: "chip", title: "필터 해제", "aria-label": `${label} ${v} 필터 해제`,
      onclick: () => { state[k] = typeof state[k] === "boolean" ? false : ""; syncControls(); apply(); },
    }, `${label} `, el("b", {}, v), icon("x"))),
    ...(items.length > 1 ? [el("button", { class: "link-btn af-clear", onclick: () => $("#resetBtn").click() }, "모두 해제")] : []),
  );
}

function renderTopCompanies() {
  if (!topCompanies.length) return;
  $("#topCompaniesWrap").hidden = false;
  $("#topCompanies").replaceChildren(...topCompanies.map(([v, n], i) => el("li", {},
    el("button", {
      "aria-pressed": String(state.company === v), title: state.company === v ? "선택 해제" : `${v} 기사만 보기`,
      onclick: () => { state.company = state.company === v ? "" : v; syncControls(); apply(); },
    }, el("span", { class: "rk" }, String(i + 1)), el("span", { class: "nm" }, v), el("span", { class: "ct" }, n.toLocaleString())),
  )));
}

function rerenderCard(id) {
  const old = document.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
  const a = all.find((x) => x.id === id) || bookmarks[id]?.article;
  if (old && a) old.replaceWith(card(a));
}

function toggleBookmark(a) {
  if (bookmarks[a.id]) delete bookmarks[a.id];
  else bookmarks[a.id] = { memo: "", savedAt: new Date().toISOString(), article: a };
  saveBookmarks();
  state.bookmarkOnly ? apply() : rerenderCard(a.id);
}

function editMemo(a) {
  const dlg = $("#memoDialog");
  $("#memoTitle").textContent = a.title;
  $("#memoText").value = bookmarks[a.id]?.memo || "";
  dlg.onclose = () => {
    if (dlg.returnValue !== "save") return;
    const memo = $("#memoText").value.trim();
    bookmarks[a.id] = { savedAt: new Date().toISOString(), ...bookmarks[a.id], memo, article: a }; // 메모하면 자동 저장
    saveBookmarks();
    rerenderCard(a.id);
  };
  dlg.showModal();
}

// ── 컨트롤 ──────────────────────────────────────────────
function fillOptions(sel, values, label) {
  sel.replaceChildren(el("option", { value: "" }, label), ...values.map(([v, n]) => el("option", { value: v }, `${v} (${n})`)));
}
function countBy(fn) {
  const m = new Map();
  for (const a of all) for (const v of fn(a)) m.set(v, (m.get(v) || 0) + 1);
  return [...m.entries()].sort((x, y) => y[1] - x[1]);
}
function syncControls() {
  $("#topic").value = state.topic; $("#company").value = state.company; $("#source").value = state.source;
  $("#period").value = state.period; $("#q").value = state.q;
  $("#unreadOnly").checked = state.unreadOnly; $("#bookmarkOnly").checked = state.bookmarkOnly;
}

function bindControls() {
  let t;
  $("#q").addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => { state.q = e.target.value.trim(); apply(); }, 200); });
  $("#topic").onchange = (e) => { state.topic = e.target.value; apply(); };
  $("#source").onchange = (e) => { state.source = e.target.value; apply(); };
  $("#period").onchange = (e) => { state.period = e.target.value; apply(); };
  $("#company").addEventListener("change", (e) => {
    const v = e.target.value.trim();
    const names = new Set(all.flatMap((a) => a.companies));
    state.company = names.has(v) ? v : ""; // 목록에 없는 값은 무시
    if (!state.company) e.target.value = "";
    apply();
  });
  $("#unreadOnly").onchange = (e) => { state.unreadOnly = e.target.checked; apply(); };
  $("#bookmarkOnly").onchange = (e) => { state.bookmarkOnly = e.target.checked; apply(); };
  $("#resetBtn").onclick = () => {
    Object.assign(state, { industry: "", topic: "", company: "", source: "", period: "7", q: "", unreadOnly: false, bookmarkOnly: false });
    syncControls(); apply();
  };
  $("#markAllBtn").onclick = () => {
    if (!confirm(`보이는 ${filtered.length}건을 모두 읽음으로 표시할까요?`)) return;
    filtered.forEach((a) => readSet.add(a.id)); saveRead(); apply();
  };
  $("#themeBtn").onclick = () => {
    const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = t; localStorage.setItem("theme", t);
  };
  $("#filterToggle").onclick = () => {
    const open = $("#sidebar").classList.toggle("open");
    $("#filterToggle").setAttribute("aria-expanded", String(open));
  };
  document.querySelector("#empty [data-reset]")?.addEventListener("click", () => $("#resetBtn").click());
  // "/" 로 검색창 포커스
  document.addEventListener("keydown", (e) => {
    if (e.key !== "/" || e.isComposing || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.target.closest?.("input, textarea, select, [contenteditable]")) return;
    e.preventDefault(); $("#q").focus();
  });
  new IntersectionObserver((es) => { if (es[0].isIntersecting && shown < filtered.length) renderMore(); }, { rootMargin: "400px" })
    .observe($("#sentinel"));
}

// ── 시작 ────────────────────────────────────────────────
async function main() {
  bindControls();
  const config = await fetch("config.json", { cache: "no-store" }).then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
  initChat(config);
  try {
    const res = await fetch("data/articles.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(res.status);
    const snap = await res.json();
    all = snap.articles;
    $("#updated").textContent = `업데이트 ${relTime(snap.generatedAt)} · 최근 ${snap.windowDays}일 ${snap.count.toLocaleString()}건`;
    const bad = (snap.feeds || []).filter((f) => !f.ok);
    $("#feedHealth").textContent = ` · 수집 피드 ${snap.feeds.length - bad.length}/${snap.feeds.length} 정상`;
    if (bad.length) $("#feedHealth").title = bad.map((f) => `${f.name}: ${f.error}`).join("\n");
  } catch (e) {
    $("#loading").hidden = true;
    $("#empty").hidden = false;
    $("#empty").textContent = "기사 데이터를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";
    return;
  }
  $("#loading").hidden = true;
  fillOptions($("#topic"), countBy((a) => a.topics), "주제 전체");
  fillOptions($("#source"), countBy((a) => [a.source]), "매체 전체");
  const companyCounts = countBy((a) => a.companies);
  $("#companyList").replaceChildren(...companyCounts.map(([v, n]) => el("option", { value: v }, `${n}건`)));
  topCompanies = companyCounts.slice(0, 8);
  syncControls();
  apply();
}
main();
