"""
SQLite-safe UUID column type.

sqlalchemy.dialects.postgresql.UUID(as_uuid=True) compiles to a column
whose DDL type name is literally "UUID". Postgres understands that;
SQLite does not, and since "UUID" contains none of the substrings
SQLite's type-affinity rule looks for ("INT", "CHAR", "CLOB", "TEXT",
"BLOB", "REAL", "FLOA", "DOUB"), the column falls back to NUMERIC
affinity. On INSERT, SQLite tries to coerce any text value that parses
as a complete number -- including scientific notation -- into INTEGER
or REAL. A UUID's hex digest can do that: "81036993e07135829103711548
293829" (31 decimal digits, one "e", decimal digits after) is valid
float syntax, and SQLite silently stores it as REAL -- here, inf, since
the "exponent" is absurd. Reproduced directly against this project's
own SQLite test databases (see tests/test_sqlite_uuid_affinity.py);
this is what crashed app/services/ml/synthetic_benchmark.py's
generate_dataset roughly once per ~10,000 generated examples, the rate
matching how rarely a random 128-bit value's hex digest both avoids
every a/b/c/d/f character except for a single legally-placed "e".

Every app/models/*.py file imports UUID from here instead of directly
from sqlalchemy.dialects.postgresql -- same `UUID(as_uuid=True)` call
sites, same real Postgres behavior in production, zero other code
changes required anywhere that already uses this import.
"""
import uuid as _uuid_module

from sqlalchemy import CHAR
from sqlalchemy.dialects.postgresql import UUID as _PostgresUUID
from sqlalchemy.types import TypeDecorator


class _SQLiteSafeUUID(TypeDecorator):
    """CHAR(32)-backed UUID storage for SQLite -- see module docstring."""

    impl = CHAR(32)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, _uuid_module.UUID):
            return value.hex
        return _uuid_module.UUID(value).hex

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return _uuid_module.UUID(value)


def UUID(as_uuid: bool = False):
    """
    Drop-in replacement for sqlalchemy.dialects.postgresql.UUID(as_uuid=...):
    native postgres.UUID on a real Postgres connection, CHAR(32)-backed
    (with the same Python uuid.UUID round-tripping when as_uuid=True) on
    SQLite.
    """
    pg_type = _PostgresUUID(as_uuid=as_uuid)
    sqlite_type = _SQLiteSafeUUID() if as_uuid else CHAR(32)
    return pg_type.with_variant(sqlite_type, "sqlite")
