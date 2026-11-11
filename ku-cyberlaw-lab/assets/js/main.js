/* 공통 헤더/푸터 삽입 + 페이지별 데이터 렌더링 */
(function () {
  const L = window.LAB || {};
  const NAV = [
    ["index.html", "홈"],
    ["about.html", "연구실 소개"],
    ["people.html", "구성원"],
    ["research.html", "연구"],
    ["publications.html", "논문·성과"],
    ["news.html", "소식"],
    ["contact.html", "오시는 길"],
  ];
  const current = location.pathname.split("/").pop() || "index.html";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (sel) => document.querySelector(sel);

  // ---------- Header ----------
  const header = document.createElement("header");
  header.className = "site-header";
  header.innerHTML = `
    <div class="container header-inner">
      <a class="brand" href="index.html" aria-label="홈으로">
        <span class="brand-mark">CLP</span>
        <span class="brand-text"><small>${esc(L.org)}</small><strong>${esc(L.name)}</strong></span>
      </a>
      <button class="nav-toggle" aria-label="메뉴 열기" aria-expanded="false">☰</button>
      <nav class="nav"><ul>
        ${NAV.map(([href, label]) => `<li><a href="${href}" class="${href === current ? "active" : ""}">${label}</a></li>`).join("")}
      </ul></nav>
    </div>`;
  document.body.prepend(header);
  const toggle = header.querySelector(".nav-toggle");
  const nav = header.querySelector(".nav");
  toggle.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    toggle.setAttribute("aria-expanded", open);
  });

  // ---------- Footer ----------
  const footer = document.createElement("footer");
  footer.className = "site-footer";
  footer.innerHTML = `
    <div class="container footer-inner">
      <div>
        <strong style="color:#fff">${esc(L.org)} ${esc(L.name)}</strong><br/>
        ${esc(L.orgEn)} · ${esc(L.nameEn)}<br/>
        ${esc(L.address)}
      </div>
      <div>
        E-mail: ${esc(L.email)}<br/>
        Tel: ${esc(L.phone)}<br/>
        © ${new Date().getFullYear()} ${esc(L.nameEn)}. All rights reserved.
      </div>
    </div>`;
  document.body.append(footer);

  // ---------- Renderers ----------
  const avatar = (p) => `<div class="avatar">${p.photo ? `<img src="${esc(p.photo)}" alt="${esc(p.name)}">` : esc((p.name || "?").replace(/[\[\]]/g, "").charAt(0))}</div>`;

  const researchCards = (items) => items.map((r) => `
    <div class="card"><div class="icon">${r.icon}</div><h3>${esc(r.title)}</h3><p>${esc(r.desc)}</p></div>`).join("");

  const pubItem = (p) => `
    <li>
      <span class="tag">${esc(p.type)}</span><span class="meta">${esc(p.year)}</span><br/>
      <span class="title">${p.link ? `<a href="${esc(p.link)}" target="_blank" rel="noopener">${esc(p.title)}</a>` : esc(p.title)}</span><br/>
      <span class="meta">${esc(p.authors)} · <em>${esc(p.venue)}</em></span>
    </li>`;

  const newsItem = (n) => `
    <li class="news-item"><time>${esc(n.date)}</time>
      <div><div class="title">${esc(n.title)}</div>${n.body ? `<div class="meta">${esc(n.body)}</div>` : ""}</div>
    </li>`;

  // 홈: 연구분야 미리보기 + 최근 소식
  if ($("#home-research")) $("#home-research").innerHTML = researchCards((L.research || []).slice(0, 3));
  if ($("#home-news")) $("#home-news").innerHTML = (L.news || []).slice(0, 4).map(newsItem).join("");

  // 연구 페이지
  if ($("#research-list")) $("#research-list").innerHTML = researchCards(L.research || []);
  if ($("#project-list")) $("#project-list").innerHTML = (L.projects || []).map((p) => `
    <li><span class="meta">${esc(p.period)}</span><br/><span class="title">${esc(p.title)}</span><br/><span class="meta">${esc(p.funder)}</span></li>`).join("");

  // 구성원 페이지
  if ($("#pi")) {
    const pi = L.pi || {};
    $("#pi").innerHTML = `
      <div class="pi-card">
        ${avatar(pi)}
        <div>
          <h2 style="margin:0">${esc(pi.name)} <small style="color:var(--muted);font-weight:400">${esc(pi.nameEn)}</small></h2>
          <div class="role" style="color:var(--crimson);font-weight:600">${esc(pi.title)}</div>
          <p class="meta">E-mail: ${esc(pi.email)}</p>
          <div>${(pi.interests || []).map((i) => `<span class="tag">${esc(i)}</span>`).join("")}</div>
          <h4 style="margin:20px 0 0">학력</h4><ul>${(pi.education || []).map((e) => `<li>${esc(e)}</li>`).join("")}</ul>
          <h4 style="margin:16px 0 0">주요 경력</h4><ul>${(pi.career || []).map((e) => `<li>${esc(e)}</li>`).join("")}</ul>
        </div>
      </div>`;
  }
  if ($("#members")) {
    const groups = ["박사과정", "석사과정", "연구원", "졸업생"];
    $("#members").innerHTML = groups.map((g) => {
      const list = (L.members || []).filter((m) => m.group === g);
      if (!list.length) return "";
      return `<h3 class="group-title">${g}</h3>
        <div class="grid grid-4">${list.map((m) => `
          <div class="card person">${avatar(m)}<strong>${esc(m.name)}</strong>
            <div class="role">${esc(m.group)}</div><div class="interest">${esc(m.interest)}</div></div>`).join("")}
        </div>`;
    }).join("");
  }

  // 논문 페이지 (유형 필터)
  if ($("#pub-list")) {
    const pubs = (L.publications || []).slice().sort((a, b) => b.year - a.year);
    const types = ["전체", ...new Set(pubs.map((p) => p.type))];
    const filters = $("#pub-filters");
    const draw = (t) => {
      $("#pub-list").innerHTML = pubs.filter((p) => t === "전체" || p.type === t).map(pubItem).join("") || "<li>등록된 항목이 없습니다.</li>";
      filters.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.t === t));
    };
    filters.innerHTML = types.map((t) => `<button data-t="${esc(t)}">${esc(t)}</button>`).join("");
    filters.addEventListener("click", (e) => { if (e.target.dataset.t) draw(e.target.dataset.t); });
    draw("전체");
  }

  // 소식 페이지
  if ($("#news-list")) $("#news-list").innerHTML = (L.news || []).map(newsItem).join("");

  // 연락처
  document.querySelectorAll("[data-lab]").forEach((el) => { el.textContent = L[el.dataset.lab] ?? ""; });
})();
