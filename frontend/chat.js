// 학습 도우미 챗봇
// - 프론트는 내 API(API Gateway → Lambda)만 호출한다. Gemini 키는 서버(SSM)에만 있다.
// - 지금 보고 있는 기사(제목·링크, 선택 시 요약)를 컨텍스트로 보낸다.
const $ = (s) => document.querySelector(s);
const MAX_INPUT = 1000;
const MAX_HISTORY = 6; // 최근 3턴만 서버로

let apiUrl = "";
let current = null; // 현재 기사
let history = []; // [{role: "user"|"model", text}]
let busy = false;

// 모델 응답의 간단한 마크다운(**굵게**, `코드`, 목록, 인용, 제목)만 표시.
// innerHTML을 쓰지 않고 DOM 노드를 직접 만들어서 응답에 HTML이 섞여 있어도 실행되지 않는다(XSS 안전).
function inline(parent, text) {
  const re = /(\*\*[^*\n]+\*\*|`[^`\n]+`)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) parent.append(text.slice(last, m.index));
    const tok = m[0];
    const el = document.createElement(tok.startsWith("**") ? "strong" : "code");
    el.textContent = tok.startsWith("**") ? tok.slice(2, -2) : tok.slice(1, -1);
    parent.append(el);
    last = m.index + tok.length;
  }
  if (last < text.length) parent.append(text.slice(last));
}

export function renderMarkdown(container, text) {
  container.replaceChildren();
  let list = null;
  for (const raw of text.replace(/\r/g, "").split("\n")) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (bullet) {
      const tag = /^\s*\d/.test(line) ? "OL" : "UL";
      if (!list || list.tagName !== tag) { list = document.createElement(tag); container.append(list); }
      const li = document.createElement("li"); inline(li, bullet[1]); list.append(li);
      continue;
    }
    list = null;
    if (!line.trim()) continue;
    let el;
    const quote = line.match(/^>\s?(.*)$/), head = line.match(/^#{1,6}\s+(.*)$/);
    if (quote) { el = document.createElement("blockquote"); inline(el, quote[1]); }
    else if (head) { el = document.createElement("p"); const b = document.createElement("strong"); b.textContent = head[1].replace(/\*\*/g, ""); el.append(b); }
    else { el = document.createElement("p"); inline(el, line); }
    container.append(el);
  }
}

function addMsg(kind, text) {
  const d = document.createElement("div");
  d.className = `msg ${kind}`;
  d.textContent = text; // 모델 응답도 텍스트로만 렌더 → XSS 안전
  $("#chatLog").append(d);
  $("#chatLog").scrollTop = $("#chatLog").scrollHeight;
  return d;
}

function renderContext() {
  const box = $("#chatContext");
  box.replaceChildren();
  if (!current) return;
  box.append("📄 참고 기사: ", Object.assign(document.createElement("strong"), { textContent: current.title }));
  const x = Object.assign(document.createElement("button"), { className: "link-btn", textContent: "해제" });
  x.onclick = () => { current = null; renderContext(); };
  box.append(" ", x);
}

// 오늘 사용량 표시: 횟수 / 상한 · 토큰 · 예상 비용
function renderUsage(u) {
  if (!u) return;
  const cost = u.freeTier ? "$0 (무료 티어)" : `약 $${u.estCostUsd.toFixed(4)}`;
  $("#chatUsage").textContent =
    `오늘 ${u.count}/${u.limit}회 · 토큰 ${(u.inputTokens + u.outputTokens).toLocaleString()}` +
    ` (입력 ${u.inputTokens.toLocaleString()} / 출력 ${u.outputTokens.toLocaleString()}) · 예상 비용 ${cost}`;
}

async function refreshUsage() {
  if (!apiUrl) return;
  try {
    const usageUrl = apiUrl.slice(0, apiUrl.lastIndexOf("/")) + "/usage";
    const r = await fetch(usageUrl, { cache: "no-store" });
    if (r.ok) renderUsage((await r.json()).usage);
  } catch { /* 표시용이라 실패해도 무시 */ }
}

export function setChatArticle(article, open = false) {
  current = article;
  renderContext();
  if (open) {
    $("#chatPanel").hidden = false;
    refreshUsage();
    $("#chatInput").focus();
  }
}

const ERRORS = {
  daily_limit: "오늘 사용 가능한 질문 수를 모두 썼어요. 내일 다시 이용해 주세요. 🙏",
  rate_limited: "지금 질문이 몰려서 무료 한도에 걸렸어요. 1분쯤 뒤에 다시 시도해 주세요.",
  too_long: `질문은 ${MAX_INPUT}자 이내로 써 주세요.`,
  forbidden: "허용되지 않은 위치에서의 요청입니다.",
  overloaded: "지금 AI 모델 사용자가 많아 응답이 늦어요. 잠시 후 다시 물어봐 주세요.",
  not_configured: "챗봇 API 키가 아직 설정되지 않았어요. (관리자: SSM 파라미터 확인)",
};

async function send(question) {
  if (!apiUrl) {
    addMsg("err", "챗봇은 AWS에 배포한 뒤에 사용할 수 있어요. (로컬 미리보기에서는 비활성)");
    return;
  }
  busy = true;
  $("#chatSend").disabled = true;
  addMsg("user", question);
  const pending = addMsg("bot", "생각 중…");
  const article = current
    ? { title: current.title, link: current.link, source: current.source, ...($("#sendSummary").checked ? { summary: current.summary } : {}) }
    : null;
  try {
    const res = await fetch(apiUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, article, history: history.slice(-MAX_HISTORY) }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      pending.className = "msg err";
      pending.textContent = ERRORS[data.error] || (res.status === 429 ? ERRORS.rate_limited : "답변을 받지 못했어요. 잠시 후 다시 시도해 주세요.");
      return;
    }
    pending.classList.add("md");
    renderMarkdown(pending, data.answer);
    renderUsage(data.usage);
    history.push({ role: "user", text: question }, { role: "model", text: data.answer });
  } catch {
    pending.className = "msg err";
    pending.textContent = "네트워크 오류가 발생했어요.";
  } finally {
    busy = false;
    $("#chatSend").disabled = false;
  }
}

export function initChat(config) {
  apiUrl = config.chatApiUrl || "";
  $("#sendSummary").checked = localStorage.getItem("chatSendSummary") === "1";
  $("#sendSummary").onchange = (e) => localStorage.setItem("chatSendSummary", e.target.checked ? "1" : "0");
  $("#chatFab").onclick = () => {
    $("#chatPanel").hidden = !$("#chatPanel").hidden;
    if (!$("#chatPanel").hidden) { $("#chatInput").focus(); refreshUsage(); }
  };
  $("#chatClose").onclick = () => { $("#chatPanel").hidden = true; };
  $("#chatSettingsBtn").onclick = () => { $("#chatSettings").hidden = !$("#chatSettings").hidden; };
  $("#chatClear").onclick = () => { history = []; $("#chatLog").replaceChildren(); };
  $("#chatForm").onsubmit = (e) => {
    e.preventDefault();
    const q = $("#chatInput").value.trim();
    if (!q || busy) return;
    if (q.length > MAX_INPUT) { addMsg("err", ERRORS.too_long); return; }
    $("#chatInput").value = "";
    send(q);
  };
  $("#chatInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#chatForm").requestSubmit(); }
  });
  addMsg("bot", "안녕하세요! 기사 카드의 \"💬 이 기사 물어보기\"를 누르거나 기사를 연 뒤 모르는 용어를 물어보세요.");
}
