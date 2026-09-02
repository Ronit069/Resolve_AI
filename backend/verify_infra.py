import asyncio
from app.core.database import SessionLocal
from app.core.storage import storage_client
from app.worker.celery_app import celery_app
from fastapi.testclient import TestClient
from app.main import app

from sqlalchemy import text

def verify_all():
    print("--- 1. Testing DB Connection ---")
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        print("DB Connection: OK")
    except Exception as e:
        print(f"DB Connection: FAILED - {e}")

    print("--- 2. Testing Redis / Celery Connection ---")
    try:
        # Check if broker is reachable
        conn = celery_app.connection()
        conn.connect()
        print("Redis/Celery Broker: OK")
    except Exception as e:
        print(f"Redis/Celery Broker: FAILED - {e}")

    print("--- 3. Testing MinIO Abstraction ---")
    try:
        # Check bucket existence (storage_client creates it in __init__)
        storage_client.s3_client.head_bucket(Bucket=storage_client.bucket)
        print("MinIO Connection & Bucket: OK")
    except Exception as e:
        print(f"MinIO Connection: FAILED - {e}")

    print("--- 4. Testing FastAPI Health Endpoint ---")
    client = TestClient(app)
    response = client.get("/health")
    if response.status_code == 200:
        print(f"FastAPI /health: OK - {response.json()}")
    else:
        print(f"FastAPI /health: FAILED - {response.status_code}")

if __name__ == "__main__":
    verify_all()
