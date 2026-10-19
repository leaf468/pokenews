# 🃏 포켓몬 카드 Threads 봇

포켓몬 카드의 **최신 뉴스·발매 소식·오프라인 이벤트**(특히 한국 소식)를 수집해서
Threads용 포스트를 만들어 GitHub Issue로 쌓는 (게시는 사람이 복붙) 초경량 GitHub Actions 봇. 밈/공감·트리비아·꿀팁 같은
뉴스가 아닌 콘텐츠는 다루지 않는 "소식 전달" 채널.

DeepAgents 같은 무거운 에이전트 프레임워크 없이, 소스를 직접 수집하는 스크립트와 OpenAI API
호출만으로 구성되어 있음.

## ⏰ 발행 주기

[`pokemon-watch.yml`](.github/workflows/pokemon-watch.yml) 의 상시 감시 루프가 모든 자동 발행을 맡는다.
GitHub 예약 실행(cron)은 몇 시간씩 밀리거나 빠지는 일이 잦아서, 실행 하나를 약 5시간 40분 유지하면서 그 안에서
시각을 직접 보고, 끝나면 자기 자신을 다시 실행해 이어 간다 (2시간마다 도는 cron 은 사슬이 끊겼을 때의 안전장치).

- **속보: 10분마다** — 새로 올라온 지 8시간 이내 글 중 새 행사·팝업·공연 발표, 예매·응모 오픈, 발매·입고·추첨
  공지, 공식 발표, 큰 사건만. 직전 게시 후 30분은 쉼
- **정기 발행: 한국시간 09·13·17·21시** — 최근 48시간 글 중 가장 좋은 1건 (못 채우면 웹 검색 후보로 한 번 더)
- **정가 구매 정보: 한국시간 12·18시**
- 올릴 게 없으면 건너뜀. 루프를 멈추려면 Actions 탭에서 `Pokemon Cards Watch Loop` 워크플로를 Disable

[`pokemon-cards.yml`](.github/workflows/pokemon-cards.yml) 은 수동 실행과 브라우저 감시기의 입고 알림(stockalert)용이다.
여러 실행이 동시에 돌아도 [`scripts/publish.sh`](scripts/publish.sh) 가 상태 파일을 합쳐서 올리므로 기록이 지워지지 않는다.

## 📡 수집 소스

[`scripts/collect_sources.py`](scripts/collect_sources.py) 가 글이 올라오는 곳을 직접 읽어 발행 시각이
붙은 목록을 만든다. 모델의 웹 검색은 검색 색인에 올라온 뒤에야 보여서 속보에 늦고 묵은 페이지가
섞이기 때문에, 모델은 이 목록 안에서 고르고 고른 글의 원문만 근거로 포스트를 쓴다.

| 소스 | 필요한 설정 |
| --- | --- |
| 포켓몬코리아 공식 소식 | 없음 |
| 다음 뉴스 검색 최신순 (국내 언론) | 없음 |
| Bing 뉴스 RSS (국내·해외 언론) | 없음 |
| PokéBeach (해외 TCG 속보) | 없음 |
| 블로그 RSS (`BLOG_FEEDS` 목록) | 없음 — 목록에 피드 주소 추가 |
| Threads 최신순 검색 + 지정 계정 최근 글 | 없음 — 계정을 지켜보려면 변수 `THREADS_ACCOUNTS` (공백 구분) |
| X 최근 검색 | 시크릿 `X_BEARER_TOKEN` (X API 유료 플랜) |
| 인스타그램 계정 최근 게시물 | 시크릿 `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID` (본인 비즈니스/크리에이터 계정의 Graph API 토큰), 변수 `INSTAGRAM_ACCOUNTS` (지켜볼 계정, 공백 구분) |

Threads는 로그인 없는 일반 브라우저에는 글을 내려주지 않고 검색엔진 크롤러에게만 글이 담긴 페이지를
준다. 그래서 크롤러 UA로 공개 검색 결과를 읽는다. 공식 API가 아닌 비공식 경로라 Meta가 막으면
0건이 될 수 있다 (그 경우 로그의 `📥 Threads 검색` 건수가 0으로 찍힌다). SNS 글은 24시간 이내 것만 쓴다.
X와 인스타그램은 이런 경로가 없어 공식 API 토큰이 있어야만 읽을 수 있다.
수집 결과만 확인하려면: `python scripts/collect_sources.py`

## 🎯 다루는 콘텐츠

