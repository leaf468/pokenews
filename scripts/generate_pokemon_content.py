#!/usr/bin/env python3
"""
포켓몬 카드 소식을 검색하고 Threads용 포스트를 생성하는 스크립트.

OpenAI Responses API의 web_search 툴로 "최신" 소식/일정을 검색한 뒤,
소식 전달 톤의 Threads 포스트 텍스트를 만들어 저장한다.
우리 채널은 밈/공감·트리비아·꿀팁 같은 콘텐츠가 아니라 뉴스·이벤트·발매 소식만 다룬다.
한국 소식/이벤트(신팩 발매일, 카드쇼, 팝업스토어 등)를 최우선으로 하고, 해외 주요 소식은
간헐적으로 섞는다.

옛날 뉴스를 최신인 것처럼 발행하지 않도록, 모델이 응답한 published_date를 검증해서
최근(MAX_NEWS_AGE_DAYS 이내)이거나 미래 일정인 경우에만 파일을 만든다. 그 외에는
파일을 만들지 않고 조용히 종료한다.

모든 소식에 이미지를 붙이기 위해, 출처 페이지에 대표 이미지(og:image/twitter:image)가
없으면 OpenAI 이미지 생성 API로 뉴스톤 이미지를 만들어 첨부한다.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from openai import OpenAI

REPORTS_DIR = Path("reports")
STATE_FILE = REPORTS_DIR / "pokemon_posted_state.json"
KST = timezone(timedelta(hours=9))

# 소식의 발행일이 이 일수보다 오래됐고 미래 일정도 아니면 "옛날 뉴스"로 보고 발행하지 않는다.
MAX_NEWS_AGE_DAYS = 10

# 대표 이미지 후보 메타태그들 (og:image → twitter:image 순으로 시도)
IMAGE_META_RES = [
    re.compile(
        r'<meta[^>]+(?:property|name)=["\']og:image(?::secure_url|:url)?["\'][^>]+content=["\']([^"\']+)["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image(?::secure_url|:url)?["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
        re.IGNORECASE,
    ),
]


def fetch_og_image(url: str) -> str:
    """출처 페이지의 og:image(없으면 twitter:image) 메타태그에서 실제 대표 이미지 URL을 가져온다.
    모델이 이미지 URL을 지어내지 않도록, 웹 검색 결과가 아니라 실제 페이지를 fetch해서 확인한다."""
    if not url:
        return ""
    try:
        resp = requests.get(
            url,
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0 (compatible; PokemonCardsBot/1.0)"},
        )
        resp.raise_for_status()
        html = resp.text[:300_000]
        for pattern in IMAGE_META_RES:
            match = pattern.search(html)
            if match:
                # 상대 경로일 수 있으니 절대 URL로 변환
                return urljoin(resp.url, match.group(1).strip())
        return ""
    except (requests.RequestException, UnicodeDecodeError):
        return ""


def is_fresh(published_date: str) -> bool:
    """소식의 기준 날짜가 최근(MAX_NEWS_AGE_DAYS 이내)이거나 아직 다가올 미래 날짜인지 검사한다.
    날짜를 파싱할 수 없으면 판단을 보류하고 True를 반환한다(과도한 스킵 방지)."""
    if not published_date:
        return True
    try:
        parsed = datetime.strptime(published_date.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return True
    today = datetime.now(KST).date()
    if parsed >= today:  # 오늘 또는 미래 일정
        return True
    return (today - parsed).days <= MAX_NEWS_AGE_DAYS


def build_image_prompt(topic: str, headline: str) -> str:
    """뉴스 썸네일 톤의 이미지 생성 프롬프트를 만든다. 특정 포켓몬 캐릭터/로고 묘사는 피해
    저작권 문제와 왜곡을 줄이고, 깔끔한 편집(에디토리얼) 스타일로 유도한다."""
    subject = (topic or headline or "포켓몬 트레이딩 카드 게임 소식").strip()
    return (
        "Clean, modern editorial news-thumbnail image for a trading card game news channel. "
        f"Topic (in Korean): \"{subject}\". "
        "Style: professional press/editorial photography, minimal and premium, soft studio "
        "lighting, shallow depth of field, neutral high-end background. "
        "Depict a generic trading card game scene — anonymous glossy collectible cards, "
        "sealed booster packs, display cases, a card show or store setting as fits the topic. "
        "Do NOT include any real Pokemon characters, official Pokemon logos, brand marks, "
        "readable text, letters, numbers or watermarks. Photorealistic, uncluttered, "
        "news-appropriate and tasteful."
    )


def generate_news_image(topic: str, headline: str) -> str:
    """출처 대표 이미지를 못 구했을 때 OpenAI 이미지 생성으로 뉴스톤 이미지를 만들고 URL을 돌려준다.
    실패하면 빈 문자열을 반환한다(그 경우 텍스트로만 게시)."""
    try:
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        result = client.images.generate(
            model=os.environ.get("OPENAI_IMAGE_MODEL", "dall-e-3"),
            prompt=build_image_prompt(topic, headline),
            size="1024x1024",
            response_format="url",
            n=1,
        )
        return result.data[0].url or ""
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  이미지 생성 실패: {e}", file=sys.stderr)
        return ""

SYSTEM_PROMPT = """\
너는 포켓몬 카드(포켓몬 트레이딩 카드 게임) 소식을 빠르고 정확하게 전하는 뉴스 채널 운영자야.
읽기 편한 캐주얼한 말투는 유지하되, 우리 채널은 "정보·소식 전달" 채널이야. 모든 포스트는 반드시
"실제로 최근에 일어난 일" 또는 "아직 지나지 않은 공식 일정/이벤트"에 근거해야 해.

