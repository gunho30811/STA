# 디자인 시스템 현황 인벤토리 (초안)

이 문서는 두 부분으로 구성된다. 뒷부분(스캔 범위 이후)은 판단·개선안 없이 **현재 코드에 실제로
박혀 있는 값**만 기계적으로 스캔해서 정리한 인벤토리다. 값별로 총 사용 횟수와 사용된 파일(파일당
횟수)을 표기했다. 앞부분(바로 아래)은 이 인벤토리를 `docs/reference-rules.md`(레퍼런스 스크린샷
5장 측정 규칙)와 대조해서 뽑은 **금지/강제 규칙**이다 — 리디자인 작업의 가드레일로 쓴다.

## 금지/강제 규칙

아래 규칙은 인벤토리(이 문서 하단)와 `docs/reference-rules.md`를 항목별로 대조해서 나온 것이다.
각 항목 뒤 괄호는 근거(인벤토리 수치 또는 레퍼런스 측정값)다.

### 금지
- 폰트 크기 8종 초과 사용 (현재 코드는 38종 — 레퍼런스 5개 화면은 화면당 4~6종)
- 소수점 px 폰트 크기 (`11.5px`·`12.5px`·`13.5px`·`15.5px`·`9.5px` — 합쳐서 60회 이상 등장,
  4px 배수 원칙과 정면 충돌)
- 4px로 나누어떨어지지 않는 spacing 값 (`7px`·`9px`·`11px`·`13px`·`15px`·`17px`·`19px`·`21px`·
  `23px` 등 — margin/padding/gap 전체 255개 값 중 다수. 레퍼런스 5개 화면은 예외 없이 4px 배수)
- 그림자(box-shadow) 3종 초과 (현재 28종 병존 — 레퍼런스는 5개 화면 전체에서 사실상 안 보이는
  수준으로만 사용)
- border와 진한 box-shadow를 같은 요소에 동시 적용 (택1 — 레퍼런스는 면 분리를 border/배경대비로
  하고 shadow는 opacity 0.03~0.05 이하로만 보조적으로 씀)
- 브랜드색을 배경 면적으로 사용 (레퍼런스 전체 화면에서 브랜드색 면적은 1~3%, CTA 버튼만 예외
  5~6% — 텍스트·보더·뱃지·버튼 용도로만 등장, 배경 전체를 채운 화면 없음)
- 같은 브랜드색을 페이지마다 다른 hex로 재정의 (`#4321F3`[프론트엔드 4개 뷰어, 50회] vs
  `#4D2EE9`/`#3A1FC9`/`#3d25b5`[web/portal.py 계산기 페이지 자체 `:root` 토큰] — 사실상 같은
  보라색 계열인데 코드상 3벌 이상의 값으로 나뉘어 있음)
- 배경 그레이 톤 4종 초과 정의 (`#f8fafc`·`#f9fafb`·`#f1f5f9`·`#f3f5f7`·`#f4f5f7`·`#eef0f2`·
  `#f7f8fb` 등 유사 회색 난립 — 레퍼런스는 화면당 배경 레이어 2~3단으로 제한)

### 강제
- spacing(margin/padding/gap)은 4px 배수만 사용 (레퍼런스 5개 화면 전체에서 예외 없이 관찰,
  현재 코드는 정반대)
- 폰트 위계는 인접 단계 배율 1.2~1.5배 유지, 히어로/헤드라인 1곳만 2~3배 예외 (레퍼런스 실측치)
- 핵심 수치(가격 등 비교 대상 데이터)는 라벨 대비 최소 1.5배, 강조가 필요하면 2배 이상 —
  단순 카운트·보조 수치는 크기 대신 색상 대비로만 구분 (레퍼런스: 가격 1.7배, 순번 숫자 2.1배,
  단순 카운트는 1배·색상만)
- 면 분리는 border 또는 배경색 대비를 우선 사용, box-shadow를 쓰더라도 opacity 0.05 이하 1종만
  보조적으로
- 브랜드색은 CSS 커스텀 프로퍼티(토큰) 1개로 정의하고 코드에 hex 직접 하드코딩 금지 — 페이지마다
  값이 갈라지는 것을 막기 위함
- 배경은 흰색 + 연회색 1단(+ 포인트 브랜드 틴트 1곳)까지만 사용
- 손익/증감처럼 의미가 고정된 색(수익=녹색 계열, 손실=빨강 계열)은 유지 — 이건 스크린샷 비교가
  아니라 기존에 확인된 프로젝트 원칙("색상은 항상 시맨틱하게 쓴다")에서 온 것

위 규칙에 못 들어간 것: 그라데이션 배경·이모지 아이콘 대용·전 섹션 카드 래핑·질문형 헤드라인
반복 같은 패턴은 인벤토리 스캔이 색상/폰트크기/radius/shadow/spacing만 다뤄서 근거 데이터가
없다. 필요하면 별도로 스캔해서 추가할 것.

---

## 스캔 범위

### 포함한 소스
실제로 배포되는(production) 스타일 소스만 포함했다.

| 파일 | 종류 | 비고 |
|---|---|---|
| `frontend/src/chat/styles.css` | 순수 CSS | chat 뷰어 |
| `frontend/src/gangnam/styles.css` | 순수 CSS | gangnam 뷰어 |
| `frontend/src/profit/styles.css` | 순수 CSS | profit 뷰어 |
| `frontend/src/samsam/styles.css` | 순수 CSS | samsam 뷰어 |
| `frontend/src/**/*.jsx` (18개 파일) | JSX 인라인 `style={{ }}` | 4개 뷰어 공통 |
| `web/portal.py` | Python 문자열 내 `<style>` (LANDING/PUBLIC_LANDING/CALC_PAGE 3개 템플릿) + inline `style="..."` | 랜딩/계산기 페이지 |
| `web/map_view.py` | Python 문자열 내 `<style>` (MAP_PAGE) + inline `style="..."` | 지도 페이지 |
| `web/auth.py` | Python 문자열 내 `<style>` (PAGE, 2개 블록) + inline `style="..."` | 로그인 페이지 |

Tailwind는 이 소스들에는 설정되어 있지 않다 (`tailwind.config.*` 없음, 전부 순수 CSS/인라인 style).

### 제외한 소스
- `design/culc_redesign/**` — Figma Make로 생성된 리디자인 초안(Tailwind v4 + shadcn 토큰 사용,
  `git status`상 미커밋 상태). "실제 사용 중"인 배포 코드가 아니라 새 디자인 목업이라 이번
  인벤토리에서는 제외했다. 필요하면 별도로 스캔 가능.
- `design/screenshots/**` — 정적 스크린샷 HTML.
- `web/gangnam_app.py`, `web/profit_app.py`, `web/samsam_app.py` — JSON API만 제공, 임베디드
  스타일 없음(해당 뷰어의 스타일은 `frontend/src/*/styles.css`가 담당).
- `frontend/dist/**`, `frontend/node_modules/**` — 빌드 산출물/의존성.

