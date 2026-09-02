import random
import uuid
import json
import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, Any, List

from sqlalchemy.orm import Session
from app.core.database import SessionLocal

from app.models.shared import Case, Merchant, ProcessingState, AppUser
from app.models.module_a import Dispute
from app.models.module_b import Payment, Order, Shipment, Refund
from app.models.module_c import EvidenceDocument, EvidenceType, ScanStatus, EvidenceProcessingStatus, EvidenceRequirement
from app.models.module_d import DocumentExtraction, DocumentProcessingJob
from app.models.module_e import (
    EvidencePolicyVersion, EvidenceValidationRun, ValidationRuleCatalog,
    ValidationRuleVersion, EvidencePolicyRuleVersion, EValidationRunStatus,
    ERuleSeverity
)

from app.services.validation import prepare_validation_run
from app.services.validation_rules import evaluate_validation_run
from app.services.feature_snapshot import generate_feature_snapshot

from app.services.ml.feature_builder import FeatureBuilderContext, build_ml_features
from app.services.ml.label_policy import LabelContext, generate_contestability_label
from app.services.ml.example_materializer import materialize_example_from_context, MLExample

logger = logging.getLogger(__name__)

class ScenarioFamily(str, Enum):
    STRONG_CONTESTABLE = "STRONG_CONTESTABLE"
    WEAK_EVIDENCE = "WEAK_EVIDENCE"
    CONTRADICTORY = "CONTRADICTORY"
    QUALITY_DEGRADED = "QUALITY_DEGRADED"
    PARTIAL_REFUND = "PARTIAL_REFUND"
    BORDERLINE = "BORDERLINE"
    DEADLINE_STATUS_BLOCK = "DEADLINE_STATUS_BLOCK"


