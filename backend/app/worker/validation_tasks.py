import uuid
import logging
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.core.database import SessionLocal
from app.worker.celery_app import celery_app
from app.models.module_e import EvidenceValidationRun, EValidationRunStatus
from app.models.shared import Case, ProcessingState
from app.services.validation_rules import evaluate_validation_run
from app.services.feature_snapshot import generate_feature_snapshot
from sqlalchemy import text

logger = logging.getLogger(__name__)

@celery_app.task(bind=True, max_retries=3)
def execute_evidence_validation(self, validation_run_id: str):
    db: Session = SessionLocal()
    
    # 1. Execution Claim Transaction (Short Transaction)
    try:
        run_uuid = uuid.UUID(validation_run_id)
        
        query = db.query(EvidenceValidationRun)
        if db.bind.dialect.name != "sqlite":
            query = query.with_for_update(skip_locked=True)
            
        run = query.filter(EvidenceValidationRun.id == run_uuid).first()
        
        if not run:
            logger.info(f"Validation run {validation_run_id} is locked by another worker. Skipping.")
            return
            
        if run.status == EValidationRunStatus.COMPLETED:
            logger.info(f"Validation run {validation_run_id} is already {run.status.value}. Skipping.")
            db.rollback()
            return
            
        case = db.query(Case).filter(Case.case_id == run.case_id).first()
        if not case:
            logger.error(f"Case {run.case_id} not found for validation run {validation_run_id}.")
            db.rollback()
            return
            
        # Eligibility Gate Check (E-01)
        if case.processing_state != ProcessingState.D_INTELLIGENCE_READY:
            logger.warning(f"Case {case.case_id} not in D_INTELLIGENCE_READY status. Found: {case.processing_state}. Failing run.")
            run.status = EValidationRunStatus.FAILED
            db.commit()
            return
            
        # Claim successful
        case.processing_state = ProcessingState.E_VALIDATING
        run.status = EValidationRunStatus.RUNNING
        
        db.commit() # Releases the lock
        
    except SQLAlchemyError as e:
        logger.error(f"Database error during claim for {validation_run_id}: {e}")
        db.rollback()
        raise self.retry(exc=e)
    finally:
        db.close()
        
    # 2. Deterministic Computation (Outside lock)
    db = SessionLocal()
    try:
        run = db.query(EvidenceValidationRun).filter(EvidenceValidationRun.id == run_uuid).first()
        if not run:
            return
            
        results, assessments, links = evaluate_validation_run(db, run)
        feature_snapshot = generate_feature_snapshot(db, run, results, assessments, links)
        
    except Exception as e:
        logger.error(f"System failure during computation for {validation_run_id}: {e}")
        db.close()
        # Fallback for system failure
        with SessionLocal() as fail_db:
            failed_run = fail_db.query(EvidenceValidationRun).get(run_uuid)
            if failed_run:
                failed_run.status = EValidationRunStatus.FAILED
                
            failed_case = fail_db.query(Case).get(failed_run.case_id) if failed_run else None
            if failed_case and failed_case.processing_state == ProcessingState.E_VALIDATING:
                failed_case.processing_state = ProcessingState.D_INTELLIGENCE_READY
                
            fail_db.commit()
        raise self.retry(exc=e)
    finally:
        db.close()
        
    # 3. Atomic Persistence Transaction
    db = SessionLocal()
    try:
        query = db.query(EvidenceValidationRun)
        if db.bind.dialect.name != "sqlite":
            query = query.with_for_update()
            
        run = query.filter(EvidenceValidationRun.id == run_uuid).first()
        
        if not run:
            db.rollback()
            return
            
        if run.status != EValidationRunStatus.RUNNING:
            logger.info(f"Validation run {validation_run_id} is no longer RUNNING. Skipping persistence.")
            db.rollback()
            return
            
        case = db.query(Case).filter(Case.case_id == run.case_id).first()
        
        db.add_all(results)
        db.add_all(assessments)
        db.add_all(links)
        if feature_snapshot:
            db.add(feature_snapshot)
            
        case.processing_state = ProcessingState.FEATURE_READY
        run.status = EValidationRunStatus.COMPLETED
        
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        with SessionLocal() as fail_db:
            failed_run = fail_db.query(EvidenceValidationRun).get(run_uuid)
            if failed_run:
                failed_run.status = EValidationRunStatus.FAILED
                
            failed_case = fail_db.query(Case).get(failed_run.case_id) if failed_run else None
            if failed_case and failed_case.processing_state == ProcessingState.E_VALIDATING:
                failed_case.processing_state = ProcessingState.D_INTELLIGENCE_READY
                
            fail_db.commit()
        raise self.retry(exc=e)
    finally:
        db.close()
