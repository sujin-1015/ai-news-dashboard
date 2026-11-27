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
let windowDays = 7;
let filtered = []; // 필터를 통과한 기사
let groups = []; // 화면 카드 = 비슷한 기사 묶음 [{ rep, others }]
let groupOf = new Map(); // 대표 기사 id → 같은 묶음의 나머지 기사
let shown = 0;
let lastDay = ""; // 날짜 구분선용
let dayCounts = new Map();
const DEFAULTS = { industry: "", topic: "", company: "", keyword: "", source: "", period: "7", q: "", unreadOnly: false, bookmarkOnly: false };
const state = Object.assign({ ...DEFAULTS }, store.get("filters", {}));

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

// 요약은 완결된 문장으로만 보여준다. 예전 수집분·북마크 사본처럼 중간에서 잘린 요약('…')은
// 마지막 완결 문장까지만, 완결 문장이 없으면 숨긴다.
function displaySummary(s) {
  if (!s) return "";
  if (/[.?!]["”’']?$/.test(s)) return s;
  const m = s.match(/^.*[다요][.]["”’']?(?=\s)|^.*[?!](?=\s)/s);
  return m ? m[0].trim() : "";
}

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

// skip: 이 필터들은 무시 (예: 업종 탭 건수는 업종 필터 없이, 순위는 기업·이슈 필터 없이 센다)
function matches(a, skip = []) {
  const on = (k) => !skip.includes(k);
  if (on("industry") && state.industry && !a.industries.includes(state.industry)) return false;
  if (state.topic && !a.topics.includes(state.topic)) return false;
  if (on("company") && state.company && !a.companies.includes(state.company)) return false;
  if (on("keyword") && state.keyword && !(a.keywords || []).includes(state.keyword)) return false;
  if (state.source && a.source !== state.source) return false;
  if (state.unreadOnly && readSet.has(a.id)) return false;
  if (state.bookmarkOnly && !bookmarks[a.id]) return false;
  if (on("period") && !state.bookmarkOnly && !inPeriod(a)) return false;
  if (state.q) {
    const hay = `${a.title} ${a.summary} ${a.companies.join(" ")} ${(a.keywords || []).join(" ")}`.toLowerCase();
    if (!state.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
  }
  return true;
}

// 비슷한 기사 묶음(수집기가 붙인 cluster)으로 카드 하나씩. 입력은 최신순이어야 한다.
function groupArticles(list) {
  const m = new Map();
  for (const a of list) {
    const k = a.cluster || a.id;
    if (!m.has(k)) m.set(k, []);
    m.get(k).push(a);
  }
  return [...m.values()].map((items) => {
    const rep = items.find((a) => a.clusterRep) || items[0];
    return { rep, others: items.filter((a) => a !== rep), items };
  });
}

function currentPool() {
  // 북마크 보기: 스냅샷(7일)에서 빠진 기사도 북마크에 저장된 사본으로 보여준다
  if (!state.bookmarkOnly) return all;
  const ids = new Set(all.map((a) => a.id));
  return [...all, ...Object.values(bookmarks).map((b) => b.article).filter((a) => a && !ids.has(a.id))];
}

function apply() {
  store.set("filters", { ...state, q: "" });
  const pool = currentPool();
  filtered = pool.filter((a) => matches(a)).sort((a, b) => b.publishedAt.localeCompare(a.publishedAt));
  groups = groupArticles(filtered).sort((a, b) => b.rep.publishedAt.localeCompare(a.rep.publishedAt));
  groupOf = new Map(groups.map((g) => [g.rep.id, g.others]));
  // 업종 탭 건수 (업종 외 필터 적용 기준)
  renderTabs(groupArticles(pool.filter((a) => matches(a, ["industry"]))));
  $("#count").textContent = groups.length === filtered.length
    ? `${filtered.length.toLocaleString()}건`
    : `${groups.length.toLocaleString()}개 이슈 · 기사 ${filtered.length.toLocaleString()}건`;
  $("#count").title = "같은 사건을 다룬 여러 매체 기사는 카드 하나로 묶어 보여줍니다";
  $("#list").replaceChildren();
  shown = 0;
  lastDay = "";
  dayCounts = new Map();
  for (const g of groups) { const d = dayOf(g.rep); dayCounts.set(d, (dayCounts.get(d) || 0) + 1); }
  renderMore();
  $("#empty").hidden = groups.length > 0;
  $("#markAllBtn").disabled = filtered.length === 0;
  renderActiveFilters();
  renderRanks(pool);
}

// ── 렌더링 ──────────────────────────────────────────────
function renderTabs(baseGroups) {
  const counts = Object.fromEntries(INDUSTRIES.map((i) => [i, 0]));
  for (const g of baseGroups) {
    for (const i of new Set(g.items.flatMap((a) => a.industries))) if (i in counts) counts[i]++;
  }
  const tab = (value, label, n) =>
    el("button", {
      class: "tab", role: "tab", "aria-selected": String(state.industry === value),
      onclick: () => { state.industry = value; apply(); },
    }, label, el("span", { class: "n" }, n.toLocaleString()));
  $("#industryTabs").replaceChildren(tab("", "전체", baseGroups.length), ...INDUSTRIES.map((i) => tab(i, i, counts[i])));
}

function badge(kind, value, onclick) {
  return el("button", { class: `badge ${kind}`, dataset: { v: value }, onclick, title: "이 분류로 필터" }, value);
}
const act = (cls, label, iconName, onclick, extra = {}, text = null) =>
  el("button", { class: `act ${cls}`, "aria-label": label, title: label, onclick, ...extra }, icon(iconName), text ? el("span", {}, text) : null);

// "외 N개 매체" — 같은 사건을 다룬 다른 기사 링크 (펼침)
function relatedBox(others, setRead) {
  if (!others.length) return null;
  const srcs = [...new Set(others.map((o) => o.source))];
  const list = el("ul", { class: "related-list", hidden: true },
    ...others.map((o) => el("li", {},
      el("span", { class: "src" }, o.source),
      el("a", { href: safeUrl(o.link), target: "_blank", rel: "noopener noreferrer", onclick: () => setRead(o), onauxclick: () => setRead(o) }, o.title),
      el("time", { datetime: o.publishedAt }, relTime(o.publishedAt)),
    )));
  const label = `외 ${others.length}건 · ${srcs.slice(0, 3).join(", ")}${srcs.length > 3 ? ` 등 ${srcs.length}곳` : ""}`;
  const btn = el("button", {
    class: "related-toggle", "aria-expanded": "false",
    onclick: () => { list.hidden = !list.hidden; btn.setAttribute("aria-expanded", String(!list.hidden)); },
  }, label);
  return el("div", { class: "related" }, btn, list);
}

function card(a, others = groupOf.get(a.id) || []) {
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
    displaySummary(a.summary) ? el("p", { class: "summary" }, displaySummary(a.summary)) : null,
    relatedBox(others, (o) => { if (!readSet.has(o.id)) { readSet.add(o.id); saveRead(); } }),
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
  const next = groups.slice(shown, shown + PAGE);
  const nodes = [];
  for (const { rep: a, others } of next) {
    const d = dayOf(a);
    if (d !== lastDay) {
      const [title, sub] = dayLabel(d);
      nodes.push(el("li", { class: "day-head", role: "presentation" }, title,
        el("span", {}, sub), el("span", { class: "day-n" }, `${(dayCounts.get(d) || 0).toLocaleString()}건`)));
      lastDay = d;
    }
    nodes.push(card(a, others));
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
    ["keyword", "이슈", state.keyword],
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

// 묶음(이슈) 단위로 센다: 같은 사건을 5개 매체가 써도 1
function countGroups(gs, valuesOf) {
  const m = new Map();
  for (const g of gs) for (const v of new Set(g.items.flatMap(valuesOf))) m.set(v, (m.get(v) || 0) + 1);
  return m;
}

// 순위: 지금 고른 필터 기준. 단, 자기 자신(이슈 순위는 이슈 필터, 기업 순위는 기업 필터)은 빼고 센다
// → "해킹"을 고르면 기업 순위가 해킹 이슈의 기업들로 바뀐다
function renderRanks(pool) {
  const groupsSkipping = (key) => groupArticles(pool.filter((a) => matches(a, [key])));
  // 1건짜리는 순위에서 뺀다 (기사가 적은 시간대에 우연히 한 번 나온 말이 올라오지 않게)
  const top = (m) => [...m.entries()].filter(([, n]) => n >= 2).sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0])).slice(0, 8);

  // 급상승: 오늘 이슈 수 vs 그 전 날들의 하루 평균 (기간 필터와 무관하게 스냅샷 전체 기준)
  const today = kstDay(new Date());
  const allGs = groupArticles(pool.filter((a) => matches(a, ["keyword", "period"])));
  const todayN = countGroups(allGs.filter((g) => dayOf(g.rep) === today), (a) => a.keywords || []);
  const prevN = countGroups(allGs.filter((g) => dayOf(g.rep) < today), (a) => a.keywords || []);
  const prevDays = Math.max(1, windowDays - 1);
  const rising = (k) => {
    const t = todayN.get(k) || 0, avg = (prevN.get(k) || 0) / prevDays;
    return t >= 3 && t >= 2.5 * avg ? `오늘 ${t}개 이슈 · 이전 하루 평균 ${avg.toFixed(1)}개` : "";
  };

  renderRank("keyword", top(countGroups(groupsSkipping("keyword"), (a) => a.keywords || [])), rising);
  renderRank("company", top(countGroups(groupsSkipping("company"), (a) => a.companies)));
}

function renderRank(key, list, risingOf = () => "") {
  const wrap = $(key === "keyword" ? "#topKeywordsWrap" : "#topCompaniesWrap");
  const ol = $(key === "keyword" ? "#topKeywords" : "#topCompanies");
  wrap.hidden = list.length === 0 && !state[key];
  ol.replaceChildren(...list.map(([v, n], i) => {
    const up = risingOf(v);
    return el("li", {},
      el("button", {
        "aria-pressed": String(state[key] === v),
        title: [state[key] === v ? "선택 해제" : `${v} 기사만 보기`, up].filter(Boolean).join("\n"),
        onclick: () => { state[key] = state[key] === v ? "" : v; syncControls(); apply(); },
      },
      el("span", { class: "rk" }, String(i + 1)),
      el("span", { class: "nm" }, v, up ? el("span", { class: "up", "aria-label": "급상승" }, "▲") : null),
      el("span", { class: "ct" }, n.toLocaleString())));
  }));
}

function rerenderCard(id) {
  const old = document.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
  const a = all.find((x) => x.id === id) || bookmarks[id]?.article;
  if (old && a) old.replaceWith(card(a, groupOf.get(id) || []));
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
    Object.assign(state, DEFAULTS);
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
  new IntersectionObserver((es) => { if (es[0].isIntersecting && shown < groups.length) renderMore(); }, { rootMargin: "400px" })
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
    windowDays = snap.windowDays || 7;
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
  syncControls();
  apply();
}
main();
