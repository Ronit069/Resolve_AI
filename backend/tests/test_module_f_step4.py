import pytest
import os
import json
from pathlib import Path
from typing import Dict, Any

from app.services.ml.synthetic_benchmark import SyntheticBenchmarkGenerator, ScenarioFamily

@pytest.fixture(autouse=True)
def sqlite_test_db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base
    import app.core.database
    import app.services.ml.synthetic_benchmark
    
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    monkeypatch.setattr(app.core.database, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(app.services.ml.synthetic_benchmark, "SessionLocal", TestSessionLocal)
    yield

def test_deterministic_reproducibility():
    gen1 = SyntheticBenchmarkGenerator(seed=42)
    # Generate small deterministic batch
    examples_1, dist_1 = gen1.generate_dataset(size=10)
    
    from app.core.database import SessionLocal, Base
    engine = SessionLocal().get_bind()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    gen2 = SyntheticBenchmarkGenerator(seed=42)
    examples_2, dist_2 = gen2.generate_dataset(size=10)
    
    assert len(examples_1) == 10
    assert len(examples_2) == 10
    
    for e1, e2 in zip(examples_1, examples_2):
        assert e1["scenario_family"] == e2["scenario_family"]
        assert e1["features"] == e2["features"]
        assert e1["label"] == e2["label"]
        assert e1["feature_hash"] == e2["feature_hash"]
        # Ensure ordered identically
        assert e1["case_id"] == e2["case_id"]

def test_scenario_families_coverage():
    """
    Explicitly forces generation of exactly one case per ScenarioFamily 
    to guarantee A-E integration validity without distorting the production benchmark.
    """
    from app.core.database import SessionLocal
    
    gen = SyntheticBenchmarkGenerator(seed=99)
    db = SessionLocal()
    gen._setup_base_policies(db)
    
    for scenario in ScenarioFamily:
        example, attrs = gen.generate_single_example(db, scenario)
        
        # Verify it successfully passed through F1, F2, F3
        assert example.label in [0, 1]
        assert "timeline_valid" in example.features
        
        if scenario == ScenarioFamily.STRONG_CONTESTABLE:
            # Should lean towards positive, have invoice
            assert attrs["has_invoice"] is True
            
        elif scenario == ScenarioFamily.CONTRADICTORY:
            # Mismatch causes 0 label
            assert attrs["amount"] != attrs["invoice_amount"]
            
        elif scenario == ScenarioFamily.QUALITY_DEGRADED:
            assert attrs["ocr_confidence"] < 0.8
            
        elif scenario == ScenarioFamily.DEADLINE_STATUS_BLOCK:
            assert attrs["dispute_status"] == "lost"
            
    db.close()

def test_leakage_absence():
    gen = SyntheticBenchmarkGenerator(seed=42)
    examples, dist = gen.generate_dataset(size=5)
    
    for ex in examples:
        features = ex["features"]
        assert "scenario_family" not in features
        assert "label" not in features
        assert "label_rationale" not in features
        assert "future_outcome" not in features
        assert "reviewer_decision" not in features
