#!/usr/bin/env python3
"""
포켓몬 카드 소식을 검색하고 Threads용 포스트를 생성하는 스크립트.

소식을 찾는 길은 두 가지다.

1. 직접 수집 (기본) — collect_sources.py 가 공식 사이트·뉴스·블로그(·Threads·X·인스타그램)에서 발행 시각이
   붙은 글 목록을 긁어온다. 모델은 그 목록 안에서 고르기만 하고, 고른 글의 원문을 코드가 가져와
   그 본문만 근거로 포스트를 쓰게 한다. 모델의 웹 검색은 색인이 늦어 속보를 못 잡고 묵은 페이지를
   섞어 오기 때문에, 속보(breaking)는 이 길로만 만든다.
2. 웹 검색 (보조) — 정기 슬롯에서 직접 수집으로 올릴 게 없을 때만, OpenAI web_search 로 후보를
   여러 개 받아 코드로 하나씩 검증한다.

우리 채널은 밈/공감·트리비아·꿀팁 같은 콘텐츠가 아니라 뉴스·이벤트·발매 소식만 다룬다.
한국 소식/이벤트(신팩 발매일, 카드쇼, 팝업스토어 등)를 최우선으로 하고, 해외 주요 소식은
간헐적으로 섞는다.

모델의 자체 보고(날짜·"새 소식"이라는 주장)는 믿지 않는다. 실제로 작년 행사 페이지를
올해 소식으로 착각하거나(연도 없는 "10월 13일"), 같은 행사를 며칠 간격으로 다시 올리는
일이 반복됐기 때문에, 웹 검색 후보에는 아래 검증을 전부 코드로 강제한다 (직접 수집한 글은
발행 시각을 피드에서 받으므로 1·3과 본문 요일 검사만 적용):

1. 출처 확인 — source_url을 직접 fetch해서 열리는 페이지인지 확인 (죽은/지어낸 URL 탈락).
2. 최신성 — 둘 중 하나를 출처 페이지 내용으로 입증해야 한다.
   (a) 기사 발행일이 최근 MAX_NEWS_AGE_DAYS 이내 (메타태그/작성일 실측, 없으면 모델이 보고한
       날짜가 본문에 연도 포함으로 적혀 있어야 함)
   (b) 일정 시작일이 오늘~UPCOMING_WINDOW_DAYS 이내이고, 그 날짜가 출처 본문에서 확인됨
       (연도 포함 표기, 또는 연도 없는 표기라면 요일이 그 연도와 일치해야 함)
3. 중복 — 같은 URL, 또는 최근 DEDUP_WINDOW_DAYS 동안 다룬 소재와 겹치면 탈락
   (글자 유사도 → 애매한 건 모델에게 같은 소식인지 한 번 더 판정시킴).

어느 길로 고른 소식이든 마지막에 교차 검증을 거친다: 같은 소식을 다룬 다른 출처를 찾아 함께
읽히고, 완성된 포스트의 사실 하나하나를 출처별로 대조시킨다. 핵심 사실이 공식 출처 또는 서로 다른
두 곳 이상에서 확인되지 않거나, 출처끼리 어긋나는 내용이 포스트에 남아 있으면 게시하지 않는다.

통과한 후보가 없으면 파일을 만들지 않고 조용히 종료한다 (아무거나 올리는 것보다 건너뛰는 게 낫다).

모든 소식에 이미지를 붙이기 위해, 출처 페이지에 대표 이미지(og:image/twitter:image)가
없으면 OpenAI 이미지 생성 API로 뉴스톤 이미지를 만들어 첨부한다.
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import requests
from openai import OpenAI

from collect_sources import collect_all, fetch_bing_news, fetch_daum_news

REPORTS_DIR = Path("reports")
STATE_FILE = REPORTS_DIR / "pokemon_posted_state.json"
KST = timezone(timedelta(hours=9))

# 기사 발행일이 이 일수보다 오래되면 "옛날 뉴스"로 본다.
MAX_NEWS_AGE_DAYS = 7
# 예고 포스트는 시작일이 오늘부터 이 일수 이내인 일정만 허용한다 (몇 달 뒤 행사를 반복 예고하지 않도록).
UPCOMING_WINDOW_DAYS = 14
# 출처 페이지에 적힌 가장 최근 날짜가 이 일수보다 오래됐으면 작년 페이지 등 묵은 페이지로 본다.
STALE_PAGE_DAYS = 120
# 이 기간 안에 다룬 소재와 겹치면 중복으로 본다. URL 중복은 기간과 무관하게 이력 전체에서 본다.
DEDUP_WINDOW_DAYS = 60
# 소재 유사도(문자 바이그램 포함 계수)가 이 값 이상이면 모델 판정 없이 바로 중복 처리한다.
# 제품명이 긴 소식끼리는 다른 소식이어도 0.7 안팎까지 겹치므로, 그 아래 애매한 구간은 모델 판정에 맡긴다.
TOPIC_SIMILARITY_THRESHOLD = 0.8
# 이력 보관 개수 (하루 3~4건 기준 약 4~5개월치).
HISTORY_KEEP = 500
# 한 번 실행에서 모델에게 받는 후보 수. 앞에서부터 검증해서 처음 통과한 것을 쓴다.
MAX_CANDIDATES = 5
# 직접 수집한 글 중 이 시간 안에 올라온 것만 본다 (속보 / 정기).
BREAKING_WINDOW_HOURS = 24
SCHEDULED_WINDOW_HOURS = 48
# SNS 글은 금방 낡으므로 모드와 상관없이 이 시간 안에 올라온 것만 본다.
SOCIAL_WINDOW_HOURS = 24
SOCIAL_KINDS = ("threads", "x", "instagram")
# 속보가 연달아 쏟아지지 않도록, 직전 게시 후 이 시간 동안은 속보를 올리지 않는다.
MIN_BREAKING_GAP_MINUTES = 30
# 속보는 올라온 지 이 시간 안의 글만 (날짜만 아는 공식 공지는 오늘·어제 글).
BREAKING_MAX_AGE_HOURS = 8
# 모델에게 한 번에 보여주는 수집 글 수 (최신순 상위).
MAX_RANK_ITEMS = 60
# 원문에서 이 글자 수 이상을 확보해야 포스트를 쓴다 (제목만 보고 지어내지 않도록).
MIN_CONTENT_CHARS = 120
# 이 도메인의 글은 그 자체로 공식 출처로 본다 (교차 확인 없이도 핵심 사실로 인정).
OFFICIAL_HOSTS = ("pokemonkorea.co.kr", "pokemoncard.co.kr", "pokemonstore.co.kr", "pokemon.com", "pokemon.co.jp", "pokemon-card.com")
# 한 번 실행에서 교차 검증까지 시도하는 후보 수 (후보 하나에 모델 호출이 2~5번 든다).
MAX_VERIFY_ATTEMPTS = 3
# 이 매체들의 기사는 한 곳만 보도해도 "단독 보도"로 인정한다 (출처를 밝히고, 기사에 적힌 내용만 쓴다).
# 다음 뉴스 검색으로 들어온 기사는 다음과 제휴한 국내 언론사라 같은 대우를 한다.
REPUTABLE_HOSTS = (
    "pokebeach.com", "pokeguardian.com", "ign.com", "polygon.com", "dexerto.com", "kotaku.com", "gamerant.com",
    "screenrant.com", "mashable.com", "theverge.com", "eurogamer.net", "nintendolife.com", "siliconera.com",
    "automaton-media.com", "dotesports.com", "pcgamer.com", "gamespot.com", "cbr.com", "gonintendo.com",
    "pokemongohub.net", "invenglobal.com", "inven.co.kr", "watch.impress.co.jp", "4gamer.net", "famitsu.com",
    "oricon.co.jp", "dengekionline.com", "inside-games.jp",
)
# 교차 검증에 쓰는 보조 출처 수와, 같은 소식을 다룬 글로 보는 기간.
MAX_CROSS_SOURCES = 3
CROSS_SOURCE_DAYS = 7
# 수집 목록에서 같은 소식을 다룬 글로 보는 제목 유사도.
CROSS_TITLE_SIMILARITY = 0.4
# 다른 매체 기사를 옮겨 싣기만 하는 곳은 독립 출처가 아니고 본문도 스크립트로만 내려와 읽을 수 없다.
AGGREGATOR_HOSTS = ("msn.com", "news.google.com", "bing.com", "yahoo.com")
# 구매 정보(buyguide) 글: 정가로 살 수 있는 곳·때·방법을 모아 올린다.
BUY_RE = re.compile(r"입고|재입고|발주|판매|추첨|응모|예약|정가|선착순|오픈런|재고|구매|입점|출시|당첨")
BUY_NEWS_QUERIES = ("포켓몬 카드 입고", "포켓몬 카드 추첨", "포켓몬 카드 판매")
BUY_WINDOW_HOURS = 72
# 같은 구매처 정보를 이 기간 안에 다시 올리지 않는다.
BUY_REPEAT_DAYS = 3
MAX_BUY_OPPORTUNITIES = 4
# 속보 체크에서는 제목/요약에 이런 단어가 있는 글(과 공식 공지·SNS 글)만 모델에게 보여준다.
# 새 글이 올라올 때마다 모델을 부르면 대부분 칼럼·핫딜 기사에 토큰을 쓰게 되기 때문이다.
URGENT_RE = re.compile(
    r"입고|재입고|재판|발매|출시|예약|추첨|응모|품절|매진|완판|판매|배포|공지|발표|공개|개최|오픈|"
    r"적발|도난|사기|리콜|연기|취소|restock|release|launch|announc|reveal|pre-?order|delay|"
    r"cancel|recall|stolen|counterfeit|sold out",
    re.IGNORECASE,
)
URGENT_KINDS = {"official", "threads", "x", "instagram"}
# 속보 판정을 마친 글 URL을 기억하는 기간. 같은 글을 20분마다 다시 판정하지 않기 위한 것.
CHECKED_KEEP_DAYS = 7

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

# 실제 발행일 후보 메타태그들 (사이트마다 관례가 달라 여러 개를 순서대로 시도).
# <time datetime> 은 사이드바의 다른 글 날짜가 먼저 잡혀 묵은 페이지가 최신으로 보일 수 있어 쓰지 않는다.
DATE_META_RES = [
    re.compile(
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']article:published_time["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]+(?:name|itemprop)=["\']datePublished["\'][^>]+content=["\']([^"\']+)["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]+name=["\']pubdate["\'][^>]+content=["\']([^"\']+)["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]+name=["\']sailthru\.date["\'][^>]+content=["\']([^"\']+)["\']',
        re.IGNORECASE,
    ),
    re.compile(r'"datePublished"\s*:\s*"([^"]+)"', re.IGNORECASE),
]

# 본문에 적힌 "연도 포함" 날짜 표기들.
_YMD_KO = r"(20\d{2})\s*[년年]\s*(\d{1,2})\s*[월月]\s*(\d{1,2})\s*[일日]"
_YMD_NUM = r"(?<![\d.\-/])(20\d{2})\s?[-./]\s?(\d{1,2})\s?[-./]\s?(\d{1,2})(?!\d)"
FULL_DATE_RES = [re.compile(_YMD_KO), re.compile(_YMD_NUM)]
EN_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}
EN_DATE_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b",
    re.IGNORECASE,
)
# 공식 공지류는 메타태그 없이 본문에 "작성일 2026년 09월 28일" 식으로 발행일을 적는다.
LABELED_DATE_RE = re.compile(
    r"(?:작성일|등록일|게시일|기사입력|입력|公開日|掲載日)\s*[:：]?\s*(?:" + _YMD_KO + "|" + _YMD_NUM + ")"
)
# 연도 없는 "10월 13일(월)" / "10/13(월)" 표기. 요일이 붙어 있으면 어느 해 날짜인지 가려낼 수 있다.
WEEKDAYS = {c: i for i, c in enumerate("월화수목금토일")} | {c: i for i, c in enumerate("月火水木金土日")}
_WEEKDAY = r"\s*[(（]\s*([월화수목금토일月火水木金土日])"
MONTH_DAY_RE = re.compile(
    r"(?:(20\d{2})\s*[년年]\s*)?(?<!\d)(\d{1,2})\s*[월月]\s*(\d{1,2})\s*[일日](?:" + _WEEKDAY + ")?"
)
SLASH_DAY_RE = re.compile(r"(?<![\d/.])(\d{1,2})/(\d{1,2})" + _WEEKDAY)
EN_MONTH_DAY_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?!,?\s+20\d{2})",
    re.IGNORECASE,
)


def today_kst() -> date:
    return datetime.now(KST).date()


def make_date(year, month, day) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except (TypeError, ValueError):
        return None


def parse_date(value: str) -> date | None:
    """'2026-10-02T09:00:00+09:00', '2026.10.02', '2026년 10월 2일' 같은 값에서 날짜만 뽑는다."""
    if not value:
        return None
    for pattern in FULL_DATE_RES:
        match = pattern.search(str(value))
        if match:
            return make_date(*match.groups())
    return None


def extract_full_dates(text: str) -> set[date]:
    """본문에 연도까지 명시된 날짜를 전부 모은다."""
    found = set()
    for pattern in FULL_DATE_RES:
        for match in pattern.finditer(text):
            found.add(make_date(*match.groups()))
    for match in EN_DATE_RE.finditer(text):
        found.add(make_date(match.group(3), EN_MONTHS[match.group(1).lower()], match.group(2)))
    found.discard(None)
    return found


def month_day_mentions(text: str) -> list[tuple[int | None, int, int, int | None]]:
    """본문의 월/일 표기를 (연도 or None, 월, 일, 요일 or None) 목록으로 돌려준다."""
    mentions = []
    for match in MONTH_DAY_RE.finditer(text):
        year, month, day, weekday = match.groups()
        mentions.append(
            (int(year) if year else None, int(month), int(day), WEEKDAYS.get(weekday))
        )
    for match in SLASH_DAY_RE.finditer(text):
        month, day, weekday = match.groups()
        mentions.append((None, int(month), int(day), WEEKDAYS.get(weekday)))
    for match in EN_MONTH_DAY_RE.finditer(text):
        mentions.append((None, EN_MONTHS[match.group(1).lower()], int(match.group(2)), None))
    return mentions


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def fetch_page(url: str) -> dict:
    """출처 페이지를 한 번 fetch해서 검증에 필요한 것들을 뽑는다: 열리는지(ok), 본문 텍스트,
    실제 발행일, 대표 이미지. 모델이 URL·이미지·날짜를 지어내거나 착각해도 걸러낼 수 있도록
    웹 검색 결과가 아니라 실제 페이지 내용을 근거로 삼는다."""
    page = {"ok": False, "error": "", "text": "", "image_url": "", "published_date": None, "images": [], "subpages": []}
    if not re.match(r"https?://", url or ""):
        page["error"] = "URL 없음"
        return page
    try:
        resp = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0 (compatible; PokemonCardsBot/1.0)"},
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        page["error"] = f"페이지를 열 수 없음 ({type(e).__name__})"
        return page

    # charset 헤더가 없으면 requests가 ISO-8859-1로 가정해서 한글/일본어가 깨진다.
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding
    html = resp.text[:1_500_000]
    if host_of(resp.url).endswith(OFFICIAL_HOSTS):
        # 공식 공지·행사 페이지는 내용이 대부분 이미지라, 본문 이미지와 하위 메뉴(티켓 안내 등) 주소를 모아 둔다.
        images = [urljoin(resp.url, unescape(u)) for u in re.findall(r'<img[^>]+src="([^"]+)"', html)]
        page["images"] = list(dict.fromkeys(u for u in images if "/newdata/" in u or "/upload" in u.lower()))[:12]
        path = urlsplit(resp.url).path.rstrip("/")
        if path:
            subpages = re.findall(rf'href="((?:https?://[^"]*)?{re.escape(path)}/menu\d+)"', html)
            page["subpages"] = list(dict.fromkeys(urljoin(resp.url, u) for u in subpages))[:6]
    page["text"] = html_to_text(html)
    if len(page["text"]) < 200:
        # 본문을 스크립트로 그리는 페이지는 태그만 걷어내면 남는 게 없다. 스크립트 안 데이터까지 본다.
        page["text"] = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()
    page["ok"] = True

    for pattern in IMAGE_META_RES:
        match = pattern.search(html)
        if match:
            page["image_url"] = urljoin(resp.url, unescape(match.group(1).strip()))
            break

    for pattern in DATE_META_RES:
        match = pattern.search(html)
        if match and parse_date(match.group(1)):
            page["published_date"] = parse_date(match.group(1))
            break
    if not page["published_date"]:
        match = LABELED_DATE_RE.search(page["text"])
        if match:
            page["published_date"] = parse_date(match.group(0))
    return page


def check_freshness(candidate: dict, page: dict, today: date) -> tuple[bool, str, str]:
    """후보가 '최근 기사' 또는 '임박한 일정'임을 출처 페이지 내용으로 입증할 수 있는지 검사한다.
    (통과 여부, 사유, 기준 날짜 YYYY-MM-DD)를 돌려준다."""
    text = page["text"]
    text_dates = extract_full_dates(text)
    claimed_pub = parse_date(candidate.get("published_date", ""))
    event = parse_date(candidate.get("event_date", ""))

    # (a) 최근 기사: 페이지 실측 발행일이 있으면 그것만 믿는다. 없을 때만 모델이 보고한 날짜를
    # 쓰되, 그 날짜가 본문에 연도 포함으로 적혀 있어야 한다.
    pub = page["published_date"] or (claimed_pub if claimed_pub in text_dates else None)
    if pub and -1 <= (today - pub).days <= MAX_NEWS_AGE_DAYS:
        return True, f"최근 기사(발행 {pub})", pub.isoformat()

    # (b) 임박한 일정: 시작일이 곧 다가오고, 그 날짜가 출처에서 확인돼야 한다.
    if not event:
        return False, f"발행일이 최근이 아님(실측 {pub}) · 예정 일정 없음", ""
    days_ahead = (event - today).days
    if not 0 <= days_ahead <= UPCOMING_WINDOW_DAYS:
        return False, f"발행일이 최근이 아니고(실측 {pub}) 일정({event})도 {UPCOMING_WINDOW_DAYS}일 이내가 아님", ""
    if event in text_dates:
        return True, f"임박 일정({event}, 출처에 연도 포함 표기)", event.isoformat()

    # 연도 없는 표기뿐이면 작년 같은 날짜의 행사 페이지일 수 있다. 요일과 페이지의 다른 날짜로 가려낸다.
    same_day = [m for m in month_day_mentions(text) if m[0] is None and (m[1], m[2]) == (event.month, event.day)]
    if not same_day:
        return False, f"일정 날짜({event})가 출처 본문에 없음", ""
    weekdays = {m[3] for m in same_day if m[3] is not None}
    if weekdays and weekdays != {event.weekday()}:
        return False, f"출처의 요일 표기가 {event.year}년 달력과 다름 — 다른 해의 일정으로 보임", ""
    newest = max(text_dates | ({page["published_date"]} - {None}), default=None)
    if newest and (today - newest).days > STALE_PAGE_DAYS:
        return False, f"출처 페이지의 가장 최근 날짜가 {newest} — 묵은 페이지로 보임", ""
    if not weekdays and not newest:
        return False, "출처에 연도도 요일도 없어 어느 해 일정인지 확인 불가", ""
    return True, f"임박 일정({event}, 출처 표기 확인)", event.isoformat()


def find_weekday_error(text: str, today: date) -> str:
    """포스트 본문의 '10월 13일(월)' 같은 표기가 실제 달력과 맞는지 본다. 틀리면 그 표기를 돌려준다.
    요일이 안 맞는다는 건 대개 다른 해의 일정을 올해 것으로 옮겨 적었다는 신호다."""
    for year, month, day, weekday in month_day_mentions(text):
        if weekday is None:
            continue
        if year:
            options = [make_date(year, month, day)]
        else:  # 연도가 없으면 오늘과 가장 가까운 해로 해석
            options = [make_date(today.year + d, month, day) for d in (-1, 0, 1)]
        options = [d for d in options if d]
        if not options:
            continue
        actual = min(options, key=lambda d: abs((d - today).days))
        if actual.weekday() != weekday:
            return f"{month}월 {day}일({'월화수목금토일'[weekday]}) → 실제 {actual}은 {'월화수목금토일'[actual.weekday()]}요일"
    return ""


def normalize_url(url: str) -> str:
    """같은 페이지를 가리키는 URL들이 같은 문자열이 되도록 정리한다 (추적 파라미터·www·끝 슬래시 제거)."""
    parts = urlsplit((url or "").strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_"))
    )
    return f"{host}{parts.path.rstrip('/')}" + (f"?{query}" if query else "")


def topic_bigrams(text: str) -> set[str]:
    # 날짜·가격 같은 숫자는 서로 다른 소식끼리도 겹치기 쉬워서 뺀다.
    cleaned = re.sub(r"[^a-z가-힣ぁ-ヿ一-鿿]", "", (text or "").lower())
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def topic_similarity(a: str, b: str) -> float:
    """두 소재 요약이 얼마나 겹치는지 (짧은 쪽 기준 문자 바이그램 포함 비율, 0~1)."""
    grams_a, grams_b = topic_bigrams(a), topic_bigrams(b)
    if len(grams_a) < 4 or len(grams_b) < 4:
        return 0.0
    return len(grams_a & grams_b) / min(len(grams_a), len(grams_b))


def find_duplicate(candidate: dict, history: list[dict], today: date) -> str:
    """이미 다룬 소식이면 그 이유를 돌려준다. 새 소식이면 빈 문자열."""
    url = normalize_url(candidate.get("source_url", ""))
    label = f"{candidate.get('topic', '')} {candidate.get('event_key', '')}"
    for entry in history:
        posted_urls = [entry.get("source_url", ""), *entry.get("source_urls", [])]
        if url and url in {normalize_url(u) for u in posted_urls if u}:
            return f"같은 출처 URL을 이미 게시함 ({entry.get('posted_at', '')[:10]})"
    for entry in recent_history(history, today):
        past = f"{entry.get('topic', '')} {entry.get('event_key', '')}"
        score = max([topic_similarity(label, past)] + [topic_similarity(label, k) for k in entry.get("buy_keys", [])])
        if score >= TOPIC_SIMILARITY_THRESHOLD:
            return f"이미 다룬 소재와 유사({score:.2f}): {entry.get('topic', '')[:50]}"
    return ""


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
- 네가 낸 후보는 코드가 출처 페이지를 직접 열어서 날짜·중복을 검증하고, 입증 안 되면 버려. 그러니
  반드시 웹 검색으로 출처 페이지를 실제로 확인하고, 아래 둘 중 하나에 해당하는 것만 내:
  (a) 최근 7일 이내에 발행된 기사/공지 — published_date에 그 발행일을 넣어.
  (b) 오늘부터 14일 이내에 시작하는 발매/이벤트/응모 일정 — event_date에 그 시작일을 넣어.
  몇 달 뒤 행사, 이미 시작했거나 끝난 행사, 몇 주 전 기사는 (새로운 발표가 최근 7일 내에 나온 게
  아니라면) 내지 마.
- 연도를 반드시 확인해. 검색 결과에는 작년(또는 그 이전) 같은 이름의 행사·제품 페이지가 자주 섞여
  나오고, 그런 페이지는 "10월 13일(월)"처럼 연도 없이 적혀 있어서 올해 일로 착각하기 쉬워. 페이지의
  작성일, 연도 표기, 요일이 올해 달력과 맞는지 확인하고, 올해 것이라는 확신이 없으면 버려.
- 요일은 출처에 적힌 그대로만 써. 직접 계산하거나 추측해서 붙이지 마.
- 본문에 구체적 날짜/시점을 명시해 ("O월 O일 발매", "이번 주말", "8월 O일부터" 등). 언제 일인지
  모호하게 두지 마.
- 가격·날짜·이름·장소는 출처 페이지에서 확인된 사실만. 지어내지 마.

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


def load_state() -> dict:
    """상태 파일 전체. posts: 게시 이력(오래된 것 → 최신), checked: 속보 판정을 마친 글 URL → 판정 시각."""
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}
    posts = [e for e in state.get("posts", []) if isinstance(e, dict)]
    checked = state.get("checked") if isinstance(state.get("checked"), dict) else {}
    slots = state.get("slots") if isinstance(state.get("slots"), dict) else {}
    return {"posts": posts, "checked": checked, "slots": slots}


def save_state(state: dict) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    cutoff = (datetime.now(KST) - timedelta(days=CHECKED_KEEP_DAYS)).isoformat()
    state = {
        "posts": state["posts"][-HISTORY_KEEP:],
        "checked": {url: at for url, at in state["checked"].items() if at >= cutoff},
        "slots": state.get("slots", {}),  # 감시 루프가 정기·구매정보 슬롯을 이미 돌렸는지 기록
    }
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_history() -> list[dict]:
    """게시 이력 전체(오래된 것 → 최신 순)."""
    return load_state()["posts"]


def recent_history(history: list[dict], today: date, days: int = DEDUP_WINDOW_DAYS) -> list[dict]:
    """최근 days일 안에 게시한 항목만 추린다 (소재 중복 판정 범위)."""
    cutoff = today - timedelta(days=days)
    recent = []
    for entry in history:
        try:
            posted = datetime.fromisoformat(entry["posted_at"]).date()
        except (KeyError, TypeError, ValueError):
            continue
        if posted >= cutoff:
            recent.append(entry)
    return recent


def save_post(candidate: dict, base_date: str, hook: str) -> None:
    state = load_state()
    state["posts"].append(
        {
            "topic": candidate.get("topic", ""),
            "event_key": candidate.get("event_key", ""),
            "source_url": candidate.get("source_url", ""),
            "base_date": base_date,
            "has_news": True,
            "hook": hook,
            "text": (candidate.get("threads_posts") or [""])[0][:400],
            "source_urls": candidate.get("source_urls", []),
            "buy_keys": candidate.get("buy_keys", []),
            "posted_at": datetime.now(KST).isoformat(),
        }
    )
    save_state(state)


def mark_checked(urls: list[str]) -> None:
    state = load_state()
    now = datetime.now(KST).isoformat()
    for url in urls:
        state["checked"].setdefault(normalize_url(url), now)
    save_state(state)


def build_user_prompt(mode: str, history: list[dict]) -> str:
    now = datetime.now(KST)
    weekday = "월화수목금토일"[now.weekday()]
    now_kst = f"{now:%Y-%m-%d}({weekday}) {now:%H:%M} KST"
    recent = recent_history(history, now.date())

    avoid_block = ""
    if recent:
        joined = "\n".join(
            f"- [{e.get('posted_at', '')[:10]}] {e.get('topic', '')}" for e in recent[-80:] if e.get("topic")
        )
        avoid_block = (
            "\n\n이미 게시한 소식들이야. 같은 행사·제품·사건은 다시 내지 마 (기간이 남은 행사를 한 번 더 "
            "알리는 것, 같은 발표를 다른 매체 기사로 다시 쓰는 것도 중복이야). 같은 소재는 게시일 이후에 "
            f"완전히 새로운 발표(일정 변경, 결과 발표 등)가 나온 경우에만 가능해:\n{joined}"
        )

    hook_block = ""
    recent_hooks = [e.get("hook", "") for e in recent if e.get("hook")]
    if recent_hooks:
        joined = "\n".join(f"- {h[:80]}" for h in recent_hooks[-8:])
        hook_block = (
            f"\n\n최근에 이미 쓴 첫 문장/훅들 (같은 패턴·같은 감탄사·같은 이모지 "
            f"반복하지 말고 완전히 다른 스타일로 새로 써):\n{joined}"
        )

    if mode == "scheduled":
        instruction = (
            "웹 검색으로 지금 시각 기준 최근 포켓몬 카드 관련 소식·일정을 찾아줘. 한국 소식·이벤트를 "
            "최우선으로 — 국내 신제품/재판 발매일과 예약 일정, 국내에서 곧 열리는 카드쇼·대회·"
            "팝업스토어·전시회 같은 오프라인 이벤트, 최근 시세/경매 소식, 최근 사건/이슈 순으로. "
            "국내 소재가 마땅치 않으면 해외 주요 소식/이벤트를 섞어도 돼.\n"
            "과거 사건을 오늘 소식처럼 쓰거나, 밈/공감·트리비아·보관꿀팁 같은 시간 무관 콘텐츠로 "
            "도망가는 건 금지야. 조건에 맞는 게 없으면 억지로 채우지 말고 candidates를 빈 배열로 둬."
        )
    else:  # breaking check
        instruction = (
            "최근 몇 시간~하루 이내에 포켓몬 카드 관련해서 새로 화제가 된 일이 있었는지 "
            "웹 검색으로 확인해. 예: 국내외 신제품·발매일 발표, 초고가 경매 낙찰, 대량 위조 카드 "
            "적발, 유명인 관련 화제, 품절 대란, 매장 앞 다툼 같은 사건 등. 한국 소식을 우선으로 보되 "
            "해외 주요 소식도 확인해.\n"
            "속보급 새 소식이 없으면 candidates를 빈 배열로 둬. 과거 사건을 오늘 소식처럼 재탕하거나, "
            "밈/공감·트리비아·보관꿀팁 같은 시간 무관 콘텐츠로 채우는 건 금지야."
        )

    return f"""\
