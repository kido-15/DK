# 고려대학교 정보보호대학원 사이버법정책연구실 홈페이지

빌드 도구 없이 동작하는 정적 웹사이트(HTML/CSS/JS)입니다. GitHub Pages 등에 그대로 올릴 수 있습니다.

## 폴더 구조
```
ku-cyberlaw-lab/
├── index.html          홈
├── about.html          연구실 소개
├── people.html         구성원
├── research.html       연구 분야·과제
├── publications.html   논문·성과 (유형 필터)
├── news.html           소식
├── contact.html        오시는 길·연락처
├── assets/
│   ├── css/style.css   공통 스타일 (색상은 :root 변수)
│   ├── js/data.js      ★ 콘텐츠 데이터 — 대부분의 수정은 여기서
│   ├── js/main.js      공통 헤더/푸터, 데이터 렌더링
│   └── img/            사진 (교수·구성원 사진 등)
└── .vscode/            VS Code 권장 확장(Live Server) 설정
```

## VS Code에서 시작하기
1. 저장소를 clone 한 뒤 VS Code에서 `ku-cyberlaw-lab` 폴더를 엽니다 (파일 → 폴더 열기).
2. 권장 확장 **Live Server** 설치 → `index.html` 우클릭 → *Open with Live Server*.
3. `index.html`을 브라우저로 바로 열어도 동작합니다.

## 콘텐츠 수정
- 구성원, 연구분야, 논문, 과제, 소식, 연락처: `assets/js/data.js`
- `[수정 필요]`, `[교수명]` 등 대괄호 항목은 모두 **자리표시자**입니다. 실제 정보로 교체하세요.
- 사진: `assets/img/`에 넣고 data.js의 `photo` 값에 `"assets/img/파일명.jpg"` 입력.
