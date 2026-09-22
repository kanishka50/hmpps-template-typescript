#!/usr/bin/env bash
# Dispatch the CONFIRMATORY round (methodology Decision 12).
#
# Differs from run-pilot.sh (the exploratory dispatcher) in one respect: every
# run is dispatched with required_cpu, so a job on any other processor cancels
# its own run at the first step (the "Processor gate"). Such a run measured
# nothing; it is logged as REJECTED and the same schedule entry is sent again,
# until KEEP runs per configuration have completed on the required processor.
#
#   - Runs of one subject are still serial, in a shuffled order from a seed.
#   - The four subjects may run at the same time: separate repositories, and
#     every job gets its own fresh machine.
#   - A real failure (not a rejection) is logged and counted, never hidden. The
#     entry is re-sent; after MAX_FAIL failures the script stops for a look.
#   - Resumable: re-run with the same arguments; runs already in kept-runs.csv
#     are not repeated.
#
# Output (experiment/data/confirmatory/, kept apart from exploratory data):
#   kept-runs.csv   config,run_id of every completed, accepted run
#   attempts.csv    every dispatch and its outcome (kept / rejected / failed)
#
# Usage:  bash experiment/run-confirmatory.sh KEEP SEED [CONFIGS]
#         bash experiment/run-confirmatory.sh 10 20260930
#         REQUIRED_CPU="NO-SUCH-CPU" bash experiment/run-confirmatory.sh 1 1 "C"   # test a rejection

set -uo pipefail

REPO="kanishka50/hmpps-template-typescript"
KEEP="${1:-10}"
SEED="${2:?give a seed}"
CONFIGS="${3:-A B C D E F}"
REQUIRED_CPU="${REQUIRED_CPU:-EPYC 7763}"
MAX_FAIL="${MAX_FAIL:-3}"
POLL="${POLL:-20}"

DIR="${DIR:-experiment/data/confirmatory}"   # validation: DIR=experiment/data/confirmatory-validation
KEPT="$DIR/kept-runs.csv"
LOG="$DIR/attempts.csv"
mkdir -p "$DIR"
[ -f "$KEPT" ] || echo "config,run_id" > "$KEPT"
[ -f "$LOG" ]  || echo "utc,entry,config,run_id,outcome" > "$LOG"

GH="gh"
command -v gh >/dev/null 2>&1 || GH="/c/Program Files/GitHub CLI/gh.exe"

SCHEDULE=$(
  {
    for _ in $(seq 1 "$KEEP"); do
      for c in $CONFIGS; do echo "$c"; done
    done
  } | shuf --random-source=<(yes "$SEED")
)
TOTAL=$(echo "$SCHEDULE" | wc -l)

echo "=============================================="
echo " Green DevOps - CONFIRMATORY round"
echo "=============================================="
echo "Repository : $REPO"
echo "Processor  : $REQUIRED_CPU (others rejected at the first step)"
echo "Configs    : $CONFIGS"
echo "Keep       : $KEEP completed runs per configuration"
echo "Seed       : $SEED"
echo "Schedule   : $(echo "$SCHEDULE" | tr '\n' ' ')"
echo

declare -A have seen
for c in $CONFIGS; do
  have[$c]=$(grep -c "^$c," "$KEPT" || true)
  seen[$c]=0
done

latest() {
  "$GH" run list --repo "$REPO" --workflow "$1" --limit 1 \
    --json databaseId --jq '.[0].databaseId' 2>/dev/null
}
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ),$1,$2,$3,$4" >> "$LOG"; }

fails=0
n=0
for cfg in $SCHEDULE; do
  n=$((n + 1))
  seen[$cfg]=$(( ${seen[$cfg]} + 1 ))
  [ "${seen[$cfg]}" -le "${have[$cfg]}" ] && continue     # already kept (resume)
  wf="config-$(echo "$cfg" | tr '[:upper:]' '[:lower:]').yml"
  try=0

  while :; do
    try=$((try + 1))
    prev=$(latest "$wf")
    if ! "$GH" workflow run "$wf" --repo "$REPO" -f required_cpu="$REQUIRED_CPU" >/dev/null 2>&1; then
      echo "   dispatch failed (API); retrying in 30 s"; sleep 30; continue
    fi

    run_id=""
    for _ in $(seq 1 20); do
      sleep 6
      id=$(latest "$wf")
      if [ -n "$id" ] && [ "$id" != "$prev" ]; then run_id="$id"; break; fi
    done
    [ -n "$run_id" ] || { echo "!! Could not find the run just dispatched for Config $cfg."; exit 1; }

    until [ "$("$GH" run view "$run_id" --repo "$REPO" --json status --jq '.status' 2>/dev/null)" = "completed" ]; do
      sleep "$POLL"
    done
    conclusion=$("$GH" run view "$run_id" --repo "$REPO" --json conclusion --jq '.conclusion' 2>/dev/null)

    # A rejection is a cancelled run in which some Processor gate did not pass.
    # A cancelled run whose gates all passed was cancelled by something else,
    # and counts as a failure.
    gate_cut=$("$GH" run view "$run_id" --repo "$REPO" --json jobs \
                 --jq '[.jobs[].steps[]? | select(.name == "Processor gate") | select(.conclusion != "success")] | length' 2>/dev/null)

    stamp="[$n/$TOTAL] Config $cfg try $try run $run_id"
    if [ "$conclusion" = "success" ]; then
      echo "$cfg,$run_id" >> "$KEPT"; log "$n" "$cfg" "$run_id" kept
      echo "$stamp: KEPT ($(( $(grep -c "^$cfg," "$KEPT") )) of $KEEP for $cfg)  $(date -u +%H:%M:%SZ)"
      break
    elif [ "$conclusion" = "cancelled" ] && [ "${gate_cut:-0}" -gt 0 ]; then
      log "$n" "$cfg" "$run_id" rejected
      echo "$stamp: rejected (processor), sending again"
    else
      fails=$((fails + 1)); log "$n" "$cfg" "$run_id" "failed-$conclusion"
      echo "$stamp: FAILED ($conclusion), failure $fails of $MAX_FAIL allowed"
      echo "   inspect: gh run view $run_id --repo $REPO --log-failed"
      if [ "$fails" -ge "$MAX_FAIL" ]; then
        echo "!! $MAX_FAIL failures. Stopping for investigation; re-run the same command to resume."
        exit 1
      fi
    fi
  done
done

echo
echo "=============================================="
echo " Confirmatory round complete: $KEEP per configuration."
echo " Collect with:"
echo "   RUN_LIST=$KEPT OUTDIR=$DIR bash experiment/collect-results.sh"
echo "=============================================="
