# licence-inventory

An inventory service for units with a limited lifetime: licences, accounts,
subscriptions. Django 5 + DRF. It covers the whole life of a unit: intake,
issuing to a client, renewal, a warranty window with replacement, reminders,
and an audit log.

Written as a demonstration of working with Django. Not a tutorial and not a
CRUD skeleton: a handful of concrete domain rules, each one pinned by a test,
and a handful of places where the obvious implementation is quietly wrong.

> Ukrainian version of this document: [README.uk.md](README.uk.md).

## Run it in a minute

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

`seed_demo` creates two clients, six units, three issues, one open claim and
one expired unit that `sweep_expired` picks up straight away. It also creates
a `demo` / `demo` user and **prints its token**, so the very first request
works:

```bash
curl -H "Authorization: Token <token from seed_demo output>" \
  http://localhost:8000/api/units/
```

API docs: <http://localhost:8000/api/docs/> (Swagger UI), raw schema at
`/api/schema/`.

With Docker and Postgres:

```bash
docker compose up --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_demo
```

Without Docker the database is SQLite by default, so the project starts with
no external dependencies. Set `POSTGRES_DB` in the environment or in `.env`
(template in `.env.example`) and it goes to Postgres with no code change.

## What is inside

```
inventory/
  states.py       unit states and the transition allow-list
  models.py       models, integrity constraints at the database level
  services.py     all business logic, transactions, the audit log
  permissions.py  who may do what
  exceptions.py   one translation of domain errors into status codes
  serializers.py  input and output kept apart
  views.py        a thin HTTP layer with no try/except at all
  auth_views.py   token issuance with a rate limit
  management/commands/
    send_renewal_reminders.py   idempotent reminder job for cron
    sweep_expired.py            moves expired units
    seed_demo.py                demo data and a token
tests/            99 tests
```

The logic lives in `services.py`, not in the views. That way it is reachable
from tests, from a management command and from the admin in the same way, and
it is not duplicated.

## The decisions that actually matter here

Each one is covered by a test, named next to it.

**1. A renewal counts from `max(now, current expiry)`, not from "now".**
A client renews ten days before the end, for a year. A naive `now + 365`
silently eats those ten paid days, and you learn about it from a complaint
rather than from the logs. Both branches are covered:
`test_renewal_does_not_eat_paid_days`,
`test_renewal_of_expired_counts_from_now`.

**2. Two active issues of one unit are impossible at the database level.**
The service takes `select_for_update` and checks the state. But the service
can be bypassed: a data migration, the admin, somebody else's code. So there
is a partial unique index on (unit, active issue), and
`test_database_blocks_second_active_issue_even_past_the_service` exercises the
index rather than the logic above it.
An honest caveat: on SQLite `select_for_update` does nothing, only Postgres
supports it. That is why CI runs the whole suite twice, on both backends;
otherwise the main guarantee against races would never run in CI at all.

**3. Reminders are idempotent on the key (unit, kind, expiry).**
Not on "unit" alone, otherwise a client who renewed once would never be
reminded again: `test_renewal_opens_a_new_reminder_window`.

**4. The reminder window has a lower edge, not just an upper one.**
"Expires within 14 days" is also true for a licence that died three years
ago, so the whole archive used to fall into the mailing. There is a
`grace_days` bound now: `test_long_dead_units_are_not_reminded`.

**5. A violated business rule returns 409, not 500.**
A 500 tells client code "try again" and it hammers the endpoint. A 409 says
"wrong state", and in monitoring it stands apart from real outages:
`test_double_issue_returns_409_not_500`. The same holds for `ProtectedError`:
trying to delete an object that history still references is a conflict, not a
server failure (`test_delete_unit_with_history_returns_409_not_500`).

**6. No state change bypasses the audit log, and the log names a person.**
`move_state` is the single transition point. The log is append-only, and in
the admin adding, changing and deleting entries are all disabled.
`expires_at` is not editable through the API or through the admin: the only
way is `renew`, which writes both a `Renewal` row and an audit entry
(`test_put_cannot_rewrite_expiry_behind_the_log`).
One detail matters separately: `actor` in the log is the real username from
the token. A log that records `anonymous` answers "what happened" but not
"who did it", and the second question is the one that gets asked
(`test_audit_log_records_the_real_username`).

