"""
Module L, L-06 — idempotent initialization/seed command.

Applies Alembic migrations, then seeds:
  - a demo Merchant
  - one demo AppUser per role (idempotent by email)
  - one demo reason-code policy (Module E EvidencePolicyVersion)
  - one demo Case/Dispute (via the existing, frozen
    app.services.ingestion.process_dispute_event path — the same
    canonical ingestion path Module A's webhook and dev endpoints use;
    no new demo-data logic is invented here)
  - a champion ModelVersion (Module L model-registry convention — no
    schema migration, see app.services.mlops.model_registry)

Safe to run repeatedly: every step is check-then-skip or relies on an
existing unique constraint, exactly like the rest of the codebase's
idempotency conventions (Merchant.external_merchant_id,
WebhookEvent.external_event_id, EvidencePolicyVersion's own unique
constraint).

Usage: python scripts/seed_demo.py
(inside the api/worker container: docker compose run --rm api python scripts/seed_demo.py)
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


DEMO_MERCHANT_EXTERNAL_ID = "demo_merchant_resolveai"
DEMO_USERS = [
    ("demo.admin@resolveai.local", "MERCHANT_ADMIN"),
    ("demo.analyst@resolveai.local", "RISK_ANALYST"),
    ("demo.approver@resolveai.local", "APPROVER"),
    ("demo.model_maintainer@resolveai.local", "MODEL_MAINTAINER"),
]
DEMO_CASE_EXTERNAL_DISPUTE_ID = "demo_disp_seed_001"


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = Config(os.path.join(base_dir, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(base_dir, "alembic"))
    print("Applying Alembic migrations (upgrade head)...")
    command.upgrade(config, "head")


def seed_merchant(db):
    from app.models.shared import Merchant

    merchant = db.query(Merchant).filter(Merchant.external_merchant_id == DEMO_MERCHANT_EXTERNAL_ID).first()
    if merchant is not None:
        print(f"Merchant already seeded: {merchant.merchant_id}")
        return merchant

    merchant = Merchant(external_merchant_id=DEMO_MERCHANT_EXTERNAL_ID, name="ResolveAI Demo Merchant", is_active=True)
    db.add(merchant)
    db.commit()
    db.refresh(merchant)
    print(f"Seeded demo merchant: {merchant.merchant_id}")
    return merchant


def seed_users(db, merchant):
    from app.models.shared import AppUser, AppUserRole

    for email, role_name in DEMO_USERS:
        existing = db.query(AppUser).filter(AppUser.email == email).first()
        if existing is not None:
            print(f"User already seeded: {email}")
            continue
        user = AppUser(merchant_id=merchant.merchant_id, email=email, is_active=True, role=AppUserRole(role_name))
        db.add(user)
        db.commit()
        print(f"Seeded demo user: {email} ({role_name})")


def seed_reason_code_policy(db):
    from app.models.module_e import EvidencePolicyVersion

    existing = (
        db.query(EvidencePolicyVersion)
        .filter(
            EvidencePolicyVersion.payment_network == "visa",
            EvidencePolicyVersion.reason_code == "fraud",
            EvidencePolicyVersion.phase == "pre",
            EvidencePolicyVersion.version == 1,
        )
        .first()
    )
    if existing is not None:
        print(f"Reason-code policy already seeded: {existing.policy_version_id}")
        return existing

    policy = EvidencePolicyVersion(
        payment_network="visa", reason_code="fraud", phase="pre", version=1,
        effective_from=datetime.now(timezone.utc),
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    print(f"Seeded demo reason-code policy: {policy.policy_version_id}")
    return policy


def seed_demo_case(db):
    from app.models.module_a import Dispute
    from app.services.ingestion import process_dispute_event

    existing = db.query(Dispute).filter(Dispute.external_dispute_id == DEMO_CASE_EXTERNAL_DISPUTE_ID).first()
    if existing is not None:
        print(f"Demo case already seeded: case_id={existing.case_id}")
        return existing.case_id

    now = datetime.now(timezone.utc)
    dispute_data = {
        "id": DEMO_CASE_EXTERNAL_DISPUTE_ID,
        "payment_id": "demo_pay_seed_001",
        "amount": 500000,
        "currency": "INR",
        "reason_code": "fraud",
        "status": "open",
        "phase": "pre",
        "created_at": int(now.timestamp()),
        "respond_by": int(now.timestamp()) + 7 * 86400,
    }
    raw_payload = json.dumps(dispute_data).encode("utf-8")
    case_id = process_dispute_event(
        db=db, source="synthetic", raw_payload=raw_payload,
        event_id="evt_demo_seed_001", event_type="dispute.created",
        event_time=now, dispute_data=dispute_data,
    )
    if case_id is None:
        print("Demo case ingestion returned no case_id (likely a duplicate event) — treating as already seeded.")
    else:
        print(f"Seeded demo case: case_id={case_id}")
    return case_id


def seed_champion_model(db):
    from app.models.module_f import ModelVersion
    from app.services.mlops.model_registry import CHAMPION_STATUS, mark_champion

    existing_champion = (
        db.query(ModelVersion)
        .filter(ModelVersion.algorithm == "lgbm", ModelVersion.status == CHAMPION_STATUS)
        .first()
    )
    if existing_champion is not None:
        print(f"Champion model already registered: {existing_champion.id}")
        return existing_champion

    model_version = ModelVersion(algorithm="lgbm", model_name="ResolveAI Demo LightGBM Comparator", status="pending")
    db.add(model_version)
    db.commit()
    db.refresh(model_version)
    champion = mark_champion(db, model_version.id)
    print(f"Registered champion model: {champion.id}")
    return champion


def main():
    run_migrations()

    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        merchant = seed_merchant(db)
        seed_users(db, merchant)
        seed_reason_code_policy(db)
        seed_demo_case(db)
        seed_champion_model(db)
    finally:
        db.close()

    print("Seed/init complete. Safe to re-run.")


if __name__ == "__main__":
    main()
