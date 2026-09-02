import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.database import Base
from app.models.shared import Case
from app.services.validation_rules import run_validation_rules

def check_db():
    engine = create_engine("sqlite:///benchmark_final.db")
    Session = sessionmaker(bind=engine)
    session = Session()

    with open("synthetic_benchmark_v1_validation.jsonl", "r") as f:
        line = f.readline()
        record = json.loads(line)
        case_id = record["case_id"]

    case = session.query(Case).filter(Case.case_id == case_id).first()
    if not case:
        print(f"Case {case_id} not found in DB.")
        return

    print(f"Case found: {case.case_id}, merchant: {case.merchant_id}")

    try:
        rules_run = run_validation_rules(session, case.case_id)
        print(f"Module E ran successfully. Status: {rules_run.overall_status}")
    except Exception as e:
        print(f"Module E failed: {e}")

if __name__ == "__main__":
    check_db()