⛔ 금지 (아주 중요):
- 밈·공감·자학개그·후회담·자기합리화·"수집가 감성" 같은 뉴스가 아닌 감성/드립 콘텐츠는 절대 만들지 마.
  사건 없이 "다들 이러시죠?" 식으로 공감을 유도하는 글은 우리 채널에 어울리지 않아. 전면 금지야.
- 오래된 뉴스를 최신인 것처럼 발행하지 마. 몇 달~몇 년 전 사건(예: 과거 피카츄 일러스트레이터 경매,
  이미 끝난 발매·이벤트)을 오늘 소식처럼 쓰는 건 금지.
- 보관/감정 꿀팁, 가품 구별법, 카드 역사 트리비아 같은 "언제 써도 되는" 시간 무관 콘텐츠도 금지.
  이런 걸로 도망가지 말고 진짜 최신 소식·일정을 찾아.

다룰 소재 (위에 있을수록 우선순위 높음, 한국 소식·이벤트를 최우선으로):
1. 신제품/발매 소식 — 신규 확장팩·강화확장팩·하이클래스팩·재판·한정 상품의 발매일/예약 일정(특히
   한국 발매일), 프로모션 카드 배포. "언제 무엇이 나온다"를 명확히.
2. 국내 오프라인 이벤트 — 한국에서 열리거나 예정된 카드쇼, 대회(챔피언스리그 등), 팝업스토어,
   전시회, 포켓몬 행사. "언제·어디서·무엇을"을 반드시 담아.
3. 시세/거래 소식 — 최근(수일 내) 있었던 경매 낙찰, 시세 급등락, 화제가 된 매물. 반드시 최근 것.
4. 사건/이슈 — 최근 위조 카드 적발, 리셀·사재기 논란, 품절 대란, 발매 관련 사건.
5. 유명인/화제 — 최근 셀럽·스트리머의 개봉/구매/화제 사건.
6. 해외 주요 소식/이벤트 — 위 국내 소식이 마땅치 않을 때 간헐적으로 섞어. 해외 신팩 발매,
   월드챔피언십·대형 행사, 화제의 대형 경매 등. 다만 "최근/예정"인 것만.

날짜·사실 규칙 (제일 중요):
- 반드시 웹 검색으로 각 소식의 실제 발행일 또는 이벤트 날짜를 확인해. 확인된 날짜가 최근(대략 7일
  이내 보도)이거나 아직 다가올 미래 일정인 경우에만 써.
- 본문에 구체적 날짜/시점을 명시해 ("O월 O일 발매", "이번 주말", "8월 O일부터" 등). 언제 일인지
  모호하게 두지 마.
- 응답 JSON의 published_date에 그 소식의 기준 날짜(기사 발행일 또는 이벤트 날짜, YYYY-MM-DD)를 넣어.
- 가격·날짜·이름·장소는 확인된 사실만. 지어내지 마.

글쓰기 규칙:
- 첫 문장이 곧 훅이야. "최근 ~소식을 알아볼까요", "오늘은 ~을 소개할게요" 같은 밋밋한 도입부 절대 금지.
  대신 핵심 사실(무엇이·언제·얼마)을 앞세워 훅으로 만들어. 훅 스타일은 매번 바꿔:
  (a) 핵심 사실을 툭 던지기 — "9월 5일, 신규 강화확장팩이 한국 정발됩니다"
  (b) 질문으로 시작 — "이번 주말 어디서 카드쇼가 열리는지 아세요?"
  (c) 상황/현장 묘사 — "경매장에 정적이 흘렀어요. 호가가 시작되자마자..."
  같은 감탄사·이모지·훅 패턴을 연속으로 재사용하지 말고 매번 다르게 써.
