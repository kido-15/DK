/* =========================================================
   사이트 콘텐츠 데이터 — 이 파일만 수정하면 각 페이지에 반영됩니다.
   ※ 아래 내용은 모두 예시/자리표시자입니다. 실제 정보로 교체하세요.
   ========================================================= */
window.LAB = {
  name: "사이버법정책연구실",
  nameEn: "Cyber Law & Policy Lab",
  org: "고려대학교 정보보호대학원",
  orgEn: "School of Cybersecurity, Korea University",
  email: "[수정 필요] lab@korea.ac.kr",
  phone: "[수정 필요] 02-XXXX-XXXX",
  address: "[수정 필요] 서울특별시 성북구 안암로 145 고려대학교 ○○관 ○○○호",

  // 지도교수
  pi: {
    name: "[교수명]",
    nameEn: "[Professor Name]",
    title: "교수 / 연구실 책임자",
    photo: "", // 예: "assets/img/pi.jpg"
    email: "[수정 필요]",
    interests: ["[연구분야 1]", "[연구분야 2]", "[연구분야 3]"],
    education: ["[학위] [학교] [연도]", "[학위] [학교] [연도]"],
    career: ["[경력 1]", "[경력 2]"],
  },

  // 구성원: group 값은 "박사과정" | "석사과정" | "연구원" | "졸업생"
  members: [
    { name: "[이름]", group: "박사과정", interest: "[관심분야]", photo: "" },
    { name: "[이름]", group: "박사과정", interest: "[관심분야]", photo: "" },
    { name: "[이름]", group: "석사과정", interest: "[관심분야]", photo: "" },
    { name: "[이름]", group: "석사과정", interest: "[관심분야]", photo: "" },
    { name: "[이름]", group: "연구원",   interest: "[관심분야]", photo: "" },
    { name: "[이름]", group: "졸업생",   interest: "[현 소속]",  photo: "" },
  ],

  // 연구분야 (예시 — 연구실 실제 분야로 수정)
  research: [
    { icon: "⚖️", title: "AI 거버넌스·규제", desc: "인공지능 관련 법제와 규제 체계, 투명성·책임성 확보 방안 연구 (예시)" },
    { icon: "🔐", title: "개인정보보호 법제", desc: "개인정보 처리·이전·가명처리 등 데이터 보호 법제 연구 (예시)" },
    { icon: "🛡️", title: "사이버안보 법·정책", desc: "국가 사이버안보 체계, 침해사고 대응 법제 연구 (예시)" },
    { icon: "🔎", title: "디지털 증거·포렌식 법", desc: "디지털 증거의 수집·압수수색과 증거능력 연구 (예시)" },
    { icon: "🌐", title: "국제 사이버 규범", desc: "국제법과 사이버 공간 규범, 해외 입법례 비교 연구 (예시)" },
    { icon: "📡", title: "정보통신·플랫폼 규제", desc: "정보통신망 및 플랫폼 서비스 관련 규제 연구 (예시)" },
  ],

  // 논문·보고서: type 값은 "국제학술지" | "국내학술지" | "학술대회" | "연구보고서"
  publications: [
    { year: 2026, type: "국제학술지", title: "[논문 제목]", authors: "[저자]", venue: "[학술지명]", link: "" },
    { year: 2026, type: "국내학술지", title: "[논문 제목]", authors: "[저자]", venue: "[학술지명]", link: "" },
    { year: 2025, type: "학술대회",   title: "[발표 제목]", authors: "[저자]", venue: "[학술대회명]", link: "" },
    { year: 2025, type: "연구보고서", title: "[보고서 제목]", authors: "[연구진]", venue: "[발주기관]", link: "" },
  ],

  // 프로젝트
  projects: [
    { period: "2026 – 현재", title: "[과제명]", funder: "[발주기관]" },
    { period: "2025 – 2026", title: "[과제명]", funder: "[발주기관]" },
  ],

  // 소식 (최신순)
  news: [
    { date: "2026-11-11", title: "연구실 홈페이지를 개설했습니다.", body: "" },
    { date: "2026-00-00", title: "[소식 제목]", body: "[내용]" },
  ],
};
