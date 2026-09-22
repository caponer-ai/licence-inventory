# Running this in production

The repository defaults to a local run. This is the other path, written out
because "it works on my machine" is not a deployment and because the Docker
image in this repository is built and started by CI on every push, so the
steps below are exercised rather than remembered.

## What has to be set

```bash
export DJANGO_ENV=production
export DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
export DJANGO_ALLOWED_HOSTS=licences.example.com
export POSTGRES_DB=licence_inventory
export POSTGRES_USER=licences
export POSTGRES_PASSWORD=...
export POSTGRES_HOST=db.internal
```

`DJANGO_ENV` accepts only `local` or `production`. Anything else, including a
typo like `prod`, refuses to start rather than falling back to local
behaviour with a development secret. Without `DJANGO_SECRET_KEY` the
production mode also refuses to start.

## Bringing it up

```bash
pip install -r requirements.txt -c constraints.txt gunicorn
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check --deploy --fail-level WARNING
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 1
```

`check --deploy --fail-level WARNING` has to exit zero. It does in CI, and if
it stops doing so the lint job fails.

Static files are served by WhiteNoise from the process itself. That is enough
for the admin and for the API docs; a separate CDN is a scaling decision, not
a correctness one.

## One worker, and why

The rate limit counters live in the configured cache, and the default is
local memory, which is per process. With three workers the configured limit
silently becomes three times what it says. Raise the worker count together
with a shared cache backend, not before.

## Scheduled jobs

```cron
*/15 * * * *  python manage.py sweep_expired
0    9 * * *  python manage.py send_renewal_reminders --days 14
*/5  * * * *  python manage.py deliver_notifications
```

All three are idempotent and safe to run concurrently with themselves.
Delivery is separate from the reminder job on purpose: deciding to notify
belongs in a database transaction, calling a provider does not.

## What must not be there

`seed_demo` creates a staff user whose password is printed in the README. It
refuses to run when `DJANGO_ENV=production`, and that refusal is covered by a
test. If a demo account exists on a real instance, something ran that should
not have.

## Logs

Every line is JSON and carries a `request_id`. The id is taken from an
inbound `X-Request-ID` when a proxy sets one and echoed back in the response,
so the id a client reports finds the server's side of the same request.

## What this repository does not cover

Backups and restore drills, secret rotation, metrics and alerting, and a
shared cache. They are deployment concerns rather than application ones, and
writing procedures for infrastructure that does not exist would be fiction.