현재 시각: {now_kst}
모드: {mode}

{instruction}

서로 다른 소식으로 후보를 최대 {MAX_CANDIDATES}개, 가장 새롭고 반응 좋을 만한 순서로 내줘. 코드가 앞에서부터
검증해서 처음 통과한 1개만 게시하니까, 후보끼리 소재와 출처가 겹치지 않게 해줘.
{avoid_block}
{hook_block}

다음 JSON 형식으로만 응답해:
{{
  "candidates": [
    {{
      "topic": "짧은 소재 요약 (한 줄, 중복 체크용)",
      "event_key": "이 행사/제품/사건의 고유 이름만 (예: 'SEOUL TCG CON', '30th CELEBRATION FUTURISTIC BOX')",
      "published_date": "출처 기사/공지의 발행일 YYYY-MM-DD (모르면 빈 문자열)",
      "event_date": "예정 일정이면 그 시작일 YYYY-MM-DD (아니면 빈 문자열)",
      "source_name": "출처 매체/사이트 이름",
      "source_url": "그 소식을 직접 다루는 개별 기사/공지 URL (카테고리 목록·홈페이지 금지)",
      "threads_posts": ["포스트 전체 텍스트"]
    }}
  ]
}}
"""


def parse_json_response(text: str) -> dict:
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:  # 코드펜스나 앞뒤 설명이 붙어 와도 JSON 본문만 취한다
        text = text[start : end + 1]
    # strict=False: 모델이 문자열 값 안에 이스케이프 안 된 줄바꿈 등 제어문자를 그대로
    # 넣는 경우가 있어서, 엄격 모드면 "Invalid control character" 에러로 파싱 자체가 죽는다.
    return json.loads(text, strict=False)


def generate(mode: str, history: list[dict]) -> list[dict]:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.responses.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
        tools=[{"type": "web_search"}],
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(mode, history)},
        ],
    )
    data = parse_json_response(response.output_text)
    candidates = data.get("candidates")
    if candidates is None and data.get("threads_posts"):  # 예전 단일 응답 형식으로 답한 경우
        candidates = [data]
    return [c for c in candidates or [] if isinstance(c, dict)][:MAX_CANDIDATES]


def judge_duplicates(candidates: list[dict], history: list[dict]) -> dict[int, str] | None:
    """글자 유사도로는 못 잡는 중복(같은 행사를 다른 이름·다른 매체로 다시 쓴 것)을 모델에게 판정시킨다.
    {후보 번호: 겹치는 과거 소재}를 돌려준다. 판정 호출이 실패하면 None — 중복 여부를 모르는 채로
    올리느니 이번 슬롯을 건너뛰도록 호출부에서 처리한다."""
    if not candidates or not history:
        return {}
    past = "\n".join(f"- [{e.get('posted_at', '')[:10]}] {e.get('topic', '')}" for e in history)
    new = "\n".join(
        f"{i}. {c.get('topic', '')} | {c.get('event_key', '')} | {(c.get('threads_posts') or [''])[0][:200]}"
        for i, c in enumerate(candidates)
    )
    prompt = f"""\
