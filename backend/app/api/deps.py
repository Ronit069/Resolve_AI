from typing import Optional
from fastapi import Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.core.security import InvalidAccessToken, decode_access_token
from app.models.shared import Merchant, AppUser
import uuid

def get_current_user(
    authorization: Optional[str] = Header(
        None, description="Real credential: 'Bearer <JWT access token>' from POST /api/v1/auth/login"
    ),
    x_user_id: Optional[uuid.UUID] = Header(
        None, alias="X-User-Id",
        description="Dev-only convenience header -- disabled whenever ENVIRONMENT is production-like",
    ),
    db: Session = Depends(get_db)
):
    user_id: Optional[uuid.UUID] = None

    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authorization header must be 'Bearer <token>'",
            )
        try:
            user_id = decode_access_token(token)
        except InvalidAccessToken:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired access token",
            )
    elif x_user_id is not None:
        if settings.is_production_like():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="X-User-Id header auth is disabled outside development; sign in via "
                "POST /api/v1/auth/login and send Authorization: Bearer <token> instead",
            )
        user_id = x_user_id
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing credentials: send Authorization: Bearer <token>",
        )

    user = db.query(AppUser).filter(AppUser.user_id == user_id).first()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials / User not found or inactive",
        )

    return user

# Minimal Auth Dependency for Tenant Isolation
def get_current_merchant(
    current_user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Derive the merchant directly from the authenticated user
    merchant = db.query(Merchant).filter(
        Merchant.merchant_id == current_user.merchant_id,
        Merchant.is_active == True
    ).first()
    
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials / No merchant found",
        )
    return merchant

def require_role(allowed_roles: list[str]):
    def role_checker(current_user: AppUser = Depends(get_current_user)):
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User role not authorized for this action"
            )
        return current_user
    return role_checker