### 스캔 방법
정규식 기반 파서를 작성해 (1) `.css` 파일과 Python 문자열 내 `<style>` 블록/inline `style="..."`
속성은 CSS 문법(`prop: value;`)으로, (2) JSX의 `style={{ }}` 객체는 JS 문법(`prop: value,`)으로
각각 파싱했다. 값은 원문 그대로(단위 포함) 집계했으며, JSX의 숫자 리터럴(예: `fontSize: 13`)은
React 관례상 px 단위로 해석된다.

---

## CSS 커스텀 프로퍼티(디자인 토큰) 정의 현황

`frontend/src/*.css`에는 CSS 변수(`:root{}`)가 전혀 없다 — 색상이 전부 하드코딩된 hex 값이다.

`web/` 쪽 페이지 중 2곳만 자체 `:root{}` 토큰 세트를 정의한다:

**`web/portal.py:490` (PUBLIC_LANDING)**
```css
:root{--accent:#4D2EE9;--ink:#141824;--sub:#565C6E;--mut:#6B7080;--line:#ECEEF2;--line2:#E5E8EF;--bg-soft:#F7F8FB}
```

**`web/portal.py:874` (CALC_PAGE)**
```css
:root{--brand:#4D2EE9;--brand-hover:#3A1FC9;--brand-tint:#ECEAF8;--profit:#148A5E;--loss:#D24545;
--profit-bg:#EAF6F0;--loss-bg:#FBEAEA;--gold-bg:#FDF8ED;
--bg:#E7E9F3;--text:#1B1B3A;--text-sub:#8080A8;--line:#E2E2F0;--gold:#D89700;--field-bg:#EFEFF9}
```

`web/portal.py`의 LANDING 템플릿(287번 줄), `web/map_view.py`, `web/auth.py`, 4개 프론트엔드
뷰어(`frontend/src/*/styles.css`)는 변수 없이 값을 직접 하드코딩한다.

---

## 1. 색상 (전체 177개 값)

총 매칭 수 기준 내림차순. hex(`#rgb`/`#rrggbb`/`#rrggbbaa`)와 `rgba()`/`rgb()`를 모두 포함.

