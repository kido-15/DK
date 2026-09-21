"use strict";

const $ = (id) => document.getElementById(id);
const fmtWon = (n) => (n == null || n < 0) ? "—" : n.toLocaleString("ko-KR") + "원";

// 이동시간이 어떻게 계산됐는지 사용자에게 알려 준다.
// 추정치를 실제 길찾기 결과처럼 보이게 하면 안 되므로 반드시 표시한다.
const PROVIDER_LABEL = {
  kakao: "카카오",
  ors: "ORS",
  osrm: "OSRM",
  estimate: "추정",
};

let META = null;

async function loadMeta() {
  try {
    const res = await fetch("/api/meta");
    META = await res.json();
  } catch (e) {
    showNotice("서버에 연결하지 못했습니다.", true);
    return;
  }

  const routing = Object.entries(META.routing || {})
    .filter(([k]) => k !== "estimate")
    .filter(([, v]) => v === "사용 가능")
    .map(([k]) => PROVIDER_LABEL[k] || k);

  const badges = [
    { text: `⛳ 골프장 ${META.course_count.toLocaleString("ko-KR")}곳`, cls: "" },
    { text: `📡 소스 ${(META.sources || []).length}개`, cls: "dim" },
    routing.length
      ? { text: `🚗 길찾기: ${routing.join(", ")}`, cls: "dim" }
      : { text: "🚗 길찾기: 좌표 추정만", cls: "warn" },
  ];
  $("meta-badges").innerHTML = badges
    .map((b) => `<span class="badge ${b.cls}">${escapeHtml(b.text)}</span>`)
    .join("");

  const sel = $("regions");
  for (const r of META.regions || []) {
    const opt = document.createElement("option");
    opt.value = r; opt.textContent = r;
    sel.appendChild(opt);
  }

  if (!$("date").value && META.today) $("date").value = META.today;

  const problems = [];
  if (!META.has_courses) {
    problems.push("골프장 DB가 비어 있습니다. <code>python3 scripts/fetch_golf_courses.py</code> 를 먼저 실행하세요.");
  }
  if (!META.has_sources) {
    problems.push("티타임 소스가 하나도 설정되지 않았습니다. <code>config/sources.json</code> 을 설정하거나 CSV를 지정해 실행하세요.");
  }
  if (problems.length) {
    showNotice("설정이 덜 됐습니다:<ul>" + problems.map((p) => `<li>${p}</li>`).join("") + "</ul>", true);
  }
}

function showNotice(html, isError) {
  const el = $("notice");
  el.innerHTML = html;
  el.classList.toggle("error", !!isError);
  el.classList.remove("hidden");
}
function hideNotice() { $("notice").classList.add("hidden"); }

