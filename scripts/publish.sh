#!/usr/bin/env bash
# 생성 결과를 GitHub Issue 로 남기고 상태 파일을 커밋한다.
# 사용법: scripts/publish.sh <mode> <has_content> <threads_file> <meta_file>
# 필요한 환경변수: GH_TOKEN, GITHUB_REPOSITORY
set -uo pipefail
MODE="$1"; HAS_CONTENT="$2"; THREADS_FILE="$3"; META_FILE="$4"
STATE=reports/pokemon_posted_state.json

if [ "$HAS_CONTENT" = "true" ]; then
  TOPIC=$(jq -r '.topic' "$META_FILE")
  SOURCE_NAME=$(jq -r '.source_name' "$META_FILE")
  SOURCE_URL=$(jq -r '.source_url' "$META_FILE")
  IMAGE_URL=$(jq -r '.image_url // ""' "$META_FILE")
  GENERATED_AT=$(jq -r '.generated_at' "$META_FILE")
  case "$MODE" in
    scheduled) MODE_KO="정기" ;;
    buyguide) MODE_KO="구매정보" ;;
    stockalert) MODE_KO="입고" ;;
    *) MODE_KO="속보" ;;
  esac
  BODY_FILE=$(mktemp)
  {
    if [ -n "$IMAGE_URL" ]; then echo "![관련 이미지]($IMAGE_URL)"; echo; fi
    echo "## 게시 본문"; echo
    echo '```'; cat "$THREADS_FILE"; echo '```'; echo
    echo "---"; echo
    if [ -n "$SOURCE_URL" ]; then echo "- **출처**: [$SOURCE_NAME]($SOURCE_URL)"; else echo "- **출처**: $SOURCE_NAME"; fi
    CROSS=$(jq -r '[.cross_sources[]? | "[\(.name)](\(.url))"] | join(", ")' "$META_FILE")
    if [ -n "$CROSS" ]; then echo "- **교차 확인**: $CROSS"; fi
    echo "- **모드**: $MODE"
    echo "- **생성 시각(KST)**: $GENERATED_AT"
  } > "$BODY_FILE"
  gh issue create --repo "$GITHUB_REPOSITORY" --title "🃏 [$MODE_KO] ${TOPIC:0:80}" \
    --body-file "$BODY_FILE" --label "pokemon-post" --label "mode:$MODE" || echo "⚠️ Issue 생성 실패"
fi

git config user.name "Pokemon Cards Bot"
git config user.email "pokemon-cards-bot@users.noreply.github.com"
if git diff --quiet -- "$STATE"; then
  echo "No changes to commit"
  exit 0
fi
if [ "$HAS_CONTENT" = "true" ]; then
  MESSAGE="🃏 포켓몬 카드 소식 ($MODE) - $(TZ=Asia/Seoul date +'%Y-%m-%d %H:%M KST')"
else
  MESSAGE="chore: 속보 판정 이력 갱신"
fi
# 다른 실행이 먼저 상태 파일을 고쳤을 수 있으니, 원격 최신본에 우리 기록을 합쳐서 올린다.
OURS=$(mktemp); cp "$STATE" "$OURS"
for attempt in 1 2 3 4 5; do
  git fetch -q origin main
  git reset -q --hard origin/main
  python scripts/merge_state.py "$OURS" "$STATE" "$STATE"
  git add "$STATE"
  git commit -q -m "$MESSAGE" || { echo "No changes after merge"; exit 0; }
  git push -q origin HEAD:main && { echo "✅ 상태 커밋 완료"; exit 0; }
  sleep $((attempt * 5))
done
echo "❌ 상태 커밋 실패"
