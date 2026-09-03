from app.core.config import settings
from app.models.module_a import Dispute
from app.models.module_f import RiskPrediction
from app.models.module_h import ReviewActionEnum


def requires_dual_approval(action: ReviewActionEnum, prediction: RiskPrediction, dispute: Dispute) -> bool:
    """
    H-05 Dual Control trigger condition, per the frozen Product Owner decision:
      - Only APPROVE_CONTEST and APPROVE_ACCEPT are gated.
      - Dispute amount >= DUAL_CONTROL_AMOUNT_THRESHOLD_MINOR (INR paise) requires dual approval.
      - APPROVE_CONTEST while prediction.hard_block is True requires dual approval
        regardless of amount (this is the only action H-18 treats as a hard-block override).
    """
    if action not in (ReviewActionEnum.APPROVE_CONTEST, ReviewActionEnum.APPROVE_ACCEPT):
        return False

    if dispute.amount_minor >= settings.DUAL_CONTROL_AMOUNT_THRESHOLD_MINOR:
        return True

    if action == ReviewActionEnum.APPROVE_CONTEST and prediction.hard_block:
        return True

    return False