뉴스 채널의 중복 게시를 막는 검수자 역할이야. 아래 '후보' 각각이 '이미 게시한 소식' 중 하나와 같은
소식인지 판정해줘.

중복으로 보는 경우: 같은 행사·제품·사건을 다시 알리는 것 (표현이나 출처 매체가 달라도, 진행 중인 행사를
한 번 더 상기시키는 것도 중복).
중복이 아닌 경우: 다른 행사·제품·사건이거나, 같은 소재라도 과거 게시일 이후에 나온 명백히 새로운 발표
(일정 변경, 결과 발표, 가격 공개 등)가 후보의 핵심인 경우.

이미 게시한 소식:
{past}

후보:
{new}

다음 JSON 형식으로만 응답해 (중복인 후보만 넣어):
{{"duplicates": [{{"index": 후보 번호, "same_as": "겹치는 과거 소식"}}]}}
"""
    try:
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        response = client.responses.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-5-mini"), input=prompt
        )
        verdicts = parse_json_response(response.output_text).get("duplicates", [])
        return {int(v["index"]): str(v.get("same_as", "")) for v in verdicts}
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  중복 판정 호출 실패: {e}", file=sys.stderr)
        return None


def verify_candidate(candidate: dict, history: list[dict], today: date) -> tuple[dict | None, str]:
    """후보 하나를 코드로 검증한다. 통과하면 (게시에 쓸 정보, 사유), 탈락이면 (None, 사유)."""
    posts = [p.strip() for p in candidate.get("threads_posts") or [] if isinstance(p, str) and p.strip()]
    if not posts:
        return None, "포스트 본문 없음"

    duplicate = find_duplicate(candidate, history, today)
    if duplicate:
        return None, duplicate

    weekday_error = find_weekday_error("\n".join(posts), today)
    if weekday_error:
        return None, f"본문 요일이 달력과 안 맞음: {weekday_error}"

    page = fetch_page(candidate.get("source_url", ""))
    if not page["ok"]:
        return None, f"출처 확인 실패: {page['error']}"

    fresh, reason, base_date = check_freshness(candidate, page, today)
    if not fresh:
        return None, reason
    return {"posts": posts, "page": page, "base_date": base_date}, reason


def select_candidate(candidates: list[dict], history: list[dict], mode: str) -> tuple[dict, dict] | None:
    """후보들을 순서대로 검증해서 게시할 하나를 고른다. 없으면 None."""
    today = today_kst()
    passed = []
    for index, candidate in enumerate(candidates, start=1):
        verified, reason = verify_candidate(candidate, history, today)
        mark = "통과" if verified else "탈락"
        print(f"ℹ️  [{mode}] 후보 {index} {mark} — {candidate.get('topic', '')[:60]} · {reason}")
        if verified:
            passed.append((candidate, verified))

    verdicts = judge_duplicates([c for c, _ in passed], recent_history(history, today))
    if verdicts is None:
        return None
    for index, (candidate, verified) in enumerate(passed):
        if index in verdicts:
            print(
                f"ℹ️  [{mode}] 중복 판정으로 탈락 — {candidate.get('topic', '')[:60]} "
                f"· 이미 게시: {verdicts[index][:60]}"
            )
            continue
        return candidate, verified
    return None


def item_age_label(item: dict, now: datetime) -> str:
    published = item["published"]
    if not published:
        return "시각 불명(최신순 상위)"
    if item["dated_by_day"]:
        days = (now.date() - published.astimezone(KST).date()).days
        return "오늘" if days <= 0 else f"{days}일 전"
    hours = (now - published).total_seconds() / 3600
    return f"{int(hours * 60)}분 전" if hours < 1 else f"{hours:.0f}시간 전"


def fresh_items(items: list[dict], state: dict, mode: str, now: datetime) -> list[dict]:
    """수집한 글 중 지금 다룰 만한 것만 남긴다: 최근에 올라왔고, 아직 게시하지 않았고,
    (속보 모드라면) 아직 속보 판정을 하지 않은 글."""
    hours = BREAKING_WINDOW_HOURS if mode == "breaking" else SCHEDULED_WINDOW_HOURS
    fresh = []
    for item in items:
        published = item["published"]
        social = item["kind"] in SOCIAL_KINDS
        if social and not published:
            continue
        cutoff = now - timedelta(hours=min(hours, SOCIAL_WINDOW_HOURS) if social else hours)
        if published:
            if published > now + timedelta(hours=1):
                continue
            if item["dated_by_day"]:  # 날짜만 아는 글은 날짜 단위로 비교
                if published.astimezone(KST).date() < cutoff.date():
                    continue
            elif published < cutoff:
                continue
        if mode == "breaking" and normalize_url(item["url"]) in state["checked"]:
            continue
        # SNS 글은 원문 페이지를 못 열어서 수집한 본문이 전부다. 짧은 인사·질문 글은 소식이 될 수 없다.
        if social and len(item["summary"]) < MIN_CONTENT_CHARS:
            continue
        # 제목이 비슷하다고 미리 버리면 "개최되나 → 개최 확정" 같은 후속 발표까지 막힌다.
        # 여기선 같은 URL만 버리고, 같은 소식인지는 뒤에서 모델 판정(새 발표면 통과)에 맡긴다.
        if find_duplicate({"topic": "", "source_url": item["url"]}, state["posts"], now.date()):
            continue
        fresh.append(item)
    return fresh[:MAX_RANK_ITEMS]


def read_official_images(title: str, image_urls: list[str]) -> str:
    """공식 공지·행사 페이지 이미지에 적힌 글자를 모델로 옮겨 적는다 (본문이 이미지뿐인 공지를 읽기 위해)."""
    if not image_urls:
        return ""
    content = [{
        "type": "input_text",
        "text": (
            f"포켓몬코리아 공식 페이지 '{title}'의 이미지들이야. 이미지에 적힌 글자를 빠짐없이 그대로 옮겨 적어줘 "
            "(행사명, 일정·시간, 장소, 예매·응모 방법과 기간, 가격, 구매 제한, 특전, 주의사항). 요약하거나 해석하지 말고 "
            "적힌 대로만. 글자가 없는 이미지는 건너뛰고, 읽을 글자가 하나도 없으면 빈 문자열만 답해."
        ),
    }]
    content += [{"type": "input_image", "image_url": url} for url in image_urls[:8]]
    try:
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        response = client.responses.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-5-mini"), input=[{"role": "user", "content": content}]
        )
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  공식 이미지 읽기 실패: {e}", file=sys.stderr)
        return ""
    if response.usage:
        print(f"💰 모델 호출(이미지 읽기 {len(image_urls[:8])}장): 입력 {response.usage.input_tokens} · 출력 {response.usage.output_tokens} 토큰")
    return response.output_text.strip()


def official_content(item: dict, page: dict) -> str:
    """공식 출처 글의 본문. 글자가 충분하면 그대로, 이미지뿐이면 이미지(하위 메뉴 페이지 포함)를 읽어서 만든다."""
    text = article_slice(page["text"], item["title"], 5000) if page.get("ok") else ""
    # 공식 페이지는 글자가 있어도 메뉴·바닥글이 대부분이고 실제 안내는 이미지에 있어서, 이미지가 있으면 항상 읽는다.
    images = list(dict.fromkeys(item.get("images", []) + page.get("images", [])))[:4]
    for subpage in page.get("subpages", [])[:6]:  # 행사 사이트의 '티켓 구매 안내' 같은 하위 페이지
        if len(images) >= 8:
            break
        images += [u for u in fetch_page(subpage).get("images", []) if u not in images][:2]
    transcribed = read_official_images(item["title"], images)
    if not transcribed:
        return text
    return f"제목: {item['title']}\n(공식 페이지 이미지에 적힌 내용)\n{transcribed}\n{text}"[:6000]


def ask_json(system: str, user: str) -> dict:
    """웹 검색 없이 모델에게 묻고 JSON으로 받는다."""
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.responses.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
        input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    usage = response.usage
    if usage:  # 비용 추적용: 호출마다 실제 사용 토큰을 로그에 남긴다
        print(f"💰 모델 호출: 입력 {usage.input_tokens} · 출력 {usage.output_tokens} 토큰")
    return parse_json_response(response.output_text)


RANK_SYSTEM_PROMPT = """\
너는 포켓몬 카드(포켓몬 트레이딩 카드 게임) 뉴스 채널의 데스크야. 방금 수집된 글 목록에서 지금 바로
Threads에 올릴 가치가 있는 소식을 골라줘. 독자는 한국의 포켓몬 카드 수집가·플레이어고, 카드뿐 아니라
국내 포켓몬 팝업스토어·전시·공연·페스타 같은 오프라인 행사와 한정 굿즈 판매 소식도 우리 채널이 빨리 전해야 할 소식이야.

