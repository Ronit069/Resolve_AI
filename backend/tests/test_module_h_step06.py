import inspect
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.shared import Merchant, Case
from app.models.module_a import Dispute
from app.services.external_action.deadline_gate import (
    check_dispute_actionable,
    DeadlineGateErrorCode,
    NON_ACTIONABLE_STATUSES,
    KNOWN_ACTIONABLE_STATUSES,
)

DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_h_06_pg"
TEST_DB_URL = f"postgresql://resolve_user:resolve_password@127.0.0.1:5433/{TEST_DB_NAME}"


@pytest.fixture(scope="module")
def postgres_engine():
    import sqlalchemy as sa
    try:
        engine_default = sa.create_engine(DB_URL, isolation_level="AUTOCOMMIT")
        with engine_default.connect() as conn:
            conn.execute(sa.text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)"))
            conn.execute(sa.text(f"CREATE DATABASE {TEST_DB_NAME}"))
    except Exception as e:
        pytest.skip(f"PostgreSQL not available: {e}")

    engine_test = sa.create_engine(TEST_DB_URL)
    yield engine_test

    engine_test.dispose()
    try:
        with engine_default.connect() as conn:
            conn.execute(sa.text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)"))
    except Exception:
        pass
    engine_default.dispose()


@pytest.fixture(scope="module")
def alembic_engine(postgres_engine):
    from alembic.config import Config
    from alembic import command
    import os

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ini_path = os.path.join(base_dir, "alembic.ini")

    config = Config(ini_path)
    config.set_main_option("sqlalchemy.url", TEST_DB_URL)
    config.set_main_option("script_location", os.path.join(base_dir, "alembic"))

    command.upgrade(config, "head")
    yield postgres_engine


@pytest.fixture
def db(alembic_engine):
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=alembic_engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        for table in reversed(Base.metadata.sorted_tables):
            try:
                with alembic_engine.connect() as conn:
                    conn.execute(table.delete())
                    conn.commit()
            except Exception:
                pass


def make_case_with_dispute(db: Session, status="open", respond_by=None, amount_minor=1000):
    merchant = Merchant(name="Test Merchant H06", external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
    db.add(merchant)
    db.flush()

    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db.add(case)
    db.flush()

    dispute = Dispute(
        case_id=case.case_id,
        external_dispute_id=case.external_dispute_id,
        payment_id="pay_1",
        amount_minor=amount_minor,
        currency="INR",
        reason_code="fraud",
        status=status,
        dispute_created_at=datetime.now(timezone.utc),
        respond_by=respond_by,
    )
    db.add(dispute)
    db.commit()
    return case, dispute


FIXED_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)


# 1. Actionable status + future respond_by => allowed.
def test_actionable_status_future_deadline_allowed(db):
    case, dispute = make_case_with_dispute(db, status="open", respond_by=FIXED_NOW + timedelta(days=2))
    result = check_dispute_actionable(db, case.case_id, current_time=FIXED_NOW)
    assert result.allowed is True
    assert result.error_code is None