소재 우선순위 (앞쪽일수록 더 자주 다룸, **한국 소식·이벤트 최우선**):

1. **신제품/발매 소식** — 신팩·강화확장팩·재판·한정 상품의 (특히 한국) 발매일·예약 일정, 프로모 배포
2. **국내 오프라인 이벤트** — 국내 카드쇼, 대회(챔피언스리그 등), 팝업스토어, 전시회, 포켓몬 행사
3. **시세/거래 소식** — 최근(수일 내) 경매 낙찰, 시세 급등락, 화제의 매물
4. **사건/이슈** — 최근 위조 적발, 리셀·사재기 논란, 품절 대란, 발매 관련 사건
5. **유명인/화제** — 최근 셀럽·스트리머의 개봉/구매 화제
6. **해외 주요 소식/이벤트** (간헐적) — 해외 신팩 발매, 월드챔피언십, 대형 경매 등

> ⛔ 밈/공감·자학개그, 트리비아, 보관/가품 꿀팁 같은 "시간 무관" 콘텐츠는 다루지 않음.
> 모든 포스트는 최근 보도됐거나 곧 다가올 실제 소식/일정에 근거함 (아래 "최신성·중복 검증" 참고).
> 출처 페이지의 대표 이미지(og:image / twitter:image)를 자동으로 가져와 게시물에 첨부하고,
> 대표 이미지가 없으면 OpenAI 이미지 생성으로 뉴스톤 이미지를 만들어 **항상** 이미지를 붙임.

문체는 딱딱한 보도자료 톤이 아니라, 핵심 사실(무엇·언제·얼마)을 훅으로 앞세우는 캐주얼한
소식 전달 톤을 지향함 (자세한 규칙은 `SYSTEM_PROMPT` 참고).

## 🔧 동작 방식

```
GitHub Actions (30분마다 속보 체크 + 하루 4회 정기 발행, 수동 실행 가능)
        ↓
scripts/generate_pokemon_content.py
  - collect_sources.py 로 공식 사이트·뉴스·블로그(·Threads·X·인스타그램) 최신 글 수집
  - 최근에 올라왔고 아직 안 다룬 글만 추림 (URL·소재 중복 제거, 속보는 이미 판정한 글 제외)
  - 모델이 목록에서 올릴 글을 고름 → 코드가 원문과 같은 소식의 다른 출처를 가져옴
  - 출처들만 근거로 포스트 작성 → 별도 호출로 사실 대조 → 불일치·단일 출처면 게시 안 함
  - 정기 슬롯에서 못 채우면 OpenAI web_search 후보를 받아 코드로 검증 (출처 접속 → 중복 → 최신성)
  - 출처 페이지에서 대표 이미지(og:image/twitter:image) 추출, 없으면 OpenAI로 뉴스톤 이미지 생성
        ↓ (생성됐을 때만)
GitHub Issue 생성 (라벨: pokemon-post, mode:scheduled/mode:breaking)
  - 본문 + 출처 + 모드 + 생성 시각 기록 → 여기서 본문을 복붙해 Threads에 직접 게시
    (자동 게시는 꺼 둠. 필요하면 scripts/post_to_threads.py 를 워크플로 스텝으로 다시 연결)
        ↓
reports/pokemon_posted_state.json 만 커밋 & 푸시 (게시 이력 + 속보 판정을 마친 글 목록)
```

## 🛒 정가 구매 정보 (buyguide)

품귀 제품을 리셀가가 아니라 **정가로 살 수 있는 곳·때·방법**을 모아 하루 2회(한국시간 12:10 / 18:10)
정리 글로 올린다.

- 수집: 평소 소스 + 다음 뉴스 "포켓몬 카드 입고/추첨/판매" 검색, 최근 72시간(SNS 48시간) 글 중 입고·추첨·
  예약·판매 같은 단어가 있는 글
- 추출: 모델이 편의점·대형마트·공식 스토어·공인 카드샵의 입고·재입고·추첨·예약 일정, 매장 직접 공지,
  재고 확인 방법을 뽑는다 (리셀가·시세·끝난 판매·개인 인증글·구매대행은 제외)
- 근거 조건(코드로 판정): 공식 공지, 판매처가 직접 올린 글, 또는 서로 다른 언론 두 곳 이상. 블로그는
  근거로 세지 않는다. 같은 구매처 정보는 3일 안에 다시 올리지 않는다
- 작성 후 교차 검증과 같은 사실 대조를 거친다

