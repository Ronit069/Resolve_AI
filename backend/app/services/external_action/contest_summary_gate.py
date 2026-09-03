"""
H-12 Summary Validation — mandatory safety gate at the external-action boundary.

Follows the same pure read-and-decide gate pattern established by H-06's
deadline_gate.py, H-07's contest_amount_gate.py, H-08's
evidence_mapping_gate.py, and H-10's contest_submission_action_gate.py:
no endpoint, no external HTTP call, no persistence.

Validates a trusted caller-supplied candidate contest summary string
against SUMMARY_MAX_LENGTH and against the freshest local claim-support/
guardrail state on the case's current GeneratedDraft. Does not treat
GeneratedDraft.summary as the authoritative final reviewer-edited value —
only the length ceiling and the guardrail/claim signals are read from the
DB; the text under validation always comes from the caller (the H-07
precedent). Unsupported or conflicting claims hard-block submission
("force human review") rather than being automatically removed,
rewritten, or surgically edited.
"""

import dataclasses
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.module_g import GeneratedDraft, DraftClaim, GuardrailStatus, SupportStatus


class ContestSummaryGateErrorCode:
    CONTEST_SUMMARY_INVALID = "CONTEST_SUMMARY_INVALID"


@dataclasses.dataclass(frozen=True)
class ContestSummaryGateResult:
    allowed: bool
    error_code: Optional[str]
    reason: Optional[str]
    candidate_summary_length: int
    max_length: int
    case_id: UUID
    draft_id: Optional[UUID]
    guardrail_status: Optional[str]
    unsupported_claim_count: int
    checked_at: datetime


def validate_contest_summary(
    db: Session,
    case_id: UUID,
    candidate_summary: Optional[str],
    current_time: Optional[datetime] = None,
) -> ContestSummaryGateResult:
    """
    H-12 mandatory safety gate.

    MUST be invoked immediately before any external Razorpay contest
    action, once that pipeline exists. Accepts candidate_summary as the
    value under validation, but never accepts a caller-supplied draft,
    guardrail, or claim-support state — those are always read fresh from
    the case's current GeneratedDraft.

    Blocks when:
      - no current GeneratedDraft exists for the case,
      - candidate_summary is None, empty, or whitespace-only,
      - candidate_summary exceeds SUMMARY_MAX_LENGTH characters,
      - the current draft's guardrail_status is not PASS, or
      - any DraftClaim on the current draft has support_status
        UNSUPPORTED or CONFLICT.
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    elif current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    max_length = settings.SUMMARY_MAX_LENGTH
    candidate_length = len(candidate_summary) if candidate_summary is not None else 0

    draft = (
        db.query(GeneratedDraft)
        .filter(GeneratedDraft.case_id == case_id, GeneratedDraft.is_current == True)
        .populate_existing()
        .first()
    )

    if draft is None:
        return ContestSummaryGateResult(
            allowed=False,
            error_code=ContestSummaryGateErrorCode.CONTEST_SUMMARY_INVALID,
            reason="No current draft found for case",
            candidate_summary_length=candidate_length,
            max_length=max_length,
            case_id=case_id,
            draft_id=None,
            guardrail_status=None,
            unsupported_claim_count=0,
            checked_at=current_time,
        )

    if candidate_summary is None or candidate_summary.strip() == "":
        return ContestSummaryGateResult(
            allowed=False,
            error_code=ContestSummaryGateErrorCode.CONTEST_SUMMARY_INVALID,
            reason="Summary is empty",
            candidate_summary_length=candidate_length,
            max_length=max_length,
            case_id=case_id,
            draft_id=draft.id,
            guardrail_status=draft.guardrail_status.value,
            unsupported_claim_count=0,
            checked_at=current_time,
        )

    if candidate_length > max_length:
        return ContestSummaryGateResult(
            allowed=False,
            error_code=ContestSummaryGateErrorCode.CONTEST_SUMMARY_INVALID,
            reason=f"Summary length {candidate_length} exceeds max {max_length}",
            candidate_summary_length=candidate_length,
            max_length=max_length,
            case_id=case_id,
            draft_id=draft.id,
            guardrail_status=draft.guardrail_status.value,
            unsupported_claim_count=0,
            checked_at=current_time,
        )

    if draft.guardrail_status != GuardrailStatus.PASS:
        return ContestSummaryGateResult(
            allowed=False,
            error_code=ContestSummaryGateErrorCode.CONTEST_SUMMARY_INVALID,
            reason=f"Draft guardrail_status is {draft.guardrail_status.value}, not PASS",
            candidate_summary_length=candidate_length,
            max_length=max_length,
            case_id=case_id,
            draft_id=draft.id,
            guardrail_status=draft.guardrail_status.value,
            unsupported_claim_count=0,
            checked_at=current_time,
        )

    claims = (
        db.query(DraftClaim)
        .filter(DraftClaim.draft_id == draft.id)
        .populate_existing()
        .all()
    )
    unsupported_claim_count = sum(
        1 for c in claims if c.support_status in (SupportStatus.UNSUPPORTED, SupportStatus.CONFLICT)
    )

    if unsupported_claim_count > 0:
        return ContestSummaryGateResult(
            allowed=False,
            error_code=ContestSummaryGateErrorCode.CONTEST_SUMMARY_INVALID,
            reason=f"{unsupported_claim_count} claim(s) are unsupported or conflicting",
            candidate_summary_length=candidate_length,
            max_length=max_length,
            case_id=case_id,
            draft_id=draft.id,
            guardrail_status=draft.guardrail_status.value,
            unsupported_claim_count=unsupported_claim_count,
            checked_at=current_time,
        )

    return ContestSummaryGateResult(
        allowed=True,
        error_code=None,
        reason=None,
        candidate_summary_length=candidate_length,
        max_length=max_length,
        case_id=case_id,
        draft_id=draft.id,
        guardrail_status=draft.guardrail_status.value,
        unsupported_claim_count=0,
        checked_at=current_time,
    )
