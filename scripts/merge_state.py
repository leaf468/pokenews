#!/usr/bin/env python3
"""두 상태 파일(게시 이력·속보 판정 이력)을 합친다. 동시에 돈 실행끼리 서로의 기록을 지우지 않게 한다.

사용법: python scripts/merge_state.py <우리 쪽 파일> <원격 쪽 파일> <결과 파일>
"""

import json
import sys


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def main() -> None:
    ours, theirs, out = sys.argv[1:4]
    a, b = load(ours), load(theirs)
    posts, seen = [], set()
    for entry in (b.get("posts") or []) + (a.get("posts") or []):
        key = (entry.get("posted_at"), entry.get("topic"))
        if key not in seen:
            seen.add(key)
            posts.append(entry)
    posts.sort(key=lambda e: e.get("posted_at", ""))
    checked = {**(b.get("checked") or {}), **(a.get("checked") or {})}
    slots = {**(b.get("slots") or {}), **(a.get("slots") or {})}
    merged = {"posts": posts[-500:], "checked": checked}
    if slots:
        merged["slots"] = slots
    with open(out, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