인스타그램(매장 계정)은 로그인 없이 읽을 수 없어서, 시크릿 `INSTAGRAM_ACCESS_TOKEN`·`INSTAGRAM_USER_ID`
(본인 비즈니스 계정)와 변수 `INSTAGRAM_ACCOUNTS`가 있어야 소스에 들어간다. 매장 Threads 계정은 변수
`THREADS_ACCOUNTS`에 넣으면 따로 지켜본다.

## ⚡ 입고 알림 (브라우저 감시기, 이 컴퓨터에서 실행)

인스타그램·X 는 로그인한 브라우저에만 게시물을 보여주므로, 매장 계정 입고 공지는 GitHub Actions 가 아니라
이 컴퓨터에서 상시로 도는 [`scripts/watch_browser.py`](scripts/watch_browser.py) 가 본다.

```bash
python3 -m venv .venv && .venv/bin/pip install playwright requests openai && .venv/bin/python -m playwright install chromium
.venv/bin/python scripts/watch_browser.py login            # 열린 창에서 인스타그램(·X) 로그인, 최초 1회
caffeinate -i .venv/bin/python scripts/watch_browser.py run  # 잠자기 막고 계속 확인 (기본 3분 간격)
```

- 감시 대상·간격: [`scripts/watchlist.json`](scripts/watchlist.json) (인스타그램 계정·해시태그, Threads 계정, X 계정)
- 새 글 중 6시간 이내이면서 "포켓몬"과 "입고·판매·추첨…" 이 함께 들어간 글을 찾으면 맥 알림을 띄우고,
  워크플로를 `stockalert` 모드로 실행해 Threads 초안을 Issue 로 남긴다 (실패하면 원문 그대로 Issue)
- 판매처가 직접 올린 공지라 교차 검증 대신 원문 대조만 한다
- 로그인이 풀리면 맥 알림으로 알려준다. 로그인한 계정은 자동 접근으로 제한될 수 있어 감시용 계정을 권장

## 🔎 교차 검증

고른 소식은 게시 전에 스스로 교차 검증한다. 기사 하나만 믿고 쓰면 그 기사의 오류(잘못 적힌 발매일,
한 매장 사진을 전체 출시처럼 쓴 캡션 등)가 그대로 올라가고, 며칠 간격의 글끼리 날짜·가격이 어긋난다.

1. **다른 출처 찾기** — 수집 목록과 뉴스 검색에서 같은 소식을 다룬 다른 매체 글을 최대 3개 찾아 본문을
   가져온다 (같은 도메인, 포털 전재 페이지, 블로그·SNS, 7일 넘은 글, 핵심 단어가 본문에 없는 글은 제외).
2. **여러 출처로 쓰기** — 공식 출처에 있거나 두 곳 이상에서 일치하는 사실만 쓰고, 출처끼리 다른 수치는
   뺀다. 같은 소재로 이미 올린 글도 함께 보여 줘서 과거 글과 어긋나지 않게 한다.
3. **사실 대조** — 별도의 모델 호출이 완성된 포스트의 주장을 하나씩 뽑아 어느 출처에 적혀 있는지 대조한다.
4. **코드로 판정** — 출처에 없는 주장·출처 간 불일치·왜곡이 하나라도 있으면 한 번 고쳐 쓰게 하고, 그래도
   남으면 게시하지 않는다. 핵심 사실은 **공식 출처 또는 서로 다른 도메인 두 곳 이상**이 확인해야 한다.

블로그·Threads 글은 소식의 실마리로만 쓰이고, 언론·공식 출처로 확인되지 않으면 올라가지 않는다.
확인해 준 출처는 Issue의 "교차 확인" 줄에 링크로 남는다.

## 🛡️ 최신성·중복 검증

웹 검색 후보는 모델이 보고하는 날짜와 "새 소식"이라는 주장을 믿지 않고, 출처 페이지를 직접 열어
확인한다. (직접 수집한 글은 발행 시각을 피드에서 받으므로 아래 중 출처·본문·중복 검사만 적용한다.)
작년 행사 페이지(연도 없이 "10월 13일(월)"로만 적힌 공지 등)를 올해 소식으로 올리거나 같은
행사를 며칠 간격으로 다시 올리는 일을 막기 위한 장치다.