# 2. respond_by exactly equal to current_time => blocked (inclusive boundary).
def test_respond_by_exactly_equal_to_now_blocked(db):
    case, dispute = make_case_with_dispute(db, status="open", respond_by=FIXED_NOW)
    result = check_dispute_actionable(db, case.case_id, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == DeadlineGateErrorCode.CONTEST_DEADLINE_EXPIRED


# 3. respond_by in the past => blocked.
def test_respond_by_in_past_blocked(db):
    case, dispute = make_case_with_dispute(db, status="open", respond_by=FIXED_NOW - timedelta(seconds=1))
    result = check_dispute_actionable(db, case.case_id, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == DeadlineGateErrorCode.CONTEST_DEADLINE_EXPIRED


# 4. Each non-actionable status individually => blocked with CONTEST_INVALID_STATUS.
@pytest.mark.parametrize("status", sorted(NON_ACTIONABLE_STATUSES))
def test_each_non_actionable_status_blocked(db, status):
    case, dispute = make_case_with_dispute(db, status=status, respond_by=FIXED_NOW + timedelta(days=1))
    result = check_dispute_actionable(db, case.case_id, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == DeadlineGateErrorCode.CONTEST_INVALID_STATUS


# 4b. Non-actionable status comparison is case-insensitive.
@pytest.mark.parametrize("status", ["WON", "Closed", "ExPiReD"])
def test_non_actionable_status_case_insensitive(db, status):
    case, dispute = make_case_with_dispute(db, status=status, respond_by=FIXED_NOW + timedelta(days=1))
    result = check_dispute_actionable(db, case.case_id, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == DeadlineGateErrorCode.CONTEST_INVALID_STATUS


# 5. Unknown/unsupported status values fail safely (blocked), never allowed by default.
@pytest.mark.parametrize("status", ["pending_evidence", "garbage_status", "", "   "])
def test_unknown_status_fails_safely(db, status):
    case, dispute = make_case_with_dispute(db, status=status, respond_by=FIXED_NOW + timedelta(days=1))
    result = check_dispute_actionable(db, case.case_id, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == DeadlineGateErrorCode.CONTEST_INVALID_STATUS


# 6. Missing respond_by fails closed as CONTEST_DEADLINE_EXPIRED (frozen decision).
def test_missing_respond_by_fails_closed(db):
    case, dispute = make_case_with_dispute(db, status="open", respond_by=None)
    result = check_dispute_actionable(db, case.case_id, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == DeadlineGateErrorCode.CONTEST_DEADLINE_EXPIRED


# 7. The gate function's signature makes client-supplied status/respond_by structurally
# impossible to pass in — it only ever reads them from the DB itself.
def test_signature_accepts_no_status_or_respond_by_override():
    sig = inspect.signature(check_dispute_actionable)
    param_names = set(sig.parameters.keys())
    assert param_names == {"db", "case_id", "current_time"}
    assert "status" not in param_names
    assert "respond_by" not in param_names


# 8. A blocked result performs no external action: the function is a pure read-and-decide
# predicate with no outbound HTTP/network capability at all — proven by the absence of
# any network mock being required to exercise every blocking branch above, and by
# asserting the module imports no HTTP/requests-style client.
def test_no_external_action_capability_in_module():
    # The module's docstrings intentionally reference Razorpay as a future
    # integration seam (frozen wording) — what must NOT be present is any
    # actual HTTP/outbound-call machinery.
    import app.services.external_action.deadline_gate as gate_module
    source = inspect.getsource(gate_module).lower()
    for forbidden in ("import requests", "import httpx", "import urllib", ".get(\"http", ".post(\"http"):
        assert forbidden not in source, f"unexpected outbound-call marker '{forbidden}' found in deadline_gate.py"


# 9. Exact error code string equality (not just truthy) for both branches.
def test_error_code_exact_strings():
    assert DeadlineGateErrorCode.CONTEST_DEADLINE_EXPIRED == "CONTEST_DEADLINE_EXPIRED"
    assert DeadlineGateErrorCode.CONTEST_INVALID_STATUS == "CONTEST_INVALID_STATUS"


# 10. Deterministic injected current_time: identical inputs + identical current_time
# always produce the identical result, independent of wall-clock time.
def test_deterministic_injected_current_time(db):
    case, dispute = make_case_with_dispute(db, status="open", respond_by=FIXED_NOW + timedelta(hours=1))
    r1 = check_dispute_actionable(db, case.case_id, current_time=FIXED_NOW)
    r2 = check_dispute_actionable(db, case.case_id, current_time=FIXED_NOW)
    assert r1.allowed == r2.allowed == True
    assert r1.checked_at == r2.checked_at == FIXED_NOW

    later = FIXED_NOW + timedelta(hours=2)
    r3 = check_dispute_actionable(db, case.case_id, current_time=later)
    assert r3.allowed is False
    assert r3.error_code == DeadlineGateErrorCode.CONTEST_DEADLINE_EXPIRED


# 11. Fresh DB read is used rather than a stale previously-loaded ORM instance: mutate the
# row via a separate connection (simulating a concurrent writer, bypassing this test's own
# session identity map) and confirm the gate observes the new value, not cached state.
def test_fresh_read_bypasses_stale_identity_map(db, alembic_engine):
    case, dispute = make_case_with_dispute(db, status="open", respond_by=FIXED_NOW + timedelta(days=1))

    # Load the Dispute into this session's identity map first, as ordinary app code might.
    loaded = db.query(Dispute).filter(Dispute.case_id == case.case_id).first()
    assert loaded.status == "open"

    # Mutate the row through a completely separate connection/transaction, bypassing
    # this session's identity map entirely (mirrors a concurrent writer / webhook).
    import sqlalchemy as sa
    with alembic_engine.connect() as conn:
        conn.execute(
            sa.text("UPDATE disputes SET status = :status WHERE case_id = :case_id"),
            {"status": "closed", "case_id": str(case.case_id)},
        )
        conn.commit()

    # The stale in-memory object still (incorrectly) looks unchanged...
    assert loaded.status == "open"

    # ...but the gate must see the fresh value, not the stale cached one.
    result = check_dispute_actionable(db, case.case_id, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == DeadlineGateErrorCode.CONTEST_INVALID_STATUS
    assert result.dispute_status == "closed"


# 12. No dispute row at all fails safely.
def test_missing_dispute_row_fails_safely(db):
    merchant = Merchant(name="Test Merchant H06 No Dispute", external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
    db.add(merchant)
    db.flush()
    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db.add(case)
    db.commit()

    result = check_dispute_actionable(db, case.case_id, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == DeadlineGateErrorCode.CONTEST_INVALID_STATUS


# 13. under_review is also a recognized actionable status.
def test_under_review_status_actionable(db):
    case, dispute = make_case_with_dispute(db, status="under_review", respond_by=FIXED_NOW + timedelta(days=1))
    result = check_dispute_actionable(db, case.case_id, current_time=FIXED_NOW)
    assert result.allowed is True
