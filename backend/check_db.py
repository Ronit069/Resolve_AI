import os
import sys
from sqlalchemy import create_engine, text

# Add the backend to sys.path to load settings
sys.path.insert(0, os.path.abspath(r"C:\Users\ronit\Desktop\Resolve_AI\backend"))

try:
    from app.core.config import settings
    # We construct the URL just in case
    db_url = str(settings.DATABASE_URL)
    print(f"Connecting to: {db_url}")
    
    engine = create_engine(db_url)
    with engine.connect() as conn:
        ver = conn.execute(text("SELECT version();")).scalar()
        print("Postgres Version:", ver)
        
        # Check pg_extension
        ext = conn.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector';")).scalar()
        print("pgvector installed version:", ext)
        
        # Check available extensions
        avail = conn.execute(text("SELECT default_version FROM pg_available_extensions WHERE name='vector';")).scalar()
        print("pgvector available version:", avail)
        
except Exception as e:
    print("Database connection failed:", e)
