#!/usr/bin/env python3
"""
생성된 포켓몬 카드 Threads 텍스트를 Meta Threads API로 게시한다.
파일 안에 ===POST_SEPARATOR=== 로 여러 포스트가 나뉘어 있으면
첫 포스트를 올리고 나머지는 그 글의 댓글(reply)로 스레드를 이어붙인다.

두 번째 인자로 meta.json 경로를 주면 그 안의 image_url을 읽어 첫 포스트에 이미지를 첨부한다
(우리 채널은 모든 소식에 이미지를 함께 올리는 걸 원칙으로 함). 이미지가 없으면 텍스트로만 올린다.

THREADS_ACCESS_TOKEN / THREADS_USER_ID 가 없으면 조용히 건너뛴다.
"""

import json
import os
import sys
import time
from pathlib import Path

import requests

THREADS_CHAR_LIMIT = 500
API_BASE = "https://graph.threads.net/v1.0"


def load_posts(file_path: str) -> list[str]:
    content = Path(file_path).read_text(encoding="utf-8")
    posts = [p.strip() for p in content.split("===POST_SEPARATOR===") if p.strip()]
    if not posts:
        raise ValueError("게시할 내용이 비어 있습니다.")
    return posts


def load_image_url(meta_path: str | None) -> str:
    """meta.json에서 image_url을 읽는다. 인자가 없거나 파일이 없으면 빈 문자열."""
    if not meta_path or not Path(meta_path).exists():
        return ""
    try:
        meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    return (meta.get("image_url") or "").strip()


def create_and_publish(
    text: str,
    access_token: str,
    user_id: str,
    reply_to_id: str | None = None,
    image_url: str = "",
) -> str:
    if len(text) > THREADS_CHAR_LIMIT:
        text = text[: THREADS_CHAR_LIMIT - 1] + "…"

    create_data = {
        "text": text,
        "access_token": access_token,
    }
    if image_url:
        create_data["media_type"] = "IMAGE"
        create_data["image_url"] = image_url
    else:
        create_data["media_type"] = "TEXT"
    if reply_to_id:
        create_data["reply_to_id"] = reply_to_id

    resp = requests.post(f"{API_BASE}/{user_id}/threads", data=create_data, timeout=30)
    resp.raise_for_status()
    creation_id = resp.json().get("id")
    if not creation_id:
        raise RuntimeError(f"스레드 생성 실패: {resp.text}")

    # Threads API는 컨테이너 생성 후 게시 전 약간의 처리 시간이 필요할 수 있음
    time.sleep(2)

    publish_resp = requests.post(
        f"{API_BASE}/{user_id}/threads_publish",
        data={"creation_id": creation_id, "access_token": access_token},
        timeout=30,
    )
    publish_resp.raise_for_status()
    published_id = publish_resp.json().get("id")
    if not published_id:
        raise RuntimeError(f"스레드 게시 실패: {publish_resp.text}")
    return published_id


def main() -> None:
    if len(sys.argv) < 2:
        print("사용법: python post_to_threads.py <threads_텍스트_파일> [meta_json_파일]")
        sys.exit(1)

    file_path = sys.argv[1]
    meta_path = sys.argv[2] if len(sys.argv) > 2 else None
    image_url = load_image_url(meta_path)
    access_token = os.getenv("THREADS_ACCESS_TOKEN")
    user_id = os.getenv("THREADS_USER_ID")

    if not access_token or not user_id:
        print("⚠️  THREADS_ACCESS_TOKEN / THREADS_USER_ID 가 없어 게시를 건너뜁니다.")
        sys.exit(0)

    if not Path(file_path).exists():
        print(f"⚠️  파일을 찾을 수 없습니다: {file_path}")
        sys.exit(0)

    try:
        posts = load_posts(file_path)
    except ValueError as e:
        print(f"⚠️  {e}")
        sys.exit(0)

    print(
        f"🧵 Threads 게시 시작 ({len(posts)}개 포스트)"
        + (f" · 이미지 첨부: {image_url}" if image_url else " · 이미지 없음(텍스트)")
    )

    try:
        first_id = create_and_publish(
            posts[0], access_token, user_id, image_url=image_url
        )
        print(f"✅ 게시 완료: {first_id}")

        last_id = first_id
        for i, post in enumerate(posts[1:], start=2):
            reply_id = create_and_publish(post, access_token, user_id, reply_to_id=last_id)
            print(f"✅ 답글 {i} 게시 완료: {reply_id}")
            last_id = reply_id

        print("🎉 Threads 업로드 완료")
    except Exception as e:  # noqa: BLE001
        print(f"❌ Threads 게시 실패: {e}", file=sys.stderr)
        # 게시 실패해도 워크플로우 전체를 실패시키지 않음 (다음 슬롯에서 재시도됨)
        sys.exit(0)


if __name__ == "__main__":
    main()
