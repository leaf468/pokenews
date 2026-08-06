#!/usr/bin/env python3
"""
포켓몬 카드 소식을 검색하고 Threads용 포스트를 생성하는 스크립트.

OpenAI Responses API의 web_search 툴로 최신 소식을 검색한 뒤,
가벼운 톤의 Threads 포스트 텍스트를 만들어 저장한다.
새로운 소식이 없으면 아무 파일도 만들지 않고 조용히 종료한다 (속보 체크용).
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
너는 포켓몬 카드(포켓몬 트레이딩 카드 게임) 전문 SNS 계정 운영자야.
Threads에 올릴 짧고 가벼운 포스트를 작성해.

다룰 수 있는 주제:
- 신기한 뉴스, 최신 발매/재판 소식, 가격 관련 가십
- 희귀 카드나 유명 카드의 역사/뒷이야기
- 특정 카드에 얽힌 사건, 경매 낙찰가, 화제가 된 오픈 영상 등
- 가품(가짜 카드) 구별 팁, 보관/감정(PSA 등) 관련 꿀팁

스타일 규칙:
- 반말 대신 친근한 존댓말 ("~예요", "~네요", "~해요")
- 딱딱한 번역체 금지, 실제 사람이 가볍게 수다 떠는 톤
- 이모지는 적당히 (문단마다 0~2개)
- 과장된 클릭베이트 금지, 하지만 흥미를 끄는 도입부는 좋음
- 전체 500자 이내 (Threads 글자 제한), 여러 개 올릴 경우 각각 500자 이내
- 마지막 줄에 해시태그 2~4개 (#포켓몬카드 #포켓몬TCG 등 상황에 맞게)
- 출처 매체명은 언급하되 URL은 본문에 넣지 않음 (별도로 관리)
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
            "가장 흥미로운 것 1개를 웹 검색으로 찾아서 Threads 포스트를 작성해줘. "
            "만약 따끈한 뉴스가 마땅치 않으면, 카드 역사/일화나 가품 구별 팁, "
            "보관 팁 같은 상시 유효한 콘텐츠로 대체해도 좋아. "
            "이 모드에서는 항상 결과를 만들어야 해 (정기 발행 슬롯이야)."
        )
    else:  # breaking check
        instruction = (
            "지금 시각 기준 최근 3시간 이내에 새로 터진 '진짜 속보성' 포켓몬 카드 뉴스가 "
            "있는지 웹 검색으로 확인해줘. 예: 신제품 깜짝 발표, 초고가 경매 낙찰, "
            "대량 위조 카드 적발, 유명인 관련 화제, 품절 대란 등. "
            "확실히 속보라고 할 만한 게 없으면 절대 억지로 만들지 말고 has_news를 false로 응답해."
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


def write_outputs(data: dict, mode: str) -> Path | None:
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
    return out_path


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

    out_path = write_outputs(data, mode)

    # GitHub Actions 다음 스텝에서 파일 존재 여부를 알 수 있도록 출력
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"has_content={'true' if out_path else 'false'}\n")
            f.write(f"threads_file={out_path or ''}\n")


if __name__ == "__main__":
    main()