| 검사 | 탈락 조건 |
| --- | --- |
| 출처 | `source_url`이 열리지 않음 (404, 지어낸 URL) |
| 최신성 (a) 기사 | 발행일(메타태그/작성일 실측)이 7일보다 오래됨. 실측값이 없으면 모델이 보고한 발행일이 본문에 연도 포함으로 적혀 있어야 함 |
| 최신성 (b) 일정 | 시작일이 오늘~14일 이내가 아님, 그 날짜가 출처 본문에 없음, 요일 표기가 그 해 달력과 다름, 페이지의 가장 최근 날짜가 120일보다 오래됨 |
| 본문 | 포스트에 적힌 "O월 O일(요일)"이 실제 달력과 다름 |
| 중복 | 같은 출처 URL을 게시한 적 있음(기간 무관), 최근 60일 소재와 유사도 0.8 이상, 또는 모델이 같은 소식으로 판정 |

(a)와 (b)는 둘 중 하나만 통과하면 된다. 기준값은
[`scripts/generate_pokemon_content.py`](scripts/generate_pokemon_content.py) 상단 상수
(`MAX_NEWS_AGE_DAYS`, `UPCOMING_WINDOW_DAYS`, `DEDUP_WINDOW_DAYS` 등)로 조정한다.
탈락 사유는 워크플로우 로그의 `Generate Pokemon card content` 스텝에 후보별로 찍힌다.

검증 로직 테스트: `python -m unittest discover tests`

## 🚀 설치 방법

### 1. API 키 준비

- **OpenAI API Key**: https://platform.openai.com/api-keys
- **X API / 인스타그램 Graph API** (선택, SNS 속보 수집용) — 위 "수집 소스" 참고

### 2. GitHub Secrets 설정

Repository → Settings → Secrets and variables → Actions 에 추가:

```
OPENAI_API_KEY=sk-xxxxx
```

게시는 자동으로 하지 않음. 매번 생성되는 GitHub Issue에서 본문을 복붙해서 직접 올리면 됨.

### 3. GitHub Actions 활성화

Actions 탭 → 워크플로우 활성화 → **Pokemon Cards Threads Bot** → Run workflow로 테스트

## 📖 결과물

- **GitHub Issues** (`pokemon-post` 라벨) — 매 발행마다 새 Issue 생성, 본문에 게시 텍스트
  + 출처 + 모드 + 생성 시각 기록. `mode:scheduled` / `mode:breaking` 라벨로 필터링 가능.
- `reports/pokemon_posted_state.json` — 게시 이력 (소재·출처 URL·기준일, 최근 500건. 중복 방지용, git에 커밋됨)

## 🔧 커스터마이징

### 발행 시간 변경

[`.github/workflows/pokemon-cards.yml`](.github/workflows/pokemon-cards.yml) 의 `cron` 값을 수정.
정기 발행 cron 문자열을 바꾸면 `Determine mode` 스텝의 비교 문자열도 똑같이 바꿔야 함.

💡 Cron 표현식 도움: https://crontab.guru

### 톤/주제 조정

[`scripts/generate_pokemon_content.py`](scripts/generate_pokemon_content.py) 상단의
`SYSTEM_PROMPT` 와 `build_user_prompt()` 를 수정.

### 모델 변경

```bash
# GitHub Secrets 또는 워크플로우 env에 추가
OPENAI_MODEL=gpt-5-mini
```

## 🐛 문제 해결

### 매번 같은 소재만 나와요
- `reports/pokemon_posted_state.json` 이 커밋되고 있는지 확인 (이 이력으로 URL·소재 중복을 걸러냄)
- 워크플로우 로그에서 해당 후보가 왜 통과했는지 확인하고, 필요하면 `TOPIC_SIMILARITY_THRESHOLD`를 낮춤

### 액션은 성공(success)인데 Issue가 안 쌓여요
- 워크플로우 로그에서 `Generate Pokemon card content` 스텝 출력에 "검증을 통과한 소식이 없어요"가
  찍혀 있는지 확인 (`Post to Threads`/`Create GitHub Issue`/`Commit and push` 스텝이 `skipped`로 표시됨).
  후보별 탈락 사유가 같이 찍히며, 새 소식이 없는 슬롯을 건너뛰는 건 정상 동작
- 정기 발행 모드가 원치 않게 breaking으로 판정되고 있지 않은지 `Determine mode` 스텝 로그의
  `mode=` 값과 `github.event.schedule` 값을 확인
- `Create GitHub Issue` 스텝이 라벨 관련 에러로 실패한다면 저장소에 `pokemon-post`,
  `mode:scheduled`, `mode:breaking` 라벨이 있는지 확인 (`gh label list`)

## 📝 라이선스

MIT License
