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

let currentResults = [];         // 화면에 지금 떠 있는 결과 (열 헤더로 다시 정렬할 때 씀)
let currentGroups = [];          // 연속 타임 모드일 때의 결과 (이미지 저장에서 씀)
let resultsMode = "search";      // "search" | "consecutive" — 이미지 저장이 어느 걸 그릴지 고른다
let lastResultDescribe = "";     // 검색 조건 요약(서버가 준 query.describe) — 이미지 상단에 표시
let tableSort = { key: null, dir: 1 };   // 마지막으로 클릭한 열과 방향

function renderTable(results) {
  const tbody = document.querySelector("#results tbody");
  tbody.innerHTML = "";

  results.forEach((r, i) => {
    const tr = document.createElement("tr");
    if (i < 3) tr.classList.add("top");

    const name = document.createElement("td");
    name.className = "course-name";
    name.innerHTML = (i < 3 ? `<span class="top-badge">TOP${i + 1}</span>` : "") +
      escapeHtml(r.display_name) +
      (r.nine_hole ? `<span class="tag nine-hole-badge">9홀</span>` : "") +
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
}

// -- 표 헤더 클릭 정렬 -------------------------------------------------------
//
// 서버가 이미 한 번 정렬해서 주지만("추천순" 등), 화면에서 특정 항목
// 기준으로 다시 보고 싶을 때가 있다. 새로 검색하지 않고 받아온 결과를
// 그대로 다시 늘어놓기만 하면 되므로 클라이언트에서 처리한다.
//
// 값이 없는 항목(이동시간 미계산, 가격 미기재 등)은 방향에 상관없이
// 항상 맨 뒤로 보낸다 — 오름차순으로 정렬했는데 "모름"이 1등으로
// 올라오면 더 헷갈린다.
function isUnknownValue(key, v) {
  if (v == null || v === "") return true;
  if (key === "green_fee" && v < 0) return true;
  return false;
}

function sortResults(results, key, dir, type) {
  const known = results.filter((r) => !isUnknownValue(key, r[key]));
  const unknown = results.filter((r) => isUnknownValue(key, r[key]));
  known.sort((a, b) => {
    const av = a[key], bv = b[key];
    const cmp = type === "num" ? av - bv : String(av).localeCompare(String(bv), "ko");
    return cmp * dir;
  });
  return known.concat(unknown);
}

function updateSortHeaders() {
  document.querySelectorAll("#results thead th[data-key]").forEach((th) => {
    th.classList.toggle("sorted-asc", th.dataset.key === tableSort.key && tableSort.dir === 1);
    th.classList.toggle("sorted-desc", th.dataset.key === tableSort.key && tableSort.dir === -1);
  });
}

document.querySelectorAll("#results thead th[data-key]").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    tableSort.dir = tableSort.key === key ? -tableSort.dir : 1;
    tableSort.key = key;
    updateSortHeaders();
    // currentResults 자체를 지금 보이는 순서로 갱신한다 — CSV 다운로드도
    // 이 배열을 그대로 쓰므로, 화면에서 정렬한 순서가 파일에도 반영된다.
    currentResults = sortResults(currentResults, key, tableSort.dir, th.dataset.type);
    renderTable(currentResults);
  });
});

// 일반 검색(표)과 연속 타임 찾기(카드)는 같은 결과 영역을 나눠 쓴다 —
// collect-banner·요약칩·진단 정보는 공통이고, 그 아래 표시 방식만 다르다.
function setResultsMode(mode) {
  resultsMode = mode;
  const isConsecutive = mode === "consecutive";
  document.querySelector(".table-wrap").classList.toggle("hidden", isConsecutive);
  $("consecutive-groups").classList.toggle("hidden", !isConsecutive);
  $("summary-chips").classList.toggle("hidden", isConsecutive);
  $("export-csv").classList.toggle("hidden", isConsecutive);
}