function renderStats(stats) {
  const flow = [
    ["수집", stats.fetched],
    ["조건 필터", stats.after_basic],
    ["거리 필터", stats.after_prefilter],
    ["최종", stats.final],
  ].map(([label, n]) => `<span class="step">${label} ${n}</span>`).join('<span>→</span>');

  let html = `<div class="flow">${flow}`;
  html += `<span style="margin-left:auto">길찾기 호출 ${stats.routed}회 · ${stats.elapsed_sec}초</span></div>`;

  if (stats.duplicates > 0) {
    html += `<h4>중복 제거 ${stats.duplicates}건</h4>`;
    html += `<div class="names">골프장·시각·가격·예약처가 모두 같은 티타임을 하나로 합쳤습니다.</div>`;
  }

  if (stats.route_providers && Object.keys(stats.route_providers).length) {
    const ps = Object.entries(stats.route_providers)
      .map(([k, v]) => `${PROVIDER_LABEL[k] || k} ${v}건`).join(", ");
    html += `<h4>이동시간 계산 방식</h4><div class="names">${ps}</div>`;
  }

  if (stats.unmatched > 0) {
    html += `<h4>골프장 DB에서 못 찾은 이름 ${stats.unmatched}건</h4>`;
    html += `<div class="names">${(stats.unmatched_names || []).join(", ") || "-"}</div>`;
    html += `<div class="names" style="margin-top:6px">`;
    html += `이 이름들은 좌표를 몰라 이동시간을 계산할 수 없어 제외됐습니다. `;
    html += `<code>data/golf/courses.csv</code> 의 해당 골프장 <code>aliases</code> 칸에 `;
    html += `이 이름을 넣어 주면 다음 검색부터 잡힙니다.</div>`;
  }

  const errs = Object.entries(stats.source_errors || {});
  if (errs.length) {
    html += `<h4>소스 오류</h4><div class="names">`;
    html += errs.map(([k, v]) => `<div><b>${k}</b>: ${escapeHtml(v)}</div>`).join("");
    html += `</div>`;
  }

  $("stats").innerHTML = html;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function renderSummary(results) {
  const box = $("summary-chips");
  if (!results.length) { box.innerHTML = ""; return; }

  const fees = results.map((r) => r.green_fee).filter((v) => v != null && v >= 0);
  const drives = results.map((r) => r.drive_minutes).filter((v) => v != null);
  const courses = new Set(results.map((r) => r.display_name)).size;

  const chips = [
    { k: "검색된 골프장", v: `${courses.toLocaleString("ko-KR")}곳`, hi: false },
    { k: "최저 그린피", v: fees.length ? fmtWon(Math.min(...fees)) : "—", hi: true },
    { k: "최단 이동시간", v: drives.length ? `${Math.min(...drives)}분` : "—", hi: true },
    { k: "전체 결과", v: `${results.length.toLocaleString("ko-KR")}건`, hi: false },
  ];
  box.innerHTML = chips.map((c) =>
    `<div class="chip${c.hi ? " hi" : ""}"><span class="k">${c.k}</span><span class="v">${c.v}</span></div>`
  ).join("");
}

function renderResults(data) {
  const tbody = document.querySelector("#results tbody");
  tbody.innerHTML = "";
  renderSummary(data.results);

  data.results.forEach((r, i) => {
    const tr = document.createElement("tr");
    if (i < 3) tr.classList.add("top");

    const name = document.createElement("td");
    name.className = "course-name";
    name.innerHTML = (i < 3 ? `<span class="top-badge">TOP${i + 1}</span>` : "") +
      escapeHtml(r.display_name) +
      (r.address ? `<span class="addr">${escapeHtml(r.address)}</span>` : "");
    tr.appendChild(name);

    const cells = [
      r.play_date,
      r.tee_time,
      { html: fmtWon(r.green_fee), cls: "num" },
      {
        html: r.drive_minutes == null ? "—" :
          `${r.drive_minutes}분 <span class="est">${PROVIDER_LABEL[r.route_provider] || r.route_provider}</span>`,
        cls: "num",
      },
      { html: r.distance_km == null ? "—" : `${r.distance_km}km`, cls: "num" },
      r.region || "—",
      { html: `<span class="tag">${escapeHtml(r.source)}</span>` },
    ];

    for (const c of cells) {
      const td = document.createElement("td");
      if (typeof c === "object") {
        td.innerHTML = c.html;
        if (c.cls) td.className = c.cls;
      } else {
        td.textContent = c;
      }
      tr.appendChild(td);
    }

    const book = document.createElement("td");
    if (r.booking_url) {
      const a = document.createElement("a");
      a.href = r.booking_url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.className = "book-link";
      a.textContent = "예약하기 ↗";
      book.appendChild(a);
    } else {
      book.textContent = "—";
    }
    tr.appendChild(book);

    tbody.appendChild(tr);
  });

  $("result-count").textContent = `${data.results.length}건`;
  $("results-section").classList.remove("hidden");
  renderStats(data.stats);

  const empty = $("empty");
  if (data.results.length === 0) {
    const s = data.stats;
    let msg = "조건에 맞는 티타임이 없습니다.";
    const errs = Object.values(s.source_errors || {});
    if (data.needs_collect) {
      // 원인을 정확히 아는 경우다 — 그 날짜를 아직 안 모아 봤을 뿐이다.
      // "소스 오류" 같은 뭉뚱그린 문구 대신 바로 아래 버튼으로 안내한다.
      msg += "<br>아직 그 날짜의 티타임을 모아 본 적이 없습니다.";
    } else if (s.fetched === 0 && errs.length) {
      // 진짜 이유(수집 결과 없음 등)를 "진단 정보" 뒤에 숨기지 않고 바로 보여준다.
      msg += "<br>" + errs.map((e) => escapeHtml(e).replace(/\n/g, "<br>")).join("<br>");
    } else if (s.fetched === 0) {
      msg += "<br>소스에서 받아온 티타임 자체가 0건입니다. 소스 설정이나 네트워크를 확인해 주세요.";
    } else if (s.after_basic === 0) {
      msg += "<br>가격이나 시간대 조건을 완화해 보세요.";
    } else if (s.after_prefilter === 0) {
      msg += "<br>이동 시간 상한을 늘려 보세요.";
    } else {
      msg += "<br>이동 시간 상한을 조금 늘리면 결과가 나올 수 있습니다.";
    }
    empty.innerHTML = msg;
    empty.classList.remove("hidden");
    if (data.needs_collect) renderCollectPrompt(data.needs_collect);
  } else {
    empty.classList.add("hidden");
  }
}

let lastSearchParams = null;   // 수집이 끝난 뒤 같은 조건으로 자동 재검색할 때 쓴다
let collectPollTimer = null;

async function runSearch(params) {
  hideNotice();
  const btn = $("submit-btn");
  btn.disabled = true;
  btn.querySelector(".btn-label").textContent = "검색 중";
  btn.querySelector(".spinner").classList.remove("hidden");
  $("empty").classList.add("hidden");

  try {
    const res = await fetch("/api/search?" + params.toString());
    const data = await res.json();
    if (!res.ok || data.error) {
      showNotice(escapeHtml(data.error || "검색에 실패했습니다."), true);
      $("results-section").classList.add("hidden");
    } else {
      renderResults(data);
    }
  } catch (err) {
    showNotice("검색 요청이 실패했습니다: " + escapeHtml(err.message), true);
  } finally {
    btn.disabled = false;
    btn.querySelector(".btn-label").textContent = "검색";
    btn.querySelector(".spinner").classList.add("hidden");
  }
}

$("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  const params = new URLSearchParams();
  for (const [k, v] of form.entries()) {
    if (String(v).trim()) params.set(k, v);
  }
  lastSearchParams = params;
  runSearch(params);
});

