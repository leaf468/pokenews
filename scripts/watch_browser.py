#!/usr/bin/env python3
"""
인스타그램·Threads·X 의 매장 계정과 해시태그를 실제 브라우저로 계속 열어 보면서, 포켓몬 카드 입고 글이
올라오면 바로 알리는 감시기. 이 컴퓨터에서 상시로 돌린다 (GitHub Actions 가 아니라).

인스타그램과 X 는 로그인하지 않은 브라우저에는 게시물을 보여주지 않는다. 그래서 감시기 전용 브라우저
프로필(~/.pokenews-browser)에 한 번 로그인해 두고 그 세션으로 본다. 로그인한 계정은 자동 접근으로
제한·정지될 수 있으니 본 계정 말고 감시용 계정을 쓰는 것을 권한다.

입고 글을 찾으면:
1. 이 컴퓨터에 알림을 띄운다 (가장 빠른 경로)
2. GitHub 워크플로를 stockalert 모드로 실행해 Threads 용 글을 만들고 Issue 로 남긴다
   (실행이 안 되면 원문을 그대로 Issue 로 남긴다)

사용법 (저장소 루트에서):
    .venv/bin/python scripts/watch_browser.py login   # 브라우저 창에서 인스타그램·X 로그인 (최초 1회)
    .venv/bin/python scripts/watch_browser.py once    # 한 바퀴만 확인
    caffeinate -i .venv/bin/python scripts/watch_browser.py run   # 잠자기 막고 계속 확인

감시 대상과 간격은 scripts/watchlist.json 에서 바꾼다.
"""

import json
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

KST = timezone(timedelta(hours=9))
HOME = Path.home() / ".pokenews-browser"
PROFILE_DIR = HOME / "profile"
SEEN_FILE = HOME / "seen.json"
WATCHLIST = Path(__file__).with_name("watchlist.json")
REPO = "leaf468/pokenews"
WORKFLOW = "pokemon-cards.yml"

# 편의점·마트 계정은 포켓몬 말고도 온갖 상품 글을 올리므로 두 조건을 모두 봐야 한다.
POKEMON_RE = re.compile(r"포켓몬|포켓카|pok[eé]mon|피카츄|30th\s*celebration", re.IGNORECASE)
STOCK_RE = re.compile(r"입고|재입고|발주|판매|추첨|응모|예약|선착순|오픈런|재고|입점|출시|당첨|구매\s*제한|1인")


def log(message: str) -> None:
    print(f"[{datetime.now(KST):%m-%d %H:%M:%S}] {message}", flush=True)


def load_seen() -> dict:
    try:
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_seen(seen: dict) -> None:
    cutoff = (datetime.now(KST) - timedelta(days=14)).isoformat()
    seen = {key: at for key, at in seen.items() if at >= cutoff}
    HOME.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")


def walk(node, found: list) -> None:
    """페이지가 받아 온 JSON 안에서 게시물처럼 생긴 객체를 모두 모은다."""
    if isinstance(node, dict):
        if node.get("taken_at") and node.get("code"):  # 인스타그램·Threads (같은 Meta 형식)
            found.append(("meta", node))
        elif isinstance(node.get("legacy"), dict) and node["legacy"].get("full_text") and node.get("rest_id"):  # X
            found.append(("x", node))
        for value in node.values():
            walk(value, found)
    elif isinstance(node, list):
        for value in node:
            walk(value, found)


