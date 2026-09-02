import psycopg2
conn = psycopg2.connect('postgresql://resolve_user:resolve_password@127.0.0.1:5433/resolve_db')
conn.autocommit = True
cur = conn.cursor()
for val in ['OCR_QUEUED', 'OCR_PROCESSING', 'EXTRACTED', 'REVIEW_REQUIRED', 'OCR_FAILED', 'REPROCESS_REQUESTED']:
    try:
        cur.execute(f"ALTER TYPE evidenceprocessingstatus ADD VALUE '{val}';")
        print(f"Added {val}")
    except Exception as e:
        print(e)