| 색상값 | 총 횟수 | 사용 파일(파일당 횟수) |
|---|---|---|
| `#fff` | 109 | auth.py×5, chat\styles.css×6, gangnam\styles.css×14, map_view.py×19, map_view.py (inline attr), portal.py×27, profit\App.jsx, profit\ProfitList.jsx, profit\styles.css×13, samsam\OptionView.jsx, samsam\styles.css×21 |
| `#4321F3` | 50 | auth.py×4, auth.py (inline attr), chat\styles.css×4, gangnam\styles.css×7, map_view.py×8, map_view.py (inline attr)×3, profit\App.jsx, profit\ProfitList.jsx×2, profit\styles.css×6, samsam\BuildingView.jsx×2, samsam\OptionView.jsx, samsam\TrendView.jsx, samsam\styles.css×10 |
| `#94a3b8` | 44 | auth.py×4, auth.py (inline attr), chat\styles.css, gangnam\App.jsx, gangnam\SamArea.jsx, gangnam\styles.css×4, map_view.py×6, map_view.py (inline attr)×4, portal.py×3, profit\App.jsx, profit\ProfitList.jsx×2, profit\RankDetailModal.jsx×2, profit\RankTab.jsx×2, profit\RecoTab.jsx, profit\styles.css×5, samsam\App.jsx, samsam\BuildingView.jsx×2, samsam\styles.css×3 |
| `#475569` | 23 | gangnam\styles.css×5, map_view.py×5, map_view.py (inline attr)×2, portal.py (inline attr), profit\ProfitList.jsx×2, profit\styles.css, samsam\styles.css×7 |
| `#9ca3af` | 22 | chat\AccountsPanel.jsx, chat\styles.css×3, gangnam\Modal.jsx, gangnam\styles.css×5, profit\Detail.jsx, profit\styles.css×7, samsam\OptionView.jsx, samsam\styles.css×3 |
| `#d1d5db` | 21 | auth.py, chat\styles.css×2, gangnam\App.jsx×2, gangnam\styles.css×3, profit\App.jsx, profit\RankTab.jsx, profit\styles.css×2, samsam\TrendView.jsx, samsam\styles.css×8 |
| `#e5e7eb` | 21 | auth.py, chat\styles.css×6, gangnam\styles.css×3, profit\ProfitList.jsx, profit\styles.css×6, samsam\styles.css×4 |
| `#64748b` | 19 | auth.py (inline attr)×2, chat\styles.css, gangnam\SamArea.jsx, gangnam\styles.css, map_view.py×3, map_view.py (inline attr)×2, portal.py×6, profit\ProfitList.jsx, profit\styles.css, samsam\styles.css |
| `#dc2626` | 15 | auth.py, auth.py (inline attr), chat\styles.css, gangnam\SamArea.jsx, gangnam\styles.css×2, map_view.py×2, profit\RecoTab.jsx, profit\styles.css×4, samsam\TrendView.jsx, samsam\styles.css |
| `#111827` | 14 | auth.py (inline attr), gangnam\styles.css×2, map_view.py×4, profit\ProfitList.jsx, profit\styles.css×3, samsam\styles.css×3 |
| `#cbd5e1` | 13 | auth.py×2, map_view.py×7, portal.py×2, profit\styles.css, samsam\App.jsx |
| `#eef0f2` | 13 | auth.py, auth.py (inline attr), gangnam\styles.css×2, map_view.py, map_view.py (inline attr), profit\ProfitList.jsx, profit\styles.css×4, samsam\styles.css×2 |
| `#059669` | 12 | auth.py (inline attr), chat\styles.css, gangnam\SamArea.jsx, gangnam\styles.css, map_view.py×5, map_view.py (inline attr), samsam\TrendView.jsx, samsam\styles.css |
| `#6b7280` | 12 | auth.py, chat\styles.css×4, gangnam\styles.css, profit\styles.css×3, samsam\styles.css×3 |
| `#1e293b` | 11 | auth.py×2, chat\styles.css, gangnam\styles.css, portal.py, profit\App.jsx, profit\ProfitList.jsx, profit\styles.css×3, samsam\styles.css |
| `#f1f5f9` | 10 | chat\styles.css×3, gangnam\styles.css, map_view.py, profit\ProfitList.jsx, profit\styles.css×2, samsam\styles.css×2 |
| `#0f172a` | 9 | auth.py×3, chat\styles.css, gangnam\styles.css, map_view.py, portal.py, profit\styles.css, samsam\styles.css |
| `#1f2937` | 9 | auth.py, chat\styles.css×2, gangnam\styles.css×2, map_view.py, portal.py, profit\styles.css, samsam\styles.css |
| `#0369a1` | 8 | gangnam\SamArea.jsx×2, gangnam\styles.css, profit\styles.css×2, samsam\TrendView.jsx, samsam\styles.css×2 |
| `#e2e8f0` | 8 | auth.py×2, map_view.py×3, portal.py×2, profit\App.jsx |
| `#7c3aed` | 7 | gangnam\styles.css, map_view.py, profit\RecoTab.jsx×4, samsam\OptionView.jsx |
| `#eff6ff` | 7 | auth.py×2, chat\styles.css, gangnam\SamArea.jsx, profit\ProfitList.jsx, samsam\styles.css×2 |
| `#4b5563` | 6 | auth.py, chat\styles.css, gangnam\styles.css, profit\styles.css, samsam\styles.css×2 |
| `#b45309` | 6 | profit\RankTab.jsx×2, profit\RecoTab.jsx×3, profit\styles.css |
| `#dbeafe` | 6 | gangnam\App.jsx, gangnam\SamArea.jsx, profit\ProfitList.jsx, profit\styles.css, samsam\styles.css×2 |
| `rgba(0,0,0,.2)` | 6 | auth.py, map_view.py×5 |
| `#0F9B62` | 5 | portal.py×5 |
| `#f3f5f7` | 5 | chat\styles.css, gangnam\styles.css, profit\styles.css×2, samsam\styles.css |
| `#f8fafc` | 5 | chat\styles.css×3, map_view.py, profit\ProfitList.jsx |
| `#f9fafb` | 5 | auth.py, auth.py (inline attr), profit\styles.css, samsam\styles.css×2 |
| `#fef3c7` | 5 | profit\RankTab.jsx×2, profit\RecoTab.jsx×3 |
| `rgba(0, 0, 0, .03)` | 5 | chat\styles.css, gangnam\styles.css×2, samsam\styles.css×2 |
| `rgba(0,0,0,.3)` | 5 | auth.py, map_view.py×2, profit\styles.css, samsam\styles.css |
| `rgba(0,0,0,.35)` | 5 | auth.py, map_view.py×3, portal.py |
| `rgba(255,255,255,.95)` | 5 | map_view.py×5 |
| `#334155` | 4 | map_view.py×2, map_view.py (inline attr), profit\styles.css |
| `#34d399` | 4 | portal.py×3, portal.py (inline attr) |
| `#3730a3` | 4 | gangnam\App.jsx, gangnam\styles.css×2, profit\ProfitList.jsx |
| `#374151` | 4 | gangnam\styles.css, profit\styles.css×2, samsam\styles.css |
| `#eef2ff` | 4 | gangnam\styles.css×2, profit\ProfitList.jsx, samsam\styles.css |
| `#1e3a5f` | 3 | chat\styles.css, gangnam\SamArea.jsx, profit\ProfitList.jsx |
| `#8990A0` | 3 | portal.py×3 |
| `#93c5fd` | 3 | chat\styles.css, portal.py×2 |
| `#B7791F` | 3 | portal.py×2, portal.py (inline attr) |
| `#b91c1c` | 3 | auth.py, samsam\styles.css×2 |
| `#f87171` | 3 | portal.py×2, portal.py (inline attr) |
| `#ff5a5f` | 3 | profit\styles.css, samsam\styles.css×2 |
| `rgba(15,23,42,.55)` | 3 | map_view.py, profit\styles.css, samsam\styles.css |
| `rgba(255,255,255,.92)` | 3 | portal.py×2, samsam\styles.css |
| `#03c75a` | 2 | gangnam\styles.css, profit\styles.css |
| `#064e3b` | 2 | chat\styles.css, samsam\styles.css |
| `#075985` | 2 | gangnam\App.jsx, gangnam\styles.css |
| `#0891b2` | 2 | gangnam\styles.css, map_view.py |
| `#14b8a6` | 2 | map_view.py, map_view.py (inline attr) |
| `#1e40af` | 2 | auth.py, map_view.py |
| `#4D2EE9` | 2 | portal.py×2 |
| `#6ee7b7` | 2 | chat\styles.css, samsam\styles.css |
| `#7c2d12` | 2 | chat\styles.css, samsam\styles.css |
| `#854d0e` | 2 | chat\styles.css, map_view.py |
| `#E2E2F0` | 2 | portal.py×2 |
| `#E4F3EC` | 2 | portal.py×2 |
| `#EAF6F0` | 2 | portal.py×2 |
| `#a16207` | 2 | map_view.py×2 |
| `#ca8a04` | 2 | map_view.py, map_view.py (inline attr) |
| `#f4f5f7` | 2 | map_view.py, samsam\styles.css |
| `#f59e0b` | 2 | map_view.py×2 |
| `#fbbf24` | 2 | portal.py×2 |
| `#fed7aa` | 2 | chat\styles.css, samsam\styles.css |
| `#fef2f2` | 2 | auth.py, samsam\styles.css |
| `#fef9c3` | 2 | chat\styles.css, map_view.py |
| `rgba(0, 0, 0, .3)` | 2 | gangnam\styles.css, profit\styles.css |
| `rgba(0,0,0,.25)` | 2 | portal.py×2 |
| `rgba(20,24,36,.35)` | 2 | portal.py×2 |
| `rgba(255,255,255,.03)` | 2 | portal.py×2 |
| `rgba(255,255,255,.07)` | 2 | portal.py×2 |
| `rgba(255,255,255,.72)` | 2 | portal.py, profit\styles.css |
| `rgba(255,255,255,.9)` | 2 | map_view.py×2 |
| `rgba(27,27,58,.04)` | 2 | portal.py×2 |
| `rgba(27,27,58,.15)` | 2 | portal.py×2 |
| `rgba(27,27,58,.25)` | 2 | portal.py×2 |
| `#047857` | 1 | auth.py |
| `#065f46` | 1 | map_view.py |
| `#141824` | 1 | portal.py |
| `#148A5E` | 1 | portal.py |
| `#155e75` | 1 | map_view.py |
| `#171A23` | 1 | auth.py (inline attr) |
| `#181430` | 1 | portal.py |
| `#185FA5` | 1 | portal.py |
| `#191919` | 1 | auth.py |
| `#1B1B3A` | 1 | portal.py |
| `#2563eb` | 1 | map_view.py |
| `#3517c4` | 1 | auth.py |
| `#38bdf8` | 1 | gangnam\styles.css |
| `#3A1FC9` | 1 | portal.py |
| `#3d25b5` | 1 | portal.py |
| `#565C6E` | 1 | portal.py |
| `#5b21b6` | 1 | map_view.py |
| `#6B7080` | 1 | portal.py |
| `#7C8090` | 1 | portal.py |
| `#8080A8` | 1 | portal.py |
| `#818cf8` | 1 | gangnam\styles.css |
| `#8b7dff` | 1 | auth.py |
| `#92400e` | 1 | map_view.py |
| `#991b1b` | 1 | map_view.py |
| `#9d174d` | 1 | map_view.py |
| `#A9A4C9` | 1 | portal.py |
| `#C0392B` | 1 | portal.py |
| `#D24545` | 1 | portal.py |
| `#D7DBE4` | 1 | portal.py |
| `#D89700` | 1 | portal.py |
| `#DCE1EC` | 1 | portal.py |
| `#E1E6F2` | 1 | portal.py |
| `#E5E8EF` | 1 | portal.py |
| `#E6F1FB` | 1 | portal.py |
| `#E7E9F3` | 1 | portal.py |
| `#ECEAF8` | 1 | portal.py |
| `#ECEEF2` | 1 | portal.py |
| `#EEF0F4` | 1 | portal.py |
| `#EFEFF9` | 1 | portal.py |
| `#F1F4FC` | 1 | portal.py |
| `#F2F3FA` | 1 | portal.py |
| `#F4F6FB` | 1 | portal.py |
| `#F7F8FB` | 1 | portal.py |
| `#FBEAEA` | 1 | portal.py |
| `#FBFCFE` | 1 | portal.py |
| `#FDF0D9` | 1 | portal.py |
| `#FDF8ED` | 1 | portal.py |
| `#FEE500` | 1 | auth.py |
| `#be185d` | 1 | map_view.py |
| `#bfdbfe` | 1 | gangnam\SamArea.jsx |
| `#c7bcff` | 1 | map_view.py |
| `#c7d2fe` | 1 | gangnam\styles.css |
| `#d97706` | 1 | map_view.py |
| `#db2777` | 1 | map_view.py |
| `#e0e7ff` | 1 | gangnam\App.jsx |
| `#e0f2fe` | 1 | gangnam\styles.css |
| `#e8eaed` | 1 | gangnam\styles.css |
| `#ea580c` | 1 | gangnam\styles.css |
| `#eab308` | 1 | map_view.py (inline attr) |
| `#ecfdf5` | 1 | auth.py |
| `#ecfeff` | 1 | samsam\styles.css |
| `#ef4444` | 1 | chat\styles.css |
| `#f3f4f6` | 1 | samsam\styles.css |
| `#f5dc00` | 1 | auth.py |
| `#f9a8d4` | 1 | map_view.py |
| `#fca5a5` | 1 | portal.py |
| `#fde68a` | 1 | profit\styles.css |
| `#fee2e2` | 1 | samsam\styles.css |
| `#fffbeb` | 1 | profit\styles.css |
| `rgba(0, 0, 0, .09)` | 1 | gangnam\styles.css |
| `rgba(0, 0, 0, .35)` | 1 | profit\styles.css |
| `rgba(0,0,0,.12)` | 1 | samsam\styles.css |
| `rgba(0,0,0,.15)` | 1 | samsam\styles.css |
| `rgba(15, 23, 42, .45)` | 1 | profit\styles.css |
| `rgba(15, 23, 42, .5)` | 1 | profit\styles.css |
| `rgba(15, 23, 42, .55)` | 1 | gangnam\styles.css |
| `rgba(15,23,42,.6)` | 1 | map_view.py |
| `rgba(15,23,42,.75)` | 1 | map_view.py |
| `rgba(15,23,42,.92)` | 1 | map_view.py |
| `rgba(20,24,36,.25)` | 1 | portal.py |
| `rgba(251,146,60,.05)` | 1 | portal.py |
| `rgba(251,146,60,.35)` | 1 | portal.py |
| `rgba(251,146,60,.5)` | 1 | portal.py |
| `rgba(255,255,255,.04)` | 1 | portal.py |
| `rgba(255,255,255,.09)` | 1 | portal.py |
| `rgba(255,255,255,.7)` | 1 | portal.py |
| `rgba(255,255,255,.85)` | 1 | map_view.py |
| `rgba(255,255,255,.96)` | 1 | portal.py |
| `rgba(255,255,255,0)` | 1 | portal.py |
| `rgba(27,27,58,.95)` | 1 | portal.py |
| `rgba(52,211,153,.05)` | 1 | portal.py (inline attr) |
| `rgba(52,211,153,.35)` | 1 | portal.py (inline attr) |
| `rgba(56,189,248,.05)` | 1 | portal.py (inline attr) |
| `rgba(56,189,248,.35)` | 1 | portal.py (inline attr) |
| `rgba(67,33,243,.1)` | 1 | auth.py |
| `rgba(67,33,243,.93)` | 1 | map_view.py |
| `rgba(77,46,233,.25)` | 1 | portal.py |

