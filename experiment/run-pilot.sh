#!/usr/bin/env bash
# Dispatch the six-configuration experiment, STRICTLY ONE RUN AT A TIME.
#
# Two properties this script exists to guarantee:
#   1. SERIAL EXECUTION. Concurrent runs would share GitHub's infrastructure
#      and contaminate each other's measurements, so each run is watched to
#      completion before the next is dispatched.
#   2. RANDOMISED ORDER FROM A FIXED SEED. In a fixed A,B,C,D,E order any drift
#      in GitHub's runner fleet over the collection window would align with
#      configuration and be indistinguishable from a treatment effect. The
#      order is shuffled, and the seed is recorded so the schedule reproduces.
#
# Usage:  bash experiment/run-pilot.sh [REPLICATES] [SEED] [CONFIGS] [SKIP]
#         bash experiment/run-pilot.sh 2 20260920          # 2 of each: 10 runs
#         bash experiment/run-pilot.sh 1 20260920 "C E"    # validate two configs

set -euo pipefail

REPO="kanishka50/hmpps-template-typescript"
REPLICATES="${1:-2}"
SEED="${2:-20260920}"
CONFIGS="${3:-A B C D E F}"
SKIP="${4:-0}"   # resume: skip the first SKIP entries of the seeded schedule

GH="gh"
command -v gh >/dev/null 2>&1 || GH="/c/Program Files/GitHub CLI/gh.exe"

SCHEDULE=$(
  {
    for _ in $(seq 1 "$REPLICATES"); do
      for c in $CONFIGS; do echo "$c"; done
    done
  } | shuf --random-source=<(yes "$SEED")
)

TOTAL=$(echo "$SCHEDULE" | wc -l)

echo "=============================================="
echo " Green DevOps - six-configuration experiment"
echo "=============================================="
echo "Repository : $REPO"
echo "Configs    : $CONFIGS"
echo "Replicates : $REPLICATES per configuration"
echo "Seed       : $SEED"
echo "Total runs : $TOTAL (serial)"
echo
echo "Schedule   : $(echo "$SCHEDULE" | tr '\n' ' ')"
echo

n=0
for cfg in $SCHEDULE; do
  n=$((n + 1))
  [ "$n" -le "$SKIP" ] && continue
  wf="config-$(echo "$cfg" | tr '[:upper:]' '[:lower:]').yml"

  echo "----------------------------------------------"
  echo "[$n/$TOTAL] Dispatching Config $cfg ($wf)  $(date -u +%H:%M:%SZ)"

  "$GH" workflow run "$wf" --repo "$REPO"

  sleep 8
  # Retried: a transient network error here once killed a batch after the
  # run had already been dispatched (hmpps run 35577796654).
  run_id=""
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    run_id=$("$GH" run list --repo "$REPO" --workflow "$wf" --limit 1 \
               --json databaseId --jq '.[0].databaseId' 2>/dev/null) && [ -n "$run_id" ] && break
    sleep 15
  done
  [ -n "$run_id" ] || { echo "!! Could not look up the run for Config $cfg."; exit 1; }

  echo "[$n/$TOTAL] Run $run_id started - waiting"

  # `gh run watch` is used only to BLOCK until the run finishes; its exit code
  # is not trusted, because it has been observed returning non-zero for a run
  # that GitHub recorded as successful (a multi-job Config E run). The
  # authoritative check is the conclusion reported by the API afterwards.
  #
  # `gh run watch` can also RETURN EARLY, while the run is still in progress
  # (observed on ghostfolio run 35576029304, most likely a transient API
  # error). So it is repeated until the API itself reports the run completed.
  until [ "$("$GH" run view "$run_id" --repo "$REPO" --json status --jq '.status' 2>/dev/null)" = "completed" ]; do
    "$GH" run watch "$run_id" --repo "$REPO" >/dev/null 2>&1 || sleep 30
  done

  conclusion=$("$GH" run view "$run_id" --repo "$REPO" --json conclusion --jq '.conclusion')
  if [ "$conclusion" != "success" ]; then
    echo
    echo "!! Run $run_id (Config $cfg) concluded '$conclusion'. Stopping so the"
    echo "!! cause can be investigated before more runs are collected."
    echo "!! Inspect with: gh run view $run_id --repo $REPO --log-failed"
    exit 1
  fi

  echo "[$n/$TOTAL] Config $cfg complete (run $run_id)"
done

echo
echo "=============================================="
echo " All $TOTAL runs complete."
echo " Collect them with: bash experiment/collect-results.sh"
echo "=============================================="
