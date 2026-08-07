#!/usr/bin/env python3
"""
포켓몬 카드 소식을 검색하고 Threads용 포스트를 생성하는 스크립트.

OpenAI Responses API의 web_search 툴로 최신 소식을 검색한 뒤,
가벼운 톤의 Threads 포스트 텍스트를 만들어 저장한다.
scheduled/breaking 모드 모두 진짜 소식이 없으면 가벼운 가십/트리비아로 대체해서
항상 결과를 만든다. 웹 검색이 완전히 실패하는 등 정말 아무 소재도 못 찾은 경우에만
파일을 만들지 않고 조용히 종료한다.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openai import OpenAI

REPORTS_DIR = Path("reports")
STATE_FILE = REPORTS_DIR / "pokemon_posted_state.json"
KST = timezone(timedelta(hours=9))

SYSTEM_PROMPT = """\
너는 포켓몬 카드(포켓몬 트레이딩 카드 게임) 덕질 커뮤니티에서 팔로워 많은 개인 계정 운영자야.
"발행처"가 아니라 "카드 덕후 친구"처럼 Threads에 짧고 재밌는 포스트를 써.

다룰 수 있는 주제 (위에 있을수록 우선순위 높음):
1. 가격 충격형 — "그때는 이랬는데 지금은 이렇다" 식 극적인 시세 변화, 경매 낙찰가
2. 사건/드라마형 — 매장 앞 몸싸움, 사재기·리셀 논란, 사기/위조 적발, 커뮤니티 논쟁
3. 유명인/화제형 — 셀럽이 카드 산 얘기, 개봉 방송에서 대박/폭망 난 사건, 밈이 된 순간
4. 신제품/이벤트형 — 신팩·재판·한정판 발매, 팝업스토어·전시회 등 오프라인 이벤트
5. 역사/트리비아형 — 유명 카드 뒷이야기, 희귀본 비하인드
6. 실용 팁형 (최후순위, 다른 소재가 정말 없을 때만) — 가품 구별, 보관/감정(PSA 등) 꿀팁

글쓰기 규칙 — 이게 제일 중요함:
- 첫 문장이 곧 훅이야. "최근 ~소식을 알아볼까요", "오늘은 ~을 소개할게요" 같은 밋밋한 도입부 절대 금지.
  대신 충격적인 숫자/사실을 바로 던지거나, 질문으로 던지거나, "실화냐" 느낌의 감탄으로 시작해.
  예: "이 카드 한 장이 웬만한 아파트 한 채값이래요 😳", "코스트코 주차장에서 카드 때문에 진짜 몸싸움이 났다는데요"
- 구체적인 숫자·카드 이름·사람 이름·장소를 반드시 넣어. "어떤 카드가 비싸게 팔렸어요" 같은 뭉뚱그린 표현 금지.
- 존댓말 베이스("~예요", "~해요")이되 감탄사·반말투 추임새("실화냐", "미쳤네요", "이거 실화?") 섞어서
  친구한테 카톡하듯 캐주얼하게. 딱딱한 보도자료 톤/번역체 금지.