같은 색이 `#4321F3`/`#4D2EE9`/`#3d25b5`처럼 대소문자·유사톤으로 여러 벌 존재하고, `#fff`/`white`
같은 표기 통일도 되어 있지 않다 — 이런 중복은 판단 없이 원문 그대로 남겨둔다.

---

## 2. font-size (전체 38개 값)

| font-size | 총 횟수 | 사용 파일(파일당 횟수) |
|---|---|---|
| `13px` | 55 | auth.py×3, chat\styles.css×6, gangnam\styles.css×4, map_view.py×5, map_view.py (inline attr), portal.py×22, profit\styles.css×6, samsam\styles.css×8 |
| `12px` | 49 | auth.py×3, auth.py (inline attr), chat\styles.css×2, gangnam\styles.css×6, map_view.py×6, map_view.py (inline attr), portal.py×16, profit\styles.css×6, samsam\styles.css×8 |
| `14px` | 37 | auth.py×4, chat\styles.css, gangnam\styles.css×2, map_view.py×4, map_view.py (inline attr)×2, portal.py×20, profit\styles.css, samsam\styles.css×3 |
| `11px` | 35 | auth.py (inline attr), chat\styles.css×3, gangnam\styles.css×3, map_view.py×5, map_view.py (inline attr), portal.py×9, profit\styles.css×4, samsam\styles.css×9 |
| `12.5px` | 31 | auth.py, chat\styles.css×2, gangnam\styles.css×3, map_view.py (inline attr)×3, portal.py×12, profit\styles.css×3, samsam\styles.css×7 |
| `11.5px` | 21 | chat\styles.css×4, gangnam\styles.css×3, map_view.py×7, portal.py×3, profit\styles.css×4 |
| `15px` | 17 | auth.py, auth.py (inline attr)×3, gangnam\styles.css, map_view.py×4, portal.py×6, samsam\styles.css×2 |
| `16px` | 17 | auth.py, chat\styles.css, gangnam\styles.css, map_view.py, portal.py×6, profit\styles.css×5, samsam\styles.css×2 |
| `17px` | 11 | chat\styles.css, gangnam\styles.css×2, portal.py×7, samsam\styles.css |
| `12` (JSX, px) | 9 | gangnam\SamArea.jsx, profit\Detail.jsx, profit\RankTab.jsx, samsam\BuildingView.jsx×4, samsam\MapView.jsx, samsam\OptionView.jsx |
| `13.5px` | 8 | chat\styles.css×2, gangnam\styles.css, map_view.py, portal.py×2, profit\styles.css×2 |
| `13` (JSX, px) | 7 | gangnam\App.jsx×2, gangnam\Modal.jsx, profit\App.jsx×2, profit\ProfitList.jsx×2 |
| `24px` | 7 | auth.py×3, gangnam\styles.css, portal.py, profit\styles.css×2 |
| `10.5px` | 6 | chat\styles.css×2, map_view.py, portal.py×2, samsam\styles.css |
| `10px` | 6 | chat\styles.css, map_view.py×3, portal.py, samsam\styles.css |
| `20px` | 6 | auth.py, gangnam\styles.css, portal.py×4 |
| `10` (JSX, px) | 5 | profit\RankTab.jsx×2, profit\RecoTab.jsx×3 |
| `12.5` (JSX, px) | 5 | chat\AccountsPanel.jsx, gangnam\SamArea.jsx, profit\ProfitList.jsx, samsam\RankingView.jsx, samsam\TrendView.jsx |
| `19px` | 5 | chat\styles.css, gangnam\styles.css, portal.py, samsam\styles.css×2 |
| `21px` | 5 | portal.py×4, profit\styles.css |
| `22px` | 5 | auth.py (inline attr), gangnam\styles.css, portal.py×3 |
| `14` (JSX, px) | 4 | gangnam\SamArea.jsx, profit\RankTab.jsx, profit\RecoTab.jsx×2 |
| `18px` | 4 | gangnam\styles.css, profit\styles.css×3 |
| `28px` | 4 | portal.py×3, profit\styles.css |
| `11` (JSX, px) | 3 | profit\Detail.jsx, samsam\BuildingView.jsx, samsam\OptionView.jsx |
| `11.5` (JSX, px) | 3 | gangnam\Card.jsx, samsam\App.jsx, samsam\TrendView.jsx |
| `13.5` (JSX, px) | 3 | profit\App.jsx, profit\ProfitList.jsx×2 |
| `9px` | 3 | map_view.py×3 |
| `15` (JSX, px) | 2 | profit\ProfitList.jsx×2 |
| `15.5px` | 2 | auth.py, gangnam\styles.css |
| `23px` | 2 | gangnam\styles.css, samsam\styles.css |
| `9.5px` | 2 | map_view.py (inline attr), portal.py |
| `27px` | 1 | portal.py |
| `29px` | 1 | portal.py |
| `30px` | 1 | portal.py |
| `36px` | 1 | portal.py |
| `40px` | 1 | portal.py |
| `42px` | 1 | portal.py |