function renderResults(data) {
  setResultsMode("search");
  currentResults = data.results;
  currentGroups = [];
  lastResultDescribe = (data.query && data.query.describe) || "";
  tableSort = { key: null, dir: 1 };
  updateSortHeaders();
  renderSummary(data.results);
  renderTable(data.results);

  $("result-count").textContent = `${data.results.length}건`;
  $("results-section").classList.remove("hidden");
  renderStats(data.stats);

  renderCollectBanner(data.needs_collect);

  const empty = $("empty");
  if (data.results.length === 0) {
    const s = data.stats;
    let msg = "조건에 맞는 티타임이 없습니다.";
    const errs = Object.values(s.source_errors || {});
    if (data.needs_collect) {
      // 원인을 정확히 아는 경우다 — 그 날짜를 아직 안 모아 봤을 뿐이다.
      // 실제 버튼은 위쪽 배너(renderCollectBanner)에 있다.
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
  } else {
    empty.classList.add("hidden");
  }
}

let lastSearchParams = null;   // 수집이 끝난 뒤 같은 조건으로 자동 재검색할 때 쓴다
let lastSearchMode = "search"; // "search" | "consecutive" — 재검색도 같은 모드로 한다
let collectPollTimer = null;

function rerunLastSearch() {
  if (!lastSearchParams) return;
  if (lastSearchMode === "consecutive") {
    runConsecutiveSearch(lastSearchParams);
  } else {
    runSearch(lastSearchParams);
  }
}

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

// -- 연속 타임 찾기 -----------------------------------------------------
//
// 여러 팀이 한 골프장에서 이어서 치려 할 때 쓴다. 검색 자체(날짜·시간대·
// 이동시간 필터)는 일반 검색과 똑같이 서버가 하고(golf/consecutive.py),
// 여기서는 그 결과를 골프장별 "묶음" 카드로 보여만 준다.

async function runConsecutiveSearch(params) {
  hideNotice();
  const btn = $("submit-btn");
  btn.disabled = true;
  btn.querySelector(".btn-label").textContent = "찾는 중";
  btn.querySelector(".spinner").classList.remove("hidden");
  $("empty").classList.add("hidden");

  try {
    const res = await fetch("/api/consecutive?" + params.toString());
    const data = await res.json();
    if (!res.ok || data.error) {
      showNotice(escapeHtml(data.error || "검색에 실패했습니다."), true);
      $("results-section").classList.add("hidden");
    } else {
      renderConsecutiveResults(data);
    }
  } catch (err) {
    showNotice("검색 요청이 실패했습니다: " + escapeHtml(err.message), true);
  } finally {
    btn.disabled = false;
    btn.querySelector(".btn-label").textContent = "검색";
    btn.querySelector(".spinner").classList.add("hidden");
  }
}

function renderConsecutiveGroupCard(g) {
  const slotsHtml = g.slots.map((s) => {
    const link = s.booking_url
      ? `<a href="${s.booking_url}" target="_blank" rel="noopener noreferrer" class="book-link">예약 ↗</a>`
      : "—";
    return `<tr><td>${escapeHtml(s.tee_time)}</td><td class="num">${fmtWon(s.green_fee)}</td>` +
      `<td>${s.slots ?? "—"}</td><td>${link}</td></tr>`;
  }).join("");

  const drive = g.drive_minutes == null ? "" :
    `<span class="chip-sm">🚗 ${g.drive_minutes}분</span>`;

  return `
    <div class="consecutive-card">
      <div class="cc-head">
        <h3>${escapeHtml(g.course_name)}</h3>
        <span class="chip-sm hi">${g.count}개 연속</span>
        ${drive}
        <span class="chip-sm">${escapeHtml(g.region || "—")}</span>
        <span class="tag">${escapeHtml(sourceLabel(g.source))}</span>
      </div>
      <div class="cc-range">${escapeHtml(g.first_tee)} ~ ${escapeHtml(g.last_tee)}</div>
      <table class="cc-slots">
        <thead><tr><th>티오프</th><th class="num">그린피</th><th>잔여</th><th>예약</th></tr></thead>
        <tbody>${slotsHtml}</tbody>
      </table>
    </div>`;
}

function renderConsecutiveResults(data) {
  setResultsMode("consecutive");
  currentResults = [];
  currentGroups = data.groups || [];
  lastResultDescribe = (data.query && data.query.describe) || "";

  const groups = data.groups || [];
  $("result-count").textContent = `${groups.length}개 골프장`;
  $("results-section").classList.remove("hidden");
  renderStats(data.stats);
  renderCollectBanner(data.needs_collect);

  $("consecutive-groups").innerHTML = groups.map(renderConsecutiveGroupCard).join("");

  const empty = $("empty");
  if (groups.length === 0) {
    let msg = `조건에 맞게 ${escapeHtml(String(data.min_count))}개 이상 연속으로 이어지는 골프장을 못 찾았습니다.`;
    if (data.needs_collect) {
      msg += "<br>아직 그 날짜의 티타임을 모아 본 적이 없습니다.";
    } else {
      msg += "<br>티오프 시간대를 넓히거나, 연속 개수를 줄여 보세요.";
    }
    empty.innerHTML = msg;
    empty.classList.remove("hidden");
  } else {
    empty.classList.add("hidden");
  }
}

$("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  const params = new URLSearchParams();
  for (const [k, v] of form.entries()) {
    if (String(v).trim()) params.set(k, v);
  }
  const wantsConsecutive = $("find_consecutive").checked;
  params.delete("find_consecutive");
  lastSearchParams = params;
  lastSearchMode = wantsConsecutive ? "consecutive" : "search";
  rerunLastSearch();
});

