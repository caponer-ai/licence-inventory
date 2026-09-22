# Why the code looks like this

Each item below is a place where the obvious implementation is quietly
wrong, and each is held by a named test. Moved out of the README so the
front page stays about what this is and how to run it.

## A renewal counts from max(now, current expiry)

A client who renews ten days early would otherwise lose those ten paid days,
and you would hear about it from a complaint rather than from the logs.
`test_renewal_does_not_eat_paid_days`.

## Invariants live in the code and in the database

The service takes `select_for_update` and checks state; a partial unique
index enforces the same rule for anything that bypasses the service, such as
a data migration or the admin.
`test_database_blocks_second_active_issue_even_past_the_service` exercises
the index rather than the logic above it.

## One payment buys exactly one approved replacement per issue

The most expensive mistake here, and it took three passes to close.

First, a second claim could be filed against a closed issue: REVOKED to
REVOKED is a no-op, so the service handed out another free unit.

Then, with that fixed, two independent reviewers found the same hole still
open under concurrency: `approve_claim` read the claim without a lock, so two
simultaneous approvals both passed the check and both issued a replacement.
No index catches that, because the two replacements are different units.

Closed by three checks in `open_claim`, a lock on the claim row, and a
partial unique index on (issue, open claim). `tests/test_fraud.py` and
`tests/test_concurrency.py`.

Scope, precisely: one approved replacement per issue. A replacement is itself
an issue with a fresh warranty, so a defective replacement can be claimed in
turn. That is a policy, not an oversight.

## A retry is not a second renewal

The server renews the licence, the response is lost on the way back, the
client retries. A row lock orders operations; it cannot tell that two
separate requests mean the same thing.

`renew_unit` accepts an idempotency key and returns the first result for a
repeat. The same key with different arguments is refused rather than
answered with somebody else's result. The key is written in the same
transaction as the renewal, because a key stored afterwards is lost exactly
when it is needed. `tests/test_idempotency.py`.

## Nothing writes more columns than it was asked to

`read_only_fields` stops a value from being accepted, not from being written.
Django's `ModelSerializer.update()` and the admin's `save_model` both call
`save()` with no `update_fields`, so every column goes back to the database,
including the expiry the request read when it started. A PATCH editing a note
rolled back a renewal that committed moments earlier, with no error anywhere.

Fixed in both places. The first attempt still had the hole: it fell back to a
full save when nothing writable was touched, so an empty PATCH wiped the
renewal exactly as before. A no-op write has to be a no-op.
`test_patch_does_not_roll_back_a_concurrent_renewal`,
`test_empty_patch_writes_nothing`,
`test_admin_edit_does_not_roll_back_a_renewal`.

## Domain rules live in the service, not in a serializer

The service is the entry point for the API, for management commands and for
the admin. Bounds checked only in a serializer are not checked at all for two
of those three. A direct call with a negative price used to raise
IntegrityError and one with three million days raised OverflowError, and both
reach a client as a 500. `test_renewal_bounds_live_in_the_service`.

## A violated business rule returns 409, not 500

A 500 tells client code to try again and it hammers the endpoint; a 409 says
the state is wrong and stands apart from real outages in monitoring. The same
for `ProtectedError`: deleting an object that history still references is a
conflict, not a server failure.

## No state change bypasses the audit log, and the log names a person

`move_state` is the single transition point, the log is append-only, and
`expires_at` changes only through `renew`. `actor` is the real username from
the token: a log that records `anonymous` answers what happened but not who
did it, and the second question is the one that gets asked.

## Nothing is called sent until a provider says so

The reminder job used to write an event named `reminder.sent` while nothing
was sent anywhere. The name was worse than the missing feature: an audit log
that claims delivery cannot answer whether the client knew.

Now the job records intent in the same transaction as the reminder and
commits. `inventory/delivery.py` picks it up afterwards, because a provider
call inside a transaction holds row locks for the length of a network round
trip. Notifications move pending, sent or failed; a timeout counts as a
failed attempt and stays pending, because the honest position is that nobody
knows yet. Each notification commits on its own, so one provider failure
cannot roll back deliveries that already succeeded.

## Reminders are batched, and the window has two edges

The idempotency key is (unit, kind, expiry), not the unit alone, so a client
who renewed is reminded again next time. Expiring within 14 days is also true
of a licence that died three years ago, hence a lower bound.

On query count the honest claim is batched with no work per row: five queries
for the batch instead of roughly five per unit. Not constant in SQL, because
`bulk_create` splits into backend-dependent batches.

## Money in whole cents, datetimes always aware

Floating point money surfaces when reconciling against a payment provider,
and a naive datetime shifts by an hour twice a year, right on the edge of a
warranty window.

## Settings are checked, not assumed

`DJANGO_ENV` accepts only `local` or `production`; a typo used to fall through
to local behaviour with a development secret and no hardening. Production
without a secret key fails at startup. `seed_demo` refuses to run in
production, because it creates a staff user whose password is in the README.

## The login endpoint has its own limit

DRF ships `ObtainAuthToken` with an empty throttle list, so the one endpoint
that checks a password had no limit at all.

The boundary, stated rather than implied: this is an application-level speed
bump, not authentication hardening. The counter lives in the configured
cache, the default is per-process, and the container therefore runs a single
worker. A shared cache backend is what makes the limit global.
