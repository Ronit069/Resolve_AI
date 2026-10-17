"""
Regression test for the SQLite UUID-affinity corruption bug.

sqlalchemy.dialects.postgresql.UUID(as_uuid=True) compiles to a column whose
DDL type name is literally "UUID". SQLite assigns column affinity by matching
substrings in the type name ("INT", "CHAR", "TEXT", "BLOB", "REAL", "FLOA",
"DOUB"); "UUID" matches none of them, so the column gets NUMERIC affinity.
On INSERT, SQLite tries to losslessly coerce any text value that looks like a
complete number -- including scientific notation -- into INTEGER/REAL. A
uuid4 hex digest is 32 hex characters; when every character happens to be a
decimal digit except for a single, legally-placed 'e', the digest parses as
a float literal and SQLite silently rewrites it (here, to inf), corrupting
the UUID. This is what intermittently crashed dataset generation roughly
once per several thousand generated examples.

app.core.types.UUID fixes this by swapping in a CHAR(32)-backed
TypeDecorator on the sqlite dialect (TEXT affinity, no coercion) while
keeping native postgresql.UUID(as_uuid=True) for real Postgres.
"""
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.core.types import UUID, _SQLiteSafeUUID
import app.models.shared  # noqa: F401
import app.models.module_a  # noqa: F401
import app.models.module_b  # noqa: F401
import app.models.module_c  # noqa: F401
import app.models.module_d  # noqa: F401
from app.models.module_e import EvidenceRequirementAssessment, ERequirementState

# A real digest that previously corrupted to float('inf') under the plain
# postgresql.UUID DDL type: 32 hex chars, all decimal digits except one 'e'
# placed where it parses as a valid exponent.
TRICKY_HEX = "81036993e07135829103711548293829"[:32]


@pytest.fixture(scope="module")
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)


@pytest.fixture
def db_session(engine):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    yield session
    session.close()


def test_uuid_column_ddl_type_is_not_literal_uuid_on_sqlite(engine):
    with engine.connect() as conn:
        info = conn.execute(
            text("PRAGMA table_info(evidence_requirement_assessments)")
        ).fetchall()
        col = next(row for row in info if row[1] == "validation_run_id")
        declared_type = col[2]
    assert declared_type != "UUID"
    assert "CHAR" in declared_type.upper()


def test_tricky_hex_digest_survives_as_text_not_coerced_to_float(engine):
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                INSERT INTO evidence_requirement_assessments
                (id, validation_run_id, evidence_type, requirement_level, status, document_ids, reason)
                VALUES (:id, :vrid, 'INVOICE', 'REQUIRED', 'PRESENT', '[]', 'test')
                """
            ),
            {"id": uuid.uuid4().hex, "vrid": TRICKY_HEX},
        )
        conn.commit()
        stored_type, stored_value = conn.execute(
            text(
                "SELECT typeof(validation_run_id), validation_run_id "
                "FROM evidence_requirement_assessments WHERE validation_run_id = :vrid"
            ),
            {"vrid": TRICKY_HEX},
        ).fetchone()
    assert stored_type == "text"
    assert stored_value == TRICKY_HEX


def test_tricky_uuid_round_trips_through_orm(db_session):
    tricky_uuid = uuid.UUID(hex=TRICKY_HEX)

    row = EvidenceRequirementAssessment(
        validation_run_id=tricky_uuid,
        evidence_type="INVOICE",
        requirement_level="REQUIRED",
        status=ERequirementState.PRESENT,
    )
    db_session.add(row)
    db_session.commit()
    row_id = row.id
    db_session.expunge_all()

    fetched = db_session.get(EvidenceRequirementAssessment, row_id)
    assert fetched.validation_run_id == tricky_uuid
    assert isinstance(fetched.validation_run_id, uuid.UUID)


@pytest.mark.parametrize(
    "value",
    [
        uuid.UUID(hex=TRICKY_HEX),
        uuid.uuid4(),
        uuid.UUID("00000000-0000-0000-0000-000000000000"),
        uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
    ],
)
def test_sqlite_safe_uuid_type_decorator_round_trips(value):
    decorator = _SQLiteSafeUUID()
    bound = decorator.process_bind_param(value, dialect=None)
    assert bound == value.hex
    assert decorator.process_result_value(bound, dialect=None) == value


def test_sqlite_safe_uuid_type_decorator_handles_none():
    decorator = _SQLiteSafeUUID()
    assert decorator.process_bind_param(None, dialect=None) is None
    assert decorator.process_result_value(None, dialect=None) is None


def test_sqlite_safe_uuid_type_decorator_accepts_string_input():
    decorator = _SQLiteSafeUUID()
    dashed = "81036993-e071-3582-9103-711548293829"
    assert decorator.process_bind_param(dashed, dialect=None) == TRICKY_HEX