**7. The number of database queries does not grow with the data.**
Naive reminders called `get_or_create` and wrote an event per unit: 51
queries for 10 units, about five each. On ten thousand licences that is tens
of thousands of round trips per cron run. The batched version makes 4 queries
regardless of size, pinned by a test that compares 5 units against 40:
`test_reminders_query_count_does_not_grow_with_size`. The counter discards
SAVEPOINT and RELEASE: their number depends on the backend, and CI runs both
SQLite and Postgres, so an exact number only makes sense for real queries.

**8. One payment means exactly one free replacement.**
The most expensive mistake in this project, and it surfaced only after 76
tests were already green. Scenario: issue, claim, approve. The old unit
becomes REVOKED, the issue closes, the client receives a replacement. Then,
while the warranty window is still open, a second claim is filed against that
same closed issue. Moving REVOKED to REVOKED is a no-op, it passes silently,
and the service hands out another free unit. In money: one payment, two free
replacements.
Line coverage would not have helped: every line on its own behaved correctly.
It is only visible when the scenario is walked end to end and the result is
read in money (`tests/test_fraud.py`).
Closed by three checks in `open_claim` plus a partial unique index on
(issue, open claim), the same device as in point 2: the rule lives both in
the code and in the database.

**9. The AVAILABLE state does not mean "works".**
It only means "not issued to anyone". A unit can be renewed while free, then
sit around and go stale, and its state stays AVAILABLE. Without a separate
check the client pays and receives a dead licence
(`test_expired_unit_cannot_be_sold`). No expiry at all means perpetual, not
stale, and that is under test too.

**10. Money in whole cents, datetimes always aware.**
`0.1 + 0.2 != 0.3` surfaces exactly when reconciling against a payment
provider. A naive datetime gives a silent one-hour shift twice a year, right
on the edge of a warranty window.

**11. Settings are covered by tests as well.**
`DJANGO_ENV=production` without `DJANGO_SECRET_KEY` fails at startup instead
of quietly running with a key from a public repository. In production
`check --deploy` is clean: zero warnings, not "six, we know about them".
Locally no secrets are needed, otherwise the one-minute start would be a lie.
Check it yourself:

```bash
DJANGO_ENV=production DJANGO_SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(50))") \
  python manage.py check --deploy --fail-level WARNING
```

One subtlety there: `check --deploy` exits zero even with six warnings, so
without `--fail-level WARNING` such a test would be decoration.

**12. A custom login endpoint instead of the ready-made one.**
In DRF, `ObtainAuthToken` is declared with `throttle_classes = ()`. For the
one place that is reachable without a token and that checks a password, that
is the worst possible spot for "no limit": brute force becomes free. Here it
has its own scope and its own budget (`test_login_is_rate_limited`).
A neighbouring detail that makes it easy to write a hollow test: the order of
checks in DRF is authentication, permissions, throttle. An anonymous request
to a closed endpoint therefore gets a 401 before the counter ever runs, so
the limit has to be exercised on the open login endpoint.

## About the tests

99 of them, the suite runs in 3.5 s, coverage 98%.

The only uncovered lines are in `config/settings.py` and run under
`DJANGO_ENV=production`: they are checked in `tests/test_settings.py`, but
those tests spawn a separate process and coverage does not look into child
processes. Putting a `pragma: no cover` there would hide the truth instead of
explaining it.

Two stories from this suite are worth telling, because they show how it
catches things.

Three expiry checks originally compared the result against `timezone.now()`
through `.days`. On their own they passed; on a full run they failed roughly
once in three. They were replaced with a corridor between two time readings
taken around the call. A flaky test is worse than a missing one: it teaches
you to ignore red.

The suite once took 22.4 s. The first guess was that the three subprocess
tests were to blame. The guess was wrong: `--durations` showed about 0.57 s
spent in the **setup** of nearly every test, because creating a user hashes a
password with PBKDF2 and hundreds of thousands of iterations. Hash strength
is not what these tests check, so they use the cheapest hasher, and 22.4 s
became 3.5 s.

