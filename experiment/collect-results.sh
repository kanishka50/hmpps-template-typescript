#!/usr/bin/env bash
# Download every run's artefact and concatenate them into one dataset.
#
# Each run uploads results/measurements.csv holding one row per pipeline stage.
# Config E uploads one artefact PER JOB, all of which belong to the same run:
# the `job` column distinguishes them, and a Config E run's energy is the SUM
# over its jobs.
#
# Artefact downloads from this network have intermittently failed with TLS
# handshake timeouts to GitHub's blob storage. Each download is therefore
# retried, and a run whose artefact still cannot be fetched is recovered from
# its job LOG, into which the workflow prints the same CSV.
#
# Usage:  bash experiment/collect-results.sh

set -uo pipefail

REPO="kanishka50/hmpps-template-typescript"
OUTDIR="${OUTDIR:-experiment/data}"   # confirmatory: OUTDIR=experiment/data/confirmatory
RAWDIR="$OUTDIR/raw"
COMBINED="$OUTDIR/measurements-all.csv"
HEADER="config,job,run_id,run_number,cpu_model,stage_index,label,cpu_avg_pct,energy_j,power_avg_w,duration_s"

GH="gh"
command -v gh >/dev/null 2>&1 || GH="/c/Program Files/GitHub CLI/gh.exe"

mkdir -p "$RAWDIR"

if [ -n "${RUN_LIST:-}" ]; then
  # Confirmatory round: exactly the runs the dispatcher kept, nothing else.
  echo "Reading kept runs from $RUN_LIST..."
  RUN_IDS=$(tail -n +2 "$RUN_LIST" | cut -d, -f2)
else
echo "Listing successful runs..."
RUN_IDS=$("$GH" run list --repo "$REPO" --limit 300 \
            --json databaseId,conclusion,name \
            --jq '.[] | select(.conclusion == "success") | select(.name | startswith("Pipeline - Config")) | .databaseId')
fi

if [ -z "$RUN_IDS" ]; then
  echo "No successful runs found."
  exit 1
fi

echo "Found $(echo "$RUN_IDS" | wc -l) successful runs."

for id in $RUN_IDS; do
  if [ -f "$RAWDIR/$id.csv" ]; then
    continue                      # already collected
  fi

  got=""
  for attempt in 1 2 3; do
    tmp="$RAWDIR/tmp-$id"
    rm -rf "$tmp"; mkdir -p "$tmp"
    if "$GH" run download "$id" --repo "$REPO" --dir "$tmp" --pattern 'result-*' >/dev/null 2>&1; then
      if find "$tmp" -name measurements.csv | grep -q .; then
        find "$tmp" -name measurements.csv -exec tail -n +2 {} \; > "$RAWDIR/$id.csv"
        got="artefact"
        break
      fi
    fi
    sleep $((attempt * 5))
  done
  rm -rf "$RAWDIR/tmp-$id"

  if [ -z "$got" ]; then
    # Fallback: the workflow prints the CSV, so the log carries the same rows.
    "$GH" run view "$id" --repo "$REPO" --log 2>/dev/null \
      | sed 's/\x1b\[[0-9;]*m//g' \
      | grep -oE '[A-F],(main|lint|test|build),[0-9]+,[0-9]+,"[^"]*",[0-9]+,[a-z-]+,[0-9.]+,[0-9.]+,[0-9.]+,[0-9.]+' \
      | sort -u > "$RAWDIR/$id.csv"
    if [ -s "$RAWDIR/$id.csv" ]; then
      got="log"
    else
      rm -f "$RAWDIR/$id.csv"
      echo "  run $id: NO DATA (artefact and log both unavailable)"
      continue
    fi
  fi
  echo "  run $id: $(wc -l < "$RAWDIR/$id.csv") rows (from $got)"
done

echo "$HEADER" > "$COMBINED"
cat "$RAWDIR"/*.csv >> "$COMBINED" 2>/dev/null

rows=$(( $(wc -l < "$COMBINED") - 1 ))
runs=$(tail -n +2 "$COMBINED" | cut -d, -f3 | sort -u | wc -l)

echo
echo "=============================================="
echo " $rows measurement rows from $runs runs"
echo " -> $COMBINED"
echo "=============================================="
echo
echo "Rows per configuration:"
tail -n +2 "$COMBINED" | cut -d, -f1 | sort | uniq -c
echo
echo "Processors the runs landed on:"
tail -n +2 "$COMBINED" | cut -d, -f5 | sort | uniq -c