def to_post(kind: str, node: dict, platform: str, fallback_account: str) -> dict | None:
    if kind == "meta":
        caption = node.get("caption") or {}
        text = caption.get("text", "") if isinstance(caption, dict) else ""
        account = (node.get("user") or {}).get("username") or fallback_account
        images = (node.get("image_versions2") or {}).get("candidates") or [{}]
        if platform == "threads":
            url = f"https://www.threads.com/@{account}/post/{node['code']}"
        else:
            url = f"https://www.instagram.com/p/{node['code']}/"
        published = datetime.fromtimestamp(int(node["taken_at"]), tz=timezone.utc)
        image = images[0].get("url", "")
    else:
        legacy = node["legacy"]
        text = legacy.get("full_text", "")
        account = fallback_account
        user = (((node.get("core") or {}).get("user_results") or {}).get("result") or {})
        account = (user.get("core") or {}).get("screen_name") or (user.get("legacy") or {}).get("screen_name") or account
        url = f"https://x.com/{account}/status/{node['rest_id']}"
        try:
            published = datetime.strptime(legacy["created_at"], "%a %b %d %H:%M:%S %z %Y")
        except (KeyError, ValueError):
            return None
        media = (legacy.get("entities") or {}).get("media") or [{}]
        image = media[0].get("media_url_https", "")
    if not text:
        return None
    return {"platform": platform, "account": account, "url": url, "caption": text,
            "published": published, "image": image}


def read_page(context, url: str, platform: str, account: str) -> tuple[list[dict], str]:
    """페이지를 열고, 페이지가 불러오는 JSON 과 페이지에 박힌 JSON 에서 게시물을 뽑는다.
    (게시물 목록, 문제) 를 돌려준다. 문제는 로그인 필요 등 — 없으면 빈 문자열."""
    page = context.new_page()
    found = []

    def on_response(response):
        content_type = response.headers.get("content-type", "")
        if "json" in content_type or "graphql" in response.url or "/api/" in response.url:
            try:
                walk(response.json(), found)
            except Exception:  # noqa: BLE001 — JSON 이 아닌 응답은 건너뛴다
                pass

    page.on("response", on_response)
    problem = ""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        page.mouse.wheel(0, 2500)  # 첫 화면 아래 글까지 불러오게 한 번 내린다
        page.wait_for_timeout(2500)
        if re.search(r"/accounts/login|/challenge|/login|i/flow/login", page.url):
            problem = "로그인 필요"
        for block in re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', page.content(), re.S):
            if "taken_at" in block or "full_text" in block:
                try:
                    walk(json.loads(block), found)
                except ValueError:
                    continue
    except PlaywrightTimeout:
        problem = "페이지 로딩 시간 초과"
    finally:
        page.close()

    if not found and not problem and platform in ("instagram", "x"):
        # 로그인이 풀리면 화면은 열리는데 게시물 데이터만 안 내려온다.
        problem = "게시물을 읽지 못함 (로그인 필요)"
    posts, urls = [], set()
    for kind, node in found:
        post = to_post(kind, node, platform, account)
        if post and post["url"] not in urls:
            urls.add(post["url"])
            posts.append(post)
    return posts, problem


def targets(watch: dict) -> list[tuple[str, str, str]]:
    """(플랫폼, 이름, 주소) 목록."""
    result = []
    for account in watch.get("instagram_accounts", []):
        result.append(("instagram", account, f"https://www.instagram.com/{account}/"))
    for tag in watch.get("instagram_hashtags", []):
        result.append(("instagram", f"#{tag}", f"https://www.instagram.com/explore/tags/{tag}/"))
    for account in watch.get("threads_accounts", []):
        result.append(("threads", account, f"https://www.threads.com/@{account}"))
    for account in watch.get("x_accounts", []):
        result.append(("x", account, f"https://x.com/{account}"))
    return result


def notify(title: str, message: str) -> None:
    """맥 알림 센터에 알림을 띄운다."""
    script = f"display notification {json.dumps(message[:200])} with title {json.dumps(title)} sound name \"Glass\""
    subprocess.run(["osascript", "-e", script], check=False)


def dispatch(post: dict) -> None:
    """GitHub 워크플로를 stockalert 모드로 실행한다. 실패하면 원문을 그대로 Issue 로 남긴다."""
    payload = json.dumps(
        {**post, "caption": post["caption"][:1500], "published": post["published"].isoformat()}, ensure_ascii=False
    )
    run = subprocess.run(
        ["gh", "workflow", "run", WORKFLOW, "--repo", REPO, "-f", "mode=stockalert", "-f", f"payload={payload}"],
        capture_output=True, text=True,
    )
    if run.returncode == 0:
        log("   → stockalert 워크플로 실행 요청 완료")
        return
    log(f"   → 워크플로 실행 실패, 원문을 Issue 로 남깁니다: {run.stderr.strip()[:200]}")
    body = (
        f"**{post['platform']} @{post['account']}** · {post['published'].astimezone(KST):%Y-%m-%d %H:%M} KST\n\n"
        f"{post['caption']}\n\n- 원문: {post['url']}\n- (자동 작성 실패 — 원문 그대로)"
    )
    subprocess.run(
        ["gh", "issue", "create", "--repo", REPO, "--title", f"🛒 [입고 원문] @{post['account']}",
         "--body", body, "--label", "pokemon-post"],
        check=False,
    )


