#!/usr/bin/env bash
# GitHub Actions 안에서 몇 시간 동안 돌면서 10분마다 속보를 확인하고, 정해진 시각이 되면 정기 발행·구매 정보를
# 만든다. GitHub 예약 실행(cron)은 몇 시간씩 밀리거나 빠지는 일이 잦아서, 실행 하나를 길게 유지하는 방식으로
# 바꿨다. 끝나면 워크플로가 자기 자신을 다시 실행해 이어 간다.
#
# 필요한 환경변수: OPENAI_API_KEY, GH_TOKEN, GITHUB_REPOSITORY (+ 선택 소스용 변수들)
set -uo pipefail
LOOP_SECONDS="${LOOP_SECONDS:-20400}"     # 5시간 40분 (job 제한 6시간 안에서 마무리)
INTERVAL_SECONDS="${INTERVAL_SECONDS:-600}"
SCHEDULED_HOURS="09 13 17 21"             # 한국시간 정기 발행
BUYGUIDE_HOURS="12 18"                    # 한국시간 정가 구매 정보
END=$(( $(date +%s) + LOOP_SECONDS ))

run_mode() {
  local mode="$1" out
  out=$(mktemp)
  echo "::group::$(TZ=Asia/Seoul date +'%m-%d %H:%M') $mode"
  GITHUB_OUTPUT="$out" python scripts/generate_pokemon_content.py "$mode"
  local has_content threads_file meta_file
  has_content=$(grep -m1 '^has_content=' "$out" | cut -d= -f2-)
  threads_file=$(grep -m1 '^threads_file=' "$out" | cut -d= -f2-)
  meta_file=$(grep -m1 '^meta_file=' "$out" | cut -d= -f2-)
  bash scripts/publish.sh "$mode" "${has_content:-false}" "$threads_file" "$meta_file"
  echo "::endgroup::"
}

# 이번 시각의 정기·구매정보 슬롯을 이미 돌렸는지 상태 파일의 slots 로 확인하고 기록한다.
slot_due() {
  local kind="$1" hours="$2" hour slot
  hour=$(TZ=Asia/Seoul date +%H)
  [[ " $hours " == *" $hour "* ]] || return 1
  slot="$(TZ=Asia/Seoul date +%Y-%m-%d)T$hour"
  python - "$kind" "$slot" <<'PY'
import json, sys
kind, slot = sys.argv[1:3]
path = "reports/pokemon_posted_state.json"
state = json.load(open(path, encoding="utf-8"))
slots = state.setdefault("slots", {})
if slots.get(kind) == slot:
    sys.exit(1)
slots[kind] = slot
json.dump(state, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
}

while [ "$(date +%s)" -lt "$END" ]; do
  started=$(date +%s)
  git fetch -q origin main && git reset -q --hard origin/main
  if slot_due scheduled "$SCHEDULED_HOURS"; then run_mode scheduled; fi
  if slot_due buyguide "$BUYGUIDE_HOURS"; then run_mode buyguide; fi
  run_mode breaking
  elapsed=$(( $(date +%s) - started ))
  [ "$elapsed" -lt "$INTERVAL_SECONDS" ] && sleep $(( INTERVAL_SECONDS - elapsed ))
done
echo "감시 루프 종료 — 다음 실행으로 이어집니다."