// -- 검색한 날짜가 없을 때 그 자리에서 모으기 --------------------------------

function renderCollectPrompt(needsCollect) {
  const box = $("empty");
  const { source, date: theDate } = needsCollect;
  const div = document.createElement("div");
  div.className = "collect-prompt";
  div.innerHTML =
    `<p>${escapeHtml(theDate)} 날짜는 아직 모아 본 적이 없습니다.</p>` +
    `<button type="button" id="collect-now-btn">지금 모으기 (약 7~10분, ${escapeHtml(source)})</button>` +
    `<div id="collect-progress" class="collect-progress hidden"></div>`;
  box.appendChild(div);

  $("collect-now-btn").addEventListener("click", () => startCollectFlow(source, theDate));
}

async function startCollectFlow(source, theDate) {
  const btn = $("collect-now-btn");
  const progress = $("collect-progress");
  btn.disabled = true;
  btn.textContent = "시작하는 중…";
  progress.classList.remove("hidden");

  try {
    const res = await fetch("/api/collect/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date: theDate }),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      progress.innerHTML = `⚠ ${escapeHtml(data.error || "시작하지 못했습니다.")}`;
      btn.disabled = false;
      btn.textContent = "다시 시도";
      return;
    }
  } catch (err) {
    progress.innerHTML = "⚠ 요청이 실패했습니다: " + escapeHtml(err.message);
    btn.disabled = false;
    btn.textContent = "다시 시도";
    return;
  }

  btn.textContent = "모으는 중…";
  pollCollectStatus();
}

function pollCollectStatus() {
  if (collectPollTimer) clearInterval(collectPollTimer);
  collectPollTimer = setInterval(async () => {
    let status;
    try {
      status = await (await fetch("/api/collect/status")).json();
    } catch (err) {
      return;   // 한 번 실패해도 다음 폴링에서 다시 시도
    }
    const progress = $("collect-progress");
    if (!progress) { clearInterval(collectPollTimer); return; }

    if (status.status === "running") {
      const bucket = status.current_bucket ? ` · ${escapeHtml(status.current_bucket)}` : "";
      progress.innerHTML =
        `모으는 중${bucket} — ${status.rows_so_far.toLocaleString("ko-KR")}건 ` +
        `· ${Math.floor(status.elapsed_sec / 60)}분 ${Math.floor(status.elapsed_sec % 60)}초`;
    } else if (status.status === "done") {
      clearInterval(collectPollTimer);
      progress.innerHTML =
        `✅ ${status.result_rows.toLocaleString("ko-KR")}건 모았습니다. 다시 검색합니다…`;
      if (lastSearchParams) runSearch(lastSearchParams);
    } else if (status.status === "error") {
      clearInterval(collectPollTimer);
      progress.innerHTML = "⚠ 수집 중 오류가 발생했습니다: " + escapeHtml(status.error);
      const btn = $("collect-now-btn");
      if (btn) { btn.disabled = false; btn.textContent = "다시 시도"; }
    }
  }, 1500);
}

// 시간대 빠른 선택
for (const btn of document.querySelectorAll(".presets button")) {
  btn.addEventListener("click", () => {
    $("tee_from").value = btn.dataset.from;
    $("tee_to").value = btn.dataset.to;
    for (const b of document.querySelectorAll(".presets button")) b.classList.remove("on");
    btn.classList.add("on");
  });
}
for (const id of ["tee_from", "tee_to"]) {
  $(id).addEventListener("input", () => {
    for (const b of document.querySelectorAll(".presets button")) b.classList.remove("on");
  });
}

$("toggle-stats").addEventListener("click", () => {
  const el = $("stats");
  el.classList.toggle("hidden");
  $("toggle-stats").textContent = el.classList.contains("hidden") ? "진단 정보 보기" : "진단 정보 숨기기";
});

loadMeta();