def check_once(context, watch: dict, seen: dict, warned: set) -> int:
    max_age = timedelta(hours=watch.get("max_age_hours", 6))
    now = datetime.now(timezone.utc)
    hits = 0
    for platform, name, url in targets(watch):
        posts, problem = read_page(context, url, platform, name)
        if problem:
            if (platform, problem) not in warned:  # 같은 경고를 매 바퀴 띄우지 않는다
                warned.add((platform, problem))
                notify("포켓몬 입고 감시기", f"{platform} {problem} — login 명령으로 다시 로그인해 주세요")
            log(f"⚠️  {platform} {name}: {problem}")
        else:
            warned = {w for w in warned if w[0] != platform}
        fresh = 0
        for post in posts:
            if post["url"] in seen:
                continue
            seen[post["url"]] = datetime.now(KST).isoformat()
            if now - post["published"] > max_age:
                continue
            fresh += 1
            if POKEMON_RE.search(post["caption"]) and STOCK_RE.search(post["caption"]):
                hits += 1
                first_line = post["caption"].strip().splitlines()[0]
                log(f"🛒 입고 글 발견 — {platform} @{post['account']}: {first_line[:60]}")
                notify(f"포켓몬 입고: @{post['account']}", first_line)
                dispatch(post)
        log(f"   {platform:9} {name:24} 게시물 {len(posts):3}개 · 새 글 {fresh}개")
        time.sleep(random.uniform(2, 5))  # 연달아 열지 않도록 잠깐 쉰다
    save_seen(seen)
    return hits


def open_context(playwright, headless: bool):
    return playwright.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        headless=headless,
        locale="ko-KR",
        timezone_id="Asia/Seoul",
        viewport={"width": 1280, "height": 900},
    )


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "run"
    HOME.mkdir(parents=True, exist_ok=True)
    watch = json.loads(WATCHLIST.read_text(encoding="utf-8"))

    with sync_playwright() as playwright:
        if command == "login":
            context = open_context(playwright, headless=False)
            page = context.new_page()
            page.goto("https://www.instagram.com/accounts/login/")
            context.new_page().goto("https://x.com/i/flow/login")
            print("열린 브라우저 창에서 인스타그램(필요하면 X도) 로그인을 마친 뒤, 여기서 Enter 를 누르세요.")
            input()
            context.close()
            print("로그인 세션을 저장했습니다. 이제 once 또는 run 으로 실행하세요.")
            return

        context = open_context(playwright, headless=True)
        seen, warned = load_seen(), set()
        first_round = not seen
        if command == "once":
            check_once(context, watch, seen, warned)
        elif command == "run":
            if first_round:
                log("처음 실행 — 지금 보이는 글은 기록만 하고, 이후 새로 올라오는 글부터 알립니다.")
            while True:
                if first_round:
                    # 처음 한 바퀴는 이미 올라와 있던 글로 알림이 쏟아지지 않게 기록만 한다.
                    for platform, name, url in targets(watch):
                        for post in read_page(context, url, platform, name)[0]:
                            seen[post["url"]] = datetime.now(KST).isoformat()
                    save_seen(seen)
                    first_round = False
                else:
                    check_once(context, watch, seen, warned)
                wait = watch.get("interval_minutes", 3) * 60 * random.uniform(0.85, 1.15)
                log(f"다음 확인까지 {wait / 60:.1f}분")
                time.sleep(wait)
        else:
            print(__doc__)
        context.close()


if __name__ == "__main__":
    main()
