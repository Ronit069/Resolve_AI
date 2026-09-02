import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import app.core.database
import app.services.ml.synthetic_benchmark
from app.core.database import Base

if os.path.exists("benchmark.db"):
    os.remove("benchmark.db")
engine = create_engine("sqlite:///benchmark.db")
Base.metadata.create_all(bind=engine)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
app.core.database.SessionLocal = TestSessionLocal
app.services.ml.synthetic_benchmark.SessionLocal = TestSessionLocal

from app.services.ml.synthetic_benchmark import SyntheticBenchmarkGenerator

engine = None

def generate_benchmark():
    def recreate_db(db_name="benchmark.db"):
        global engine
        if engine is not None:
            engine.dispose()
        if os.path.exists(db_name):
            try:
                os.remove(db_name)
            except PermissionError:
                pass 
        engine = create_engine(f"sqlite:///{db_name}")
        Base.metadata.create_all(bind=engine)
        TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        app.core.database.SessionLocal = TestSessionLocal
        app.services.ml.synthetic_benchmark.SessionLocal = TestSessionLocal

    output_path = os.path.join(os.getcwd(), "synthetic_benchmark_v1.jsonl")
    
    print("Initializing benchmark generator...")
    generator = SyntheticBenchmarkGenerator(seed=42)
    print("Initial Weights:", generator.scenario_weights)
    
    # 1. Calibration run (small size, fast)
    print("Starting calibration run (1,000 size)...")
    recreate_db("benchmark_calib.db")
    _, dist = generator.generate_dataset(size=1000)
    pos_rate = dist["positive_rate"]
    print(f"Calibration positive rate: {pos_rate:.4f}")
    
    if abs(pos_rate - 0.35) > 0.05:
        print("Calibrating weights to hit 35% target...")
        from app.services.ml.synthetic_benchmark import ScenarioFamily
        if pos_rate > 0.35:
            diff = pos_rate - 0.35
            generator.scenario_weights[ScenarioFamily.STRONG_CONTESTABLE] -= diff
            generator.scenario_weights[ScenarioFamily.WEAK_EVIDENCE] += diff / 2
            generator.scenario_weights[ScenarioFamily.CONTRADICTORY] += diff / 2
        else:
            diff = 0.35 - pos_rate
            generator.scenario_weights[ScenarioFamily.STRONG_CONTESTABLE] += diff
            generator.scenario_weights[ScenarioFamily.WEAK_EVIDENCE] -= diff / 2
            generator.scenario_weights[ScenarioFamily.CONTRADICTORY] -= diff / 2
            
        total = sum(generator.scenario_weights.values())
        for k in generator.scenario_weights:
            generator.scenario_weights[k] = max(0.01, generator.scenario_weights[k] / total)
            
        print("Calibrated Weights:", generator.scenario_weights)
        
    print("Starting FINAL generation (10,000 size)...")
    recreate_db("benchmark_final.db")
    
    # Generate 10k examples with adjusted weights
    examples, final_dist = generator.generate_dataset(size=10000, output_path=output_path)
    
    print("--- BENCHMARK GENERATION COMPLETE ---")
    print(f"Generated {len(examples)} examples.")
    print("Final Label Distribution:")
    print(f"SAFE_TO_CONTEST (1): {final_dist['positive_rate']*100:.2f}%")
    print(f"NOT_SAFE_TO_AUTOMATE (0): {final_dist['negative_rate']*100:.2f}%")
    
    # Calculate scenario distribution
    scenario_counts = {}
    for ex in examples:
        sf = ex["scenario_family"]
        scenario_counts[sf] = scenario_counts.get(sf, 0) + 1
        
    print("\nScenario Distribution:")
    for sf, count in scenario_counts.items():
        print(f"  {sf}: {count} ({count/len(examples)*100:.2f}%)")
        
    print(f"\nFinal Calibrated Weights:")
    for sf, w in generator.scenario_weights.items():
        print(f"  {sf.value}: {w:.4f}")
        
    print(f"\nOutput saved to: {output_path}")

if __name__ == "__main__":
    generate_benchmark()
