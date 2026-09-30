"""Seed the default policy rules for one business (tenant).

Run from the backend/ directory:
    python seed_policies.py --tenant-id 3

Safe to re-run — rules already present for that tenant (matched by name)
are skipped, not duplicated. New businesses created through
POST /api/v1/tenants are seeded automatically; use this only to backfill
businesses that predate that behavior.
"""
import argparse
import asyncio
import sys

from sqlalchemy import select

from app.database import async_session
from app.models.policy_rule import PolicyRule
from app.models.tenant import Tenant
from app.services.policy_defaults import DEFAULT_RULES, build_default_rules


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed default policy rules for a tenant.")
    parser.add_argument("--tenant-id", type=int, required=True, help="Target tenant id")
    return parser.parse_args(argv)


async def seed_for_tenant(tenant_id: int) -> tuple[int, int]:
    """Insert any missing default rules for the tenant. Returns (inserted, skipped)."""
    inserted = 0
    skipped = 0

    async with async_session() as db:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
        if tenant is None:
            raise SystemExit(f"No tenant with id {tenant_id}")

        existing_names = set(
            (
                await db.execute(
                    select(PolicyRule.rule_name).where(PolicyRule.tenant_id == tenant_id)
                )
            ).scalars().all()
        )

        for rule in build_default_rules(tenant_id):
            if rule.rule_name in existing_names:
                print(f"  SKIP   '{rule.rule_name}' (already exists for tenant {tenant_id})")
                skipped += 1
            else:
                db.add(rule)
                await db.flush()
                print(f"  INSERT '{rule.rule_name}' (id={rule.id})")
                inserted += 1

        await db.commit()

    return inserted, skipped


def main(argv: list[str] | None = None) -> None:
    ns = parse_args(argv)
    print(f"Seeding policy rules for tenant {ns.tenant_id}...\n")
    inserted, skipped = asyncio.run(seed_for_tenant(ns.tenant_id))
    print(f"\nDone — {inserted} inserted, {skipped} skipped.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        print(f"\n{exc}", file=sys.stderr)
        sys.exit(1)