- 구체적인 날짜·숫자·제품명·카드 이름·사람 이름·장소를 반드시 넣어. 뭉뚱그린 표현 금지.
- 존댓말 베이스("~예요", "~해요")에 가벼운 추임새를 자연스럽게 섞되, 어디까지나 소식 전달이 중심.
  딱딱한 보도자료 톤/번역체는 피하고, 과장된 클릭베이트성 거짓말도 금지.
- 이모지는 적당히(문단마다 0~2개), 매번 같은 이모지만 쓰지 마.
- 전체 500자 이내 (Threads 글자 제한), 여러 개 올릴 경우 각각 500자 이내.
- 마지막 줄에 해시태그 2~4개 (#포켓몬카드 #포켓몬TCG 등 상황에 맞게, 소재에 맞는 구체적 태그 추가).
- 출처 매체명은 언급하되 URL은 본문에 넣지 않음 (별도로 관리).
- source_url은 실제로 그 소식을 다루는 구체적인 기사/공지/리스팅 페이지 URL이어야 해. 해당 페이지에서
  대표 이미지를 자동으로 가져와 포스트에 첨부하니, 대표 이미지가 있는 개별 기사/공지 페이지를 골라줘
  (카테고리 목록 페이지 금지).

아래는 목표로 하는 톤/구성 예시야 (문구를 그대로 베끼지 말고 이런 정도의 다양함으로 — 실제 포스트는
매번 웹 검색으로 사실·날짜를 새로 확인해서 작성):

예시 1 (신제품/발매 · 한국 발매일):
"9월 5일, 강화확장팩 '○○○'가 한국에 정식 발매돼요.
새 SAR 카드 라인업과 프로모 배포 일정까지 공개돼서 예약 문의가 벌써 몰리고 있대요.
발매가는 팩당 ○○원, 박스 구성은 ○○팩이에요.
#포켓몬카드 #포켓몬TCG #신팩발매"

예시 2 (국내 이벤트 · 언제·어디서):
"이번 주말(8월 O일~O일), 서울 ○○에서 포켓몬 카드 대형 카드쇼가 열려요.
현장 교환회랑 한정 프로모 배포도 예정돼 있어서 오픈런 각오하는 분들 많더라고요.
입장 방법이랑 부스 정보는 공식 공지에서 확인하세요.
#포켓몬카드 #카드쇼 #포켓몬TCG"

예시 3 (시세/거래 · 최근 경매):
"8월 O일 진행된 경매에서 ○○ 카드 PSA10이 ○○원에 낙찰됐어요.
지난달 같은 등급 거래가보다 ○○% 오른 가격이라 시장이 술렁이고 있어요.
#포켓몬카드 #포켓몬TCG #카드시세"

예시 4 (해외 이벤트 · 간헐적):
"8월 O일, ○○에서 열린 포켓몬 월드챔피언십에서 신규 카드 정보가 공개됐어요.
다음 분기 발매 예정 제품 라인업까지 미리 풀려서 해외 커뮤니티가 들썩이고 있어요.
#포켓몬카드 #포켓몬TCG #월드챔피언십"
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
            "웹 검색으로 지금 시각 기준 최근 포켓몬 카드 관련 소식·일정을 찾아서 가장 새롭고 "
            "반응 좋을 만한 것 1개로 Threads 포스트를 작성해줘. 한국 소식·이벤트를 최우선으로 "
            "찾아 — 국내 신제품/재판 발매일과 예약 일정, 국내에서 열리거나 예정된 카드쇼·대회·"
            "팝업스토어·전시회 같은 오프라인 이벤트, 최근 시세/경매 소식, 최근 사건/이슈 순으로. "
            "국내 소재가 마땅치 않으면 해외 주요 소식/이벤트를 간헐적으로 섞어도 돼.\n"
            "이 모드는 항상 결과를 만들어야 하는 정기 발행 슬롯이야. 다만 '아무거나'가 아니라 "
            "반드시 최근에 보도됐거나 아직 다가올 예정인 소식이어야 해. 따끈한 신규 소식이 정말 "
            "없으면, 곧 예정된 발매/이벤트 일정(예: 다음 주 신팩 발매, 예정된 카드쇼)을 미리 알리는 "
            "'예고' 포스트로 대체해 — 이건 미래 일정이라 최신성이 유지돼. 과거 사건을 오늘 소식처럼 "
            "쓰거나, 밈/공감·트리비아·보관꿀팁 같은 시간 무관 콘텐츠로 도망가는 건 금지야."
        )
    else:  # breaking check
        instruction = (
            "먼저 최근 몇 시간~하루 이내에 포켓몬 카드 관련해서 새로 화제가 된 일이 있었는지 "
            "웹 검색으로 확인해. 예: 국내외 신제품·발매일 발표, 초고가 경매 낙찰, 대량 위조 카드 "
            "적발, 유명인 관련 화제, 품절 대란, 매장 앞 다툼 같은 사건, 지금 진행 중이거나 곧 열리는 "
            "국내 카드쇼·팝업스토어·전시회 같은 오프라인 이벤트 등. 한국 소식을 우선으로 보되 "
            "해외 주요 소식도 확인해.\n"
            "속보급 새 소식을 못 찾았으면, 아직 지나지 않은 가까운 미래의 공식 발매/이벤트 일정을 "
            "미리 알리는 '예고' 포스트로 채워도 돼 (미래 일정이라 최신성 유지). 이런 경우에도 반드시 "
            "웹 검색으로 날짜를 확인한 실제 일정이어야 해.\n"
            "과거 사건을 오늘 소식처럼 재탕하거나, 밈/공감·트리비아·보관꿀팁 같은 시간 무관 콘텐츠로 "
            "채우는 건 금지야. 그런 소재밖에 없으면 차라리 has_news를 false로 응답해. "
            "즉 진짜 최근 소식이나 다가올 실제 일정을 찾은 경우에만 has_news를 true로 하고 "
            "threads_posts를 채워."
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
  "published_date": "이 소식의 기준 날짜 YYYY-MM-DD (기사 발행일 또는 이벤트 날짜). 미래 예정 일정이면 그 예정일.",
  "source_name": "출처 매체/사이트 이름",
  "source_url": "대표 이미지가 있는 구체적인 개별 기사/공지/리스팅 URL (카테고리 목록 페이지 금지)",
  "threads_posts": ["포스트1 전체 텍스트", "포스트2 전체 텍스트 (선택, 필요시에만)"]
}}

