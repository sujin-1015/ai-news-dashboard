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
  renderMore();
  $("#empty").hidden = filtered.length > 0;
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
  appendAll(li,
    el("h2", {}, el("a", { href: safeUrl(a.link), target: "_blank", rel: "noopener noreferrer", onclick: markRead, onauxclick: markRead }, a.title)),
    el("div", { class: "meta" },
      el("span", {}, a.source),
      el("time", { datetime: a.publishedAt, title: a.publishedAt }, relTime(a.publishedAt) + (a.dateGuessed ? " (추정)" : "")),
    ),
    a.summary ? el("p", { class: "summary" }, a.summary) : null,
    el("div", { class: "badges" },
      ...a.industries.map((v) => badge("ind", v, () => { state.industry = v; apply(); })),
      ...a.topics.map((v) => badge("tp", v, () => { state.topic = v; syncControls(); apply(); })),
      ...a.companies.slice(0, 5).map((v) => badge("co", v, () => { state.company = v; syncControls(); apply(); })),
    ),
    bm?.memo ? el("div", { class: "memo" }, bm.memo) : null,
    whyBox,
    el("div", { class: "actions" },
      el("button", { class: bm ? "on" : "", onclick: () => toggleBookmark(a), "aria-pressed": String(!!bm) }, bm ? "★ 저장됨" : "☆ 저장"),
      el("button", { onclick: () => editMemo(a) }, "✎ 메모"),
      el("button", { onclick: () => { readSet.has(a.id) ? readSet.delete(a.id) : readSet.add(a.id); saveRead(); li.classList.toggle("read"); } },
        "◐ 읽음 전환"),
      el("button", { onclick: () => { whyBox.hidden = !whyBox.hidden; } }, "🔍 분류 근거"),
      el("button", { onclick: () => { setChatArticle(a, true); } }, "💬 이 기사 물어보기"),
    ),
  );
  return li;
}

function renderMore() {
  const next = filtered.slice(shown, shown + PAGE);
  $("#list").append(...next.map(card));
  shown += next.length;
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
    $("#empty").hidden = false;
    $("#empty").textContent = "기사 데이터를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";
    return;
  }
  fillOptions($("#topic"), countBy((a) => a.topics), "주제 전체");
  fillOptions($("#source"), countBy((a) => [a.source]), "매체 전체");
  $("#companyList").replaceChildren(...countBy((a) => a.companies).map(([v, n]) => el("option", { value: v }, `${n}건`)));
  syncControls();
  apply();
}
main();