`(JSX, px)` 표기는 React 인라인 `style={{ fontSize: N }}`처럼 단위 없이 숫자로 쓴 값으로,
브라우저 기본 해석은 px다.

---

## 3. border-radius (전체 31개 값)

| border-radius | 총 횟수 | 사용 파일(파일당 횟수) |
|---|---|---|
| `999px` | 23 | chat\styles.css×2, gangnam\styles.css×3, map_view.py×7, portal.py×6, samsam\styles.css×5 |
| `12px` | 19 | chat\styles.css×2, gangnam\styles.css×2, map_view.py×2, portal.py×9, profit\styles.css, samsam\styles.css×3 |
| `8px` | 19 | auth.py×6, chat\styles.css, gangnam\styles.css, map_view.py×6, portal.py×3, samsam\styles.css×2 |
| `10px` | 15 | auth.py (inline attr), chat\styles.css×3, map_view.py×2, map_view.py (inline attr), portal.py×2, profit\styles.css×4, samsam\styles.css×2 |
| `16px` | 12 | auth.py, gangnam\styles.css, map_view.py, portal.py×8, samsam\styles.css |
| `7px` | 10 | chat\styles.css×2, gangnam\styles.css×3, profit\styles.css×2, samsam\styles.css×3 |
| `6px` | 8 | chat\styles.css, gangnam\styles.css, profit\styles.css×3, samsam\styles.css×3 |
| `14px` | 5 | portal.py×3, profit\styles.css×2 |
| `4` (JSX, px) | 5 | profit\RankTab.jsx×2, profit\RecoTab.jsx×3 |
| `16px 16px 0 0` | 4 | gangnam\styles.css, map_view.py, profit\styles.css, samsam\styles.css |
| `50%` | 4 | map_view.py×3, portal.py |
| `9px` | 4 | map_view.py, portal.py×3 |
| `11px` | 3 | portal.py×3 |
| `7` (JSX, px) | 3 | gangnam\App.jsx×2, profit\App.jsx |
| `13px` | 2 | gangnam\styles.css, map_view.py |
| `20px` | 2 | portal.py×2 |
| `6` (JSX, px) | 2 | profit\RankTab.jsx, samsam\TrendView.jsx |
| `8` (JSX, px) | 2 | gangnam\SamArea.jsx, profit\App.jsx |
| `0 7px 7px 0` | 1 | samsam\styles.css |
| `10px 10px 0 0` | 1 | chat\styles.css |
| `14px 14px 0 0` | 1 | profit\styles.css |
| `18px` | 1 | portal.py |
| `22px` | 1 | portal.py |
| `24px` | 1 | portal.py |
| `3px 3px 0 0` | 1 | portal.py |
| `4px 4px 0 0` | 1 | portal.py |
| `5px` | 1 | samsam\styles.css |
| `7px 0 0 7px` | 1 | samsam\styles.css |
| `8px 8px 0 0` | 1 | profit\styles.css |
| `9` (JSX, px) | 1 | profit\ProfitList.jsx |
| `999` (JSX, px) | 1 | samsam\OptionView.jsx |

`999px`/`999`는 pill(알약형) 뱃지·버튼에, `50%`는 원형 아바타/도트에 쓰인다.

---

## 4. box-shadow (전체 28개 값)

색상이 대부분 `rgba()`라 완전히 동일한 값이 드물다 — offset·blur·투명도 조합이 사실상 파일마다
다르게 하드코딩되어 있다.

| box-shadow | 총 횟수 | 사용 파일(파일당 횟수) |
|---|---|---|
| `0 1px 2px rgba(0, 0, 0, .03)` | 5 | chat\styles.css, gangnam\styles.css×2, samsam\styles.css×2 |
| `0 1px 4px rgba(0,0,0,.2)` | 5 | map_view.py×5 |
| `0 0 0 3px var(--brand-tint)` | 2 | portal.py×2 |
| `0 1px 2px rgba(27,27,58,.04),0 14px 28px -16px rgba(27,27,58,.15)` | 2 | portal.py×2 |
| `0 20px 60px rgba(0, 0, 0, .3)` | 2 | gangnam\styles.css, profit\styles.css |
| `0 20px 60px rgba(0,0,0,.3)` | 2 | auth.py, profit\styles.css |
| `0 2px 8px rgba(0,0,0,.35)` | 2 | map_view.py×2 |
| `0 -8px 30px rgba(0, 0, 0, .35)` | 1 | profit\styles.css |
| `0 -8px 40px rgba(0,0,0,.3)` | 1 | samsam\styles.css |
| `0 0 0 3px rgba(67,33,243,.1)` | 1 | auth.py |
| `0 10px 30px rgba(0,0,0,.25)` | 1 | portal.py |
| `0 12px 32px -8px rgba(27,27,58,.25)` | 1 | portal.py |
| `0 12px 32px rgba(0,0,0,.25)` | 1 | portal.py |
| `0 14px 28px -14px rgba(77,46,233,.25)` | 1 | portal.py |
| `0 16px 40px rgba(0,0,0,.35)` | 1 | portal.py |
| `0 1px 4px rgba(0,0,0,.15)` | 1 | samsam\styles.css |
| `0 1px 4px rgba(0,0,0,.35)` | 1 | map_view.py |
| `0 1px 4px rgba(27,27,58,.25)` | 1 | portal.py |
| `0 1px 5px rgba(0,0,0,.3)` | 1 | map_view.py |
| `0 1px 6px rgba(0,0,0,.2)` | 1 | auth.py |
| `0 20px 50px -24px rgba(20,24,36,.35)` | 1 | portal.py |
| `0 2px 8px rgba(0,0,0,.3)` | 1 | map_view.py |
| `0 30px 70px -34px rgba(20,24,36,.35)` | 1 | portal.py |
| `0 6px 16px -8px var(--accent)` | 1 | portal.py |
| `0 6px 18px rgba(0, 0, 0, .09)` | 1 | gangnam\styles.css |
| `0 6px 20px -14px rgba(20,24,36,.25)` | 1 | portal.py |
| `0 8px 20px rgba(0,0,0,.35)` | 1 | auth.py |
| `2px 0 4px -2px rgba(0,0,0,.12)` | 1 | samsam\styles.css |