고르는 기준 (위에 있을수록 우선):
1. 지금 행동할 수 있는 한국 소식 — 발매·입고·재입고·예약/추첨 시작, 판매처·수량·가격 안내, 프로모 배포
2. 공식 발표 — 포켓몬코리아·포켓몬컴퍼니의 신제품, 대회, 이벤트, 일정 변경 공지
3. 사건·이슈 — 품절 대란, 구매 제한, 위조 적발, 사기·도난, 리셀 논란, 시세 급변
4. 국내 포켓몬 오프라인 행사 — 팝업스토어, 전시, 공연, 페스타, 카드쇼, 대회 (새로 발표·예매 시작이면 1순위급)
5. 해외 주요 소식 — 신세트 공개·발매, 대형 경매, 화제의 사건

threads·x·instagram 글은 개인이 쓴 것일 수 있어. 공식 계정·매장 계정의 공지이거나, 본인이 직접 확인한
구체적 사실(어느 매장에 무엇이 몇 개, 언제부터)을 담은 글만 골라. 소문·추측·잡담·판매글은 고르지 마.

고르지 말 것:
- 게임·애니·앱만의 소식, 포켓몬이 지나가듯 언급된 기사 (국내 오프라인 행사·굿즈 판매는 고를 것)
- 해설·칼럼·투자 분석·"시장이 커진다"류 트렌드 기사처럼 새로 일어난 일이 없는 글
- 보관법·구별법·입문 가이드 같은 꿀팁, 제품 리뷰·개봉기, 쇼핑몰 핫딜·제휴 광고, 구매대행 홍보
- '이미 게시한 소식'과 같은 행사·제품·사건 (다른 매체가 같은 발표를 다시 쓴 것도 포함)
- 이미 끝난 일정

같은 소식을 여러 글이 다루면 가장 원문에 가깝고 자세한 글 하나만 골라.

'이미 게시한 소식'과 같은 행사라도, 그 뒤에 나온 새 발표(개최 확정, 예매 오픈, 굿즈 공개, 일정 변경, 결과)는
새 소식이야. 같은 발표를 다시 쓴 기사만 고르지 마.

breaking 은 "방금(몇 시간 안에) 처음 나온 발표·사건이고 바로 알려야 하는 소식"일 때 true 로 해:
새 행사·팝업·공연 발표나 예매·응모 오픈, 발매·입고·추첨 공지, 공식 발표, 큰 사건. 며칠 된 이야기를
다시 다룬 글, 해외 할인 정보, 칼럼·트렌드 기사는 false 야.
"""


def coverage_count(item: dict, pool: list[dict]) -> int:
    """같은 소식을 다룬 것으로 보이는 다른 매체 글 수 (제목 유사도 기준, 교차 검증 가능성의 힌트)."""
    outlets = {
        re.sub(r"\s+", "", other["source"]).lower()
        for other in pool
        if other is not item
        and other["source"] != item["source"]
        and other["kind"] not in (*SOCIAL_KINDS, "blog")
        and topic_similarity(item["title"], other["title"]) >= 0.35
    }
    return len(outlets)


def rank_items(items: list[dict], history: list[dict], now: datetime, pool: list[dict] | None = None) -> list[dict]:
    """모델에게 수집 글 목록을 보여주고 올릴 만한 글을 순서대로 고르게 한다."""
    pool = pool or items
    listing = "\n".join(
        f"[{i}] ({item['kind']} · {item['source']} · {item_age_label(item, now)} · 다른 매체 {coverage_count(item, pool)}곳) {item['title']}"
        + (f" — {item['summary'][:160]}" if item["summary"] else "")
        for i, item in enumerate(items)
    )
    posted = "\n".join(
        f"- [{e.get('posted_at', '')[:10]}] {e.get('topic', '')}" for e in recent_history(history, now.date())[-60:]
    )
    weekday = "월화수목금토일"[now.weekday()]
    user = f"""\
현재 시각: {now:%Y-%m-%d}({weekday}) {now:%H:%M} KST

이미 게시한 소식:
{posted or '(없음)'}

수집된 글 (최신순):
{listing}

올릴 만한 글을 가치가 높은 순서로 최대 {MAX_CANDIDATES}개 골라줘. 마땅한 게 없으면 빈 배열로 둬 —
억지로 채우지 마. 다음 JSON 형식으로만 응답해:
{{"picks": [{{"index": 글 번호, "breaking": true 또는 false, "why": "고른 이유 한 줄",
  "query": "같은 소식을 다룬 다른 기사를 찾을 뉴스 검색어 (핵심 고유명사 2~4개, 예: 'GS25 포켓몬 30주년 확장팩')"}}]}}