The numbers in `test_scale.py` are exact, not "at most". If an extra query
appears, the test has to fail rather than silently let the regression through.

## Access

Everything is closed by default; the open endpoints opt in explicitly.
Forgetting to close is easier than forgetting to open.

| Who | What they can do |
|---|---|
| anonymous | only `/healthz/` and obtaining a token |
| authenticated | read and perform every action |
| staff | additionally delete |

Deletion is singled out because it is the one action history does not save:
everything else is either reversible or leaves a trace in the audit log.

`/healthz/` is open and unthrottled on purpose: the load balancer has no
token and hits it every second, and no data leaks from there. It does run
`SELECT 1` though: an endpoint that always answers "ok" keeps an instance in
rotation with a dead database behind it.

## API

| Method | Path | What it does |
|---|---|---|
| POST | `/api/auth/token/` | obtain a token with username and password |
| GET | `/api/units/?state=available` | inventory filtered by state |
| POST | `/api/units/` | register a unit |
| POST | `/api/units/{ref}/renew/` | extend the term |
| GET | `/api/units/expiring/?days=14` | whose term is running out |
| POST | `/api/issues/` | issue a unit to a client |
| POST | `/api/issues/{id}/claim/` | file a warranty claim |
| POST | `/api/claims/{id}/approve/` | approve it, issue a replacement |
| POST | `/api/claims/{id}/reject/` | reject it |
| GET | `/api/events/?unit_ref=UNIT-001` | audit log for one unit |
| GET | `/api/schema/`, `/api/docs/` | OpenAPI schema and Swagger UI |
| GET | `/healthz/` | heartbeat that checks the database |

Lists and `expiring` return the same envelope
`{count, next, previous, results}`. A different shape on two neighbouring
endpoints forces the client to keep two parsing branches, and sooner or later
one of them is forgotten.

## Unit states

```
available ──► reserved ──► issued ──► expired ──► issued (after renewal)
    │            │            │           │
    └────────────┴────────────┴───────────┴──► revoked (terminal)
```

Anything absent from `ALLOWED_TRANSITIONS` is forbidden and raises
`IllegalTransition`. The rule sits in one dictionary instead of being
scattered across if-statements.

The state is not the whole truth about a unit: `available` plus an expiry in
the past means dead stock, and it cannot be issued.

## Scheduled jobs

```cron
*/15 * * * *  python manage.py sweep_expired
0    9 * * *  python manage.py send_renewal_reminders --days 14
```

Both are idempotent, so restarting after a failure duplicates nothing and
breaks nothing. Each has a `--dry-run`.

## Environment

| Variable | Default | Why |
|---|---|---|
| `DJANGO_ENV` | `local` | `production` switches on HTTPS, HSTS, secure cookies and requires a key |
| `DJANGO_SECRET_KEY` | dev key locally | mandatory in production, otherwise startup fails |
| `DJANGO_DEBUG` | `0` | ignored in production |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | |
| `POSTGRES_DB` | unset | set it and the app uses Postgres instead of SQLite |
| `THROTTLE_ANON` | `20/min` | |
| `THROTTLE_USER` | `600/min` | |
| `THROTTLE_LOGIN` | `5/min` | separate and stricter: a password is checked there |

They are read from the environment or from `.env` in the project root.
Variables already set in the environment win over the file: otherwise a
`.env` on disk would silently override what systemd or docker compose set.

## CI

GitHub Actions on every push:

- `makemigrations --check`: migrations have not drifted from the models
- `ruff check`
- `check --deploy --fail-level WARNING` in production mode
- `pytest --cov` **twice**: on SQLite and on Postgres, coverage at least 97%

## What is deliberately absent

So as not to pretend to be more than it is:

- **Roles beyond "staff or not".** Real production needs groups and
  object-level permissions; that is a separate piece of work.
- **Actual message delivery.** `send_renewal_reminders` writes an audit event
  instead of calling an email or messenger provider. There is exactly one
  place to plug that in.
- **Stock and purchasing.** Acquisition cost is stored, margin reporting is not.
- **An async queue.** At this volume cron or a systemd timer is more honest
  than Celery with a broker.

## Licence

MIT.