---

## 5. spacing (margin / padding / gap)

### 5-1. margin (전체 78개 값)

| margin | 총 횟수 | 사용 파일(파일당 횟수) |
|---|---|---|
| `0` | 34 | auth.py×2, auth.py (inline attr), chat\styles.css×2, gangnam\styles.css×3, map_view.py, portal.py×17, profit\App.jsx, profit\RecoTab.jsx, profit\styles.css×4, samsam\styles.css×2 |
| `6px` | 21 | chat\styles.css, gangnam\styles.css, portal.py×15, profit\styles.css×2, samsam\styles.css×2 |
| `16px` | 19 | auth.py, chat\styles.css, gangnam\styles.css×3, map_view.py, map_view.py (inline attr), portal.py×10, samsam\styles.css×2 |
| `8px` | 15 | chat\styles.css, gangnam\styles.css×3, map_view.py, portal.py×8, profit\styles.css, samsam\styles.css |
| `0 auto` | 12 | chat\styles.css, gangnam\styles.css, portal.py×9, samsam\styles.css |
| `12px` | 9 | chat\styles.css, gangnam\styles.css, portal.py, profit\styles.css×5, samsam\styles.css |
| `4px` | 9 | chat\styles.css, gangnam\styles.css, portal.py×4, profit\styles.css, samsam\styles.css×2 |
| `5` (JSX, px) | 9 | gangnam\SamArea.jsx, profit\RankTab.jsx×3, profit\RecoTab.jsx×5 |
| `10px` | 8 | auth.py (inline attr), chat\styles.css, gangnam\styles.css×2, map_view.py (inline attr), portal.py×3 |
| `20px` | 8 | auth.py, auth.py (inline attr)×2, portal.py×5 |
| `3px` | 8 | chat\styles.css×2, gangnam\styles.css, portal.py, profit\styles.css×2, samsam\styles.css×2 |
| `0 0 10px` | 7 | chat\styles.css, portal.py, profit\ProfitList.jsx, profit\RankTab.jsx, profit\RecoTab.jsx, samsam\styles.css×2 |
| `2px` | 7 | auth.py (inline attr), chat\styles.css, map_view.py, portal.py×2, profit\styles.css, samsam\styles.css |
| `10` (JSX, px) | 4 | profit\ProfitList.jsx×2, profit\RankTab.jsx, profit\RecoTab.jsx |
| `13` (JSX, px) | 4 | gangnam\App.jsx×4 |
| `14px` | 4 | portal.py×4 |
| `1px` | 4 | map_view.py, profit\styles.css×2, samsam\styles.css |
| `0 0 4px` | 3 | auth.py, portal.py, samsam\styles.css |
| `22px 0 6px` | 3 | auth.py (inline attr)×3 |
| `2px 0 8px` | 3 | profit\RankTab.jsx, profit\RecoTab.jsx×2 |
| `32px` | 3 | portal.py×3 |
| `5px 0 0` | 3 | chat\styles.css, gangnam\styles.css, samsam\styles.css |
| `6` (JSX, px) | 3 | profit\RankTab.jsx, profit\RecoTab.jsx, samsam\App.jsx |
| `-20px -14px 20px` | 2 | portal.py×2 |
| `0 0 12px` | 2 | portal.py, samsam\styles.css |
| `0 0 2px` | 2 | portal.py×2 |
| `0 0 6px` | 2 | portal.py, profit\styles.css |
| `0 0 8px` | 2 | gangnam\styles.css, portal.py |
| `12` (JSX, px) | 2 | profit\ProfitList.jsx×2 |
| `12px 0 4px` | 2 | auth.py, map_view.py |
| `14px 0` | 2 | auth.py (inline attr), portal.py |
| `18px` | 2 | gangnam\styles.css, portal.py |
| `22px` | 2 | portal.py×2 |
| `26px` | 2 | portal.py×2 |
| `5px` | 2 | chat\styles.css, portal.py |
| `-20px -20px 16px` | 1 | portal.py |
| `0 0 16px` | 1 | portal.py |
| `0 0 18px` | 1 | portal.py |
| `0 0 20px` | 1 | auth.py |
| `0 0 9px` | 1 | portal.py |
| `0 60px 2px 0` | 1 | gangnam\styles.css |
| `0 6px` | 1 | portal.py |
| `0 auto 28px` | 1 | portal.py |
| `0 auto 32px` | 1 | portal.py |
| `10px 0 4px` | 1 | portal.py |
| `10px 0 8px` | 1 | portal.py |
| `11px` | 1 | gangnam\styles.css |
| `12px 0` | 1 | auth.py |
| `12px 0 0` | 1 | portal.py |
| `14` (JSX, px) | 1 | profit\ProfitList.jsx |
| `14px 0 8px` | 1 | portal.py |
| `18px 0` | 1 | auth.py |
| `2` (JSX, px) | 1 | profit\ProfitList.jsx |
| `20px 0` | 1 | portal.py |
| `24px` | 1 | portal.py |
| `28px` | 1 | portal.py |
| `2px 0 12px` | 1 | profit\App.jsx |
| `2px 0 14px` | 1 | portal.py |
| `2px 0 16px` | 1 | portal.py |
| `2px 4px 0` | 1 | portal.py |
| `3` (JSX, px) | 1 | gangnam\SamArea.jsx |
| `3px 0 10px` | 1 | profit\styles.css |
| `3px 2px` | 1 | samsam\styles.css |
| `4` (JSX, px) | 1 | profit\RecoTab.jsx |
| `40px` | 1 | portal.py |
| `4px 0 0` | 1 | profit\styles.css |
| `4px 0 10px` | 1 | samsam\TrendView.jsx |
| `4px 0 12px` | 1 | samsam\RankingView.jsx |
| `4px 0 2px` | 1 | portal.py |
| `64px` | 1 | portal.py |
| `6px 0 14px` | 1 | profit\ProfitList.jsx |
| `6px 0 28px` | 1 | portal.py |
| `7px` | 1 | portal.py |
| `8` (JSX, px) | 1 | gangnam\SamArea.jsx |
| `8px 0` | 1 | auth.py |
| `8px 0 0` | 1 | portal.py |
| `9px` | 1 | profit\styles.css |
| `auto` | 1 | auth.py |