"""
    picks = []
    for pick in ask_json(RANK_SYSTEM_PROMPT, user).get("picks", []):
        try:
            index = int(pick["index"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= index < len(items) and index not in [p["index"] for p in picks]:
            picks.append(
                {
                    "index": index,
                    "breaking": bool(pick.get("breaking")),
                    "why": str(pick.get("why", "")),
                    "query": str(pick.get("query", "")),
                }
            )
    return picks[:MAX_CANDIDATES]


def host_of(url: str) -> str:
    """출처 독립성 비교용 도메인 (www·모바일·AMP 접두어 제거)."""
    return re.sub(r"^(www|m|amp|biz|news)\.", "", urlsplit(url).netloc.lower())


def outlet_of(source: dict) -> str:
    """출처가 어느 매체인지 나타내는 키. 서로 다른 매체 두 곳인지 셀 때 쓴다.
    다음이 호스팅하는 기사(v.daum.net)는 도메인이 전부 같으므로 매체 이름으로 구분한다."""
    host = host_of(source["url"])
    if host == "v.daum.net":
        return re.sub(r"\s+", "", source["name"]).lower()
    return host


def article_slice(text: str, title: str, limit: int) -> str:
    """페이지 텍스트 앞쪽의 메뉴·배너를 건너뛰고 제목이 나오는 곳부터 limit 글자를 돌려준다."""
    start = text.find(title.strip()[:15]) if len(title.strip()) >= 8 else -1
    return text[max(start, 0) :][:limit]


OFFICIAL_CITATION_RE = re.compile(r"(포켓몬코리아|포켓몬\s*코리아|포켓몬스토어|포켓몬\s*카드\s*게임)\s*공식\s*(인스타그램|홈페이지|사이트|공지|채널|X|트위터)")


def cites_official(source: dict) -> bool:
    """블로그 글이 포켓몬코리아 공식 채널 공지를 정리한 글인지 (원 공지를 직접 못 읽을 때의 차선책)."""
    return source["kind"] == "blog" and bool(OFFICIAL_CITATION_RE.search(source["text"][:4000]))


def is_press(source: dict) -> bool:
    """언론사 기사인지 (단독 보도로 인정할 수 있는 출처인지)."""
    host = host_of(source["url"])
    return source["kind"] in ("news", "overseas") and (host == "v.daum.net" or host.endswith(REPUTABLE_HOSTS) or "news" in host)


def make_source(name: str, url: str, kind: str, text: str, published: str = "") -> dict:
    return {"name": name, "url": url, "kind": kind, "text": text, "published": published}


def earliest_report(title: str, related: list[dict]) -> str:
    """같은 소식으로 보이는 글 중 가장 이른 게시일 (YYYY-MM-DD). 모르면 빈 문자열."""
    # 매번 비슷한 제목으로 열리는 이벤트(원더픽 이벤트 등)를 옛 소식으로 착각하지 않게 기준을 높게 잡는다.
    dates = [i["published"] for i in related if i.get("published") and topic_similarity(title, i["title"]) >= 0.6]
    return f"{min(dates).astimezone(KST):%Y-%m-%d}" if dates else ""


def find_cross_sources(
    primary: dict, title: str, query: str, pool: list[dict], now: datetime
) -> tuple[list[dict], str]:
    """같은 소식을 다룬 '다른 곳'의 글을 찾아 본문까지 가져온다. 수집 목록에서 제목이 비슷한 글과,
    뉴스 검색(관련도순) 결과에서 고른다. 주 출처와 도메인이 같은 글은 독립 출처가 아니라서 뺀다."""
    cutoff = now - timedelta(days=CROSS_SOURCE_DAYS)
    # 검색 결과에는 무관한 기사도 섞여 오므로, 검색어의 핵심 단어가 실제로 나오는 글만 쓴다.
    keywords = {w.lower() for w in re.findall(r"[0-9A-Za-z가-힣]{2,}", query or title)}
    keywords -= {"포켓몬", "카드", "포켓몬카드", "pokemon", "pokémon", "tcg", "cards", "card"}
    needed = min(2, len(keywords))

    def mentions(text: str) -> bool:
        return sum(word in text.lower() for word in keywords) >= needed

    related = [
        i for i in pool
        if topic_similarity(title, i["title"]) >= CROSS_TITLE_SIMILARITY or (needed and mentions(f"{i['title']} {i['summary']}"))
    ]
    for search in filter(None, [query.strip(), title.strip()[:60]]):
        english = bool(re.fullmatch(r"[\x00-\x7f]+", search))
        searches = [lambda: fetch_bing_news(search, "en-US" if english else "ko-KR", newest=False)]
        if not english:
            searches.insert(0, lambda: fetch_daum_news(search, newest=False))
        for run in searches:
            try:
                related += run()[:8]
            except Exception as e:  # noqa: BLE001
                print(f"⚠️  교차 검증용 검색 실패({search[:30]}): {type(e).__name__}", file=sys.stderr)

    # 주 출처와 같은 매체(이름 또는 도메인)는 독립 출처가 아니다.
    seen_hosts = {host_of(primary["url"]), re.sub(r"\s+", "", primary["name"]).lower()}
    for other in related:  # 공식 사이트 글은 어느 경로로 찾았든 공식 출처로 취급하고 먼저 본다
        if host_of(other["url"]).endswith(OFFICIAL_HOSTS):
            other["kind"] = "official"
    related.sort(key=lambda i: i["kind"] != "official")
    sources = []
    for other in related:
        host = outlet_of({"url": other["url"], "name": other["source"]})
        outlet_name = re.sub(r"\s+", "", other["source"]).lower()
        # 블로그·SNS 글은 소식의 실마리는 되지만 다른 글을 확인해 주는 근거로는 쓰지 않는다.
        if outlet_name in seen_hosts or host in seen_hosts or host.endswith(AGGREGATOR_HOSTS) or other["kind"] in (*SOCIAL_KINDS, "blog"):
            continue
        if other["published"] and other["published"] < cutoff:  # 오래된 글은 근거로는 안 쓰고, 처음 보도일 계산에만 쓴다
            continue
        page = fetch_page(other["url"])
        if not page["ok"] or len(page["text"]) < 300:
            continue
        if page["published_date"] and (now.date() - page["published_date"]).days > CROSS_SOURCE_DAYS:
            continue
        if not mentions(page["text"]):
            continue
        seen_hosts.update({host, outlet_name})
        stamp = f"{other['published'].astimezone(KST):%Y-%m-%d}" if other["published"] else ""
        sources.append(
            make_source(other["source"], other["url"], other["kind"], article_slice(page["text"], other["title"], 3000), stamp)
        )
        if len(sources) >= MAX_CROSS_SOURCES:
            break
    return sources, earliest_report(title, related)


def format_sources(sources: list[dict], first_reported: str = "") -> str:
    blocks = [f"(참고: 수집된 글 기준으로 이 소식이 처음 보도된 날은 {first_reported})"] if first_reported else []
    for index, source in enumerate(sources):
        role = "주 출처" if index == 0 else "교차 확인용"
        official = " · 공식" if source["kind"] == "official" else ""
        blocks.append(
            f"[출처 {index} — {role}{official}] {source['name']} · 게시 {source['published'] or '불명'}\n"
            f"\"\"\"\n{source['text']}\n\"\"\""
        )
    return "\n\n".join(blocks)


def related_past_posts(title: str, history: list[dict], today: date) -> str:
    """같은 소재로 우리가 이미 올린 글 (새 글이 과거 글과 날짜·가격이 어긋나지 않게 참고시킨다)."""
    lines = []
    for entry in recent_history(history, today):
        label = f"{entry.get('topic', '')} {entry.get('event_key', '')}"
        if topic_similarity(title, label) >= 0.3:
            body = entry.get("text") or entry.get("hook", "")
            lines.append(f"- [{entry.get('posted_at', '')[:10]}] {entry.get('topic', '')} :: {body[:300]}")
    return "\n".join(lines[-5:])


WRITING_STYLE = """\
말투 (우리 채널이 원래 올리던 톤 — 아래 예시를 꼭 따라 해):
- 친구한테 소식 전하듯 가볍고 생기 있게. "~예요/~래요/~네요/~더라고요" 존댓말 구어체가 기본이고,
  "~다/~합니다"로 딱딱하게 끝나는 보도자료·번역체 문장은 피해.
- 첫 문장이 훅이야. 핵심 사실을 툭 던지거나("이제 딱 3주 남았어요 — ..."), 질문으로 시작하거나
  ("이 카드 한 장, 3천 달러대 추정이라고요?"), 반응을 먼저 보여줘("부산이 지금 포켓몬 천국이에요").
  같은 훅 패턴을 연달아 쓰지 마.
- 사실(날짜·장소·가격·제품명)은 문장 속에 자연스럽게 녹여. 항목 나열하듯 끊지 마.
- 수집가 입장에서 한마디 리액션이나 실전 팁을 섞어 ("지갑은 조심하되 관전 포인트로는 꿀잼",
  "현장 갈 분들은 일정 다시 한 번 확인하세요", "낙찰가 어디까지 뛸지 같이 지켜볼까요").
- 이모지는 1~2개. 끝에 "출처: 매체명" 짧게, 해시태그 2~4개. 전체 500자 이내.
- 포켓몬·인물·게임 기능 이름은 한국 공식 명칭으로 (Charmander→파이리, Chansey→럭키, Wonder Pick→원더픽).
  확실하지 않으면 영어 이름을 그대로 둬.
- 단, 말투만 가볍게 하는 거야. 지어낸 경험담·과장·밈은 금지, 사실은 출처에 있는 것만.

정보는 빠짐없이 (말투는 가볍게, 내용은 꽉 차게):
- 행사·팝업·공연: 이름, 기간(요일 포함은 출처에 있을 때만), 운영 시간, 장소(건물·층·주소), 입장·예매 방법(예매처,
  1인 제한, 사전예약 여부), 가격, 특전·한정 굿즈, 주의할 점(현장판매 없음 등)
- 발매·입고·추첨: 제품명, 날짜·시간, 판매처, 가격, 구매 제한, 응모·결제 기간
- 출처에 있는 핵심 정보를 빼고 감상만 쓰면 안 돼. 필요하면 줄바꿈으로 덩어리를 나눠 읽기 쉽게.

우리 채널이 실제로 올렸던 글 (말투와 정보량 참고용 — 내용·문구는 베끼지 마):
1) 서울 종로에서 9월 18일(금), 한복 입은 피카츄 굿즈를 바로 살 수 있어요 — '포켓몬 고풍 상점' 팝업이
   하이커그라운드 5층에서 9월 18일부터 10월 11일까지 열려요. 🧧
   운영 10:00–20:00, 장소는 하이커그라운드 5층(서울 중구 청계천로 40).
   스탬프 랠리 완주하면 30주년 한복 피카츄 스티커를 주고, 프리미엄 레더 굿즈는 한정 수량·1인 구매 제한이
   있으니 참고하세요. 팝업에선 카드 확장팩은 안 판대요!
   출처: K-Style Seoul #포켓몬팝업 #포켓몬카드
2) 이 카드 한 장, 3천 달러대 추정이라고요? 'Pikachu With Gray Felt Hat' PSA 10이 오늘 Crafted Auctions 경매에
   올랐어요. 감정가 $3,000–$4,000, 현재 입찰 $1,500. 반 고흐 콜라보 프로모라 커뮤니티 관심도 높아요 — 낙찰가
   어디까지 뛸지 같이 지켜볼까요. 출처: LiveAuctioneers #포켓몬카드 #경매
3) 30주년 DAY·NIGHT 박스 노리시는 분? 데코데코 그랜드 오픈 기념 응모가 9월 15일 0시부터 시작됐어요.
   기간은 10월 10일 22:10까지, 누적 실결제 30만원마다 응모권 1장(60만원이면 2장). DAY·NIGHT 각 1명에게
   Ultra-Premium BOX를 주고, 추첨은 10월 10일 밤 10시 10분이에요. 취소·환불 금액은 빠지니 결제 완료 꼭 확인하세요!
   출처: 데코데코 이벤트 페이지 #포켓몬카드 #30주년

금지:
- 검증 과정을 드러내는 말 ("보도로 확인됐다", "교차 확인", "출처 1", "확인된 바에 따르면")
- 독자에게 떠넘기는 말 ("자세한 내용은 공지를 확인하세요", "공지 본문을 참고하세요")
- "어디서: / 무엇: / 언제:" 같은 항목 이름 나열, 영어 기사 문장을 그대로 옮긴 번역투
"""

# 글 모양 규칙 위반을 코드로도 잡는다 (위반하면 사실 대조와 같은 방식으로 고쳐 쓰게 한다).
STYLE_BANNED_RE = re.compile(
    r"교차\s*확인|보도로\s*확인|확인된\s*바에|출처\s*\d|공지\s*본문|자세한\s*(내용|사항)[은는]?.{0,12}확인|"
    r"(어디서|무엇|언제|어떻게)\s*[:：]"
)


def style_problems(posts: list[str]) -> list[str]:
    text = "\n".join(posts)
    problems = []
    match = STYLE_BANNED_RE.search(text)
    if match:
        problems.append(f"글 모양 규칙 위반: '{match.group(0)}' 같은 검증·안내·항목 이름 표현은 빼고 자연스러운 문장으로")
    if len(text) > 500:
        problems.append(f"글이 {len(text)}자라 500자를 넘음 — 줄여서")
    return problems


def write_post(
    title: str, sources: list[dict], history: list[dict], now: datetime, feedback: str = "", first_reported: str = ""
) -> dict:
    """출처 본문들만 근거로 포스트를 쓰게 한다 (웹 검색 없음 — 출처에 없는 내용은 못 쓴다)."""
    recent = recent_history(history, now.date())
    posted = "\n".join(f"- [{e.get('posted_at', '')[:10]}] {e.get('topic', '')}" for e in recent[-40:])
    hooks = "\n".join(f"- {e.get('hook', '')[:80]}" for e in recent[-8:] if e.get("hook"))
    related = related_past_posts(title, history, now.date())
    weekday = "월화수목금토일"[now.weekday()]
    retry = f"\n앞서 쓴 글이 사실 대조에서 걸렸어. 아래 문제를 모두 고쳐서 다시 써 (해당 내용은 빼는 게 원칙):\n{feedback}\n" if feedback else ""
    user = f"""\
현재 시각: {now:%Y-%m-%d}({weekday}) {now:%H:%M} KST