// -- 검색한 날짜에 아직 안 모은 소스가 있으면 그 자리에서 모으기 ---------------
//
// 소스가 여러 개(골팡·카카오골프예약)라, 다른 소스가 이미 결과를 줘서
// 검색 결과가 나온 뒤에도 특정 소스는 빠져 있을 수 있다("골팡만 모으고
// 카카오는 빠뜨린 채" 착각하지 않도록) — 그래서 결과가 있든 없든 늘
// 같은 배너에 띄운다. 버튼 하나로 빠진 소스들을 한꺼번에 시작하고,
// 소스마다 걸리는 시간이 달라(카카오는 골프장을 하나씩 돌아 훨씬 오래
// 걸린다) 진행 상황도 소스별로 따로 보여 준다.

const SOURCE_LABELS = { golfpang: "골팡", kakao: "카카오골프예약" };
const sourceLabel = (id) => SOURCE_LABELS[id] || id;

// 티타임은 실시간으로 열리고 닫혀서, 오래전에 모은 결과를 계속 최신인 것처럼
// 보여주면 안 된다(golf/collect.py의 STALE_AFTER_SECONDS 참고). 언제 모았는지
// 눈에 보이게 해서, 다시 모을지 kd님이 판단할 수 있게 한다.
function formatCollectedAgo(epochSeconds) {
  if (epochSeconds == null) return "아직 모아 본 적 없음";
  const minutes = Math.floor(Math.max(0, Date.now() / 1000 - epochSeconds) / 60);
  if (minutes < 1) return "방금 모음";
  if (minutes < 60) return `${minutes}분 전 모음`;
  return `${Math.floor(minutes / 60)}시간 전 모음`;
}

function renderCollectBanner(needsCollect) {
  const box = $("collect-banner");
  if (!needsCollect || !needsCollect.length) {
    box.innerHTML = "";
    box.classList.add("hidden");
    return;
  }
  const names = needsCollect.map((n) => sourceLabel(n.source)).join(" · ");
  const lines = needsCollect
    .map((n) => `${escapeHtml(sourceLabel(n.source))}: ${formatCollectedAgo(n.last_collected_at)}`)
    .join(" · ");
  box.innerHTML =
    `<p>${lines}</p>` +
    `<button type="button" id="collect-now-btn">지금 모으기 (${escapeHtml(names)})</button>` +
    `<div id="collect-progress" class="collect-progress hidden"></div>`;
  box.classList.remove("hidden");

  $("collect-now-btn").addEventListener("click", () => startCollectFlow(needsCollect));
}