- 이모지는 적당히 (문단마다 0~2개), 과장된 클릭베이트성 거짓말은 금지하되 흥미를 끄는 과장(감탄사, 리액션)은 좋음.
- 전체 500자 이내 (Threads 글자 제한), 여러 개 올릴 경우 각각 500자 이내
- 마지막 줄에 해시태그 2~4개 (#포켓몬카드 #포켓몬TCG 등 상황에 맞게, 소재에 맞는 구체적 태그 추가)
- 출처 매체명은 언급하되 URL은 본문에 넣지 않음 (별도로 관리)

아래는 목표로 하는 톤/구성 예시야 (내용을 그대로 베끼지 말고 이런 느낌으로 — 실제 포스트는 매번
웹 검색으로 사실을 새로 확인해서 작성해):

예시 1 (가격 충격형):
"1998년 코로코로 대회에서 딱 20장 뿌려진 카드, 지금 얼마인지 아세요? 😳
'피카츄 일러스트레이터' PSA10 등급 한 장이 이번에 165억 원에 낙찰됐어요. 원래 3천만 원대에
거래되던 카드가 몇 년 새 이렇게 됐다는 거... 카드 한 장이 웬만한 건물값이네요.
#포켓몬카드 #피카츄일러스트레이터 #포켓몬TCG"

예시 2 (사건/드라마형):
"코스트코 주차장에서 포켓몬 카드 때문에 진짜 주먹다짐이 났대요 🥊
한정판 부스터 박스 사려고 줄 서 있던 사람들 사이에 새치기 시비가 붙었는데 결국 몸싸움까지
번져서 경찰 출동. 영상 퍼지면서 '카드 하나에 저렇게까지?' 반응 폭발 중이에요.
#포켓몬카드 #포켓몬TCG #카드리셀"

예시 3 (신제품/이벤트형):
"9월 16일 30주년 기념팩 나옵니다 🎉
초기 카드들 리메이크 일러스트로 채워질 예정이라 벌써 프리오더 정보 찾아다니는 사람 많아요.
11월엔 메가진화 신규 확장팩까지 예고돼서 하반기 내내 지갑 위험할 듯...
#포켓몬카드 #포켓몬TCG #30주년기념팩"
"""


def load_recent_topics(days: int = 7) -> list[str]:
    """최근 게시한 소식 제목 목록을 불러와 중복 소재를 피한다."""
    if not STATE_FILE.exists():
        return []
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    cutoff = datetime.now(KST) - timedelta(days=days)
    topics = []
    for entry in state.get("posts", []):
        try:
            posted_at = datetime.fromisoformat(entry["posted_at"])
        except (KeyError, ValueError):
            continue
        if posted_at >= cutoff:
            topics.append(entry.get("topic", ""))
    return [t for t in topics if t]


def save_topic(topic: str, has_news: bool) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    state = {"posts": []}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {"posts": []}

    state.setdefault("posts", []).append(
        {
            "topic": topic,
            "has_news": has_news,
            "posted_at": datetime.now(KST).isoformat(),
        }
    )
    # 최근 60개만 유지
    state["posts"] = state["posts"][-60:]
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def build_user_prompt(mode: str, recent_topics: list[str]) -> str:
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    avoid_block = ""
    if recent_topics:
        joined = "\n".join(f"- {t}" for t in recent_topics[-20:])
        avoid_block = f"\n\n최근에 이미 다룬 소재 (겹치지 않게 새로운 걸 찾아줘):\n{joined}"

    if mode == "scheduled":
        instruction = (
            "지금 시각 기준으로 최근 24~48시간 내 포켓몬 카드 관련 소식 중 "
            "가장 재밌고 반응 좋을 만한 것 1개를 웹 검색으로 찾아서 Threads 포스트를 작성해줘. "
            "SYSTEM_PROMPT의 소재 우선순위(가격 충격 → 사건/드라마 → 유명인/화제 → 신제품/이벤트 "
            "→ 역사/트리비아 → 실용 팁)를 따라서, 가능하면 앞쪽 카테고리에서 골라. "
            "따끈한 뉴스가 정말 마땅치 않으면 역사/트리비아나 실용 팁으로 대체해도 좋지만, "
            "그럴 때도 뻔한 요약이 아니라 훅이 있는 이야기로 풀어줘. "
            "이 모드에서는 항상 결과를 만들어야 해 (정기 발행 슬롯이야)."
        )
    else:  # breaking check
        instruction = (
            "먼저 최근 몇 시간 이내에 포켓몬 카드 관련해서 화제가 될 만한 일이 있었는지 "
            "웹 검색으로 확인해. 예: 신제품 깜짝 발표, 초고가 경매 낙찰, 대량 위조 카드 적발, "
            "유명인 관련 화제, 품절 대란, 매장 앞 다툼 같은 사건, 팝업스토어·전시회 같은 "
            "오프라인 이벤트 소식(지금 한창 진행 중인 것도 포함, 예: 잠실 팝업스토어), "
            "커뮤니티에서 화제가 된 개봉 영상/짤 등.\n"
            "그런 화제성 소식을 못 찾았어도 실패로 취급하지 마. 그 경우엔 SYSTEM_PROMPT의 소재 "
            "우선순위를 따라 가격 충격형이나 유명 카드 경매/거래/사기 비하인드, 카드 역사 뒷이야기 "
            "같은 가벼운 가십·트리비아를 골라서 Threads 포스트를 작성해. 이런 상시 소재는 검색 "
            "없이도 네가 이미 알고 있는 걸로 충분하니 실용 팁으로 도망가지 말고 흥미로운 이야기로 "
            "채워.\n"
            "즉 이 요청에 대해 has_news는 항상 true로 응답하고 threads_posts를 채워. "
            "has_news를 false로 응답하는 건 금지야 — 웹 검색 도구 자체가 완전히 실패해서 "
            "정말 아무것도 확인할 수 없는 극히 예외적인 경우가 아니면 절대 false를 쓰지 마."
        )

    return f"""\
현재 시각: {now_kst}
모드: {mode}

{instruction}
{avoid_block}

다음 JSON 형식으로만 응답해:
{{
  "has_news": true 또는 false,
  "topic": "짧은 소재 요약 (한 줄, 중복 체크용)",
  "source_name": "출처 매체/사이트 이름",
  "source_url": "출처 URL",
  "threads_posts": ["포스트1 전체 텍스트", "포스트2 전체 텍스트 (선택, 필요시에만)"]
}}

has_news가 false면 threads_posts는 빈 배열로 둬.
"""


def generate(mode: str) -> dict:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    recent_topics = load_recent_topics()

    response = client.responses.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
        tools=[{"type": "web_search"}],
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(mode, recent_topics)},
        ],
    )

    text = response.output_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def write_outputs(data: dict, mode: str) -> tuple[Path, Path] | None:
    if not data.get("has_news") or not data.get("threads_posts"):
        print(f"ℹ️  [{mode}] 새로 올릴 만한 소식이 없어요. 이번 실행은 건너뜁니다.")
        return None

    REPORTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(KST).strftime("%Y%m%d-%H%M")
    out_path = REPORTS_DIR / f"{timestamp}-pokemon-threads.txt"

    posts = data["threads_posts"]
    body = "\n\n===POST_SEPARATOR===\n\n".join(p.strip() for p in posts if p.strip())
    out_path.write_text(body, encoding="utf-8")

    meta_path = REPORTS_DIR / f"{timestamp}-pokemon-meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "topic": data.get("topic", ""),
                "source_name": data.get("source_name", ""),
                "source_url": data.get("source_url", ""),
                "mode": mode,
                "generated_at": datetime.now(KST).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"✅ 생성 완료: {out_path}")
    save_topic(data.get("topic", ""), True)
    return out_path, meta_path


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "scheduled"
    if mode not in ("scheduled", "breaking"):
        print(f"알 수 없는 모드: {mode} (scheduled 또는 breaking)", file=sys.stderr)
        sys.exit(1)

    try:
        data = generate(mode)
    except Exception as e:  # noqa: BLE001
        print(f"❌ 생성 중 에러: {e}", file=sys.stderr)
        sys.exit(1)

    result = write_outputs(data, mode)
    out_path, meta_path = result if result else (None, None)

    # GitHub Actions 다음 스텝에서 파일 존재 여부를 알 수 있도록 출력
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"has_content={'true' if out_path else 'false'}\n")
            f.write(f"threads_file={out_path or ''}\n")
            f.write(f"meta_file={meta_path or ''}\n")


if __name__ == "__main__":
    main()
