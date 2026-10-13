# 🃏 포켓몬 카드 Threads 봇

포켓몬 카드의 **최신 뉴스·발매 소식·오프라인 이벤트**(특히 한국 소식)를 수집해서
Threads에 자동으로 올리는 초경량 GitHub Actions 봇. 밈/공감·트리비아·꿀팁 같은
뉴스가 아닌 콘텐츠는 다루지 않는 "소식 전달" 채널.

DeepAgents 같은 무거운 에이전트 프레임워크 없이, 소스를 직접 수집하는 스크립트와 OpenAI API
호출만으로 구성되어 있음.

## ⏰ 발행 주기

- **속보 체크: 20분마다** — 소스를 직접 수집해서 최근 24시간 안에 올라온 새 글이 있을 때만
  모델을 호출하고, 모델이 "속보"로 판정한 것만 바로 게시 (직전 게시 후 60분 동안은 쉼)
- **정기 발행 4회** (한국시간 09:00 / 13:00 / 17:00 / 21:00) — 최근 72시간 수집 글 중 가장 좋은 것 1건.
  수집 글로 못 채우면 웹 검색 후보(아래 검증 통과분)로 한 번 더 시도
- 올릴 게 없으면 그 실행은 건너뜀 (과거 뉴스 재탕·중복 게시·밈/트리비아 대체 없음)

> 모드는 실행 시각이 아니라 `github.event.schedule`(어느 cron이 트리거했는지)로 판정함.
> 공개 저장소라 Actions 사용량 제한이 없어 20분 간격으로 돌림. 비공개로 바꾸면 간격을 늘릴 것.

## 📡 수집 소스

[`scripts/collect_sources.py`](scripts/collect_sources.py) 가 글이 올라오는 곳을 직접 읽어 발행 시각이
붙은 목록을 만든다. 모델의 웹 검색은 검색 색인에 올라온 뒤에야 보여서 속보에 늦고 묵은 페이지가
섞이기 때문에, 모델은 이 목록 안에서 고르고 고른 글의 원문만 근거로 포스트를 쓴다.

| 소스 | 필요한 설정 |
| --- | --- |
| 포켓몬코리아 공식 소식 | 없음 |
| Bing 뉴스 RSS (국내·해외 언론) | 없음 |
| PokéBeach (해외 TCG 속보) | 없음 |
| 블로그 RSS (`BLOG_FEEDS` 목록) | 없음 — 목록에 피드 주소 추가 |
| 네이버 뉴스·블로그·카페 최신순 | 시크릿 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` ([네이버 개발자센터](https://developers.naver.com/apps/#/register)에서 "검색" API 등록, 무료) |
| 인스타그램 계정 최근 게시물 | 시크릿 `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID` (본인 비즈니스/크리에이터 계정의 Graph API 토큰), 변수 `INSTAGRAM_ACCOUNTS` (지켜볼 계정, 공백 구분) |

네이버 검색 화면과 인스타그램은 로그인 없는 자동 접근을 막고 있어서 공식 API 키가 있어야만 읽을 수 있다.
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
GitHub Actions (20분마다 속보 체크 + 하루 4회 정기 발행, 수동 실행 가능)
        ↓
scripts/generate_pokemon_content.py
  - collect_sources.py 로 공식 사이트·뉴스·블로그(·카페·인스타그램) 최신 글 수집
  - 최근에 올라왔고 아직 안 다룬 글만 추림 (URL·소재 중복 제거, 속보는 이미 판정한 글 제외)
  - 모델이 목록에서 올릴 글을 고름 → 코드가 원문을 가져옴 → 모델이 원문만 근거로 포스트 작성
  - 정기 슬롯에서 못 채우면 OpenAI web_search 후보를 받아 코드로 검증 (출처 접속 → 중복 → 최신성)
  - 출처 페이지에서 대표 이미지(og:image/twitter:image) 추출, 없으면 OpenAI로 뉴스톤 이미지 생성
        ↓ (생성됐을 때만)
scripts/post_to_threads.py
  - Threads API로 자동 게시 (첫 글에 이미지 첨부, 여러 포스트면 답글로 스레드 연결)
        ↓
GitHub Issue 생성 (라벨: pokemon-post, mode:scheduled/mode:breaking)
  - 본문 + 출처 + 모드 + 생성 시각 기록 → 발행 로그이자 수동 복붙용
        ↓
reports/pokemon_posted_state.json 만 커밋 & 푸시 (게시 이력 + 속보 판정을 마친 글 목록)
```

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
- **네이버 검색 API** (선택, 블로그·카페 수집용), **인스타그램 Graph API** (선택) — 위 "수집 소스" 참고
- **Threads API** (선택, 자동 게시용): https://developers.facebook.com/docs/threads
  - `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID` 발급

### 2. GitHub Secrets 설정

Repository → Settings → Secrets and variables → Actions 에 추가:

```
OPENAI_API_KEY=sk-xxxxx
THREADS_ACCESS_TOKEN=your_threads_access_token   # 선택
THREADS_USER_ID=your_threads_user_id             # 선택
```

Threads 시크릿이 없으면 게시는 건너뛰고, 대신 매번 생성되는 GitHub Issue에서 본문을
복붙해서 수동으로 올리면 됨.

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

### Threads에 안 올라가요
- `THREADS_ACCESS_TOKEN` / `THREADS_USER_ID` 시크릿이 올바른지 확인
- 토큰 만료 여부 확인 (Threads 장기 토큰은 60일마다 갱신 필요)

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