### 5-2. padding (전체 152개 값)

| padding | 총 횟수 | 사용 파일(파일당 횟수) |
|---|---|---|
| `14px` | 11 | auth.py (inline attr), chat\styles.css, map_view.py (inline attr), portal.py×4, profit\styles.css×4 |
| `3px 8px` | 7 | gangnam\styles.css, profit\RankDetailModal.jsx×2, profit\RecoTab.jsx×2, samsam\styles.css×2 |
| `12px` | 6 | auth.py×3, chat\styles.css, map_view.py, profit\styles.css |
| `4px 10px` | 6 | map_view.py×4, portal.py, samsam\BuildingView.jsx |
| `7px 9px` | 6 | chat\styles.css, gangnam\App.jsx×2, gangnam\styles.css, samsam\styles.css×2 |
| `8px 12px` | 6 | chat\styles.css×2, portal.py, profit\styles.css×2, samsam\styles.css |
| `10px 12px` | 5 | auth.py×2, chat\styles.css, portal.py, samsam\styles.css |
| `1px 5px` | 5 | profit\RankTab.jsx×2, profit\RecoTab.jsx×3 |
| `15px` | 4 | chat\styles.css, gangnam\styles.css×2, samsam\styles.css |
| `16px 24px` | 4 | chat\styles.css×2, samsam\styles.css×2 |
| `2px 7px` | 4 | profit\RecoTab.jsx×2, samsam\styles.css×2 |
| `2px 8px` | 4 | auth.py, chat\styles.css, samsam\OptionView.jsx, samsam\styles.css |
| `8px 10px` | 4 | auth.py, chat\styles.css, profit\styles.css×2 |
| `10px 14px` | 3 | chat\styles.css, portal.py×2 |
| `14px 12px` | 3 | chat\styles.css, gangnam\styles.css, samsam\styles.css |
| `14px 14px` | 3 | chat\styles.css, gangnam\styles.css, samsam\styles.css |
| `16px` | 3 | portal.py, profit\styles.css×2 |
| `20px` | 3 | auth.py, portal.py, profit\styles.css |
| `40px 0` | 3 | chat\styles.css, profit\styles.css, samsam\styles.css |
| `5px 11px` | 3 | map_view.py, samsam\styles.css×2 |
| `6px 11px` | 3 | map_view.py×2, samsam\styles.css |
| `6px 12px` | 3 | map_view.py, portal.py×2 |
| `6px 8px` | 3 | profit\styles.css, samsam\TrendView.jsx, samsam\styles.css |
| `7px 14px` | 3 | portal.py×2, profit\styles.css |
| `8px 18px` | 3 | gangnam\styles.css, map_view.py, samsam\styles.css |
| `0 4px` | 2 | gangnam\styles.css, profit\styles.css |
| `11px` | 2 | auth.py, portal.py |
| `11px 20px` | 2 | portal.py×2 |
| `12px 13px` | 2 | portal.py, samsam\styles.css |
| `12px 18px` | 2 | auth.py, portal.py |
| `13px 18px` | 2 | gangnam\styles.css, samsam\styles.css |
| `14px 40px` | 2 | portal.py×2 |
| `16px 20px` | 2 | portal.py, profit\styles.css |
| `18px 24px` | 2 | gangnam\styles.css×2 |
| `24px` | 2 | portal.py×2 |
| `26px` | 2 | portal.py×2 |
| `28px` | 2 | portal.py×2 |
| `30` (JSX, px) | 2 | profit\RankDetailModal.jsx×2 |
| `40px 16px` | 2 | gangnam\styles.css, profit\styles.css |
| `4px 11px` | 2 | gangnam\styles.css, portal.py |
| `4px 7px` | 2 | map_view.py, samsam\styles.css |
| `4px 8px` | 2 | profit\RankTab.jsx, profit\styles.css |
| `5px 9px` | 2 | map_view.py, samsam\styles.css |
| `6px` | 2 | portal.py, profit\styles.css |
| `6px 10px` | 2 | portal.py, profit\App.jsx |
| `6px 13px` | 2 | gangnam\styles.css, samsam\styles.css |
| `8px 15px` | 2 | portal.py×2 |
| `9px 18px` | 2 | gangnam\styles.css, profit\styles.css |
| `0` | 1 | gangnam\styles.css |
| `0 2px` | 1 | profit\styles.css |
| `0 32px 0 12px` | 1 | portal.py |
| `0 3px` | 1 | gangnam\styles.css |
| `10px` | 1 | map_view.py |
| `10px 16px` | 1 | profit\styles.css |
| `10px 22px` | 1 | profit\App.jsx |
| `10px 8px` | 1 | map_view.py (inline attr) |
| `11px 14px` | 1 | samsam\styles.css |
| `11px 16px` | 1 | map_view.py |
| `11px 22px` | 1 | portal.py |
| `11px 8px` | 1 | map_view.py |
| `12` (JSX, px) | 1 | profit\ProfitList.jsx |
| `12px 0` | 1 | profit\styles.css |
| `12px 10px` | 1 | auth.py |
| `12px 14px` | 1 | profit\styles.css |
| `12px 16px` | 1 | portal.py |
| `12px 18px 18px` | 1 | samsam\styles.css |
| `12px 26px` | 1 | profit\ProfitList.jsx |
| `12px 28px` | 1 | map_view.py (inline attr) |
| `14px 16px 18px` | 1 | gangnam\styles.css |
| `14px 18px` | 1 | map_view.py |
| `14px 22px` | 1 | profit\styles.css |
| `14px 24px` | 1 | auth.py |
| `15px 22px` | 1 | profit\styles.css |
| `16px 10px` | 1 | gangnam\styles.css |
| `16px 16px` | 1 | gangnam\styles.css |
| `16px 18px` | 1 | portal.py |
| `16px 18px 10px` | 1 | samsam\styles.css |
| `16px 30px` | 1 | portal.py |
| `16px 36px` | 1 | portal.py |
| `16px 8px` | 1 | profit\styles.css |
| `18px` | 1 | portal.py |
| `18px 10px` | 1 | map_view.py (inline attr) |
| `18px 20px` | 1 | portal.py |
| `18px 22px` | 1 | portal.py |
| `18px 24px 24px` | 1 | gangnam\styles.css |
| `1px 6px` | 1 | chat\styles.css |
| `1px 6px 1px 3px` | 1 | map_view.py |
| `20px 0` | 1 | gangnam\styles.css |
| `20px 14px 60px` | 1 | portal.py |
| `20px 22px` | 1 | profit\styles.css |
| `20px 24px` | 1 | gangnam\styles.css |
| `22px` | 1 | portal.py |
| `22px 16px` | 1 | auth.py |
| `22px 24px` | 1 | portal.py |
| `24px 16px` | 1 | profit\ProfitList.jsx |
| `24px 22px` | 1 | portal.py |
| `2px 18px 18px` | 1 | map_view.py (inline attr) |
| `2px 8px 2px 4px` | 1 | map_view.py |
| `2px 9px` | 1 | samsam\styles.css |
| `32px` | 1 | auth.py |
| `32px 20px` | 1 | portal.py |
| `32px 40px` | 1 | portal.py |
| `34px 26px` | 1 | portal.py |
| `38px` | 1 | portal.py |
| `3px 6px 3px 12px` | 1 | map_view.py |
| `3px 9px` | 1 | gangnam\styles.css |
| `44px 20px` | 1 | portal.py |
| `45px 40px` | 1 | portal.py |
| `4px 0` | 1 | profit\styles.css |
| `4px 12px` | 1 | portal.py |
| `4px 12px 14px` | 1 | map_view.py |
| `4px 6px 4px 11px` | 1 | gangnam\styles.css |
| `5` (JSX, px) | 1 | gangnam\SamArea.jsx |
| `52px` | 1 | map_view.py |
| `52px 22px` | 1 | portal.py |
| `5px` | 1 | gangnam\styles.css |
| `5px 10px` | 1 | map_view.py |
| `5px 12px` | 1 | map_view.py |
| `60px 0` | 1 | gangnam\styles.css |
| `64px 40px 20px` | 1 | portal.py |
| `64px 40px 68px` | 1 | portal.py |
| `68px 40px` | 1 | portal.py |
| `6px 10px 12px` | 1 | auth.py |
| `6px 14px` | 1 | samsam\BuildingView.jsx |
| `6px 18px` | 1 | samsam\styles.css |
| `6px 4px` | 1 | portal.py |
| `6px 6px` | 1 | samsam\styles.css |
| `70px 40px 68px` | 1 | portal.py |
| `72px 40px` | 1 | portal.py |
| `72px 40px 68px` | 1 | portal.py |
| `7px` | 1 | gangnam\styles.css |
| `7px 0` | 1 | gangnam\App.jsx |
| `7px 10px` | 1 | gangnam\SamArea.jsx |
| `7px 16px` | 1 | profit\App.jsx |
| `7px 6px` | 1 | portal.py |
| `7px 8px` | 1 | auth.py |
| `80px 40px` | 1 | portal.py |
| `8px` | 1 | map_view.py (inline attr) |
| `8px 0` | 1 | profit\ProfitList.jsx |
| `8px 12px 16px` | 1 | profit\styles.css |
| `8px 12px 4px` | 1 | chat\styles.css |
| `8px 14px` | 1 | auth.py |
| `8px 16px` | 1 | profit\styles.css |
| `8px 22px 0` | 1 | profit\styles.css |
| `8px 9px` | 1 | samsam\styles.css |
| `9px` | 1 | map_view.py (inline attr) |
| `9px 0` | 1 | samsam\styles.css |
| `9px 11px` | 1 | map_view.py |
| `9px 13px` | 1 | profit\ProfitList.jsx |
| `9px 16px` | 1 | chat\styles.css |
| `9px 20px` | 1 | samsam\styles.css |

