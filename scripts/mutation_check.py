"""Break each guard on purpose and check that the suite notices.

A green suite proves that the code passes its tests. It does not prove that
the tests would fail if the code were wrong, and those are different claims.
This script makes the second one checkable: it removes one protection at a
time, runs the tests that are supposed to defend it, and reports whether they
turned red.

Run it from the repository root:

    python scripts/mutation_check.py

Every mutation is reverted afterwards, including on failure. The script
refuses to start if the working tree is dirty, so a crash can never be
confused with an uncommitted change of yours.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Mutation:
    """One deliberately broken guard and the tests that should catch it."""

    name: str
    path: str
    before: str
    after: str
    tests: str


MUTATIONS = [
    Mutation(
        name="claim lock removed (the concurrency blocker)",
        path="inventory/services.py",
        before="        WarrantyClaim.objects.select_for_update()\n"
        '        .select_related("issue__unit", "issue__client")',
        after='        WarrantyClaim.objects.select_related("issue__unit", "issue__client")',
        tests="tests/test_concurrency.py",
    ),
    Mutation(
        name="serializer writes every column again",
        path="inventory/serializers.py",
        before='        instance.save(update_fields=[*touched, "updated_at"])',
        after="        instance.save()",
        tests="tests/test_api_contract.py",
    ),
    Mutation(
        name="empty update falls back to a full save",
        path="inventory/serializers.py",
        before="        if not touched:\n            return instance",
        after="        if not touched:\n            instance.save()\n            return instance",
        tests="tests/test_api_contract.py",
    ),
    Mutation(
        name="admin saves every column again",
        path="inventory/admin.py",
        after="        obj.save()",
        before='        obj.save(update_fields=[*self.WRITABLE, "updated_at"])',
        tests="tests/test_admin_and_commands.py",
    ),
    Mutation(
        name="idempotency key ignored",
        path="inventory/services.py",
        before="        seen = IdempotencyRecord.objects.filter(key=idempotency_key).first()",
        after="        seen = None",
        tests="tests/test_idempotency.py",
    ),
    Mutation(
        name="closed issues can be claimed again",
        path="inventory/services.py",
        before="    if not issue.is_active:\n"
        '        raise IssueClosed(f"issue {issue_id} is closed, it cannot be claimed")',
        after='    if False:\n        raise IssueClosed(f"issue {issue_id} is closed, it cannot be claimed")',
        tests="tests/test_fraud.py",
    ),
    Mutation(
        name="renewal counts from now instead of the current expiry",
        path="inventory/services.py",
        before="    base = unit.expires_at if unit.expires_at and unit.expires_at > now else now",
        after="    base = now",
        tests="tests/test_renewal.py",
    ),
    Mutation(
        name="expired stock can be sold",
        path="inventory/services.py",
        before="    if unit.expires_at and unit.expires_at <= now:",
        after="    if False:",
        tests="tests/test_fraud.py",
    ),
    Mutation(
        name="reminders lose their idempotency key",
        path="inventory/services.py",
        before="    fresh = [u for u in units if (u.id, expiries[u.id]) not in already]",
        after="    fresh = list(units)",
        tests="tests/test_reminders.py",
    ),
    Mutation(
        name="a failed delivery is reported as sent",
        path="inventory/delivery.py",
        before='                    locked.save(update_fields=["attempts", "last_error", "state"])\n'
        "                    continue",
        after="                    locked.state = Notification.State.SENT\n"
        '                    locked.save(update_fields=["attempts", "last_error", "state"])\n'
        "                    continue",
        tests="tests/test_delivery.py",
    ),
]


def working_tree_is_clean() -> bool:
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
    return not out.strip()


def run_tests(selector: str) -> tuple[bool, str]:
    """Return (tests still pass, a note about why that might be meaningless).

    A suite where everything was skipped exits zero, so a mutation would be
    reported as surviving when in truth nothing was run. That distinction
    matters more than it sounds: the first run of this script reported the
    claim lock as undefended, and the real reason was that the concurrency
    tests skip themselves without Postgres.
    """
    proc = subprocess.run(
        # No -q: pytest.ini already passes one, and a second suppresses the
        # summary line this function reads. That exact mistake made the
        # skip detector below silently useless the first time it was written.
        [sys.executable, "-m", "pytest", selector, "--no-cov", "-x"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    output = proc.stdout + proc.stderr
    ran_nothing = " passed" not in output and " skipped" in output
    note = "every test was skipped here, so nothing could catch it" if ran_nothing else ""
    return proc.returncode == 0, note


def apply(mutation: Mutation) -> None:
    path = ROOT / mutation.path
    text = path.read_text(encoding="utf-8")
    if mutation.before not in text:
        raise SystemExit(f"cannot apply {mutation.name!r}: the code has moved on")
    path.write_text(text.replace(mutation.before, mutation.after, 1), encoding="utf-8")


def revert() -> None:
    subprocess.run(["git", "checkout", "--", "."], cwd=ROOT, check=True)


def main() -> int:
    if not working_tree_is_clean():
        print("working tree is dirty; commit or stash first so a revert cannot lose work")
        return 2

    print(f"running {len(MUTATIONS)} mutations\n")
    survivors = []

    for mutation in MUTATIONS:
        apply(mutation)
        try:
            still_green, note = run_tests(mutation.tests)
        finally:
            revert()

        if still_green:
            survivors.append((mutation, note))
            suffix = f"  [{note}]" if note else f"  ({mutation.tests} stayed green)"
            print(f"  SURVIVED  {mutation.name}{suffix}")
        else:
            print(f"  caught    {mutation.name}")

    print()
    if survivors:
        print(f"{len(survivors)} of {len(MUTATIONS)} mutations survived.")
        skipped_only = [m for m, note in survivors if note]
        if skipped_only:
            print()
            print("Some of those only survived because their tests were skipped on this")
            print("backend. Run the script on Postgres to exercise them:")
            for m in skipped_only:
                print(f"  - {m.name}")
        print("A surviving mutation means the guard is not defended by a test that ran.")
        return 1

    print(f"all {len(MUTATIONS)} mutations were caught")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
