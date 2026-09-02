from fastapi import Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.shared import Merchant, AppUser
import uuid

# Minimal Auth Dependency for Tenant Isolation
def get_current_merchant(db: Session = Depends(get_db)):
    # For MVP foundation, we simulate a logged-in user context.
    # In a real app, this would verify a JWT and fetch the user/merchant.
    # Here we just fetch the first active merchant to prove the abstraction works.
    merchant = db.query(Merchant).filter(Merchant.is_active == True).first()
    if not merchant:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials / No merchant found",
        )
    return merchant

# MVP Authentication Dependency for Reviewer Identity
def get_current_user(
    x_user_id: uuid.UUID = Header(..., alias="X-User-Id", description="Mocked authenticated user ID for MVP"),
    merchant: Merchant = Depends(get_current_merchant),
    db: Session = Depends(get_db)
):
    user = db.query(AppUser).filter(AppUser.user_id == x_user_id).first()
    
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials / User not found or inactive",
        )
        
    if user.merchant_id != merchant.merchant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to the authenticated merchant",
        )
        
    return user

def require_role(allowed_roles: list[str]):
    def role_checker(current_user: AppUser = Depends(get_current_user)):
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User role not authorized for this action"
            )
        return current_user
    return role_checker
