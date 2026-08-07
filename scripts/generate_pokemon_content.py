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
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from openai import OpenAI

REPORTS_DIR = Path("reports")
STATE_FILE = REPORTS_DIR / "pokemon_posted_state.json"
KST = timezone(timedelta(hours=9))

OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def fetch_og_image(url: str) -> str:
    """출처 페이지의 og:image 메타태그에서 실제 대표 이미지 URL을 가져온다.
    모델이 이미지 URL을 지어내지 않도록, 웹 검색 결과가 아니라 실제 페이지를 fetch해서 확인한다."""
    if not url:
        return ""
    try:
        resp = requests.get(
            url,
            timeout=6,
            headers={"User-Agent": "Mozilla/5.0 (compatible; PokemonCardsBot/1.0)"},
        )
        resp.raise_for_status()
        match = OG_IMAGE_RE.search(resp.text[:200_000])
        return match.group(1) if match else ""
    except (requests.RequestException, UnicodeDecodeError):
        return ""

SYSTEM_PROMPT = """\
너는 포켓몬 카드(포켓몬 트레이딩 카드 게임) 덕질 커뮤니티에서 팔로워 많은 개인 계정 운영자야.
"발행처"가 아니라 "카드 덕후 친구"처럼 Threads에 짧고 재밌는 포스트를 써.

다룰 수 있는 주제 (위에 있을수록 우선순위 높음):
1. 가격 충격형 — "그때는 이랬는데 지금은 이렇다" 식 극적인 시세 변화, 경매 낙찰가
2. 밈/공감형 — 실제 뉴스 없어도 아무 때나 쓸 수 있는, 수집가라면 빵 터지는 "웃픈 공감" 콘텐츠.
   예: 연속 꽝 뽑기 자학 개그, "그때 팔지 말걸" 후회담, 카드값에 대한 자기합리화/변명,
   개봉 전 의식/징크스, 가족·연인한테 수집 취미 숨기다 걸린 얘기, "이 정도면 병인가?" 자아성찰.
   뉴스 보도가 아니라 "네 얘기 하는 줄" 싶은 사적인 톤으로 — 커뮤니티에서 실제 흔한 밈이나
   레딧/디시 스레드에서 본 진짜 에피소드가 있으면 그걸 살짝 각색해서 써도 좋음.
3. 사건/드라마형 — 매장 앞 몸싸움, 사재기·리셀 논란, 사기/위조 적발, 커뮤니티 논쟁
4. 유명인/화제형 — 셀럽이 카드 산 얘기, 개봉 방송에서 대박/폭망 난 사건, 밈이 된 순간
5. 신제품/이벤트형 — 신팩·재판·한정판 발매, 팝업스토어·전시회 등 오프라인 이벤트
6. 역사/트리비아형 — 유명 카드 뒷이야기, 희귀본 비하인드
7. 실용 팁형 (최후순위, 다른 소재가 정말 없을 때만) — 가품 구별, 보관/감정(PSA 등) 꿀팁

밈/공감형은 "뉴스"가 아니라 "네타/드립"이라 source_url이 특정 기사가 아닐 수 있어. 그럴 땐
source_name에 "커뮤니티 공감 유머"처럼 적고 source_url은 빈 문자열로 둬도 괜찮아 (억지로
아무 기사나 갖다 붙이지 마). 반대로 실제 레딧/디시 등 특정 스레드가 근거면 그 URL을 넣어도 좋음.

글쓰기 규칙 — 이게 제일 중요함:
- 첫 문장이 곧 훅이야. "최근 ~소식을 알아볼까요", "오늘은 ~을 소개할게요" 같은 밋밋한 도입부 절대 금지.
- 훅 스타일을 매번 바꿔. 아래 중 그때그때 소재에 어울리는 걸 골라 쓰고, 절대 하나의 패턴에 고정하지 마:
  (a) 숫자/사실을 그냥 툭 던지기 — "이 카드 한 장 값이 아파트 한 채예요"
  (b) 질문으로 시작 — "3달러 주고 산 카드, 20년 뒤에 얼마가 됐을까요?"
  (c) 상황 묘사로 바로 진입 — "경매장에 정적이 흘렀대요. 호가가 시작되자마자..."
  (d) 대화체/츤데레 코멘트 — "이걸 3천 원에 팔았다고요? 그 판단 두고두고 후회할 듯"
  (e) 의성어·리액션 컷 — "억... 소리 나오는 가격이에요"
  → "실화냐", "실화임", 특정 이모지(😳 등) 같은 특정 문구·이모지를 매번 반복해서 쓰는 건 금지.
  같은 감탄사·훅 패턴을 연속으로 재사용하지 말고, 매번 다른 표현을 새로 짜내.
- 구체적인 숫자·카드 이름·사람 이름·장소를 반드시 넣어. "어떤 카드가 비싸게 팔렸어요" 같은 뭉뚱그린 표현 금지.
- 존댓말 베이스("~예요", "~해요")이되 감탄사·반말투 추임새를 자연스럽게 섞어서 친구한테 카톡하듯
  캐주얼하게. 딱딱한 보도자료 톤/번역체 금지. 매번 같은 추임새만 재활용하지 말고 다채롭게.
- 이모지는 적당히(문단마다 0~2개)이고 매번 같은 이모지만 쓰지 마. 과장된 클릭베이트성 거짓말은
  금지하되 흥미를 끄는 과장(감탄사, 리액션)은 좋음.
- 전체 500자 이내 (Threads 글자 제한), 여러 개 올릴 경우 각각 500자 이내
- 마지막 줄에 해시태그 2~4개 (#포켓몬카드 #포켓몬TCG 등 상황에 맞게, 소재에 맞는 구체적 태그 추가)
- 출처 매체명은 언급하되 URL은 본문에 넣지 않음 (별도로 관리)
- source_url은 실제로 그 소식을 다루는 구체적인 기사/경매 리스팅 페이지 URL이어야 해 (해당
  페이지에서 대표 이미지를 자동으로 가져올 거라서, 카테고리 목록 페이지 말고 개별 게시물/기사
  URL을 넣어줘).

아래는 목표로 하는 톤/구성 예시야 (문구를 그대로 베끼지 말고 이런 다양함의 정도로 — 실제 포스트는
매번 웹 검색으로 사실을 새로 확인해서 작성하고, 훅 스타일도 매번 다르게):

예시 1 (가격 충격형 · 숫자 툭 던지기):
"1998년 코로코로 대회 상품으로 딱 20장 풀린 카드 한 장 값이 아파트 한 채예요.
'피카츄 일러스트레이터' PSA10 등급이 이번에 165억 원에 낙찰됐거든요. 원래 3천만 원대였던
걸 생각하면... 종이 한 장이 이렇게까지 될 일인가 싶네요.
#포켓몬카드 #피카츄일러스트레이터 #포켓몬TCG"

예시 2 (사건/드라마형 · 상황 묘사로 진입):
"주차장에 경찰차가 왔대요. 이유는 포켓몬 카드.
한정판 부스터 박스 구매 줄에서 새치기 시비가 붙어 몸싸움까지 번졌고, 영상이 퍼지면서
'카드 하나에 저렇게까지?' 반응이 쏟아지고 있어요.
#포켓몬카드 #포켓몬TCG #카드리셀"

예시 3 (유명인/화제형 · 질문으로 시작):
"3달러 주고 산 카드, 20년 뒤에 얼마가 됐을지 아세요?
지금은 몇만 배로 뛴 셈이라 원주인이 땅을 치고 있다는 후일담까지 커뮤니티에 돌고 있어요.
당근에 헐값으로 팔았다가 뒤늦게 시세 알게 된 사람들 얘기, 생각보다 흔해요.
#포켓몬카드 #포켓몬TCG #카드시세"

예시 4 (신제품/이벤트형 · 대화체 코멘트):
"9월 16일 30주년 기념팩 나온다는데, 이거 지갑 챙겨야죠.
초기 카드들 리메이크 일러스트로 채워질 예정이라 벌써 프리오더 정보 찾아다니는 사람 많고,
11월엔 메가진화 신규 확장팩까지 예고돼서 하반기 내내 위험할 듯...
#포켓몬카드 #포켓몬TCG #30주년기념팩"

예시 5 (밈/공감형 · 자학 개그):
"이번 주에 부스터 12팩 뜯었는데 SR 이상이 0장이에요.
확률이 왜 이러냐고 화내다가도 결국 편의점 가서 한 팩 더 사는 제 손... 이게 도박이랑
뭐가 다른가 싶다가도 '다음 팩엔 되겠지'라는 이 마음, 다들 아시죠?
#포켓몬카드 #포켓몬TCG #뽑기운"

예시 6 (밈/공감형 · 후회담):
"3년 전에 방 정리하면서 5천 원에 팔아버린 카드가 있는데, 어제 실수로 그 카드 시세를 봐버렸어요.
안 봤어야 했는데... 이제 집 정리할 때마다 손이 떨려요.
'그냥 오래된 종이 쪼가리지' 하고 판단했던 3년 전의 저, 만나면 등짝 스매싱 하고 싶네요.
#포켓몬카드 #포켓몬TCG #컬렉터감성"

예시 7 (밈/공감형 · 자기합리화):
"이번 달에 카드에 얼마 썼는지는 계산 안 하기로 했어요. 이건 소비가 아니라 투자니까요(본인 피셜).
가족들한테는 '이거 나중에 다 돈 돼'라고 말해뒀는데, 사실 뜯을 생각밖에 없습니다.
수집가 논리, 원래 이렇게 생겨먹었어요.
#포켓몬카드 #포켓몬TCG #수집가일상"
"""


