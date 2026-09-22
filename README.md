# licence-inventory

An inventory service for units with a limited lifetime: licences, accounts,
subscriptions. Django 5 + DRF.

It covers the life of a unit: intake, issuing to a client, renewal with an
idempotency key, a warranty window with replacement, queued reminders with
their own delivery pipeline, and an append-only audit log.

## Run it

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt -c constraints.txt
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

On Postgres, through the same stack CI builds and starts on every push:

```bash
docker compose up --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py seed_demo
```

Without Docker the database is SQLite, so the project starts with no external
dependencies. Set `POSTGRES_DB` (environment or `.env`, template in
`.env.example`) and it uses Postgres with no code change.

## A two-minute tour

```bash
# register a unit that expires on a known date, issue it, renew it
curl -XPOST localhost:8000/api/units/ -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"ref":"LIC-1","tier":"company","expires_at":"2027-01-01T00:00:00Z"}'
curl -XPOST localhost:8000/api/issues/ -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"unit_ref":"LIC-1","client_id":1,"price_cents":35000}'
curl -XPOST localhost:8000/api/units/LIC-1/renew/ -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"period_days":365,"price_cents":20000}'

# the whole history of that unit, including who did what
curl -H "$AUTH" 'localhost:8000/api/events/?unit_ref=LIC-1'
```

## Layout

```
inventory/
  states.py       unit states and the transition allow-list
  models.py       models and database-level constraints
  services.py     business logic, transactions, the audit log
  delivery.py     notification delivery, kept out of transactions
  permissions.py  who may do what
  exceptions.py   domain errors translated into status codes, once
  serializers.py  input and output kept apart
  views.py        a thin HTTP layer with no try/except
  auth_views.py   token issuance with a rate limit
  management/commands/   reminders, delivery, expiry sweep, demo data
scripts/
  mutation_check.py      breaks each guard and checks the suite notices
tests/            132 tests, 4 of them Postgres-only
docs/
  decisions.md    why the code looks like this, with the test names
  testing.md      what the tests prove, and what a green suite does not
  production.md   the deployment path, environment and scheduled jobs
```

Business logic lives in `services.py`, not in the views, so it is reachable
from tests, from a management command and from the admin the same way.

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
Unknown filter values are a 400, not an empty list: returning nothing for a
typo sends the caller debugging their data instead of their spelling.

Anonymous requests reach only `/healthz/` and the token endpoint;
authenticated users read and act; staff additionally delete. Deletion is
singled out because it is the one action history does not save.

## Unit states

```
available ──► reserved ──► issued ──► expired ──► issued (after renewal)
    │            │            │           │
    └────────────┴────────────┴───────────┴──► revoked (terminal)
```

Anything absent from `ALLOWED_TRANSITIONS` raises `IllegalTransition`. The
state is not the whole truth: `available` plus a past expiry is dead stock
and cannot be issued.

## CI

Four jobs on every push:

- lint: `ruff`, `mypy`, migrations match the models, production checklist clean
- tests on SQLite
- tests on Postgres, plus the concurrency suite and the mutation check, which
  only mean anything where row-level locking exists
- a build of the `docker compose` stack that waits for `/healthz/` to answer

## Reading further

- [docs/decisions.md](docs/decisions.md) for the places where the obvious
  implementation is quietly wrong, each with the test that holds it
- [docs/testing.md](docs/testing.md) for what a green suite does and does not
  prove, and the mutation run that found a guard nothing was defending
- [docs/production.md](docs/production.md) for the deployment path

## Known limits

- Roles stop at "staff or not". Groups and object-level permissions are a
  separate piece of work.
- The default notification provider writes to the log. The pipeline around it
  is real, the provider is not.
- No payment entity. Prices are cents on issues and renewals, one unnamed
  currency: single-currency by construction.
- No stock, purchasing or margin reporting.
- No async queue. At this volume cron is more honest than Celery.
- One gunicorn worker, because the rate limits count per process. More
  workers need a shared cache first.
- Backups, secret rotation, metrics and alerting are deployment concerns this
  repository does not pretend to cover.

MIT.