async function startCollectFlow(needsCollect) {
  const btn = $("collect-now-btn");
  const progress = $("collect-progress");
  btn.disabled = true;
  btn.textContent = "시작하는 중…";
  progress.classList.remove("hidden");

  const started = [];
  for (const { source, date: theDate } of needsCollect) {
    try {
      const res = await fetch("/api/collect/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source, date: theDate }),
      });
      const data = await res.json();
      if (!res.ok || data.error) {
        progress.innerHTML += `<div>⚠ ${escapeHtml(sourceLabel(source))}: ${escapeHtml(data.error || "시작하지 못했습니다.")}</div>`;
        continue;
      }
      started.push(source);
    } catch (err) {
      progress.innerHTML += `<div>⚠ ${escapeHtml(sourceLabel(source))}: 요청이 실패했습니다 (${escapeHtml(err.message)})</div>`;
    }
  }

  if (!started.length) {
    btn.disabled = false;
    btn.textContent = "다시 시도";
    return;
  }

  btn.textContent = "모으는 중…";
  pollCollectStatus(started);
}

function pollCollectStatus(sources) {
  if (collectPollTimer) clearInterval(collectPollTimer);
  const pending = new Set(sources);
  const lines = {};

  collectPollTimer = setInterval(async () => {
    const progress = $("collect-progress");
    if (!progress) { clearInterval(collectPollTimer); return; }

    for (const source of Array.from(pending)) {
      let status;
      try {
        status = await (await fetch("/api/collect/status?source=" + encodeURIComponent(source))).json();
      } catch (err) {
        continue;   // 이번 폴링만 건너뛰고 다음에 다시 시도
      }
      const label = sourceLabel(source);
      if (status.status === "running") {
        const bucket = status.current_bucket ? ` · ${escapeHtml(status.current_bucket)}` : "";
        lines[source] =
          `${escapeHtml(label)}: 모으는 중${bucket} — ${status.rows_so_far.toLocaleString("ko-KR")}건 ` +
          `· ${Math.floor(status.elapsed_sec / 60)}분 ${Math.floor(status.elapsed_sec % 60)}초`;
      } else if (status.status === "done") {
        lines[source] = `${escapeHtml(label)}: ✅ ${status.result_rows.toLocaleString("ko-KR")}건 모았습니다.`;
        pending.delete(source);
      } else if (status.status === "error") {
        lines[source] = `${escapeHtml(label)}: ⚠ 오류 — ${escapeHtml(status.error)}`;
        pending.delete(source);
      }
    }
    progress.innerHTML = Object.values(lines).map((l) => `<div>${l}</div>`).join("");

    if (pending.size === 0) {
      clearInterval(collectPollTimer);
      progress.innerHTML += "<div>다시 검색합니다…</div>";
      rerunLastSearch();
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

// -- CSV 다운로드 -------------------------------------------------------
//
// 서버가 외부 라이브러리 없이 표준 라이브러리만 쓰므로, xlsx를 서버에서
// 만드는 대신 화면에 이미 있는 결과를 브라우저에서 바로 CSV로 내보낸다.
// 엑셀·넘버스 모두 CSV를 더블클릭으로 그대로 연다.

function toCsvField(v) {
  const s = v == null ? "" : String(v);
  return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

function exportResultsCsv() {
  if (!currentResults.length) return;
  const headers = ["골프장", "9홀여부", "주소", "날짜", "티오프", "그린피", "이동시간(분)", "거리(km)", "지역", "소스", "예약링크"];
  const rows = currentResults.map((r) => [
    r.display_name, r.nine_hole ? "9홀" : "", r.address || "", r.play_date, r.tee_time,
    r.green_fee >= 0 ? r.green_fee : "", r.drive_minutes ?? "", r.distance_km ?? "",
    r.region || "", r.source, r.booking_url || "",
  ]);
  const csv = [headers, ...rows].map((row) => row.map(toCsvField).join(",")).join("\r\n");
  // 엑셀이 UTF-8 CSV를 한글 깨짐 없이 열도록 BOM을 붙인다.
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `골프검색결과_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

$("export-csv").addEventListener("click", exportResultsCsv);

// -- 이미지로 저장(카톡 등으로 공유) -------------------------------------
//
// 화면을 그대로 캡처하려면 html2canvas 같은 외부 라이브러리가 필요한데,
// 이 프로젝트는 서버뿐 아니라 화면 쪽도 외부 의존성을 두지 않는다. 대신
// 지금 결과를 <canvas> 위에 직접 그려서(=우리가 이미 갖고 있는 데이터로
// 새로 그리는 "공유 카드"), CSV와 같은 방식(Blob + <a download>)으로
// PNG 파일로 저장한다. 실제 화면 그대로는 아니지만 공유용으로는 오히려
// 더 깔끔하다(체크박스·버튼 같은 조작 UI가 안 찍힌다).

const SHARE_WIDTH = 720;
const SHARE_PAD = 24;
const SHARE_FONT = "-apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif";

function downloadCanvas(canvas, filename) {
  canvas.toBlob((blob) => {
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }, "image/png");
}

function makeShareCanvas(height) {
  const scale = 2;   // 레티나 화면에서도 흐릿하지 않게
  const canvas = document.createElement("canvas");
  canvas.width = SHARE_WIDTH * scale;
  canvas.height = height * scale;
  const ctx = canvas.getContext("2d");
  ctx.scale(scale, scale);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, SHARE_WIDTH, height);
  return { canvas, ctx };
}

function drawShareHeader(ctx, title, subtitle) {
  let y = SHARE_PAD + 24;
  ctx.fillStyle = "#1a1d21";
  ctx.font = `700 21px ${SHARE_FONT}`;
  ctx.fillText(title, SHARE_PAD, y);
  y += 24;
  if (subtitle) {
    ctx.fillStyle = "#6b7280";
    ctx.font = `13px ${SHARE_FONT}`;
    ctx.fillText(subtitle, SHARE_PAD, y);
    y += 18;
  }
  return y + 10;
}

function drawShareFooter(ctx, y) {
  ctx.strokeStyle = "#e2e5ea";
  ctx.beginPath();
  ctx.moveTo(SHARE_PAD, y);
  ctx.lineTo(SHARE_WIDTH - SHARE_PAD, y);
  ctx.stroke();
  y += 20;
  ctx.fillStyle = "#9aa3ad";
  ctx.font = `11px ${SHARE_FONT}`;
  ctx.fillText("그린피·잔여좌석은 예약 사이트 기준이며 실제와 다를 수 있습니다. 예약 전 원 사이트에서 확인하세요.", SHARE_PAD, y);
  y += 16;
  ctx.fillText(`⛳ 골프장 티타임 검색 · ${new Date().toLocaleString("ko-KR")}`, SHARE_PAD, y);
  return y;
}

const SHARE_MAX_ROWS = 12;

function exportTableAsImage() {
  if (!currentResults.length) return;
  const rows = currentResults.slice(0, SHARE_MAX_ROWS);
  const extra = currentResults.length > rows.length;
  const rowH = 40;
  const height = 90 + rows.length * rowH + (extra ? 24 : 0) + 70;

  const { canvas, ctx } = makeShareCanvas(height);
  let y = drawShareHeader(ctx, "⛳ 골프장 티타임 검색 결과", lastResultDescribe);

  rows.forEach((r, i) => {
    ctx.fillStyle = "#1a1d21";
    ctx.font = `600 15px ${SHARE_FONT}`;
    ctx.fillText(`${i + 1}. ${r.display_name}${r.nine_hole ? " (9홀)" : ""}`, SHARE_PAD, y + 15);
    ctx.fillStyle = "#6b7280";
    ctx.font = `12px ${SHARE_FONT}`;
    const drive = r.drive_minutes != null ? `${r.drive_minutes}분` : "이동시간 모름";
    ctx.fillText(
      `${r.play_date} ${r.tee_time} · ${fmtWon(r.green_fee)} · ${drive} · ${r.region || "-"} · ${sourceLabel(r.source)}`,
      SHARE_PAD, y + 32);
    y += rowH;
    if (i < rows.length - 1) {
      ctx.strokeStyle = "#eef0f2";
      ctx.beginPath();
      ctx.moveTo(SHARE_PAD, y - 6);
      ctx.lineTo(SHARE_WIDTH - SHARE_PAD, y - 6);
      ctx.stroke();
    }
  });

  if (extra) {
    ctx.fillStyle = "#9aa3ad";
    ctx.font = `12px ${SHARE_FONT}`;
    ctx.fillText(`외 ${currentResults.length - rows.length}건 더 있음`, SHARE_PAD, y + 6);
    y += 24;
  }

  drawShareFooter(ctx, y + 8);
  downloadCanvas(canvas, `골프검색결과_${new Date().toISOString().slice(0, 10)}.png`);
}

const SHARE_MAX_SLOTS_PER_GROUP = 8;

function groupBlockHeight(g) {
  const shown = Math.min(g.slots.length, SHARE_MAX_SLOTS_PER_GROUP);
  let h = 38 + shown * 19;
  if (g.slots.length > shown) h += 19;
  return h + 16;
}

function drawGroupBlock(ctx, g, y) {
  ctx.fillStyle = "#1a1d21";
  ctx.font = `700 15px ${SHARE_FONT}`;
  ctx.fillText(`${g.course_name} · ${g.count}개 연속 (${g.first_tee}~${g.last_tee})`, SHARE_PAD, y + 14);
  ctx.font = `12px ${SHARE_FONT}`;
  ctx.fillStyle = "#6b7280";
  const drive = g.drive_minutes != null ? ` · 🚗${g.drive_minutes}분` : "";
  ctx.fillText(`${g.region || "-"} · ${sourceLabel(g.source)}${drive}`, SHARE_PAD, y + 30);
  y += 38;

  const shown = g.slots.slice(0, SHARE_MAX_SLOTS_PER_GROUP);
  ctx.font = `13px ${SHARE_FONT}`;
  shown.forEach((s) => {
    ctx.fillStyle = "#1a1d21";
    ctx.fillText(`${s.tee_time}   ${fmtWon(s.green_fee)}`, SHARE_PAD + 10, y + 12);
    y += 19;
  });
  if (g.slots.length > shown.length) {
    ctx.fillStyle = "#9aa3ad";
    ctx.fillText(`외 ${g.slots.length - shown.length}개 더`, SHARE_PAD + 10, y + 12);
    y += 19;
  }
  y += 8;
  ctx.strokeStyle = "#eef0f2";
  ctx.beginPath();
  ctx.moveTo(SHARE_PAD, y);
  ctx.lineTo(SHARE_WIDTH - SHARE_PAD, y);
  ctx.stroke();
  return y + 8;
}

function exportGroupsAsImage() {
  if (!currentGroups.length) return;
  const height = 90 + currentGroups.reduce((sum, g) => sum + groupBlockHeight(g), 0) + 70;

  const { canvas, ctx } = makeShareCanvas(height);
  let y = drawShareHeader(ctx, "⛳ 연속 타임 검색 결과", lastResultDescribe);
  for (const g of currentGroups) {
    y = drawGroupBlock(ctx, g, y);
  }
  drawShareFooter(ctx, y + 8);
  downloadCanvas(canvas, `골프연속타임_${new Date().toISOString().slice(0, 10)}.png`);
}

$("export-image").addEventListener("click", () => {
  if (resultsMode === "consecutive") {
    exportGroupsAsImage();
  } else {
    exportTableAsImage();
  }
});

loadMeta();