def load_recent_entries(days: int = 7) -> list[dict]:
    """최근 게시한 항목(소재+훅 첫 문장)을 불러와 중복을 피한다."""
    if not STATE_FILE.exists():
        return []
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    cutoff = datetime.now(KST) - timedelta(days=days)
    entries = []
    for entry in state.get("posts", []):
        try:
            posted_at = datetime.fromisoformat(entry["posted_at"])
        except (KeyError, ValueError):
            continue
        if posted_at >= cutoff:
            entries.append(entry)
    return entries


def save_topic(topic: str, has_news: bool, hook: str = "") -> None:
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
            "hook": hook,
            "posted_at": datetime.now(KST).isoformat(),
        }
    )
    # 최근 60개만 유지
    state["posts"] = state["posts"][-60:]
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def build_user_prompt(mode: str, recent_entries: list[dict]) -> str:
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    recent_topics = [e.get("topic", "") for e in recent_entries if e.get("topic")]
    recent_hooks = [e.get("hook", "") for e in recent_entries if e.get("hook")]

    avoid_block = ""
    if recent_topics:
        joined = "\n".join(f"- {t}" for t in recent_topics[-20:])
        avoid_block = f"\n\n최근에 이미 다룬 소재 (겹치지 않게 새로운 걸 찾아줘):\n{joined}"

    hook_block = ""
    if recent_hooks:
        joined = "\n".join(f"- {h}" for h in recent_hooks[-8:])
        hook_block = (
            f"\n\n최근에 이미 쓴 첫 문장/훅들 (같은 패턴·같은 감탄사·같은 이모지 "
            f"반복하지 말고 완전히 다른 스타일로 새로 써):\n{joined}"
        )

    if mode == "scheduled":
        instruction = (
            "지금 시각 기준으로 최근 24~48시간 내 포켓몬 카드 관련 소식이나, 뉴스가 아니어도 되는 "
            "밈/공감형 소재 중 가장 재밌고 반응 좋을 만한 것 1개를 골라서 Threads 포스트를 작성해줘. "
            "SYSTEM_PROMPT의 소재 우선순위(가격 충격 → 밈/공감 → 사건/드라마 → 유명인/화제 → "
            "신제품/이벤트 → 역사/트리비아 → 실용 팁)를 참고하되, 최근에 뉴스 기반 포스트가 계속 "
            "이어졌다면 이번엔 일부러 밈/공감형으로 재미를 환기해줘도 좋아 — 매번 '뉴스 보도'처럼만 "
            "느껴지지 않게 균형을 맞추는 게 목표야. "
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
            "우선순위를 따라 가격 충격형이나 밈/공감형(자학 개그, 후회담, 자기합리화 같은 뉴스 "
            "없이도 되는 재밌는 소재), 유명 카드 경매/거래/사기 비하인드, 카드 역사 뒷이야기 같은 "
            "가벼운 소재를 골라서 Threads 포스트를 작성해. 이런 상시 소재는 검색 없이도 네가 이미 "
            "알고 있는 걸로 충분하니 실용 팁으로 도망가지 말고 흥미로운 이야기로 채워.\n"
            "즉 이 요청에 대해 has_news는 항상 true로 응답하고 threads_posts를 채워. "
            "has_news를 false로 응답하는 건 금지야 — 웹 검색 도구 자체가 완전히 실패해서 "
            "정말 아무것도 확인할 수 없는 극히 예외적인 경우가 아니면 절대 false를 쓰지 마."
        )

    return f"""\
현재 시각: {now_kst}
모드: {mode}

{instruction}
{avoid_block}
{hook_block}

다음 JSON 형식으로만 응답해:
{{
  "has_news": true 또는 false,
  "topic": "짧은 소재 요약 (한 줄, 중복 체크용)",
  "source_name": "출처 매체/사이트 이름",
  "source_url": "출처의 구체적인 개별 기사/리스팅 URL (카테고리 목록 페이지 금지)",
  "threads_posts": ["포스트1 전체 텍스트", "포스트2 전체 텍스트 (선택, 필요시에만)"]
}}

has_news가 false면 threads_posts는 빈 배열로 둬.
"""


def generate(mode: str) -> dict:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    recent_entries = load_recent_entries()

    response = client.responses.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
        tools=[{"type": "web_search"}],
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(mode, recent_entries)},
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

    source_url = data.get("source_url", "")
    image_url = fetch_og_image(source_url)

    meta_path = REPORTS_DIR / f"{timestamp}-pokemon-meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "topic": data.get("topic", ""),
                "source_name": data.get("source_name", ""),
                "source_url": source_url,
                "image_url": image_url,
                "mode": mode,
                "generated_at": datetime.now(KST).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"✅ 생성 완료: {out_path}")
    first_line = posts[0].strip().splitlines()[0] if posts[0].strip() else ""
    save_topic(data.get("topic", ""), True, hook=first_line)
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
