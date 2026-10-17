from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token, verify_password
from app.models.shared import AppUser
from app.schemas.auth import LoginRequest, LoginResponse

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """
    Real authentication: issues a signed JWT access token for an
    AppUser with a matching email + password. Replaces the X-User-Id
    header as the credential everything else (app/api/deps.py) checks.

    A user with no password_hash set (every row seeded before this
    endpoint existed) cannot log in here -- there is no "empty
    password" bypass. Set one directly (see
    app/core/security.py::hash_password) before they can.
    """
    user = db.query(AppUser).filter(AppUser.email == payload.email).first()

    invalid_credentials = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect email or password",
    )

    if not user or not user.is_active or not user.password_hash:
        raise invalid_credentials

    if not verify_password(payload.password, user.password_hash):
        raise invalid_credentials

    access_token = create_access_token(user.user_id)
    return LoginResponse(access_token=access_token, user_id=str(user.user_id), role=user.role.value)
