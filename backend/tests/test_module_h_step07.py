import inspect
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.shared import Merchant, Case
from app.models.module_a import Dispute
from app.services.external_action.contest_amount_gate import (
    validate_contest_amount,
    ContestAmountGateErrorCode,
)

DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_h_07_pg"
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


def make_case_with_dispute(db: Session, amount_minor=10000):
    merchant = Merchant(name="Test Merchant H07", external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
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
        status="open",
        dispute_created_at=datetime.now(timezone.utc),
        respond_by=None,
    )
    db.add(dispute)
    db.commit()
    return case, dispute


FIXED_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)


# 1. contest_amount_minor == 0 => blocked.
def test_zero_amount_blocked(db):
    case, dispute = make_case_with_dispute(db, amount_minor=10000)
    result = validate_contest_amount(db, case.case_id, 0, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == ContestAmountGateErrorCode.CONTEST_AMOUNT_INVALID
    assert result.is_full_contest is None


# 2. negative amount => blocked.
def test_negative_amount_blocked(db):
    case, dispute = make_case_with_dispute(db, amount_minor=10000)
    result = validate_contest_amount(db, case.case_id, -1, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == ContestAmountGateErrorCode.CONTEST_AMOUNT_INVALID
    assert result.is_full_contest is None


# 3. amount == 1 with dispute amount >= 1 => allowed.
def test_minimum_positive_amount_allowed(db):
    case, dispute = make_case_with_dispute(db, amount_minor=10000)
    result = validate_contest_amount(db, case.case_id, 1, current_time=FIXED_NOW)
    assert result.allowed is True
    assert result.error_code is None
    assert result.is_full_contest is False


# 4. amount == dispute amount => allowed + is_full_contest=True.
def test_amount_equal_to_dispute_amount_is_full_contest(db):
    case, dispute = make_case_with_dispute(db, amount_minor=10000)
    result = validate_contest_amount(db, case.case_id, 10000, current_time=FIXED_NOW)
    assert result.allowed is True
    assert result.error_code is None
    assert result.is_full_contest is True
    assert result.dispute_amount_minor == 10000
    assert result.candidate_contest_amount_minor == 10000


# 5. amount one minor unit below dispute amount => allowed + is_full_contest=False.
def test_amount_one_below_dispute_amount_is_partial_contest(db):
    case, dispute = make_case_with_dispute(db, amount_minor=10000)
    result = validate_contest_amount(db, case.case_id, 9999, current_time=FIXED_NOW)
    assert result.allowed is True
    assert result.error_code is None
    assert result.is_full_contest is False


# 6. amount == dispute amount + 1 => blocked.
def test_amount_exceeding_dispute_amount_blocked(db):
    case, dispute = make_case_with_dispute(db, amount_minor=10000)
    result = validate_contest_amount(db, case.case_id, 10001, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == ContestAmountGateErrorCode.CONTEST_AMOUNT_INVALID
    assert result.is_full_contest is None
    assert result.dispute_amount_minor == 10000


# 7. Missing Dispute row => blocked, fail closed.
def test_missing_dispute_fails_closed(db):
    merchant = Merchant(name="Test Merchant H07 No Dispute", external_merchant_id=f"ext_{uuid.uuid4()}", is_active=True)
    db.add(merchant)
    db.flush()
    case = Case(merchant_id=merchant.merchant_id, external_dispute_id=f"disp_{uuid.uuid4()}", source="razorpay")
    db.add(case)
    db.commit()

    result = validate_contest_amount(db, case.case_id, 5000, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == ContestAmountGateErrorCode.CONTEST_AMOUNT_INVALID
    assert result.dispute_amount_minor is None
    assert result.is_full_contest is None


# 8. Fresh DB read sees a changed dispute amount despite a stale identity-map object.
def test_fresh_read_bypasses_stale_identity_map(db, alembic_engine):
    case, dispute = make_case_with_dispute(db, amount_minor=10000)

    # Load the Dispute into this session's identity map first, as ordinary app code might.
    loaded = db.query(Dispute).filter(Dispute.case_id == case.case_id).first()
    assert loaded.amount_minor == 10000

    # Mutate the row through a completely separate connection/transaction, bypassing
    # this session's identity map entirely (mirrors a concurrent writer / webhook).
    import sqlalchemy as sa
    with alembic_engine.connect() as conn:
        conn.execute(
            sa.text("UPDATE disputes SET amount_minor = :amount WHERE case_id = :case_id"),
            {"amount": 5000, "case_id": str(case.case_id)},
        )
        conn.commit()

    # The stale in-memory object still (incorrectly) looks unchanged...
    assert loaded.amount_minor == 10000

    # ...but the gate must see the fresh ceiling, not the stale cached one:
    # a candidate of 10000 was valid against the old ceiling but must now be
    # rejected against the freshly-read, lower ceiling of 5000.
    result = validate_contest_amount(db, case.case_id, 10000, current_time=FIXED_NOW)
    assert result.allowed is False
    assert result.error_code == ContestAmountGateErrorCode.CONTEST_AMOUNT_INVALID
    assert result.dispute_amount_minor == 5000

    # And a candidate matching the fresh ceiling is now correctly a full contest.
    result2 = validate_contest_amount(db, case.case_id, 5000, current_time=FIXED_NOW)
    assert result2.allowed is True
    assert result2.is_full_contest is True


# 9. The gate function's signature makes a caller-supplied dispute amount structurally
# impossible — it only ever reads the ceiling fresh from the DB itself.
def test_signature_accepts_no_dispute_amount_override():
    sig = inspect.signature(validate_contest_amount)
    param_names = set(sig.parameters.keys())
    assert param_names == {"db", "case_id", "contest_amount_minor", "current_time"}
    assert "dispute_amount_minor" not in param_names


# 10. Exact error code string equality.
def test_error_code_exact_string():
    assert ContestAmountGateErrorCode.CONTEST_AMOUNT_INVALID == "CONTEST_AMOUNT_INVALID"


# 11. No external-action / outbound-call capability in the module.
def test_no_external_action_capability_in_module():
    import app.services.external_action.contest_amount_gate as gate_module
    source = inspect.getsource(gate_module).lower()
    for forbidden in ("import requests", "import httpx", "import urllib", ".get(\"http", ".post(\"http"):
        assert forbidden not in source, f"unexpected outbound-call marker '{forbidden}' found in contest_amount_gate.py"


# 12. Deterministic behavior: identical inputs + identical current_time always
# produce an identical result.
def test_deterministic_behavior(db):
    case, dispute = make_case_with_dispute(db, amount_minor=10000)
    r1 = validate_contest_amount(db, case.case_id, 7000, current_time=FIXED_NOW)
    r2 = validate_contest_amount(db, case.case_id, 7000, current_time=FIXED_NOW)
    assert r1.allowed == r2.allowed == True
    assert r1.is_full_contest == r2.is_full_contest == False
    assert r1.checked_at == r2.checked_at == FIXED_NOW


# 13. Integer minor-unit handling: values remain plain Python ints, no float/Decimal drift.
def test_integer_minor_unit_handling(db):
    case, dispute = make_case_with_dispute(db, amount_minor=999999999)
    result = validate_contest_amount(db, case.case_id, 999999999, current_time=FIXED_NOW)
    assert result.allowed is True
    assert isinstance(result.candidate_contest_amount_minor, int)
    assert isinstance(result.dispute_amount_minor, int)
    assert result.dispute_amount_minor == 999999999
