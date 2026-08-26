# 🃏 포켓몬 카드 Threads 봇

포켓몬 카드의 **최신 뉴스·발매 소식·오프라인 이벤트**(특히 한국 소식)를 수집해서
Threads에 자동으로 올리는 초경량 GitHub Actions 봇. 밈/공감·트리비아·꿀팁 같은
뉴스가 아닌 콘텐츠는 다루지 않는 "소식 전달" 채널.

DeepAgents 같은 무거운 에이전트 프레임워크 없이, OpenAI API를 스크립트에서 직접 호출하는
단일 파이프라인으로 구성되어 있음.

## ⏰ 발행 주기

- **정기 발행 6회** (한국시간 09:00 / 12:00 / 15:00 / 18:00 / 21:00 / 23:00)
  - 따끈한 신규 소식이 없으면, 곧 예정된 발매/이벤트 일정을 미리 알리는 "예고" 포스트로 대체
    (미래 일정이라 최신성이 유지됨). 과거 뉴스 재탕·밈/트리비아 대체는 하지 않음
- **속보 체크** (그 사이 2시간 간격, 하루 8회)
  - 최근 소식(깜짝 발표, 초고가 낙찰, 위조 적발, 팝업스토어 등 오프라인 이벤트 등)을 먼저 찾고,
  - 마땅한 게 없으면 가까운 미래의 실제 발매/이벤트 일정으로 대체. 진짜 소식도 예정 일정도 없으면
    조용히 스킵 (옛날 뉴스로 억지로 채우지 않음)

> ⚠️ 정기/속보 모드 판정은 실행 시각이 아니라 `github.event.schedule`(어느 cron이 트리거했는지)로
> 판정합니다. GitHub Actions 스케줄은 부하 시 최대 1시간 가까이 지연될 수 있는데, 실행 시각 기준으로
> 판정하면 지연된 정기 슬롯이 속보 체크로 잘못 분류되는 문제가 있어 이렇게 고쳤습니다.

## 🎯 다루는 콘텐츠

소재 우선순위 (앞쪽일수록 더 자주 다룸, **한국 소식·이벤트 최우선**):

1. **신제품/발매 소식** — 신팩·강화확장팩·재판·한정 상품의 (특히 한국) 발매일·예약 일정, 프로모 배포
2. **국내 오프라인 이벤트** — 국내 카드쇼, 대회(챔피언스리그 등), 팝업스토어, 전시회, 포켓몬 행사
3. **시세/거래 소식** — 최근(수일 내) 경매 낙찰, 시세 급등락, 화제의 매물
4. **사건/이슈** — 최근 위조 적발, 리셀·사재기 논란, 품절 대란, 발매 관련 사건
5. **유명인/화제** — 최근 셀럽·스트리머의 개봉/구매 화제
6. **해외 주요 소식/이벤트** (간헐적) — 해외 신팩 발매, 월드챔피언십, 대형 경매 등

> ⛔ 밈/공감·자학개그, 트리비아, 보관/가품 꿀팁 같은 "시간 무관" 콘텐츠는 다루지 않음.
> 모든 포스트는 최근 보도됐거나 아직 다가올 실제 소식/일정에 근거함 (`published_date` 검증).
> 출처 페이지의 대표 이미지(og:image / twitter:image)를 자동으로 가져와 게시물에 첨부하고,
> 대표 이미지가 없으면 OpenAI 이미지 생성으로 뉴스톤 이미지를 만들어 **항상** 이미지를 붙임.

문체는 딱딱한 보도자료 톤이 아니라, 핵심 사실(무엇·언제·얼마)을 훅으로 앞세우는 캐주얼한
소식 전달 톤을 지향함 (자세한 규칙은 `SYSTEM_PROMPT` 참고).

## 🔧 동작 방식

```
GitHub Actions (매 2시간, 하루 14회 트리거)
        ↓
정기 슬롯(6회)인지 속보 체크 슬롯인지 판별 (github.event.schedule 기준)
        ↓
scripts/generate_pokemon_content.py
  - OpenAI Responses API (web_search 툴)로 최신 소식·일정 검색
  - 최근 게시 소재(reports/pokemon_posted_state.json) 중복 회피
  - published_date 검증으로 옛날 뉴스 재탕 차단 (최근 10일 이내 or 미래 일정만 통과)
  - 출처 페이지에서 대표 이미지(og:image/twitter:image) 추출, 없으면 OpenAI로 뉴스톤 이미지 생성
        ↓ (생성됐을 때만)
scripts/post_to_threads.py
  - Threads API로 자동 게시 (첫 글에 이미지 첨부, 여러 포스트면 답글로 스레드 연결)
        ↓
GitHub Issue 생성 (라벨: pokemon-post, mode:scheduled/mode:breaking)
  - 본문 + 출처 + 모드 + 생성 시각 기록 → 발행 로그이자 수동 복붙용
        ↓
reports/pokemon_posted_state.json 만 커밋 & 푸시 (중복 방지용 이력)
```

## 🚀 설치 방법

### 1. API 키 준비

- **OpenAI API Key**: https://platform.openai.com/api-keys
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
- `reports/pokemon_posted_state.json` — 최근 게시 소재 이력 (중복 방지용, git에 커밋됨)

## 🔧 커스터마이징

### 발행 시간 변경

[`.github/workflows/pokemon-cards.yml`](.github/workflows/pokemon-cards.yml) 의 `cron` 값과
`Determine mode` 스텝의 UTC 시간대 목록을 함께 수정.

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
- `reports/pokemon_posted_state.json` 이 커밋되고 있는지 확인 (최근 7일 소재를 프롬프트에 넣어 회피)

### Threads에 안 올라가요
- `THREADS_ACCESS_TOKEN` / `THREADS_USER_ID` 시크릿이 올바른지 확인
- 토큰 만료 여부 확인 (Threads 장기 토큰은 60일마다 갱신 필요)

### 액션은 성공(success)인데 Issue가 안 쌓여요
- 워크플로우 로그에서 `Generate Pokemon card content` 스텝 출력에 "새로 올릴 만한 소식이 없어요"가
  찍혀 있는지 확인 (`Post to Threads`/`Create GitHub Issue`/`Commit and push` 스텝이 `skipped`로 표시됨)
- 정기 발행 모드가 원치 않게 breaking으로 판정되고 있지 않은지 `Determine mode` 스텝 로그의
  `mode=` 값과 `github.event.schedule` 값을 확인
- `Create GitHub Issue` 스텝이 라벨 관련 에러로 실패한다면 저장소에 `pokemon-post`,
  `mode:scheduled`, `mode:breaking` 라벨이 있는지 확인 (`gh label list`)

## 📝 라이선스

MIT License