class SyntheticBenchmarkGenerator:
    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)
        self.seed = seed
        self.generation_timestamp = datetime.now(timezone.utc)
        
        # Initial scenario weights
        self.scenario_weights = {
            ScenarioFamily.STRONG_CONTESTABLE: 0.30,
            ScenarioFamily.WEAK_EVIDENCE: 0.20,
            ScenarioFamily.CONTRADICTORY: 0.15,
            ScenarioFamily.QUALITY_DEGRADED: 0.10,
            ScenarioFamily.PARTIAL_REFUND: 0.10,
            ScenarioFamily.BORDERLINE: 0.10,
            ScenarioFamily.DEADLINE_STATUS_BLOCK: 0.05,
        }

    def _setup_base_policies(self, db: Session):
        """Ensures that the required policies exist in the database."""
        policy = db.query(EvidencePolicyVersion).filter_by(
            payment_network="Visa", reason_code="10.4", phase="chargeback"
        ).first()
        
        if not policy:
            policy = EvidencePolicyVersion(
                payment_network="Visa",
                reason_code="10.4",
                phase="chargeback",
                version=1,
                effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)
            )
            db.add(policy)
            db.commit()
            
            # Setup rule catalogs
            rules = [
                ("REQUIRED_EVIDENCE_PRESENT", ERuleSeverity.ERROR),
                ("INVOICE_AMOUNT_MATCH", ERuleSeverity.ERROR),
                ("DELIVERY_BEFORE_DISPUTE", ERuleSeverity.ERROR),
                ("OCR_CONFIDENCE_ACCEPTABLE", ERuleSeverity.WARN),
            ]
            
            for code, sev in rules:
                cat = db.query(ValidationRuleCatalog).filter_by(rule_code=code).first()
                if not cat:
                    cat = ValidationRuleCatalog(
                        rule_code=code,
                        category="SYNTHETIC",
                        description=f"Synthetic {code}",
                        severity_default=sev
                    )
                    db.add(cat)
                    db.commit()
                    
                v = ValidationRuleVersion(
                    rule_id=cat.rule_id,
                    version=1,
                    parameters_json={"amount_tolerance_minor": 0, "ocr_threshold": 0.8},
                    effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
                    checksum="synth_checksum"
                )
                db.add(v)
                db.commit()
                
                db.add(EvidencePolicyRuleVersion(
                    policy_version_id=policy.policy_version_id,
                    rule_id=cat.rule_id,
                    rule_version_id=v.id
                ))
            
            db.add(EvidenceRequirement(
                reason_code="10.4",
                evidence_type="INVOICE",
                requirement_level="REQUIRED",
                active=True
            ))
            db.commit()

    def _generate_case_attributes(self, scenario: ScenarioFamily) -> Dict[str, Any]:
        """Generates deterministic A-E source data based on the scenario."""
        # Baseline deterministic values
        now = self.generation_timestamp
        dispute_time = now - timedelta(days=10)
        
        attrs = {
            "merchant_id": f"merch_e2e_{self.rng.getrandbits(64)}",
            "group": f"group_{self.rng.randint(1, 100)}",
            "amount": 5000,
            "dispute_time": dispute_time,
            "delivery_time": dispute_time - timedelta(days=2),
            "invoice_amount": 5000,
            "ocr_confidence": 0.95,
            "refund_amount": 0,
            "has_invoice": True,
            "dispute_status": "open",
            "deadline": dispute_time + timedelta(days=20)
        }
        
        if scenario == ScenarioFamily.STRONG_CONTESTABLE:
            pass
        elif scenario == ScenarioFamily.WEAK_EVIDENCE:
            attrs["has_invoice"] = False
        elif scenario == ScenarioFamily.CONTRADICTORY:
            attrs["invoice_amount"] = 3000  # Mismatch
        elif scenario == ScenarioFamily.QUALITY_DEGRADED:
            attrs["ocr_confidence"] = 0.60
        elif scenario == ScenarioFamily.PARTIAL_REFUND:
            attrs["refund_amount"] = 2000
        elif scenario == ScenarioFamily.BORDERLINE:
            # Just on the edge of delivery timeline (exactly at dispute time)
            attrs["delivery_time"] = dispute_time
        elif scenario == ScenarioFamily.DEADLINE_STATUS_BLOCK:
            attrs["dispute_status"] = "lost" # structurally un-contestable
            
        return attrs

    def _build_database_records(self, db: Session, attrs: Dict[str, Any]) -> uuid.UUID:
        merchant = db.query(Merchant).filter_by(external_merchant_id=attrs["merchant_id"]).first()
        if not merchant:
            merchant = Merchant(
                external_merchant_id=attrs["merchant_id"],
                name="Synthetic Merchant"
            )
            db.add(merchant)
            db.flush()
        
        case_id = uuid.UUID(int=self.rng.getrandbits(128))
        case = Case(
            case_id=case_id,
            merchant_id=merchant.merchant_id,
            external_dispute_id=f"disp_synth_{self.rng.getrandbits(64)}",
            source="synthetic"
        )
        db.add(case)
        db.commit()
        
        db.add(Dispute(
            case_id=case.case_id,
            external_dispute_id=case.external_dispute_id,
            payment_id="pay_synth_1",
            amount_minor=attrs["amount"],
            currency="USD",
            reason_code="10.4",
            phase="chargeback",
            status=attrs["dispute_status"],
            dispute_created_at=attrs["dispute_time"],
            respond_by=attrs["deadline"]
        ))
        
        db.add(Payment(
            case_id=case.case_id,
            external_payment_id="pay_synth_1",
            amount_minor=attrs["amount"],
            currency="USD",
            status="captured",
            method="card",
            network="Visa",
            fetched_at=self.generation_timestamp,
            created_at_source=attrs["dispute_time"] - timedelta(days=10)
        ))
        
        db.add(Order(
            case_id=case.case_id,
            external_order_id="ord_synth_1",
            order_amount_minor=attrs["amount"],
            fetched_at=self.generation_timestamp
        ))
        
        db.add(Shipment(
            case_id=case.case_id,
            external_order_id="ord_synth_1",
            shipment_id="ship_synth_1",
            delivery_at=attrs["delivery_time"]
        ))
        
        if attrs["refund_amount"] > 0:
            db.add(Refund(
                case_id=case.case_id,
                external_refund_id="ref_synth_1",
                external_payment_id="pay_synth_1",
                refund_amount_minor=attrs["refund_amount"],
                status="succeeded"
            ))
            
        if attrs["has_invoice"]:
            doc = EvidenceDocument(
                case_id=case.case_id,
                merchant_id=case.merchant_id,
                evidence_type="INVOICE",
                original_filename="inv.pdf",
                mime_type="application/pdf",
                file_size_bytes=1024,
                sha256="synth_hash",
                object_key=f"obj_{self.rng.getrandbits(64)}",
                processing_status=EvidenceProcessingStatus.EXTRACTED,
                scan_status=ScanStatus.CLEAN
            )
            db.add(doc)
            db.flush()
            
            doc_job = DocumentProcessingJob(
                document_id=doc.document_id,
                case_id=case.case_id,
                merchant_id=merchant.merchant_id,
                job_type="EXTRACTION",
                idempotency_key=str(uuid.UUID(int=self.rng.getrandbits(128))),
                pipeline_version="v1",
                status="COMPLETED"
            )
            db.add(doc_job)
            db.flush()
            
            # Deterministic adapter for Module D
            ext = DocumentExtraction(
                job_id=doc_job.job_id,
                document_id=doc.document_id,
                case_id=case.case_id,
                expected_evidence_type="INVOICE",
                type_match_status="MATCH",
                extraction_status="COMPLETED",
                schema_version="v1",
                extracted_json={"amount": attrs["invoice_amount"]},
                overall_confidence=attrs["ocr_confidence"]
            )
            db.add(ext)
            
        case.processing_state = ProcessingState.D_INTELLIGENCE_READY
        db.commit()
        return case.case_id

    def _execute_module_e_pipeline(self, db: Session, case_id: uuid.UUID) -> EvidenceValidationRun:
        case = db.query(Case).filter_by(case_id=case_id).first()
        run, _ = prepare_validation_run(db, case_id, case.merchant_id)
        
        # Claim
        case.processing_state = ProcessingState.E_VALIDATING
        run.status = EValidationRunStatus.RUNNING
        db.commit()
        
        # Evaluate rules deterministically
        results, assessments, links = evaluate_validation_run(db, run)
        snapshot = generate_feature_snapshot(db, run, results, assessments, links)
        
        # Persist
        db.add_all(results)
        db.add_all(assessments)
        db.add_all(links)
        if snapshot:
            db.add(snapshot)
            
        case.processing_state = ProcessingState.FEATURE_READY
        run.status = EValidationRunStatus.COMPLETED
        db.commit()
        return run

    def _generate_ml_example(self, db: Session, case_id: uuid.UUID, run: EvidenceValidationRun) -> MLExample:
        # Load F1/F2 context
        case = db.query(Case).filter_by(case_id=case_id).first()
        dispute = db.query(Dispute).filter_by(case_id=case_id).first()
        
        from app.models.module_e import EvidenceValidationResult, EvidenceRequirementAssessment
        results = db.query(EvidenceValidationResult).filter_by(validation_run_id=run.id).all()
        assessments = db.query(EvidenceRequirementAssessment).filter_by(validation_run_id=run.id).all()
        
        context = FeatureBuilderContext(
            case=case,
            dispute=dispute,
            payments=db.query(Payment).filter_by(case_id=case_id).all(),
            orders=db.query(Order).filter_by(case_id=case_id).all(),
            shipments=db.query(Shipment).filter_by(case_id=case_id).all(),
            refunds=db.query(Refund).filter_by(case_id=case_id).all(),
            documents=db.query(EvidenceDocument).filter_by(case_id=case_id).all(),
            extractions=db.query(DocumentExtraction).filter_by(case_id=case_id).all(),
            extracted_fields=[],
            quality_assessments=[],
            run=run,
            assessments=assessments,
            results=results,
            links=[]
        )
        
        return materialize_example_from_context(context, self.generation_timestamp)

    def generate_single_example(self, db: Session, scenario: ScenarioFamily) -> tuple[MLExample, Dict[str, Any]]:
        attrs = self._generate_case_attributes(scenario)
        case_id = self._build_database_records(db, attrs)
        run = self._execute_module_e_pipeline(db, case_id)
        example = self._generate_ml_example(db, case_id, run)
        return example, attrs

    def generate_dataset(self, size: int, output_path: str = None) -> tuple[List[Dict[str, Any]], Dict[str, float]]:
        # Reset local RNG
        self.rng = random.Random(self.seed)
        
        db = SessionLocal()
        self._setup_base_policies(db)
        
        scenarios = list(self.scenario_weights.keys())
        weights = list(self.scenario_weights.values())
        
        examples = []
        while len(examples) < size:
            chosen_scenario = self.rng.choices(scenarios, weights=weights, k=1)[0]
            try:
                example, attrs = self.generate_single_example(db, chosen_scenario)
                ex_dict = {
                    "example_id": str(uuid.UUID(int=self.rng.getrandbits(128))),
                    "case_id": example.case_id,
                    "prediction_timestamp": example.prediction_timestamp.isoformat(),
                    "synthetic_customer_group": attrs["group"],
                    "reason_code": "10.4",
                    "feature_schema_version": example.feature_schema_version,
                    "features": example.features,
                    "label_schema_version": example.label_schema_version,
                    "label": example.label,
                    "label_rationale": example.label_rationale,
                    "feature_hash": example.feature_hash,
                    "scenario_family": chosen_scenario.value
                }
                examples.append(ex_dict)
            except Exception as e:
                logger.error(f"Failed to generate example: {e}")
                db.rollback()
                
        db.close()
        
        # Sort lexicographically by case_id for exact determinism
        examples.sort(key=lambda x: x["case_id"])
        
        # Calculate distribution
        total = len(examples)
        if total == 0:
            return examples, {"positive_rate": 0, "negative_rate": 0}
            
        positives = sum(1 for e in examples if e["label"] == 1)
        negatives = total - positives
        dist = {
            "positive_rate": positives / total,
            "negative_rate": negatives / total
        }
        
        if output_path:
            with open(output_path, 'w') as f:
                for e in examples:
                    f.write(json.dumps(e) + '\n')
                    
        return examples, dist
        