> 스캔 중 `web/portal.py:888`의 한글 주석("...body padding:0)와 간격 맞춤.")이 정규식에 오탐으로
> 걸려 제외했다 — 실제 CSS 선언이 아니라 코드 주석이다.

### 5-3. gap (전체 25개 값)

| gap | 총 횟수 | 사용 파일(파일당 횟수) |
|---|---|---|
| `8px` | 19 | auth.py×2, chat\styles.css×4, gangnam\styles.css, map_view.py, portal.py×4, profit\styles.css, samsam\styles.css×6 |
| `6px` | 17 | chat\styles.css, gangnam\styles.css×3, map_view.py×2, map_view.py (inline attr), portal.py×6, profit\styles.css, samsam\styles.css×3 |
| `12px` | 14 | auth.py (inline attr), gangnam\styles.css, map_view.py, portal.py×6, profit\styles.css×3, samsam\styles.css×2 |
| `10px` | 11 | auth.py, chat\styles.css, gangnam\styles.css, map_view.py, portal.py×4, profit\styles.css×2, samsam\styles.css |
| `4px` | 9 | auth.py, chat\styles.css, gangnam\styles.css, map_view.py, portal.py×2, profit\styles.css, samsam\styles.css×2 |
| `16px` | 8 | portal.py×8 |
| `5px` | 8 | gangnam\styles.css×2, map_view.py, portal.py×2, samsam\styles.css×3 |
| `4` (JSX, px) | 6 | gangnam\App.jsx×3, profit\ProfitList.jsx, samsam\FilterPanel.jsx×2 |
| `14px` | 5 | chat\styles.css, gangnam\styles.css×2, portal.py, samsam\styles.css |
| `14` (JSX, px) | 4 | profit\RankTab.jsx, profit\RecoTab.jsx, samsam\RankingView.jsx, samsam\TrendView.jsx |
| `20px` | 3 | portal.py×3 |
| `3px` | 3 | map_view.py×2, profit\styles.css |
| `12` (JSX, px) | 2 | profit\App.jsx, profit\ProfitList.jsx |
| `7` (JSX, px) | 2 | gangnam\App.jsx×2 |
| `7px` | 2 | gangnam\styles.css, map_view.py |
| `9px` | 2 | portal.py×2 |
| `0` | 1 | auth.py |
| `10` (JSX, px) | 1 | profit\App.jsx |
| `18px` | 1 | portal.py |
| `1px` | 1 | portal.py |
| `26px` | 1 | portal.py |
| `2px` | 1 | profit\styles.css |
| `36px` | 1 | portal.py |
| `6` (JSX, px) | 1 | gangnam\App.jsx |
| `7px 18px` | 1 | gangnam\styles.css |

---

## 부록: 스캔 방법 노트

- 색상 정규식: `#[0-9a-fA-F]{3,8}`, `rgba?\([^)]+\)`, `hsla?\([^)]+\)`, `oklch\([^)]+\)` (프로젝트
  코드에는 hsl/oklch 값은 없었다).
- CSS 문법 소스는 `prop: value;` 또는 `prop: value}` (선택자 경계) 둘 다에서 값을 끊어 잡도록
  처리했다 — Python 문자열 내 `<style>` 블록이 줄바꿈 없이 압축(minify)되어 있어 `;`/`}` 둘 다를
  종료 지점으로 취급하지 않으면 값이 다음 규칙까지 이어붙는 오류가 난다.
- JSX 소스는 `style={{ ... }}` 블록을 중괄호 균형으로 추출한 뒤, `key: 'value'` / `key: 123`
  형태를 프로퍼티명(`fontSize`, `borderRadius`, `boxShadow`, `margin(Top|Bottom|Left|Right)?`,
  `padding(Top|Bottom|Left|Right)?`, `gap`)별로 따로 파싱했다.
- 이 문서는 개선안이나 우선순위 판단을 포함하지 않는다. 현황 정리가 목적이다.
