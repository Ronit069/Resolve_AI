import dataclasses
import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.models.shared import Case
from app.models.module_a import Dispute
from app.models.module_b import Payment, Order, Shipment, Refund
from app.models.module_c import EvidenceDocument
from app.models.module_d import DocumentExtraction, ExtractedField, DocumentQualityAssessment
from app.models.module_e import EvidenceValidationRun, CaseFeatureSnapshot
from app.services.ml.feature_builder import FeatureBuilderContext, build_ml_features


def generate_feature_snapshot(db: Session, run: EvidenceValidationRun, results: list, assessments: list, links: list) -> CaseFeatureSnapshot:
    """
    Generates the immutable CaseFeatureSnapshot by computing the real
    ml_features_v1 schema (app.services.ml.feature_builder.build_ml_features)
    from this case's own Module A-D records plus this validation run's own
    E-stage outputs (results/assessments/links) -- the same deterministic
    builder the offline training pipeline uses to materialize examples, so
    live and offline features share one definition.
    """
    case_id = run.case_id
    case = db.query(Case).filter(Case.case_id == case_id).first()
    dispute = db.query(Dispute).filter(Dispute.case_id == case_id).first()

    documents = db.query(EvidenceDocument).filter(EvidenceDocument.case_id == case_id).all()
    document_ids = [d.document_id for d in documents]

    extractions = db.query(DocumentExtraction).filter(DocumentExtraction.case_id == case_id).all()
    extracted_fields = (
        db.query(ExtractedField).filter(ExtractedField.document_id.in_(document_ids)).all()
        if document_ids else []
    )
    quality_assessments = (
        db.query(DocumentQualityAssessment).filter(DocumentQualityAssessment.document_id.in_(document_ids)).all()
        if document_ids else []
    )

    context = FeatureBuilderContext(
        case=case,
        dispute=dispute,
        payments=db.query(Payment).filter(Payment.case_id == case_id).all(),
        orders=db.query(Order).filter(Order.case_id == case_id).all(),
        shipments=db.query(Shipment).filter(Shipment.case_id == case_id).all(),
        refunds=db.query(Refund).filter(Refund.case_id == case_id).all(),
        documents=documents,
        extractions=extractions,
        extracted_fields=extracted_fields,
        quality_assessments=quality_assessments,
        run=run,
        assessments=assessments,
        results=results,
        links=links,
    )

    prediction_timestamp = datetime.now(timezone.utc)
    ml_features = build_ml_features(context, prediction_timestamp)
    features = dataclasses.asdict(ml_features)
    feature_schema_version = features.pop("version")

    # Matches example_materializer.py's own hashing convention exactly, so
    # a live snapshot and an offline-materialized example of the same
    # underlying features hash identically.
    canonical_json = json.dumps(features, sort_keys=True, separators=(',', ':'), default=str)
    feature_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    # Mark older snapshots as not current
    db.query(CaseFeatureSnapshot).filter(
        CaseFeatureSnapshot.case_id == case_id,
        CaseFeatureSnapshot.is_current == True
    ).update({"is_current": False})

    snapshot = CaseFeatureSnapshot(
        case_id=case_id,
        validation_run_id=run.id,
        feature_schema_version=feature_schema_version,
        features_json=features,
        feature_hash=feature_hash,
        is_current=True
    )

    return snapshot
