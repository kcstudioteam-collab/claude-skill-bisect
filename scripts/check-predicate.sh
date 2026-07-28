#!/usr/bin/env bash
# check-predicate: prove a predicate is trustworthy before spending a bisect on it.
#
# A bisect over a flaky predicate does not fail loudly -- it returns the wrong
# answer with total confidence. This runs the predicate repeatedly, reports
# whether the verdict is stable, and projects what the full search will cost.
#
# Convention (identical to `git bisect run`):
#   exit 0        -> bug ABSENT   ("good")
#   exit non-zero -> bug PRESENT  ("bad")
#
# Usage:
#   check-predicate.sh [--runs N] [--space N] -- <command...>
#   check-predicate.sh --runs 7 --space 4000 -- pytest -q -x tests/test_login.py

set -uo pipefail

RUNS=5
SPACE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runs)  RUNS="$2"; shift 2 ;;
    --space) SPACE="$2"; shift 2 ;;
    --)      shift; break ;;
    -h|--help)
      sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) break ;;
  esac
done

if [[ $# -eq 0 ]]; then
  echo "check-predicate: no command given" >&2
  echo "usage: check-predicate.sh [--runs N] [--space N] -- <command...>" >&2
  exit 2
fi

if ! [[ "$RUNS" =~ ^[0-9]+$ ]] || [[ "$RUNS" -lt 1 ]]; then
  echo "check-predicate: --runs must be a positive integer" >&2
  exit 2
fi

printf 'Running predicate %d times: %s\n\n' "$RUNS" "$*"

codes=()
total_ms=0
for ((i = 1; i <= RUNS; i++)); do
  start=$(date +%s%N)
  "$@" >/dev/null 2>&1
  code=$?
  end=$(date +%s%N)
  ms=$(((end - start) / 1000000))
  total_ms=$((total_ms + ms))
  codes+=("$code")

  if [[ $code -eq 0 ]]; then
    verdict="good (bug absent)"
  else
    verdict="BAD  (bug present)"
  fi
  printf '  run %-3d exit=%-4d %-20s %6d ms\n' "$i" "$code" "$verdict" "$ms"
done

# Classify each run as good/bad and count.
bad=0
for code in "${codes[@]}"; do
  [[ $code -ne 0 ]] && bad=$((bad + 1))
done
good=$((RUNS - bad))
mean_ms=$((total_ms / RUNS))

printf '\n  %d good, %d bad, mean %d ms\n\n' "$good" "$bad" "$mean_ms"

if [[ $good -gt 0 && $bad -gt 0 ]]; then
  cat <<'EOF'
VERDICT: FLAKY -- do not bisect with this predicate.

The same state produced both verdicts, so a bisect would land on whichever
commit happened to flake. Fix this first:
  - pin any randomness (seed, hash seed, shuffle order)
  - remove time/network/filesystem-order dependence
  - or wrap it: run the command N times and report bad only if it EVER fails,
    e.g.  for i in $(seq 10); do cmd || exit 1; done; exit 0
EOF
  exit 1
fi

if [[ $bad -eq $RUNS ]]; then
  echo "VERDICT: stable BAD -- this state reproduces the bug."
  echo "         Use it as the 'bad' end of the search."
else
  echo "VERDICT: stable GOOD -- this state does not reproduce the bug."
  echo "         Use it as the 'good' end of the search."
fi

if [[ -n "$SPACE" ]]; then
  if ! [[ "$SPACE" =~ ^[0-9]+$ ]] || [[ "$SPACE" -lt 1 ]]; then
    echo >&2
    echo "check-predicate: --space must be a positive integer" >&2
    exit 2
  fi
  steps=0
  remaining=$SPACE
  while [[ $remaining -gt 1 ]]; do
    remaining=$((remaining / 2))
    steps=$((steps + 1))
  done
  total_s=$((steps * mean_ms / 1000))
  printf '\nProjected cost over %d candidates: ~%d runs, ~%dm %ds\n' \
    "$SPACE" "$steps" "$((total_s / 60))" "$((total_s % 60))"
  if [[ $total_s -gt 900 ]]; then
    echo "That is long. Make the predicate faster before starting --"
    echo "every second saved is multiplied by ~$steps."
  fi
fi
