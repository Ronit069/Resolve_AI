import pytest
from sqlalchemy import text, inspect
import sqlalchemy as sa
from alembic.config import Config
from alembic import command
import os

DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_h_pg"
TEST_DB_URL = f"postgresql://resolve_user:resolve_password@127.0.0.1:5433/{TEST_DB_NAME}"

@pytest.fixture(scope="module")
def postgres_engine():
    try:
        engine_default = sa.create_engine(DB_URL, isolation_level="AUTOCOMMIT")
        with engine_default.connect() as conn:
            conn.execute(sa.text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)"))
            conn.execute(sa.text(f"CREATE DATABASE {TEST_DB_NAME}"))
    except Exception as e:
        pytest.skip(f"PostgreSQL not available or could not create test db: {e}")

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
def alembic_config():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ini_path = os.path.join(base_dir, "alembic.ini")
    
    config = Config(ini_path)
    config.set_main_option("sqlalchemy.url", TEST_DB_URL)
    config.set_main_option("script_location", os.path.join(base_dir, "alembic"))
    return config

@pytest.fixture(scope="module")
def alembic_engine(postgres_engine, alembic_config):
    # Run down to d4e5f6a1b2c3 first
    command.upgrade(alembic_config, "d4e5f6a1b2c3")
    yield postgres_engine, alembic_config

def test_migration_upgrade(alembic_engine):
    engine, cfg = alembic_engine
    
    # Upgrade to H-00
    command.upgrade(cfg, "head")
    
    # Verify all tables exist
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    expected_tables = [
        "review_queue_items",
        "review_actions",
        "contest_packages",
        "contest_package_documents",
        "razorpay_document_links",
        "external_action_outbox",
        "external_action_attempts",
        "contest_submissions",
        "dispute_outcomes",
        "curated_feedback_labels"
    ]
    
    for table in expected_tables:
        assert table in tables, f"Table {table} is missing after upgrade"

def test_primary_keys(alembic_engine):
    engine, _ = alembic_engine
    inspector = inspect(engine)
    
    expected_tables = [
        "review_queue_items",
        "review_actions",
        "contest_packages",
        "contest_package_documents",
        "razorpay_document_links",
        "external_action_outbox",
        "external_action_attempts",
        "contest_submissions",
        "dispute_outcomes",
        "curated_feedback_labels"
    ]
    
    for table in expected_tables:
        pk = inspector.get_pk_constraint(table)
        assert len(pk["constrained_columns"]) == 1
        assert pk["constrained_columns"][0] == "id", f"Primary key for {table} is not 'id'"

def test_foreign_keys(alembic_engine):
    engine, _ = alembic_engine
    inspector = inspect(engine)
    
    # Check a sample of critical FKs
    fk = inspector.get_foreign_keys("review_queue_items")
    fk_map = {f["constrained_columns"][0]: f["referred_table"] + "." + f["referred_columns"][0] for f in fk}
    
    assert fk_map["case_id"] == "cases.case_id"
    assert fk_map["prediction_id"] == "risk_predictions.prediction_id"
    assert fk_map["draft_id"] == "generated_drafts.id"
    assert fk_map["assigned_to"] == "app_users.user_id"

    fk = inspector.get_foreign_keys("razorpay_document_links")
    fk_map = {f["constrained_columns"][0]: f["referred_table"] + "." + f["referred_columns"][0] for f in fk}
    assert fk_map["document_id"] == "evidence_documents.document_id"

def test_nullability(alembic_engine):
    engine, _ = alembic_engine
    inspector = inspect(engine)
    
    cols = {c["name"]: c for c in inspector.get_columns("curated_feedback_labels")}
    assert cols["case_id"]["nullable"] is False
    assert cols["label_name"]["nullable"] is False
    assert cols["curated_by"]["nullable"] is True  # Allowed to be null for system actions
    
def test_unique_constraints(alembic_engine):
    engine, _ = alembic_engine
    inspector = inspect(engine)
    
    # external_action_outbox idempotency_key is unique
    uniques = inspector.get_unique_constraints("external_action_outbox")
    cols = [u["column_names"] for u in uniques]
    assert ["idempotency_key"] in cols or any("idempotency_key" in c for c in cols)

    # razorpay_document_links razorpay_document_id is unique
    uniques2 = inspector.get_unique_constraints("razorpay_document_links")
    cols2 = [u["column_names"] for u in uniques2]
    assert ["razorpay_document_id"] in cols2 or any("razorpay_document_id" in c for c in cols2)

def test_json_jsonb_columns(alembic_engine):
    engine, _ = alembic_engine
    inspector = inspect(engine)
    
    cols = {c["name"]: c for c in inspector.get_columns("review_actions")}
    # In PostgreSQL, JSONVariant maps to JSONB usually, or JSON depending on dialect. 
    # Just checking the name string contains JSON
    assert "JSON" in str(cols["draft_revision_json"]["type"]).upper()

def test_uuid_generation(postgres_engine, alembic_engine):
    # Verify that a row can be inserted and UUID is generated automatically
    from app.models.module_h import ExternalActionOutbox, ExternalActionType, OutboxStatus
    from app.models.shared import Case, Merchant
    from sqlalchemy.orm import Session
    
    with Session(postgres_engine) as session:
        merchant = Merchant(external_merchant_id="test_uuid_merchant", name="Test")
        session.add(merchant)
        session.commit()
        
        case = Case(merchant_id=merchant.merchant_id, external_dispute_id="disp_123", source="razorpay")
        session.add(case)
        session.commit()
        
        import uuid
        outbox = ExternalActionOutbox(
            case_id=case.case_id,
            action_type=ExternalActionType.UPLOAD_DOCUMENT,
            aggregate_id=uuid.uuid4(),
            payload_json={"test": 1},
            idempotency_key="idemp_123"
        )
        session.add(outbox)
        session.commit()
        
        assert outbox.id is not None
        assert isinstance(outbox.id, uuid.UUID)

def test_tenant_architecture(alembic_engine):
    engine, _ = alembic_engine
    inspector = inspect(engine)
    
    # verify review_queue_items has NO merchant_id
    cols = [c["name"] for c in inspector.get_columns("review_queue_items")]
    assert "merchant_id" not in cols, "Module H tables should not redundantly store merchant_id"

def test_migration_downgrade(alembic_engine):
    engine, cfg = alembic_engine
    
    # Downgrade back to G20 head
    command.downgrade(cfg, "d4e5f6a1b2c3")
    
    # Verify H tables are removed but others remain
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    expected_removed = [
        "review_queue_items",
        "review_actions",
        "contest_packages",
        "contest_package_documents",
        "razorpay_document_links",
        "external_action_outbox",
        "external_action_attempts",
        "contest_submissions",
        "dispute_outcomes",
        "curated_feedback_labels"
    ]
    
    for table in expected_removed:
        assert table not in tables, f"Table {table} still exists after downgrade"
        
    assert "cases" in tables
    assert "risk_predictions" in tables
    assert "generated_drafts" in tables
