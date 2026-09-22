# licence-inventory

An inventory service for units with a limited lifetime: licences, accounts,
subscriptions. Django 5 + DRF. It covers the life of a unit: intake, issuing
to a client, renewal, a warranty window with replacement, reminders, and an
audit log.

## Run it

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

`seed_demo` fills the database and creates a `demo` / `demo` user, printing
its token, so the first request works:

```bash
curl -H "Authorization: Token <token from seed_demo output>" \
  http://localhost:8000/api/units/
```

API docs at `/api/docs/`, raw schema at `/api/schema/`.

On Postgres, through the stack CI builds on every push:

```bash
docker compose up --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_demo
```

Without Docker the database is SQLite, so the project starts with no
external dependencies. Set `POSTGRES_DB` (environment or `.env`, template in
`.env.example`) and it uses Postgres with no code change. All settings are
documented in `.env.example`.

## Layout

```
inventory/
  states.py       unit states and the transition allow-list
  models.py       models and database-level constraints
  services.py     business logic, transactions, the audit log
  permissions.py  who may do what
  exceptions.py   domain errors translated into status codes, once
  serializers.py  input and output kept apart
  views.py        a thin HTTP layer with no try/except
  auth_views.py   token issuance with a rate limit
  management/commands/   reminders, expiry sweep, demo data
tests/            113 tests, 3 of them Postgres-only
```

Business logic lives in `services.py`, not in the views, so it is reachable
from tests, from a management command and from the admin the same way.

## The decisions worth reading

Each is covered by a test, named next to it.

**1. A renewal counts from `max(now, current expiry)`, not from "now".**
A client who renews ten days early would otherwise lose those ten paid days,
and you would hear about it from a complaint rather than from the logs:
`test_renewal_does_not_eat_paid_days`.

**2. Invariants live in the code and in the database.**
The service takes `select_for_update` and checks state; a partial unique
index enforces the same rule for anything that bypasses the service, such as
a data migration or the admin.
`test_database_blocks_second_active_issue_even_past_the_service` exercises
the index rather than the logic above it.

**3. One payment buys exactly one free replacement per issue.**
The most expensive mistake in this project, and it took three passes to
close properly. First, a second claim could be filed against a closed issue:
REVOKED to REVOKED is a no-op, so the service handed out another free unit.
Then, after that was fixed, two reviewers independently found the same hole
still open under concurrency: `approve_claim` read the claim without a lock,
so two simultaneous approvals both passed the check and both issued a
replacement. No index catches that, because the two replacements are
different units.
Closed by three checks in `open_claim`, a lock on the claim row, and a
partial unique index on (issue, open claim). Proven by
`tests/test_fraud.py` and `tests/test_concurrency.py`.
Scope of the claim, precisely: one approved replacement per issue. A
replacement is itself an issue with a fresh warranty, so a defective
replacement can be claimed in turn. That is a policy choice, not an
oversight.

**4. No state change bypasses the audit log, and the log names a person.**
`move_state` is the single transition point, the log is append-only, and
`expires_at` is not editable through the API or the admin: `renew` is the
only way in, and it writes both a `Renewal` row and an audit entry.
`actor` is the real username from the token
(`test_audit_log_records_the_real_username`).
A subtlety that cost a silent data loss: `read_only_fields` stops a value
from being *accepted*, not from being *written*. The default
`ModelSerializer.update()` saves every column from the object it loaded, so
a PATCH editing a note used to roll back a renewal that had committed a
moment earlier, with no error anywhere
(`test_patch_does_not_roll_back_a_concurrent_renewal`).

**5. A violated business rule returns 409, not 500.**
A 500 tells client code "try again" and it hammers the endpoint; a 409 says
"wrong state" and stands apart from real outages in monitoring. The same for
`ProtectedError`: deleting an object that history still references is a
conflict, not a server failure.

**6. Reminders are batched and idempotent.**
The idempotency key is (unit, kind, expiry), not the unit alone, so a client
who renewed is reminded again next time. The window has a lower edge as well
as an upper one: "expires within 14 days" is also true for a licence that
died three years ago.
On query count the honest claim is "batched, no work per row": four queries
for the batch instead of roughly five per unit. Not O(1) in SQL, because
`bulk_create` splits into backend-dependent batches, and both commands hold
their rows for the length of the transaction. `test_scale.py` pins the
counts for the sizes it measures.

**7. Money in whole cents, datetimes always aware.**
`0.1 + 0.2 != 0.3` surfaces exactly when reconciling against a payment
provider, and a naive datetime shifts by an hour twice a year, right on the
edge of a warranty window.