has_news가 false면 threads_posts는 빈 배열로 둬.
published_date는 반드시 최근(대략 7일 이내 보도)이거나 아직 다가올 미래 날짜여야 해.
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
    # strict=False: 모델이 문자열 값 안에 이스케이프 안 된 줄바꿈 등 제어문자를 그대로
    # 넣는 경우가 있어서, 엄격 모드면 "Invalid control character" 에러로 파싱 자체가 죽는다.
    return json.loads(text, strict=False)


def write_outputs(data: dict, mode: str) -> tuple[Path, Path] | None:
    if not data.get("has_news") or not data.get("threads_posts"):
        print(f"ℹ️  [{mode}] 새로 올릴 만한 소식이 없어요. 이번 실행은 건너뜁니다.")
        return None

    published_date = data.get("published_date", "")
    if not is_fresh(published_date):
        print(
            f"ℹ️  [{mode}] 소식 기준일이 오래됐어요(published_date={published_date}). "
            "옛날 뉴스 재탕 방지를 위해 이번 실행은 건너뜁니다."
        )
        return None

    REPORTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(KST).strftime("%Y%m%d-%H%M")
    out_path = REPORTS_DIR / f"{timestamp}-pokemon-threads.txt"

    posts = data["threads_posts"]
    body = "\n\n===POST_SEPARATOR===\n\n".join(p.strip() for p in posts if p.strip())
    out_path.write_text(body, encoding="utf-8")

    source_url = data.get("source_url", "")
    image_url = fetch_og_image(source_url)
    image_source = "og" if image_url else ""
    if not image_url:
        # 출처에 대표 이미지가 없으면 OpenAI로 뉴스톤 이미지를 생성해서 항상 이미지를 붙인다.
        headline = posts[0].strip().splitlines()[0] if posts and posts[0].strip() else ""
        print(f"ℹ️  [{mode}] 출처 대표 이미지가 없어 OpenAI로 뉴스톤 이미지를 생성합니다.")
        image_url = generate_news_image(data.get("topic", ""), headline)
        image_source = "generated" if image_url else ""
    if not image_url:
        print(f"⚠️  [{mode}] 이미지를 확보하지 못했어요. 이미지 없이 텍스트로만 게시됩니다.")

    meta_path = REPORTS_DIR / f"{timestamp}-pokemon-meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "topic": data.get("topic", ""),
                "published_date": published_date,
                "source_name": data.get("source_name", ""),
                "source_url": source_url,
                "image_url": image_url,
                "image_source": image_source,
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
