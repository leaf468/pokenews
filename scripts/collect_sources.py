#!/usr/bin/env python3
"""
포켓몬 카드 소식 "원문"을 여러 곳에서 직접 긁어오는 수집기.

모델의 웹 검색은 검색 색인에 올라온 뒤에야 보이기 때문에 몇 시간~며칠씩 늦고, 묵은 페이지가
섞여 나온다. 속보를 잡으려면 글이 올라오는 곳을 직접 읽어야 하므로, 여기서 발행 시각이 붙은
항목 목록을 만들고 모델은 그 목록 안에서만 고르게 한다.

소스 (키 없이 동작):
- 포켓몬코리아 공식 소식 목록
- Bing 뉴스 RSS (국내·해외 언론 보도, 발행 시각 포함)
- PokéBeach 첫 화면 (해외 TCG 속보)
- BLOG_FEEDS 에 적어 둔 블로그 RSS
- Threads 최신순 검색 결과와 THREADS_ACCOUNTS 계정의 최근 글 (비공식 경로 — fetch_threads 참고)


소스 (환경변수가 있을 때만 동작):
- X 최근 검색 (X_BEARER_TOKEN — 유료 API. X 임베드 타임라인은 키 없이 열리지만 몇 번 만에 429가
  나고 최신순도 아니라서 쓰지 않는다)
- 인스타그램 비즈니스 계정 최근 게시물 (INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_USER_ID, INSTAGRAM_ACCOUNTS)

단독 실행하면 수집 결과를 표로 찍는다: python scripts/collect_sources.py
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import parse_qs, urljoin, urlsplit

import requests

KST = timezone(timedelta(hours=9))
TIMEOUT = 12
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

NEWS_QUERIES_KO = ["포켓몬카드", "포켓몬 카드 게임", "포켓몬 TCG"]
NEWS_QUERIES_EN = ['"Pokemon TCG"']
SOCIAL_QUERIES = ["포켓몬카드", "포켓몬 카드 입고"]
# 단어를 여러 개 붙인 검색어는 결과가 0건으로 내려와서 짧은 검색어만 쓴다.
THREADS_QUERIES = ["포켓몬카드", "포켓몬 카드", "포켓몬TCG"]
# 최근 글을 직접 지켜볼 Threads 계정 (공식·카드샵 등). THREADS_ACCOUNTS 환경변수(공백 구분)로 덮어쓴다.
THREADS_ACCOUNTS = []
THREADS_CRAWLER_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
# X 검색 결과에서 이 좋아요 수 미만인 글은 버린다 (잡담·홍보 계정 걸러내기).
X_MIN_LIKES = 20

# 직접 구독할 블로그 RSS (이름, 피드 주소). 티스토리는 https://<id>.tistory.com/rss,
# 네이버 블로그는 https://rss.blog.naver.com/<id>.xml 형식이다.
BLOG_FEEDS = [
    ("Pokemon Box House", "https://pokemonboxhouse.tistory.com/rss"),
]

# 지켜볼 인스타그램 계정 (비즈니스/크리에이터 계정만 조회 가능). INSTAGRAM_ACCOUNTS 환경변수로 덮어쓴다.
INSTAGRAM_ACCOUNTS = ["pokemon_korea", "pokemoncardgame_korea"]

# 뉴스·블로그 검색 결과에는 무관한 글이 섞여 오므로 제목/요약에 이 단어가 있어야 한다.
RELEVANT_RE = re.compile(r"포켓몬|pok[eé]mon|ポケモン|ポケカ", re.IGNORECASE)
EN_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def clean(text: str) -> str:
    """태그·CDATA·엔티티를 걷어낸 한 줄 텍스트."""
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text or "", flags=re.S)
    text = unescape(unescape(text))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def make_item(kind, source, title, url, published=None, summary="", image="", dated_by_day=False):
    return {
        "kind": kind,  # official / news / blog / x / threads / instagram / overseas
        "source": source,
        "title": clean(title),
        "url": (url or "").strip(),
        "published": published,  # 시간대 포함 datetime, 모르면 None
        "dated_by_day": dated_by_day,  # 날짜만 알고 시각은 모르는 항목
        "summary": clean(summary)[:1500],
        "image": (image or "").strip(),
    }


def get(url: str, **kwargs) -> requests.Response:
    resp = requests.request(
        kwargs.pop("method", "GET"), url, timeout=TIMEOUT, headers={**HEADERS, **kwargs.pop("headers", {})}, **kwargs
    )
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding
    return resp


def tag(block: str, name: str) -> str:
    match = re.search(rf"<{name}(?:\s[^>]*)?>(.*?)</{name}>", block, re.S | re.I)
    return match.group(1) if match else ""


def parse_rfc822(value: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(clean(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_iso(value: str) -> datetime | None:
    value = clean(value).replace("Z", "+00:00")
    value = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value)  # +0000 → +00:00
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def fetch_pokemonkorea() -> list[dict]:
    """포켓몬코리아 공식 소식 목록. 목록은 페이지가 아니라 /ajax/news 로 내려온다."""
    base = "https://www.pokemonkorea.co.kr"
    resp = get(
        f"{base}/ajax/news",
        method="POST",
        data={"pn": "1", "cate": "0", "sword": "", "rcode": "menu_news"},
        headers={"Referer": f"{base}/news", "X-Requested-With": "XMLHttpRequest"},
    )
    resp.encoding = "utf-8"
    items = []
    for block in re.findall(r'<li class="col-lg-3.*?</a>\s*</li>', resp.text, re.S):
        href = re.search(r'<a href="([^"]+)"', block)
        meta = tag(block, "ul")  # <li>분류</li><li>작성일</li> — 제목에도 날짜가 들어가므로 여기서만 읽는다
        date = re.search(r"(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일", meta)
        if not href or not date:
            continue
        category = clean(tag(meta, "li"))
        image = re.search(r'<img src="([^"]+)"', block)
        url = urljoin(base, unescape(href.group(1)))
        url = re.sub(r"\?cate=0&sword=$", "", url)
        items.append(
            make_item(
                "official",
                f"포켓몬코리아({category})" if category else "포켓몬코리아",
                tag(block, "h3"),
                url,
                datetime(*map(int, date.groups()), tzinfo=KST),
                image=image.group(1) if image else "",
                dated_by_day=True,
            )
        )
    return items


def fetch_bing_news(query: str, market: str) -> list[dict]:
    """Bing 뉴스 RSS 최신순. 링크가 Bing 경유 주소라 url 파라미터에서 원문 주소를 꺼낸다."""
    lang, country = market.split("-")
    resp = get(
        "https://www.bing.com/news/search",
        params={"q": query, "format": "rss", "qft": 'sortbydate="1"', "setlang": lang, "cc": country},
    )
    items = []
    for block in re.findall(r"<item>(.*?)</item>", resp.text, re.S):
        link = unescape(clean(tag(block, "link")))
        original = parse_qs(urlsplit(link).query).get("url", [link])[0]
        image = unescape(clean(tag(block, "News:Image")))
        items.append(
            make_item(
                "news" if lang == "ko" else "overseas",
                clean(tag(block, "News:Source")) or urlsplit(original).netloc,
                tag(block, "title"),
                original,
                parse_rfc822(tag(block, "pubDate")),
                tag(block, "description"),
                image,
            )
        )
    return items


def fetch_rss(name: str, feed_url: str, kind: str = "blog") -> list[dict]:
    """일반 RSS/Atom 피드."""
    text = get(feed_url).text
    items = []
    for block in re.findall(r"<(?:item|entry)[\s>](.*?)</(?:item|entry)>", text, re.S):
        link = clean(tag(block, "link"))
        if not link:  # Atom: <link href="..."/>
            href = re.search(r'<link[^>]+href="([^"]+)"', block)
            link = unescape(href.group(1)) if href else ""
        published = parse_rfc822(tag(block, "pubDate")) or parse_iso(
            tag(block, "published") or tag(block, "updated")
        )
        summary = tag(block, "description") or tag(block, "summary") or tag(block, "content")
        items.append(make_item(kind, name, tag(block, "title"), link, published, summary))
    return items


def fetch_pokebeach() -> list[dict]:
    """PokéBeach 첫 화면의 최신 기사 (RSS가 죽어 있어 화면에서 읽는다)."""
    html = get("https://www.pokebeach.com/").text
    pattern = re.compile(
        r'<a[^>]+href="(https://www\.pokebeach\.com/20\d{2}/\d{2}/[^"]+)"[^>]*>(.*?)</a>', re.S
    )
    stamp = re.compile(
        r"([A-Za-z]{3})[a-z]*\.? (\d{1,2}), (20\d{2}) at (\d{1,2}):(\d{2}) ([AP]M)", re.I
    )
    titles, dates = {}, {}
    for url, inner in pattern.findall(html):
        text = clean(inner)
        when = stamp.search(text)
        if when:
            month, day, year, hour, minute, ampm = when.groups()
            hour = int(hour) % 12 + (12 if ampm.upper() == "PM" else 0)
            # 사이트 표기 시각은 미국 서부 기준
            dates[url] = datetime(
                int(year), EN_MONTHS.index(month.lower()) + 1, int(day), hour, int(minute),
                tzinfo=timezone(timedelta(hours=-7)),
            )
        elif len(text) > 15 and url not in titles:
            titles[url] = text
    return [
        make_item("overseas", "PokéBeach", title, url, dates.get(url))
        for url, title in titles.items()
        if url in dates
    ]


def tweet_item(tweet: dict, username: str) -> dict:
    text = tweet.get("full_text") or tweet.get("text") or ""
    media = (tweet.get("entities", {}).get("media") or [{}])[0]
    return make_item(
        "x",
        f"X @{username}",
        text.strip().splitlines()[0] if text.strip() else "",
        f"https://x.com/{username}/status/{tweet.get('id_str') or tweet.get('id', '')}",
        parse_rfc822(tweet.get("created_at", "")) or parse_iso(tweet.get("created_at", "")),
        text,
        media.get("media_url_https", ""),
    )


def fetch_x_search(query: str) -> list[dict]:
    """X API v2 최근 검색 (유료). 리트윗·답글을 빼고 반응이 어느 정도 있는 글만 남긴다."""
    resp = get(
        "https://api.x.com/2/tweets/search/recent",
        params={
            "query": f"{query} -is:retweet -is:reply",
            "max_results": 50,
            "tweet.fields": "created_at,public_metrics",
            "expansions": "author_id",
            "user.fields": "username",
        },
        headers={"Authorization": f"Bearer {os.environ['X_BEARER_TOKEN']}"},
    )
    body = resp.json()
    names = {u["id"]: u["username"] for u in body.get("includes", {}).get("users", [])}
    items = []
    for tweet in body.get("data", []):
        if tweet.get("public_metrics", {}).get("like_count", 0) < X_MIN_LIKES:
            continue
        items.append(tweet_item({**tweet, "id_str": tweet["id"]}, names.get(tweet.get("author_id"), "i")))
    return items


def fetch_threads(url: str, params: dict | None = None) -> list[dict]:
    """Threads 공개 페이지(검색 결과·프로필)에 들어 있는 글을 읽는다.
    Threads는 일반 브라우저에는 로그인 없이 글을 내려주지 않고 검색엔진 크롤러에게만 글이 담긴
    페이지를 준다. 그래서 크롤러 UA로 요청한다 — Meta가 막으면 언제든 0건이 될 수 있는 비공식 경로다."""
    html = get(url, params=params, headers={"User-Agent": THREADS_CRAWLER_UA, "Accept-Language": "ko"}).text
    posts = []

    def walk(node):
        if isinstance(node, dict):
            for thread_item in node.get("thread_items") or []:
                if isinstance(thread_item, dict) and isinstance(thread_item.get("post"), dict):
                    posts.append(thread_item["post"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for block in re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', html, re.S):
        if "thread_items" in block:
            try:
                walk(json.loads(block))
            except ValueError:
                continue

    items = []
    for post in posts:
        text = ((post.get("caption") or {}).get("text") or "").strip()
        username = (post.get("user") or {}).get("username", "")
        if not text or not username or not post.get("code") or not post.get("taken_at"):
            continue
        images = (post.get("image_versions2") or {}).get("candidates") or [{}]
        items.append(
            make_item(
                "threads",
                f"Threads @{username}",
                text.splitlines()[0],
                f"https://www.threads.com/@{username}/post/{post['code']}",
                datetime.fromtimestamp(post["taken_at"], tz=timezone.utc),
                text,
                images[0].get("url", ""),
            )
        )
    return items


def fetch_instagram(username: str) -> list[dict]:
    """인스타그램 Graph API의 business_discovery 로 다른 비즈니스 계정의 최근 게시물을 읽는다.
    로그인 없는 스크래핑은 막혀 있어서, 운영자 본인의 비즈니스 계정 토큰이 있어야만 가능하다."""
    fields = (
        f"business_discovery.username({username})"
        "{media.limit(8){caption,permalink,timestamp,media_type,media_url,thumbnail_url}}"
    )
    resp = get(
        f"https://graph.facebook.com/v21.0/{os.environ['INSTAGRAM_USER_ID']}",
        params={"fields": fields, "access_token": os.environ["INSTAGRAM_ACCESS_TOKEN"]},
    )
    media = resp.json().get("business_discovery", {}).get("media", {}).get("data", [])
    items = []
    for post in media:
        caption = post.get("caption", "")
        image = post.get("thumbnail_url") if post.get("media_type") == "VIDEO" else post.get("media_url")
        items.append(
            make_item(
                "instagram",
                f"인스타그램 @{username}",
                caption.strip().splitlines()[0] if caption.strip() else "",
                post.get("permalink", ""),
                parse_iso(post.get("timestamp", "")),
                caption,
                image or "",
            )
        )
    return items


def build_jobs() -> list[tuple[str, callable]]:
    jobs = [("포켓몬코리아 공식", fetch_pokemonkorea), ("PokéBeach", fetch_pokebeach)]
    for query in NEWS_QUERIES_KO:
        jobs.append((f"Bing 뉴스 '{query}'", lambda q=query: fetch_bing_news(q, "ko-KR")))
    for query in NEWS_QUERIES_EN:
        jobs.append((f"Bing 뉴스 {query}", lambda q=query: fetch_bing_news(q, "en-US")))
    for name, feed_url in BLOG_FEEDS:
        jobs.append((f"블로그 {name}", lambda n=name, u=feed_url: fetch_rss(n, u)))
    for query in THREADS_QUERIES:
        jobs.append((
            f"Threads 검색 '{query}'",
            lambda q=query: fetch_threads("https://www.threads.com/search", {"q": q, "serp_type": "recent"}),
        ))
    for username in os.environ.get("THREADS_ACCOUNTS", "").replace(",", " ").split() or THREADS_ACCOUNTS:
        jobs.append((f"Threads @{username}", lambda u=username.lstrip("@"): fetch_threads(f"https://www.threads.com/@{u}")))
    for query in SOCIAL_QUERIES:
        if os.environ.get("X_BEARER_TOKEN"):
            jobs.append((f"X 검색 '{query}'", lambda q=query: fetch_x_search(q)))
    if os.environ.get("INSTAGRAM_ACCESS_TOKEN") and os.environ.get("INSTAGRAM_USER_ID"):
        accounts = os.environ.get("INSTAGRAM_ACCOUNTS", "").replace(",", " ").split() or INSTAGRAM_ACCOUNTS
        for username in accounts:
            jobs.append((f"인스타그램 @{username}", lambda u=username: fetch_instagram(u)))
    return jobs


def collect_all(verbose: bool = True) -> list[dict]:
    """모든 소스를 돌아 항목을 모은다. 한 소스가 죽어도 나머지는 계속 간다."""
    items, seen = [], set()
    for name, job in build_jobs():
        try:
            found = job()
        except Exception as e:  # noqa: BLE001
            print(f"⚠️  수집 실패 — {name}: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
            continue
        kept = 0
        for item in found:
            if not item["title"] or not re.match(r"https?://", item["url"]):
                continue
            if item["kind"] != "official" and not RELEVANT_RE.search(f"{item['title']} {item['summary']}"):
                continue
            key = re.sub(r"^https?://(www\.)?", "", item["url"]).rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
            kept += 1
        if verbose:
            print(f"📥 {name}: {kept}건")
    oldest = datetime(2000, 1, 1, tzinfo=timezone.utc)
    return sorted(items, key=lambda i: i["published"] or oldest, reverse=True)


if __name__ == "__main__":
    now = datetime.now(KST)
    for entry in collect_all():
        when = entry["published"]
        age = f"{(now - when).total_seconds() / 3600:5.1f}h" if when else "   ?  "
        print(f"{age} [{entry['kind']:9}] {entry['source'][:18]:18} {entry['title'][:70]}")