**8. Settings are checked, not assumed.**
`DJANGO_ENV` accepts only `local` or `production`; a typo used to fall
through to local behaviour with a development secret and no hardening.
Production without `DJANGO_SECRET_KEY` fails at startup, and
`check --deploy --fail-level WARNING` is clean. `seed_demo` refuses to run in
production at all: it creates a staff user whose password is in this file.

**9. A custom login endpoint instead of the ready-made one.**
DRF ships `ObtainAuthToken` with `throttle_classes = ()`, so the one
endpoint that checks a password had no limit at all. It now has its own
throttle scope.
The honest boundary: this is an application-level speed bump, not
authentication hardening. The counter lives in the configured cache, and the
default local-memory cache is per-process, so several gunicorn workers count
separately. A shared cache backend makes the limit global.

## Tests

113 tests. Three need row-level locking and are skipped without Postgres, so
a local run reports 110 passed and 3 skipped. About 7 s, coverage 98%.

`tests/test_concurrency.py` runs real threads on separate connections and is
skipped on SQLite, which has no row-level locking and would only measure the
thread scheduler. That is what makes the Postgres leg of CI load-bearing.

Coverage excludes `__str__` and the production-only settings branch. Both are
deliberate and both are explained in `.coveragerc`: the first is presentation
that no test in this project's history would have caught a defect in, the
second is exercised by subprocess tests that coverage cannot see.

Two findings from building this suite. Three expiry checks originally
compared against `timezone.now()` through `.days`; alone they passed, on a
full run they failed about once in three, and they were replaced with a
corridor between two readings. And the suite once took 22.4 s: the first
guess blamed the subprocess tests, `--durations` blamed PBKDF2 in test setup,
and the cheapest hasher brought it to 3.5 s.

## Access

Anonymous requests reach only `/healthz/` and the token endpoint;
authenticated users read and act; staff additionally delete. Deletion is
singled out because it is the one action history does not save.

`/healthz/` is open and unthrottled on purpose, and it runs `SELECT 1`: an
endpoint that always answers "ok" keeps an instance in rotation with a dead
database behind it.

## API

| Method | Path | What it does |
|---|---|---|
| POST | `/api/auth/token/` | obtain a token |
| GET | `/api/units/?state=available` | inventory filtered by state |
| POST | `/api/units/` | register a unit |
| POST | `/api/units/{ref}/renew/` | extend the term |
| GET | `/api/units/expiring/?days=14` | whose term is running out |
| POST | `/api/issues/` | issue a unit to a client |
| POST | `/api/issues/{id}/claim/` | file a warranty claim |
| POST | `/api/claims/{id}/approve/` | approve, issue a replacement |
| POST | `/api/claims/{id}/reject/` | reject |
| GET | `/api/events/?unit_ref=UNIT-001` | audit log for one unit |
| GET | `/api/schema/`, `/api/docs/` | OpenAPI schema and Swagger UI |
| GET | `/healthz/` | heartbeat that checks the database |

Lists and `expiring` share the envelope `{count, next, previous, results}`.
Unknown filter values are a 400, not an empty list: returning `[]` for a typo
sends the caller debugging their data instead of their spelling.

## Unit states

```
available ──► reserved ──► issued ──► expired ──► issued (after renewal)
    │            │            │           │
    └────────────┴────────────┴───────────┴──► revoked (terminal)
```

Anything absent from `ALLOWED_TRANSITIONS` raises `IllegalTransition`. The
state is not the whole truth: `available` plus a past expiry is dead stock
and cannot be issued.

## Scheduled jobs

```cron
*/15 * * * *  python manage.py sweep_expired
0    9 * * *  python manage.py send_renewal_reminders --days 14
```

Both idempotent, both with `--dry-run`.

## CI

Three jobs on every push: lint with `ruff` plus `mypy`, migrations and the
production checklist; `pytest --cov` on SQLite and on Postgres; and a build
of the `docker compose` stack that waits for `/healthz/` to answer. The last
one exists because the documented Docker path was broken for a while and
nothing noticed.

## What is deliberately absent

- Roles beyond "staff or not"; a real deployment needs groups and
  object-level permissions.
- Actual message delivery: `send_renewal_reminders` writes an audit event
  instead of calling a provider. One place to plug that in.
- A payment entity. Prices are stored in cents on issues and renewals, in a
  single unnamed currency: the system is single-currency by construction.
- Stock and purchasing, and margin reporting.
- An async queue. At this volume cron is more honest than Celery.
- Static file serving in the container: `/admin/` under gunicorn has no CSS
  until a `collectstatic` step is added.
