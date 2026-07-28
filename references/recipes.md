# Recipes

Concrete procedures per axis. All predicates use the standard polarity:
**exit 0 = bug absent, non-zero = bug present.**

## Contents

- [No known-good point](#no-known-good-point)
- [Commits with broken builds](#commits-with-broken-builds)
- [Bisecting across merges](#bisecting-across-merges)
- [Dependency versions](#dependency-versions)
- [Test ordering / pollution](#test-ordering--pollution)
- [Environment differences](#environment-differences)
- [Performance regressions](#performance-regressions)
- [Amplifying a flaky predicate](#amplifying-a-flaky-predicate)
- [Structured input caveats](#structured-input-caveats)
- [What 1-minimal does and does not mean](#what-1-minimal-does-and-does-not-mean)

## No known-good point

Binary search needs bounds. Without a known-good commit, find one by doubling
backwards — an exponential search that costs only a handful of probes:

```bash
for n in 1 2 4 8 16 32 64 128 256 512 1024; do
  git checkout -q "HEAD~$n" 2>/dev/null || { echo "hit root at ~$n"; break; }
  if ./predicate.sh; then echo "GOOD at HEAD~$n"; break; fi
  echo "bad at HEAD~$n"
done
git checkout -q -   # return
```

Use the first good `HEAD~n` as the `good` bound and `HEAD~(n/2)` as a known bad
one. If the oldest commit is still bad, the bug predates the repository history
— switch axes.

## Commits with broken builds

Commits that fail for unrelated reasons must be *skipped*, not marked bad.
Exit **125** does that:

```bash
#!/usr/bin/env bash
# predicate.sh
make build >/dev/null 2>&1 || exit 125     # cannot judge -> skip
./run-test 2>&1 | grep -q 'NullPointer' && exit 1   # bug present
exit 0                                      # bug absent
```

If bisect ends with several skipped candidates it will say so; inspect those
manually.

Always finish with `git bisect reset`. A forgotten bisect leaves a detached
HEAD, and every later command operates on the wrong tree.

## Bisecting across merges

By default `git bisect` descends into merged branches, which surfaces commits
that never existed on the mainline and may not build. To find the *merge* that
introduced the bug:

```bash
git bisect start --first-parent
```

Then bisect inside that merge's branch separately if needed.

## Dependency versions

Lockfile updates change many packages at once. Bisect the packages first, then
the versions.

**Which package**: treat the lockfile diff as the input and reduce it. Produce a
list of `name@version` lines for the changed packages, then let ddmin find which
subset must be upgraded to trigger the bug — with a predicate that installs
exactly the listed pins and runs the test.

**Which version of one package**: list candidate versions and binary search
them by hand.

```bash
# npm
npm view <pkg> versions --json | python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin)))'
# python
pip index versions <pkg>
```

```bash
#!/usr/bin/env bash
# predicate.sh -- $1 is the version under test
npm install --no-save "<pkg>@$1" >/dev/null 2>&1 || exit 125
npm test >/dev/null 2>&1 && exit 0 || exit 1
```

For packages built from source, `git bisect` inside the *dependency's* repo is
usually faster than version-by-version installs, and gives the offending commit
rather than just the release.

## Test ordering / pollution

Symptom: the test passes alone but fails in the full suite. The space is the
list of tests running *before* it.

```bash
# pytest: capture the order, including the seed if randomised
pytest -q --collect-only | grep '::' > all-tests.txt
```

Reduce `all-tests.txt` with ddmin, keeping the failing test pinned last:

```bash
#!/usr/bin/env bash
# predicate.sh -- $1 is a candidate list of preceding tests
{ cat "$1"; echo "tests/test_target.py::test_the_failing_one"; } > order.txt
pytest -q $(tr '\n' ' ' < order.txt) >/dev/null 2>&1 && exit 0 || exit 1
```

```bash
scripts/ddmin.py --predicate './predicate.sh {}' all-tests.txt
```

The result is the minimal set of tests that must run first — usually one test
leaking global state, a monkeypatch that is never undone, or a shared temp file.

`pytest-randomly` and `pytest -p no:randomly` help confirm ordering sensitivity
before committing to the search.

## Environment differences

When it fails on CI but not locally, the space is the set of environment
variables. Dump both environments, diff them, and reduce the diff:

```bash
env | sort > local.env          # locally
env | sort > ci.env             # in the CI job
comm -13 local.env ci.env > only-on-ci.env
```

```bash
#!/usr/bin/env bash
# predicate.sh -- $1 holds KEY=VALUE lines to apply
set -a; . "$1"; set +a
./run-test >/dev/null 2>&1 && exit 0 || exit 1
```

```bash
scripts/ddmin.py --predicate './predicate.sh {}' only-on-ci.env
```

Also worth bisecting the same way: locale (`LC_ALL`), timezone (`TZ`), CPU count
(`nproc` via `taskset`), and container base image tags.

## Performance regressions

A continuous metric becomes a predicate by adding a threshold. Choose it well
clear of run-to-run noise — measure the noise first, then set the threshold at
several standard deviations.

```bash
#!/usr/bin/env bash
# predicate.sh -- "bad" means slower than 2.5s
ms=$( { /usr/bin/time -f '%e' ./benchmark ; } 2>&1 | tail -1 )
awk -v t="$ms" 'BEGIN { exit (t > 2.5) ? 1 : 0 }'
```

Take the best of N runs rather than the mean — the minimum is far more stable
against interference from other processes.

## Amplifying a flaky predicate

When flakiness cannot be removed, trade time for confidence. Report bad if the
bug appears in *any* of N runs:

```bash
#!/usr/bin/env bash
for _ in $(seq 20); do
  ./run-test >/dev/null 2>&1 || exit 1   # failed at least once -> bad
done
exit 0
```

This turns a p-probability failure into roughly `1-(1-p)^N`. With p = 0.2 and
N = 20, a bad candidate is detected ~99% of the time. Note the asymmetry: this
makes false-"good" unlikely but never impossible, so confirm the final answer by
reverting.

Verify the amplified predicate with `check-predicate.sh` before searching, and
budget for the increased cost: 20× the runtime, multiplied by log₂(space).

## Structured input caveats

`ddmin.py` operates on lines or characters, so it can produce syntactically
invalid intermediates — for JSON, XML, or source code, most candidates simply
fail to parse. That is not fatal (a parse failure is just "bug absent"), but it
wastes probes and can stop early at a poor minimum.

Options, in order of preference:

1. **Reformat first** so that one semantic element occupies one line
   (`jq . file.json`, `prettier --print-width 1`). Line-level ddmin then removes
   whole elements cleanly. This handles most real cases.
2. **Make the predicate tolerant**: treat parse errors as "bug absent" explicitly
   so they are never confused with the real failure.
3. **Use a syntax-aware reducer** for hard cases — `picireny` (hierarchical
   delta debugging), or `creduce`/`cvise` for C-family source.

## What 1-minimal does and does not mean

ddmin returns a **1-minimal** result: removing any *single* remaining unit stops
the reproduction. It does **not** return the globally smallest reproducing input
— finding that is exponential.

Consequences worth knowing:

- Two different runs can return different 1-minimal results. Both are valid.
- Removing *two* units at once might still reproduce even though removing either
  alone does not. Re-running ddmin on its own output occasionally shrinks
  further; it is cheap to try.
- A result containing several units usually means the bug requires their
  interaction. Do not discard the extras as noise — that interaction is the
  finding.