아래 출처들을 근거로 Threads 포스트 1개를 써줘. 이번에는 웹 검색을 하지 않아.
{retry}
사실 규칙:
- 공식 출처나 언론 기사에 적힌 사실은 그대로 써도 돼 (언론 한 곳만의 보도면 "○○ 보도에 따르면"처럼 밝혀).
  블로그가 공식 채널 공지(예: 포켓몬코리아 공식 인스타그램)를 정리한 글이면, 그 공지 내용은 써도 되고 출처는
  "포켓몬코리아 공식 인스타그램 공지"처럼 원래 공지로 밝혀. 블로그 글쓴이의 감상·추측은 쓰지 마.
  블로그·SNS 글에만 있는 수치는 공식 출처나 언론 기사로 확인될 때만 써.
- 출처끼리 서로 다르게 적은 사실(예: 발매일·가격이 다름)은 그 수치만 빼고 나머지 확인된 내용으로 써.
  소식의 핵심 자체(무엇이 일어났는지)가 출처끼리 엇갈릴 때만 postable 을 false 로 해.
- 사진 설명 한 줄, 한 매장의 사례를 전체 일인 것처럼 넓혀 쓰지 마 ("○○점에서 촬영"을 "○○점에서 출시"로
  바꾸는 식의 왜곡 금지).
- '우리가 이미 올린 관련 글'과 날짜·가격이 다르면, 이번 출처들로 확인되는 쪽만 써. 확인이 안 되면 그 수치는 빼.
- 어느 출처에도 없는 날짜·요일·수치는 절대 덧붙이지 마. URL은 넣지 마.
- 출처 안내에 "처음 보도된 날"이 적혀 있으면, 그날 이후 새로 생긴 일(출시·판매 시작·결과 발표 등)을
  중심으로 써. 처음 보도 이후 새로 생긴 일이 없으면 이미 지난 소식이니 postable 을 false 로 해.

{WRITING_STYLE}
다음 중 하나라도 해당하면 쓰지 말고 postable 을 false 로 해:
- 포켓몬 카드 소식이 아니거나, 새로 일어난 일 없이 해설·칼럼·꿀팁·리뷰·광고뿐이다
- 확인된 구체적 사실(무엇을·언제·어디서·얼마)이 부족해서 소식으로 쓸 수 없다
- 이미 끝난 일정이거나 오늘 올리기엔 시의성이 없다
- '이미 게시한 소식'과 같은 소식이다 (게시일 이후의 새로운 발표가 핵심인 경우는 제외)

소식 제목: {title}

{format_sources(sources, first_reported)}

우리가 이미 올린 관련 글:
{related or '(없음)'}

이미 게시한 소식:
{posted or '(없음)'}

최근에 쓴 첫 문장들 (같은 패턴 반복 금지):
{hooks or '(없음)'}

다음 JSON 형식으로만 응답해:
{{
  "postable": true 또는 false,
  "reason": "postable 이 false 인 이유 (true 면 빈 문자열)",
  "topic": "짧은 소재 요약 (한 줄, 중복 체크용)",
  "event_key": "이 행사/제품/사건의 고유 이름만",
  "threads_posts": ["포스트 전체 텍스트"]
}}
"""
    return ask_json(SYSTEM_PROMPT, user)


VERIFY_SYSTEM_PROMPT = """\
너는 뉴스 채널의 팩트체커야. 게시 직전의 포스트를 출처 원문과 대조해서, 독자를 잘못 이끌 수 있는
오류를 잡아내는 게 일이야. 표기 차이를 트집 잡는 게 아니라 "틀린 정보가 나가는 것"을 막는 게 목적이야.

