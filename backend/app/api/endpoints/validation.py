from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.deps import get_current_merchant
from app.models.shared import Merchant
from app.services.validation import (
    prepare_validation_run,
    PolicyInputUnavailable,
    PolicyNotFound,
    PolicyAmbiguous
)
from app.worker.validation_tasks import execute_evidence_validation

router = APIRouter()

@router.post("/{case_id}/validate-evidence", status_code=status.HTTP_202_ACCEPTED)
def validate_evidence(
    case_id: str,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant)
):
    try:
        run, is_reused = prepare_validation_run(
            db=db,
            case_id=case_id,
            merchant_id=str(current_merchant.merchant_id)
        )
        
        if not is_reused:
            execute_evidence_validation.delay(str(run.id))
            
        return {
            "validation_run_id": str(run.id),
            "evidence_version": run.evidence_version,
            "idempotent_reused": is_reused,
            "status": run.status.value
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except PolicyInputUnavailable as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "POLICY_INPUT_UNAVAILABLE", "message": str(e)}
        )
    except PolicyNotFound as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "POLICY_NOT_FOUND", "message": str(e)}
        )
    except PolicyAmbiguous as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "POLICY_AMBIGUOUS", "message": str(e)}
        )
