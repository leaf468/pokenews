# 🃏 포켓몬 카드 Threads 봇

포켓몬 카드 관련 신기한 뉴스, 최신 소식, 가십, 카드 역사, 가품 구별 팁 등을
가볍게 수집해서 Threads에 자동으로 올리는 초경량 GitHub Actions 봇.

DeepAgents 같은 무거운 에이전트 프레임워크 없이, OpenAI API를 스크립트에서 직접 호출하는
단일 파이프라인으로 구성되어 있음.

## ⏰ 발행 주기

- **정기 발행 6회** (한국시간 09:00 / 12:00 / 15:00 / 18:00 / 21:00 / 23:00)
  - 소재가 마땅치 않아도 카드 역사, 가품 구별 팁 등으로 항상 하나는 올림
- **속보 체크** (그 사이 2시간 간격, 하루 8회)
  - 최근 소식(깜짝 발표, 초고가 낙찰, 위조 적발, 팝업스토어 등 오프라인 이벤트 등)을 먼저 찾고,
  - 마땅한 게 없으면 정기 발행과 마찬가지로 가벼운 가십/트리비아로 대체해서 항상 게시
  - → 사실상 하루 14회 모두 콘텐츠가 쌓임 (완전히 조용히 스킵되는 건 API 실패 등 예외적인 경우뿐)

> ⚠️ 정기/속보 모드 판정은 실행 시각이 아니라 `github.event.schedule`(어느 cron이 트리거했는지)로
> 판정합니다. GitHub Actions 스케줄은 부하 시 최대 1시간 가까이 지연될 수 있는데, 실행 시각 기준으로
> 판정하면 지연된 정기 슬롯이 속보 체크로 잘못 분류되는 문제가 있어 이렇게 고쳤습니다.

## 🎯 다루는 콘텐츠

소재 우선순위 (앞쪽일수록 더 자주 다룸):

1. **가격 충격형** — "그때는 이랬는데 지금은…" 식 극적인 시세 변화, 경매 낙찰가
2. **사건/드라마형** — 매장 앞 몸싸움, 사재기·리셀 논란, 사기/위조 적발, 커뮤니티 논쟁
3. **유명인/화제형** — 셀럽 관련 화제, 개봉 방송 대박/폭망, 밈이 된 순간
4. **신제품/이벤트형** — 신팩·재판·한정판 발매, 팝업스토어·전시회
5. **역사/트리비아형** — 유명 카드 뒷이야기, 희귀본 비하인드
6. **실용 팁형** (최후순위) — 가품 구별, 보관·감정(PSA 등) 꿀팁

문체는 "보도자료" 톤이 아니라 훅으로 시작해서 구체적인 숫자·이름을 넣는 캐주얼한
개인 계정 톤을 지향함 (자세한 규칙은 `SYSTEM_PROMPT` 참고).

## 🔧 동작 방식

```
GitHub Actions (매 2시간, 하루 14회 트리거)
        ↓
정기 슬롯(6회)인지 속보 체크 슬롯인지 판별 (github.event.schedule 기준)
        ↓
scripts/generate_pokemon_content.py
  - OpenAI Responses API (web_search 툴)로 소식 검색
  - 최근 게시 소재(reports/pokemon_posted_state.json) 중복 회피
  - 정기/속보 모두 진짜 소식 없으면 가십·트리비아로 대체해서 항상 포스트 생성
        ↓ (생성됐을 때만)
scripts/post_to_threads.py
  - Threads API로 자동 게시 (여러 포스트면 답글로 스레드 연결)
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