할 일:
1. 포스트에서 소식에 관한 사실 주장을 뽑아 (날짜, 가격, 수량, 장소, 사람·회사·제품 이름, "누가 무엇을
   했다/한다"). "출처: ○○", "자세한 건 공지를 확인하세요", "~할 전망" 같은 안내·전망·출처 표기 문장은
   사실 주장이 아니니 뽑지 마.
2. 주장마다 그 내용이 적혀 있는 출처 번호를 전부 적어. 같은 뜻이면 표현이 달라도 뒷받침한 것으로 봐.
3. 문제가 있으면 problem 에 적고 severity 를 정해:
   - major: 출처에 없는 사실을 지어냄 / 출처끼리 날짜·시간·가격·수량·장소·대상이 실제로 다름 /
     포스트가 출처를 왜곡함 (한 매장 사례를 전체로, 예정을 완료로, 추정을 확정으로, 다른 제품으로)
   - minor: 표기·번역·띄어쓰기 차이 (e스포츠/이스포츠, LINE Yahoo/Yahoo), 한 출처가 더 자세할 뿐인 경우,
     반올림, 보도 날짜가 하루 다른 것처럼 소식의 사실이 바뀌지 않는 차이
   문제가 없으면 problem 은 빈 문자열.
4. core 에는 이 소식의 핵심 주장(무엇이 언제 일어났다/일어난다) 하나를 적고, 그걸 뒷받침하는 출처
   번호를 적어. 같은 통신사 기사를 그대로 옮겨 실은 글은 하나의 출처로 쳐서 대표 하나만 적어.
"""


def verify_post(posts: list[str], sources: list[dict], now: datetime, require_core: bool = True) -> dict:
    """완성된 포스트를 출처들과 대조시키고, 게시해도 되는지 코드로 판정한다.
    {"ok": bool, "problems": [...], "confirmed_by": [출처 번호...]} 를 돌려준다."""
    weekday = "월화수목금토일"[now.weekday()]
    user = f"""\
현재 시각: {now:%Y-%m-%d}({weekday}) {now:%H:%M} KST

포스트:
\"\"\"
{chr(10).join(posts)}
\"\"\"

{format_sources(sources)}

다음 JSON 형식으로만 응답해:
{{
  "claims": [{{"claim": "포스트의 사실 주장", "supported_by": [출처 번호], "problem": "문제 설명 (없으면 빈 문자열)", "severity": "major 또는 minor (문제 없으면 빈 문자열)"}}],
  "core": {{"claim": "핵심 주장", "supported_by": [출처 번호]}}
}}
"""
    result = ask_json(VERIFY_SYSTEM_PROMPT, user)

    def valid(indices) -> list[int]:
        return sorted({i for i in indices or [] if isinstance(i, int) and 0 <= i < len(sources)})

    problems = []
    for claim in result.get("claims") or []:
        text = str(claim.get("claim", ""))[:80]
        if claim.get("problem") and str(claim.get("severity", "")).lower() != "minor":
            problems.append(f"{text} — {str(claim['problem'])[:140]}")
        elif not valid(claim.get("supported_by")):
            problems.append(f"출처에 없는 내용: {text}")

    core = valid((result.get("core") or {}).get("supported_by"))
    official = any(sources[i]["kind"] == "official" for i in core)
    press = any(is_press(sources[i]) or cites_official(sources[i]) for i in core)
    independent = len({outlet_of(sources[i]) for i in core})
    if not result.get("claims"):
        problems.append("사실 대조 결과를 받지 못함")
    elif require_core and not (official or press or independent >= 2):
        problems.append("핵심 사실의 근거가 블로그·SNS뿐 (공식 출처, 언론 보도, 또는 서로 다른 두 곳 필요)")
    return {"ok": not problems, "problems": problems, "confirmed_by": core}


def write_verified_post(
    title: str, sources: list[dict], history: list[dict], now: datetime, mode: str, first_reported: str = ""
) -> tuple[dict | None, str]:
    """쓰기 → 사실 대조 → (문제 있으면 한 번 고쳐 쓰기 → 다시 대조). 통과하면 (후보, 요약), 아니면 (None, 사유)."""
    primary = sources[0]
    trusted = (
        (primary["kind"] == "official" and len(primary["text"]) >= MIN_CONTENT_CHARS * 2)
        or is_press(primary)
        or cites_official(primary)
    )
    if len(sources) < 2 and not trusted:
        return None, "블로그·SNS 단독 글이라 확인해 줄 다른 출처가 필요한데 찾지 못함"

    feedback = ""
    for attempt in (1, 2):
        candidate = write_post(title, sources, history, now, feedback, first_reported)
        posts = [p.strip() for p in candidate.get("threads_posts") or [] if isinstance(p, str) and p.strip()]
        if not candidate.get("postable") or not posts:
            return None, candidate.get("reason") or "포스트 없음"
        candidate["threads_posts"] = posts

        weekday_error = find_weekday_error("\n".join(posts), now.date())
        verdict = verify_post(posts, sources, now)
        problems = verdict["problems"] + ([f"요일이 달력과 안 맞음: {weekday_error}"] if weekday_error else [])
        problems += style_problems(posts)
        if not problems:
            used = [sources[i] for i in verdict["confirmed_by"]] or sources[:1]
            candidate["cross_sources"] = [{"name": s["name"], "url": s["url"]} for s in used]
            return candidate, "교차 확인: " + ", ".join(s["name"] for s in used)
        print(f"ℹ️  [{mode}] 사실 대조 {attempt}차 불통과 — " + " / ".join(problems)[:400])
        feedback = "\n".join(f"- {p}" for p in problems)
    return None, "사실 대조를 통과하지 못함"


def select_from_sources(mode: str, state: dict) -> tuple[dict, dict] | None:
    """직접 수집한 글에서 게시할 소식을 고른다. 없으면 None."""
    now = datetime.now(KST)
    history = state["posts"]
    items = collect_all()
    fresh = fresh_items(items, state, mode, now)
    print(f"ℹ️  [{mode}] 수집 {len(items)}건 중 새로 볼 글 {len(fresh)}건")
    if mode == "breaking":
        # 속보감이 아닌 글은 모델을 부르지 않고 넘긴다 (정기 슬롯에서는 다시 후보가 된다).
        mark_checked([item["url"] for item in fresh])
        fresh = [
            item for item in fresh
            if (item["kind"] in URGENT_KINDS or URGENT_RE.search(f"{item['title']} {item['summary'][:200]}"))
            and not (item["kind"] == "official" and re.search(r"\((게임|애니[^)]*)\)$", item["source"]))
        ]
        print(f"ℹ️  [{mode}] 그중 속보 후보 {len(fresh)}건" + ("" if fresh else " — 모델 호출 없이 종료"))
    if not fresh:
        return None

    picks = rank_items(fresh, history, now, items)[: MAX_VERIFY_ATTEMPTS + 2]
    if not picks:
        print(f"ℹ️  [{mode}] 모델이 올릴 만한 글이 없다고 판단했어요.")
    # 같은 발표를 다른 매체·다른 제목으로 다시 올리는 일을 막는다 (판정 실패 시엔 그냥 진행).
    verdicts = judge_duplicates(
        [{"topic": fresh[p["index"]]["title"], "threads_posts": [fresh[p["index"]]["summary"]]} for p in picks],
        recent_history(history, now.date()),
    ) or {}
    for number, same_as in verdicts.items():
        if 0 <= number < len(picks):
            print(f"ℹ️  [{mode}] 중복 판정으로 제외 — {fresh[picks[number]['index']]['title'][:50]} · 이미 게시: {same_as[:50]}")
    picks = [p for number, p in enumerate(picks) if number not in verdicts][:MAX_VERIFY_ATTEMPTS]

    for pick in picks:
        item = fresh[pick["index"]]
        label = f"{item['source']} · {item['title'][:50]}"
        too_old = item["published"] and (
            (now.date() - item["published"].astimezone(KST).date()).days > 1
            if item["dated_by_day"] else now - item["published"] > timedelta(hours=BREAKING_MAX_AGE_HOURS)
        )
        if mode == "breaking" and too_old:
            print(f"ℹ️  [{mode}] 올라온 지 오래돼 속보가 아님, 정기 슬롯으로 넘김 — {label}")
            continue
        if mode == "breaking" and not pick["breaking"]:
            print(f"ℹ️  [{mode}] 속보 아님, 정기 슬롯으로 넘김 — {label}")
            continue

        page = fetch_page(item["url"])
        if page["ok"] and page["published_date"] and (now.date() - page["published_date"]).days > MAX_NEWS_AGE_DAYS:
            print(f"ℹ️  [{mode}] 탈락 — {label} · 원문 발행일이 {page['published_date']} (재노출된 옛 글)")
            continue
        # SNS 글은 로그인 벽 때문에 원문 페이지를 못 여니 수집 때 받은 본문(글 전문/캡션)을 쓴다.
        social = item["kind"] in SOCIAL_KINDS
        if item["kind"] == "official":
            content = official_content(item, page)
        elif social or not (page["ok"] and len(page["text"]) >= 300):
            content = item["summary"]
        else:
            content = article_slice(page["text"], item["title"], 5000)
        if len(content) < MIN_CONTENT_CHARS and item["kind"] == "official":
            # 공식 공지는 본문이 이미지라 글자가 거의 없다. 제목·작성일만 공식 근거로 쓰고 내용은 언론 보도로 채운다.
            content = f"(공식 공지 — 본문은 이미지라 텍스트 없음) 제목: {item['title']} / 게시일: {item['published']:%Y-%m-%d}"
        elif len(content) < MIN_CONTENT_CHARS:
            print(f"ℹ️  [{mode}] 탈락 — {label} · 원문 본문을 확보하지 못함")
            continue

        published = item["published"].astimezone(KST) if item["published"] else None
        stamp = f"{published:%Y-%m-%d}" + ("" if item["dated_by_day"] else f" {published:%H:%M}") if published else ""
        primary = make_source(item["source"], item["url"], item["kind"], content, stamp)
        cross, first_reported = find_cross_sources(primary, item["title"], pick["query"], items, now)
        sources = [primary] + cross
        if first_reported and item["published"] and first_reported < f"{item['published'].astimezone(KST) - timedelta(days=2):%Y-%m-%d}":
            print(f"ℹ️  [{mode}] 참고 — 같은 소식이 {first_reported}에 처음 보도됨, 새로 생긴 일만 다루게 합니다")
        else:
            first_reported = ""
        candidate, note = write_verified_post(item["title"], sources, history, now, mode, first_reported)
        if not candidate:
            print(f"ℹ️  [{mode}] 탈락 — {label} · {note}")
            continue
        candidate.update(source_url=item["url"], source_name=item["source"])

        problem = ""
        if problem:
            print(f"ℹ️  [{mode}] 탈락 — {label} · {problem}")
            continue

        print(f"ℹ️  [{mode}] 선정 — {label} · {item_age_label(item, now)} · {note}")
        page["image_url"] = page["image_url"] or item["image"]
        base_date = (item["published"].astimezone(KST) if item["published"] else now).date().isoformat()
        return candidate, {"posts": candidate["threads_posts"], "page": page, "base_date": base_date}
    return None


def select_from_web_search(mode: str, history: list[dict]) -> tuple[dict, dict] | None:
    """웹 검색 후보 중 날짜·중복 검증을 통과한 것을, 다른 출처와 대조해 다시 써서 돌려준다."""
    now = datetime.now(KST)
    candidates = generate(mode, history)
    selected = select_candidate(candidates, history, mode)
    if not selected:
        print(f"ℹ️  [{mode}] 웹 검색 후보 {len(candidates)}개 중 최신성·중복 검증을 통과한 소식이 없어요.")
        return None
    data, verified = selected
    title = data.get("topic", "")
    kind = "official" if host_of(data.get("source_url", "")).endswith(OFFICIAL_HOSTS) else "news"
    primary = make_source(
        data.get("source_name", ""), data.get("source_url", ""), kind, article_slice(verified["page"]["text"], title, 5000)
    )
    cross, first_reported = find_cross_sources(primary, title, data.get("event_key", ""), [], now)
    sources = [primary] + cross
    if first_reported and first_reported >= f"{now - timedelta(days=3):%Y-%m-%d}":
        first_reported = ""
    candidate, note = write_verified_post(title, sources, history, now, mode, first_reported)
    if not candidate:
        print(f"ℹ️  [{mode}] 탈락 — {title[:50]} · {note}")
        return None
    print(f"ℹ️  [{mode}] 선정(웹 검색) — {title[:50]} · {note}")
    candidate.update(source_url=data.get("source_url", ""), source_name=data.get("source_name", ""))
    return candidate, {**verified, "posts": candidate["threads_posts"]}


BUY_EXTRACT_SYSTEM_PROMPT = """\
너는 포켓몬 카드 수집가를 위한 "정가 구매 정보" 담당자야. 요즘 30주년 제품처럼 인기 제품은 품귀라 리셀가가
붙는데, 독자는 공식 가격(정가)으로 살 수 있는 기회를 알고 싶어 해. 수집된 글 목록에서 그런 기회를 뽑아줘.

뽑을 것 — 지금 또는 앞으로 정가로 살 수 있는 구체적인 기회:
- 편의점·대형마트·완구점·공식 스토어·공인 카드샵의 입고/재입고/발주/판매 시작 일정
- 포켓몬 스토어 등 공식 판매처의 추첨·응모·예약 일정
- 매장이 직접 올린 입고·판매 공지 (판매 방식, 1인 구매 제한, 오픈 시간 등)
- 재고를 확인하는 공식 방법 (예: 편의점 앱 재고 조회)

뽑지 말 것: 리셀·중고 거래가, 시세, 개봉 후기, 이미 끝난 판매·추첨, 개인의 구매 인증/질문, 판매처가
불분명한 글, 구매대행·해외직구 홍보, 공식 가격보다 비싸게 파는 판매.

같은 기회를 여러 글이 다루면 하나로 합치고 sources 에 해당 글 번호를 모두 적어.
first_party 에는 그 판매처가 직접 올린 글(매장·회사 공식 계정이나 공식 공지)의 번호를 적고, 없으면 null.
"""


def extract_buy_opportunities(items: list[dict], now: datetime) -> list[dict]:
    listing = "\n".join(
        f"[{i}] ({item['kind']} · {item['source']} · {item_age_label(item, now)}) {item['title']}"
        + (f" — {item['summary'][:300]}" if item["summary"] else "")
        for i, item in enumerate(items)
    )
    weekday = "월화수목금토일"[now.weekday()]
    user = f"""\
현재 시각: {now:%Y-%m-%d}({weekday}) {now:%H:%M} KST

수집된 글:
{listing}

정가 구매 기회를 가치가 높은 순서로 최대 {MAX_BUY_OPPORTUNITIES + 2}개 뽑아줘. 없으면 빈 배열. 다음 JSON 형식으로만 응답해:
{{"opportunities": [{{"key": "판매처+제품 한 줄 (중복 체크용, 예: 'GS25 30th CELEBRATION 확장팩 순차 입고')",
  "summary": "어디서·무엇을·언제·어떻게 한 줄", "sources": [글 번호], "first_party": 글 번호 또는 null}}]}}
"""
    opportunities = []
    for raw in ask_json(BUY_EXTRACT_SYSTEM_PROMPT, user).get("opportunities", []):
        indices = sorted({i for i in raw.get("sources") or [] if isinstance(i, int) and 0 <= i < len(items)})
        first_party = raw.get("first_party")
        if not isinstance(first_party, int) or not 0 <= first_party < len(items):
            first_party = None
        if first_party is not None and first_party not in indices:
            indices.append(first_party)
        if indices and raw.get("key"):
            opportunities.append(
                {"key": str(raw["key"]), "summary": str(raw.get("summary", "")), "sources": indices, "first_party": first_party}
            )
    return opportunities


def buy_opportunity_problem(opportunity: dict, items: list[dict], history: list[dict], today: date) -> str:
    """구매 기회 하나를 게시해도 되는지 코드로 판정한다. 문제가 없으면 빈 문자열.
    근거 조건: 공식 출처, 판매처가 직접 올린 글, 또는 서로 다른 매체 두 곳 이상."""
    used = [items[i] for i in opportunity["sources"]]
    official = any(item["kind"] == "official" for item in used)
    # 블로그·SNS는 다른 글을 확인해 주는 근거로 세지 않는다 (판매처가 직접 올린 글은 first_party 로 따로 인정).
    outlets = {re.sub(r"\s+", "", item["source"]).lower() for item in used if item["kind"] not in (*SOCIAL_KINDS, "blog")}
    if not (official or opportunity["first_party"] is not None or len(outlets) >= 2):
        return "공식 출처·판매처 직접 공지·두 곳 이상 보도 중 어느 것도 없음"
    for entry in recent_history(history, today, BUY_REPEAT_DAYS):
        for past in entry.get("buy_keys", []):
            if topic_similarity(opportunity["key"], past) >= TOPIC_SIMILARITY_THRESHOLD:
                return f"{BUY_REPEAT_DAYS}일 안에 이미 올린 구매 정보: {past[:40]}"
    return ""


BUY_WRITE_RULES = """\
아래 출처들만 근거로 "정가 구매 정보" Threads 포스트 1개를 써줘. 이번에는 웹 검색을 하지 않아.

형식:
- 첫 줄은 수집가 마음을 건드리는 훅 (예: "리셀가 말고 정가로 30주년 팩 노려볼 분들, 이번 주 기회 정리했어요 👀").
  매번 표현을 바꿔.
- 기회마다 "📍 판매처 — 언제·어떻게"를 말하듯 한두 문장으로 (가격·구매 제한은 출처에 있을 때만).
  "어디서: / 무엇: / 언제:" 같은 항목 이름은 쓰지 마.
- 끝에 가벼운 팁이나 응원 한마디("오픈런 가실 분들 따뜻하게 입고 가세요" 같은)와 "재고·일정은 매장마다
  다를 수 있어요", "출처: 매체명", 해시태그 2~4개.
- 전체 500자 이내. 리셀가·시세 이야기, 검증 과정 이야기("보도로 확인") 넣지 마.

- 독자가 바로 따라 할 수 없는 기회는 빼. "어디서"와 "언제(기간·날짜·시점)"가 출처로 확인되지 않거나,
  지금 진행 중인지·아직 남았는지 확인되지 않는 기회(예: 본문 없는 공지 제목뿐인 추첨)는 넣지 마.
  남는 기회가 하나도 없으면 postable 을 false 로 해.
- 공지 게시일·수정일 같은 내부 정보는 쓰지 마.

사실 규칙:
- 출처에 적힌 사실만 써. 날짜·시간·가격·수량·매장 이름은 출처 표기 그대로. 출처끼리 다르면 그 수치는 빼.
- 한 매장의 공지를 전체 매장 이야기로 넓히지 마. 예정을 확정처럼 쓰지 마.
- 출처에 없는 요일·날짜를 덧붙이지 마.
"""
BUY_WRITE_RULES += "\n" + WRITING_STYLE


def write_buy_post(sources: list[dict], opportunities: list[dict], now: datetime, feedback: str = "") -> dict:
    weekday = "월화수목금토일"[now.weekday()]
    retry = f"\n앞서 쓴 글이 사실 대조에서 걸렸어. 아래 문제를 모두 고쳐서 다시 써 (해당 내용은 빼는 게 원칙):\n{feedback}\n" if feedback else ""
    listed = "\n".join(f"- {o['summary']} (출처 {o['source_numbers']})" for o in opportunities)
    user = f"""\
현재 시각: {now:%Y-%m-%d}({weekday}) {now:%H:%M} KST

{BUY_WRITE_RULES}{retry}
다룰 구매 기회:
{listed}

{format_sources(sources)}

다음 JSON 형식으로만 응답해:
{{"postable": true 또는 false, "reason": "false 인 이유", "threads_posts": ["포스트 전체 텍스트"]}}
"""
    return ask_json(SYSTEM_PROMPT, user)


def select_buy_guide(state: dict) -> tuple[dict, dict] | None:
    """여러 소스에서 정가 구매 기회를 모아 검증한 뒤 한 편의 정리 글로 만든다. 없으면 None."""
    now = datetime.now(KST)
    history = state["posts"]
    items = collect_all(extra_news_queries=BUY_NEWS_QUERIES)
    cutoff = now - timedelta(hours=BUY_WINDOW_HOURS)
    social_cutoff = now - timedelta(hours=SOCIAL_WINDOW_HOURS * 2)
    pool = [
        item for item in items
        if item["published"]
        and item["published"] >= (social_cutoff if item["kind"] in SOCIAL_KINDS else cutoff)
        and BUY_RE.search(f"{item['title']} {item['summary']}")
        and not find_duplicate({"topic": "", "source_url": item["url"]}, history, now.date())
    ][:MAX_RANK_ITEMS]
    print(f"ℹ️  [buyguide] 수집 {len(items)}건 중 구매 관련 최근 글 {len(pool)}건")
    if not pool:
        return None

    accepted = []
    for opportunity in extract_buy_opportunities(pool, now):
        problem = buy_opportunity_problem(opportunity, pool, history, now.date())
        print(f"ℹ️  [buyguide] {'제외' if problem else '채택'} — {opportunity['key'][:50]}" + (f" · {problem}" if problem else ""))
        if not problem:
            accepted.append(opportunity)
        if len(accepted) >= MAX_BUY_OPPORTUNITIES:
            break
    if not accepted:
        return None

    # 채택한 기회의 근거 글을 출처 목록으로 만든다 (기사는 본문을 열고, SNS 글은 수집한 전문을 쓴다).
    sources, number_of = [], {}
    for opportunity in accepted:
        # 같은 매체의 사진 기사 여러 장, 포털 전재본은 근거를 늘려 주지 않으니 매체당 하나, 기회당 최대 3개만 쓴다.
        picked, outlets = [], set()
        for index in sorted(opportunity["sources"], key=lambda i: pool[i]["kind"] != "official"):
            outlet = re.sub(r"\s+(on\s+msn)$", "", pool[index]["source"].lower())
            if host_of(pool[index]["url"]).endswith(AGGREGATOR_HOSTS) or outlet in outlets:
                continue
            outlets.add(outlet)
            picked.append(index)
        opportunity["sources"] = picked[:3]
        for index in opportunity["sources"]:
            if index in number_of:
                continue
            item = pool[index]
            text = item["summary"]
            if item["kind"] == "official":
                text = official_content(item, fetch_page(item["url"]))[:3000]
            elif item["kind"] not in SOCIAL_KINDS:
                page = fetch_page(item["url"])
                if page["ok"] and len(page["text"]) >= 300:
                    text = article_slice(page["text"], item["title"], 2500)
            if item["kind"] == "official" and len(text) < MIN_CONTENT_CHARS:
                text = f"(공식 공지 — 본문은 이미지라 텍스트 없음) 제목: {item['title']}"
            stamp = f"{item['published'].astimezone(KST):%Y-%m-%d %H:%M}"
            number_of[index] = len(sources)
            sources.append(make_source(item["source"], item["url"], item["kind"], text, stamp))
        opportunity["source_numbers"] = [number_of[i] for i in opportunity["sources"]]

    feedback = ""
    for attempt in (1, 2):
        result = write_buy_post(sources, accepted, now, feedback)
        posts = [p.strip() for p in result.get("threads_posts") or [] if isinstance(p, str) and p.strip()]
        if not result.get("postable") or not posts:
            print(f"ℹ️  [buyguide] 작성 포기 — {result.get('reason') or '포스트 없음'}")
            return None
        weekday_error = find_weekday_error("\n".join(posts), now.date())
        verdict = verify_post(posts, sources, now, require_core=False)
        problems = verdict["problems"] + ([f"요일이 달력과 안 맞음: {weekday_error}"] if weekday_error else [])
        problems += style_problems(posts)
        if not problems:
            break
        print(f"ℹ️  [buyguide] 사실 대조 {attempt}차 불통과 — " + " / ".join(problems)[:400])
        feedback = "\n".join(f"- {p}" for p in problems)
    else:
        return None

    keys = [o["key"] for o in accepted]
    candidate = {
        "topic": "정가 구매 정보: " + " / ".join(k[:30] for k in keys),
        "event_key": "buyguide",
        "threads_posts": posts,
        "source_name": sources[0]["name"],
        "source_url": sources[0]["url"],
        "source_urls": [s["url"] for s in sources],
        "buy_keys": keys,
        "cross_sources": [{"name": s["name"], "url": s["url"]} for s in sources],
    }
    # 공식 공지의 대표 이미지는 사이트 공용 이미지인 경우가 많아 기사 이미지를 먼저 쓴다.
    image_url = ""
    for index in sorted({i for o in accepted for i in o["sources"]}, key=lambda i: pool[i]["kind"] == "official"):
        item = pool[index]
        image_url = item["image"] or (fetch_page(item["url"])["image_url"] if item["kind"] not in SOCIAL_KINDS else "")
        if image_url:
            break
    page = {"image_url": image_url}
    print(f"ℹ️  [buyguide] 선정 — 구매 정보 {len(accepted)}건: " + " / ".join(k[:30] for k in keys))
    return candidate, {"posts": posts, "page": page, "base_date": now.date().isoformat()}


def select_stock_alert(state: dict) -> tuple[dict, dict] | None:
    """브라우저 감시기(watch_browser.py)가 찾은 매장 계정의 입고 글 하나를 바로 Threads 글로 만든다.
    판매처가 직접 올린 공지라 교차 검증 대신, 원문에 적힌 내용만 썼는지 대조만 한다 (속도 우선)."""
    try:
        post = json.loads(os.environ.get("STOCK_ALERT_PAYLOAD", ""))
    except ValueError:
        print("❌ stockalert: payload 가 JSON 이 아닙니다.", file=sys.stderr)
        return None
    now = datetime.now(KST)
    duplicate = find_duplicate({"topic": "", "source_url": post.get("url", "")}, state["posts"], now.date())
    if duplicate:
        print(f"ℹ️  [stockalert] 건너뜀 — {duplicate}")
        return None

    platform = {"instagram": "인스타그램", "threads": "Threads", "x": "X"}.get(post.get("platform"), post.get("platform", ""))
    name = f"{platform} @{post.get('account', '')}"
    source = make_source(name, post.get("url", ""), "official", post.get("caption", ""), post.get("published", "")[:16])
    weekday = "월화수목금토일"[now.weekday()]
    user = f"""\
현재 시각: {now:%Y-%m-%d}({weekday}) {now:%H:%M} KST

매장(판매처) 계정이 방금 올린 글이야. 포켓몬 카드를 정가로 살 수 있는 입고·판매·추첨 공지라면, 독자가 바로
따라 할 수 있게 Threads 포스트 1개로 써줘. 웹 검색은 하지 않아.

- 첫 줄에 핵심(어디서·무엇이·언제)을 담아. 예: "CU, 오늘 오후 2시부터 포켓몬카드 30주년 확장팩 판매"
- 원문에 있는 판매처·제품·날짜·시간·구매 제한·구매 방법만 써. 없는 내용은 지어내지 마.
- "{name} 공지 기준"이라고 출처를 밝히고, "매장별 재고·일정은 다를 수 있어요" 한 줄을 넣어.
- 리셀가·시세 이야기는 넣지 마. 전체 500자 이내, 마지막 줄에 해시태그 2~4개.
{WRITING_STYLE}
- 포켓몬 카드 입고·판매 공지가 아니거나(다른 상품 홍보, 이미 끝난 판매), 언제·어디서가 없어 따라 할 수
  없으면 postable 을 false 로 해.

{format_sources([source])}

다음 JSON 형식으로만 응답해:
{{"postable": true 또는 false, "reason": "false 인 이유", "topic": "짧은 소재 요약", "threads_posts": ["포스트 전체 텍스트"]}}
"""
    result = ask_json(SYSTEM_PROMPT, user)
    posts = [p.strip() for p in result.get("threads_posts") or [] if isinstance(p, str) and p.strip()]
    if not result.get("postable") or not posts:
        print(f"ℹ️  [stockalert] 건너뜀 — {result.get('reason') or '포스트 없음'}")
        return None
    verdict = verify_post(posts, [source], now, require_core=False)
    weekday_error = find_weekday_error("\n".join(posts), now.date())
    problems = verdict["problems"] + ([f"요일이 달력과 안 맞음: {weekday_error}"] if weekday_error else [])
    if problems:
        print("ℹ️  [stockalert] 사실 대조 불통과 — " + " / ".join(problems)[:400])
        return None

    candidate = {
        "topic": result.get("topic") or f"{name} 입고 공지",
        "event_key": "stockalert",
        "threads_posts": posts,
        "source_name": name,
        "source_url": post.get("url", ""),
        "buy_keys": [result.get("topic", "")],
        "cross_sources": [{"name": name, "url": post.get("url", "")}],
    }
    print(f"ℹ️  [stockalert] 선정 — {name}")
    return candidate, {"posts": posts, "page": {"image_url": post.get("image", "")}, "base_date": now.date().isoformat()}


def write_outputs(selected: tuple[dict, dict], mode: str) -> tuple[Path, Path]:
    data, verified = selected
    posts, page = verified["posts"], verified["page"]

    REPORTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(KST).strftime("%Y%m%d-%H%M")
    out_path = REPORTS_DIR / f"{timestamp}-pokemon-threads.txt"
    out_path.write_text("\n\n===POST_SEPARATOR===\n\n".join(posts), encoding="utf-8")

    headline = posts[0].splitlines()[0]
    image_url = page["image_url"]
    image_source = "og" if image_url else ""
    if not image_url:
        # 출처에 대표 이미지가 없으면 OpenAI로 뉴스톤 이미지를 생성해서 항상 이미지를 붙인다.
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
                "event_key": data.get("event_key", ""),
                "published_date": verified["base_date"],
                "source_name": data.get("source_name", ""),
                "source_url": data.get("source_url", ""),
                "cross_sources": data.get("cross_sources", []),
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
    save_post(data, verified["base_date"], hook=headline)
    return out_path, meta_path


def minutes_since_last_post(history: list[dict]) -> float:
    try:
        last = datetime.fromisoformat(history[-1]["posted_at"])
    except (IndexError, KeyError, TypeError, ValueError):
        return float("inf")
    return (datetime.now(KST) - last).total_seconds() / 60


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "scheduled"
    if mode == "officialcheck":
        # 점검용: 공식 페이지 하나를 이미지까지 읽어서 본문으로 뭐가 나오는지 출력만 한다 (게시하지 않음).
        url = os.environ.get("STOCK_ALERT_PAYLOAD") or (sys.argv[2] if len(sys.argv) > 2 else "")
        page = fetch_page(url)
        item = {"title": page["text"][:60], "images": [], "url": url}
        print(official_content(item, page))
        return
    if mode not in ("scheduled", "breaking", "buyguide", "stockalert"):
        print(f"알 수 없는 모드: {mode} (scheduled, breaking, buyguide, stockalert)", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    selected = None
    if mode == "breaking" and minutes_since_last_post(state["posts"]) < MIN_BREAKING_GAP_MINUTES:
        print(f"ℹ️  [{mode}] 직전 게시 후 {MIN_BREAKING_GAP_MINUTES}분이 안 지나 이번 속보 체크는 건너뜁니다.")
    else:
        try:
            if mode == "stockalert":
                selected = select_stock_alert(state)
            elif mode == "buyguide":
                selected = select_buy_guide(state)
            else:
                selected = select_from_sources(mode, state)
            if not selected and mode == "scheduled":
                # 정기 슬롯은 직접 수집으로 못 채우면 웹 검색 후보(엄격 검증)로 한 번 더 시도한다.
                print(f"ℹ️  [{mode}] 직접 수집한 글로는 올릴 게 없어 웹 검색 후보를 확인합니다.")
                selected = select_from_web_search(mode, state["posts"])
        except Exception as e:  # noqa: BLE001
            print(f"❌ 생성 중 에러: {e}", file=sys.stderr)
            sys.exit(1)

    if not selected:
        print(f"ℹ️  [{mode}] 이번 실행은 올릴 소식이 없어 건너뜁니다.")
    out_path, meta_path = write_outputs(selected, mode) if selected else (None, None)

    # GitHub Actions 다음 스텝에서 파일 존재 여부를 알 수 있도록 출력
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"has_content={'true' if out_path else 'false'}\n")
            f.write(f"threads_file={out_path or ''}\n")
            f.write(f"meta_file={meta_path or ''}\n")


if __name__ == "__main__":
    main()
