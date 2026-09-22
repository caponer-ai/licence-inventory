# What the tests actually prove

132 tests, four of which need Postgres and skip elsewhere. Coverage 98%.

## A green suite is a smaller claim than it looks

It proves the code passes its tests. It does not prove the tests would fail
if the code were wrong, and those are different statements.

`scripts/mutation_check.py` makes the second one checkable. It removes one
guard at a time, runs the tests meant to defend it, and reports whether they
turned red. Ten mutations, and CI runs the script on the Postgres leg.

The first local run is worth repeating, because it found something real:

```
  SURVIVED  claim lock removed (the concurrency blocker)
  caught    serializer writes every column again
  caught    empty update falls back to a full save
  caught    admin saves every column again
  caught    idempotency key ignored
  caught    closed issues can be claimed again
  caught    renewal counts from now instead of the current expiry
  caught    expired stock can be sold
  caught    reminders lose their idempotency key
  caught    a failed delivery is reported as sent
```

Nine caught, one survived, and the survivor was the most important guard in
the project. The cause was not a missing test: on SQLite the concurrency
tests skip themselves, and a skipped test is a green test. The script now
says that instead of reporting a false clean bill, and the check runs on
Postgres in CI where those tests actually execute.

## Concurrency tests that cannot pass by accident

`tests/test_concurrency.py` starts real threads on separate connections and
holds them at a barrier until all are ready, so the dangerous interleaving is
forced rather than hoped for. Starting threads one after another and trusting
the scheduler is how a concurrency test quietly becomes a sequential one that
passes for the wrong reason.

Each test asserts the specific expected error, not merely that something
failed. A deadlock and a constraint violation would both satisfy "the second
request did not succeed" while meaning something else entirely.

They skip without Postgres. SQLite has no row-level locking, so the same test
there measures the thread scheduler. That is what makes the Postgres leg of
CI load-bearing rather than decorative.

## Exact query counts

The numbers in `test_scale.py` are exact, not upper bounds. They earned it:
when queued notifications were added the count moved from four to five and
the test refused to pass until somebody looked at why.

Transaction control is filtered out of the count, because the number of
SAVEPOINT statements depends on the backend and CI runs two of them.

## Coverage excludes two things, on purpose

`__str__`, because a test for it catches a string edit and nothing else: not
one defect in this project's history would have been found that way. And the
production-only settings branch, which is exercised by subprocess tests that
coverage cannot see into. Both exclusions are written down in `.coveragerc`
with the reason next to them.

## Two findings from building the suite

Three expiry checks originally compared against `timezone.now()` through
`.days`. Alone they passed; on a full run they failed about once in three.
They were replaced with a corridor between two readings taken around the
call. A flaky test is worse than a missing one: it teaches you to ignore red.

The suite once took 22.4 seconds. The first guess blamed the three subprocess
tests. `--durations` blamed password hashing in test setup instead, the
cheapest hasher brought it under five seconds, and the lesson was to measure
before optimising rather than after.
