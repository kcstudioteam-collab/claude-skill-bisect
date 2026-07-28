---
name: bisect
description: This skill should be used when something broke and the cause is unknown — when the user says "it worked yesterday", "which commit broke this", "this started failing", "narrow down why", "find the minimal reproduction", "which config line causes this", "which dependency upgrade broke the build", "bisect this", or presents a large failing input, config, or test that needs to be reduced to its essential cause. Provides a unified method for locating a cause by binary search over commits, inputs, config, dependencies, or environment, plus working ddmin and predicate-validation tooling.
---

# Bisect: find the cause by binary search

Every "it broke and I don't know why" problem has the same shape: a **space** of
candidates, and a **predicate** that says whether any given candidate exhibits
the bug. Finding the cause is then a search — logarithmic, not linear.

This reframe matters because the search is the easy part. Two things decide
whether the answer is correct:

1. **Choosing the right axis** to search along.
2. **Writing a predicate that is fast and deterministic.**

Get those right and the answer falls out mechanically. Get the predicate wrong
and the search returns a confident, wrong answer — silently.

## The one convention

Every predicate in this skill uses the same polarity as `git bisect run`:

```
exit 0        -> bug ABSENT   ("good")
exit non-zero -> bug PRESENT  ("bad")
```

A raw failing test command already obeys this, so it can usually be passed
through unchanged. To invert a command that has the opposite polarity, prefix it
with `!` — for example `! grep -q ERROR build.log`.

## Step 1: pick the axis

Ask what changed between working and broken. Search along that dimension:

| What changed | Axis | Tool |
| --- | --- | --- |
| Time — worked at an older commit | commits | `git bisect run` |
| A specific input triggers it | the input file | `scripts/ddmin.py` |
| A config/flag change | config lines | `scripts/ddmin.py --in-place` |
| A `npm install` / `uv lock` | dependency versions | pin and bisect, see recipes |
| Only on CI, not locally | environment | env vars, see recipes |
| Only with the full test suite | test ordering | test list, see recipes |

When more than one thing changed, bisect the cheapest axis first — it often
eliminates the others.

When the axis is unclear, prefer commits: `git log` bounds the space concretely,
and the resulting diff usually reveals which other axis actually mattered.

## Step 2: write and validate the predicate

This is where bisections go wrong. Before searching, confirm the predicate is
**stable** — a flaky predicate does not produce an error, it produces the wrong
commit with full confidence.

```bash
scripts/check-predicate.sh --runs 7 --space 4000 -- pytest -q -x tests/test_login.py
```

It runs the command repeatedly and reports `stable BAD`, `stable GOOD`, or
`FLAKY`, plus a projected cost for the whole search.

Rules for a good predicate:

- **Deterministic.** If it flaps, fix that first: pin seeds (`PYTHONHASHSEED`,
  RNG seed), remove clock/network/filesystem-ordering dependence. If the
  flakiness cannot be removed, amplify instead — run it N times and report bad
  if it *ever* fails: `for i in $(seq 10); do cmd || exit 1; done; exit 0`.
- **Fast.** Cost is multiplied by ~log₂(space): a 20-second predicate over 4000
  commits is 4 minutes; a 5-minute one is an hour. Narrow the test to the single
  failing case before starting, not after.
- **Specific.** It must detect *this* bug, not any failure. A predicate that
  reports bad on an unrelated compile error will land on the wrong commit. Grep
  for the exact error, not merely a non-zero exit.
- **Self-contained.** No dependence on state left behind by previous runs. Clean
  build artefacts and caches inside the predicate if needed.

## Step 3: run the search

### Commits

```bash
git bisect start
git bisect bad                 # current, broken
git bisect good v1.4.0         # last known-good tag or commit
git bisect run ./predicate.sh  # walks it automatically
git bisect reset               # ALWAYS -- restores the original checkout
```

Exit **125** from the predicate means "skip this commit" — use it when the
build is broken for unrelated reasons, so the commit is excluded rather than
misclassified.

### Inputs, config, or code

`scripts/ddmin.py` implements delta debugging (Zeller & Hildebrandt's ddmin).
It shrinks a failing file to a **1-minimal** core: removing any single remaining
line makes the bug disappear.

```bash
# Temp-copy mode: {} is replaced by the candidate's path.
scripts/ddmin.py --predicate 'node app.js {} 2>&1 | grep -q "TypeError"' payload.json

# In-place mode: for files that must live at a fixed path (config, source).
# The original is backed up and restored afterwards.
scripts/ddmin.py --predicate 'make build 2>&1 | grep -q ERROR' --in-place config.yaml

# Character granularity, once line-level cannot shrink further.
scripts/ddmin.py --unit char --predicate '! grep -q X {}' input.txt
```

It refuses to start unless the full input reproduces and the empty input does
not — the two checks that catch a mis-polarised predicate before it wastes an
hour.

## Step 4: confirm the answer

A bisect result is a hypothesis, not a conclusion. Confirm it:

- **Revert it.** Undo just that commit/line/version and check the bug goes away.
- **Explain it.** Read the diff and state the causal mechanism. If the diff
  looks unrelated to the symptom, suspect the predicate rather than accepting a
  surprising culprit.
- **Watch for conjunctions.** ddmin keeps multiple units when the bug needs them
  together — that result *is* the answer: the interaction is the cause.

Report the culprit, the mechanism, and how it was confirmed — not just the hash.

## When not to bisect

Bisecting is not always the cheapest route. Skip it when:

- The stack trace already names the cause — read the code instead.
- The suspect range is under ~5 candidates — just read the diffs.
- No reliable predicate can be built. Without one, the search is worthless;
  spend the time on reproducing the bug reliably first.

The break-even is roughly: bisect when `log₂(space) × predicate_time` is less
than the time to read the diff. 10,000 commits is only 14 runs — the search is
almost never the expensive part. The predicate is.

## Resources

- **`scripts/ddmin.py`** — delta debugging; shrinks a failing input to a
  1-minimal core. Line or character granularity, memoised, with safety checks.
- **`scripts/check-predicate.sh`** — runs a predicate N times, reports
  stable/flaky, and projects the cost of the full search.
- **`references/recipes.md`** — concrete recipes per axis: dependency-version
  bisect, test-ordering bisect, environment bisect, `git bisect` with broken
  builds, bisecting across merges, and amplifying flaky predicates.
