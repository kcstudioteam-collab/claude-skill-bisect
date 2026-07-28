# bisect

A [Claude Code](https://claude.com/claude-code) skill for finding the cause of a
regression by binary search — over commits, inputs, config, dependencies,
environment, or test ordering.

> *"It worked yesterday."* — the space is commits.
> *"Only this 40 MB payload crashes it."* — the space is the input.
> *"Passes alone, fails in the suite."* — the space is the test order.

Same shape every time: a set of candidates, and a predicate that says whether
a candidate reproduces the bug. This skill packages that method, plus the
tooling that makes it reliable.

## Why this exists

Delta debugging has been well understood since Zeller & Hildebrandt's 1999
ddmin paper, and good standalone reducers exist (`picire`, `cvise`, `jsdelta`).
But the method is barely used in practice, because it demands exactly what
people are worst at: running the same test forty times and keeping honest
records of every verdict.

That is precisely what an agent is good at. This skill gives one the method,
the polarity conventions, and working reducers.

## What's in it

| File | Purpose |
| --- | --- |
| `SKILL.md` | The method: pick an axis, validate a predicate, search, confirm |
| `scripts/ddmin.py` | Delta debugging — shrinks a failing input to a 1-minimal core |
| `scripts/check-predicate.sh` | Proves a predicate is stable *before* you spend an hour on it |
| `references/recipes.md` | Per-axis recipes: dependencies, test ordering, env, perf, flakiness |

Zero dependencies — Python 3 standard library and bash.

## Install

Part of the **evidence** plugin by LKC:

```
/plugin marketplace add lkc-studio/claude-plugins
/plugin install evidence@lkc-plugins
```

Then start a new session and ask something like *"this test started failing sometime last week, find out which commit did it"*.

## Use the tools directly

Both scripts are standalone and useful without Claude.

Every predicate follows the `git bisect run` convention:
**exit 0 = bug absent, non-zero = bug present.**

```bash
# Shrink a 200-line config to the one line that breaks the build.
scripts/ddmin.py --predicate 'make build 2>&1 | grep -q ERROR' --in-place config.yaml

# Shrink an input file; {} is replaced with the candidate's path.
scripts/ddmin.py --predicate 'node app.js {} 2>&1 | grep -q TypeError' payload.json

# Check a predicate is not flaky, and project the cost of the search.
scripts/check-predicate.sh --runs 7 --space 4000 -- pytest -q -x tests/test_login.py
```

Real run — 200 lines down to the single culprit in 12 probes:

```
$ ddmin.py --predicate '! grep -q "timeout = -1" {}' conf.txt
  [  1]   200 units -> BAD (reproduces)
  ...
  [ 12]     1 units -> BAD (reproduces)
ddmin: 200 -> 1 lines (99.5% removed) in 12 predicate runs -> stdout
timeout = -1   # the poison
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

17 tests covering the algorithm (1-minimality, conjunctions, order preservation,
probe count) and the CLI (polarity guards, in-place restore, granularity).

## Notes

`ddmin.py` returns a **1-minimal** result: removing any single remaining unit
stops the reproduction. That is not the globally smallest input — see
`references/recipes.md` for what that implies, along with handling of structured
inputs and flaky predicates.

## License

MIT
