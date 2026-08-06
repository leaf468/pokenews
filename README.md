# 🃏 포켓몬 카드 Threads 봇

포켓몬 카드 관련 신기한 뉴스, 최신 소식, 가십, 카드 역사, 가품 구별 팁 등을
가볍게 수집해서 Threads에 자동으로 올리는 초경량 GitHub Actions 봇.

DeepAgents 같은 무거운 에이전트 프레임워크 없이, OpenAI API를 스크립트에서 직접 호출하는
단일 파이프라인으로 구성되어 있음.

## ⏰ 발행 주기

- **정기 발행 6회** (한국시간 09:00 / 12:00 / 15:00 / 18:00 / 21:00 / 23:00)
  - 소재가 마땅치 않아도 카드 역사, 가품 구별 팁 등으로 항상 하나는 올림
- **속보 체크** (그 사이 2시간 간격)
  - 최근 3시간 내 진짜 속보성 소식(깜짝 발표, 초고가 낙찰, 위조 적발 등)이 있을 때만 게시
  - 없으면 아무것도 올리지 않고 조용히 종료 (커밋도 안 함)

## 🎯 다루는 콘텐츠

- 신제품/재판 소식, 가격·품절 관련 가십
- 유명 카드의 역사와 일화, 경매 낙찰 비하인드
- 가품(가짜 카드) 구별 팁, 보관·감정(PSA 등) 꿀팁

## 🔧 동작 방식

```
GitHub Actions (매 2시간, 하루 14회 트리거)
        ↓
정기 슬롯(6회)인지 속보 체크 슬롯인지 판별
        ↓
scripts/generate_pokemon_content.py
  - OpenAI Responses API (web_search 툴)로 소식 검색
  - 최근 게시 소재(reports/pokemon_posted_state.json) 중복 회피
  - 정기 슬롯: 항상 포스트 생성
  - 속보 슬롯: 진짜 속보 없으면 아무것도 생성하지 않고 종료
        ↓ (생성됐을 때만)
scripts/post_to_threads.py
  - Threads API로 자동 게시 (여러 포스트면 답글로 스레드 연결)
        ↓
reports/ 에 커밋 & 푸시
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

Threads 시크릿이 없으면 게시는 건너뛰고 `reports/` 에 텍스트 파일만 생성됨(수동 복붙용).

### 3. GitHub Actions 활성화

Actions 탭 → 워크플로우 활성화 → **Pokemon Cards Threads Bot** → Run workflow로 테스트

## 📖 결과물

- `reports/YYYYMMDD-HHMM-pokemon-threads.txt` — 실제 게시된(또는 게시할) 본문
  - 여러 포스트로 나뉘면 `===POST_SEPARATOR===` 로 구분 (스레드 답글 연결용)
- `reports/YYYYMMDD-HHMM-pokemon-meta.json` — 소재 요약, 출처 이름/URL
- `reports/pokemon_posted_state.json` — 최근 게시 소재 이력 (중복 방지용)

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

### 속보 체크 때마다 뭔가 올라와요
- `generate_pokemon_content.py` 의 breaking 모드 프롬프트가 너무 관대하게 판단하고 있을 수 있음
  → "진짜 속보만" 기준을 더 엄격하게 수정

## 📝 라이선스

MIT License
