import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic import command
import os

DB_URL = "postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db"
TEST_DB_NAME = "resolve_test_module_g_pg"
TEST_DB_URL = f"postgresql://resolve_user:resolve_password@127.0.0.1:5433/{TEST_DB_NAME}"

@pytest.fixture(scope="module")
def postgres_engine():
    # Connect to default DB to create test DB
    try:
        engine_default = sa.create_engine(DB_URL, isolation_level="AUTOCOMMIT")
        with engine_default.connect() as conn:
            conn.execute(sa.text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)"))
            conn.execute(sa.text(f"CREATE DATABASE {TEST_DB_NAME}"))
    except Exception as e:
        pytest.skip(f"PostgreSQL not available or could not create test db: {e}")

    engine_test = sa.create_engine(TEST_DB_URL)
    
    yield engine_test
    
    # Teardown
    engine_test.dispose()
    try:
        with engine_default.connect() as conn:
            conn.execute(sa.text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)"))
    except Exception:
        pass
    engine_default.dispose()

@pytest.fixture(scope="module")
def alembic_config():
    # Construct absolute path to alembic.ini based on this file's location
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ini_path = os.path.join(base_dir, "alembic.ini")
    
    config = Config(ini_path)
    config.set_main_option("sqlalchemy.url", TEST_DB_URL)
    # Set the script location explicitly if needed
    config.set_main_option("script_location", os.path.join(base_dir, "alembic"))
    return config

def test_postgres_integration_schema_and_migrations(postgres_engine, alembic_config):
    """
    POSTGRESQL INTEGRATION TEST
    This test verifies actual PostgreSQL database catalog metadata to ensure the pgvector
    extension, VECTOR column type, and HNSW index are physically present in the database.
    """
    
    # 1. Upgrade to head
    command.upgrade(alembic_config, "head")
    
    with postgres_engine.connect() as conn:
        # Verify PostgreSQL is running and vector extension is available/enabled
        ext_res = conn.execute(sa.text("SELECT extname FROM pg_extension WHERE extname = 'vector';")).scalar()
        assert ext_res == 'vector', "pgvector extension is not installed or enabled in PostgreSQL"
        
        # Verify knowledge_sources and knowledge_chunks exist
        tables = conn.execute(sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")).scalars().all()
        assert 'knowledge_sources' in tables, "knowledge_sources table was not created"
        assert 'knowledge_chunks' in tables, "knowledge_chunks table was not created"
        
        # Verify knowledge_chunks.embedding is a PostgreSQL VECTOR column
        col_res = conn.execute(sa.text("""
            SELECT udt_name 
            FROM information_schema.columns 
            WHERE table_name = 'knowledge_chunks' AND column_name = 'embedding'
        """)).scalar()
        assert col_res == 'vector', f"Expected column type 'vector', but got {col_res}"
        
        # Verify the vector dimension is exactly 1536
        dim_res = conn.execute(sa.text("""
            SELECT format_type(atttypid, atttypmod) 
            FROM pg_attribute 
            WHERE attrelid = 'knowledge_chunks'::regclass AND attname = 'embedding'
        """)).scalar()
        assert 'vector(1536)' in dim_res.lower(), f"Expected vector(1536), but got {dim_res}"
        
        # Verify the HNSW index exists and uses vector_cosine_ops
        idx_res = conn.execute(sa.text("""
            SELECT indexdef FROM pg_indexes 
            WHERE tablename = 'knowledge_chunks' AND indexname = 'idx_knowledge_chunks_embedding'
        """)).scalar()
        assert idx_res is not None, "HNSW index 'idx_knowledge_chunks_embedding' does not exist"
        assert 'USING hnsw' in idx_res, "Index is not using HNSW"
        assert 'vector_cosine_ops' in idx_res, "Index is not using vector_cosine_ops"
    
    # 2. Verify Alembic downgrade succeeds without leaving Module G tables/extensions behind
    command.downgrade(alembic_config, "f18d2b28ac78")
    
    with postgres_engine.connect() as conn:
        tables_after = conn.execute(sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")).scalars().all()
        assert 'knowledge_sources' not in tables_after, "knowledge_sources table was not removed during downgrade"
        assert 'knowledge_chunks' not in tables_after, "knowledge_chunks table was not removed during downgrade"
        
        ext_res_after = conn.execute(sa.text("SELECT extname FROM pg_extension WHERE extname = 'vector';")).scalar()
        assert ext_res_after is None, "pgvector extension was not removed during downgrade"
