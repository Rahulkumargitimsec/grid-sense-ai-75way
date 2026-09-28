import os
import asyncio
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable

import jwt
from fastapi import Cookie, Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, add_missing_columns, engine, get_db
from .models import Role, User

app = FastAPI(title="GridSense AI API", version="0.3.0")

allowed_origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
JWT_SECRET = os.getenv("JWT_SECRET", "change-this-development-secret")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "15"))
REFRESH_TOKEN_DAYS = int(os.getenv("REFRESH_TOKEN_DAYS", "7"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
REFRESH_COOKIE_NAME = "gridsense_refresh_token"

password_hash = PasswordHash.recommended()
bearer_scheme = HTTPBearer(auto_error=False)
revoked_refresh_tokens: set[str] = set()

DEMO_USERS = (
    ("usr_super_admin", "admin@gridsense.ai", "Asha Mehta", "super_admin", "GridSenseAdmin!2025"),
    ("usr_grid_operator", "operator@gridsense.ai", "Grid Operator", "grid_operator", "GridSenseOperator!2025"),
    ("usr_research_analyst", "analyst@gridsense.ai", "Research Analyst", "research_analyst", "GridSenseAnalyst!2025"),
)
ROLE_DESCRIPTIONS = {
    "super_admin": "Manages users, models, datasets, and system controls.",
    "grid_operator": "Monitors demand, forecasts, peaks, alerts, and recommendations.",
    "research_analyst": "Reviews datasets, model training, comparisons, and research.",
}


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    role: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


def seed_database() -> None:
    Base.metadata.create_all(bind=engine)
    add_missing_columns()
    with SessionLocal() as db:
        roles = {}
        for role_name, description in ROLE_DESCRIPTIONS.items():
            role = db.scalar(select(Role).where(Role.name == role_name))
            if role is None:
                role = Role(name=role_name, description=description)
                db.add(role)
                db.flush()
            roles[role_name] = role
        for user_id, email, display_name, role_name, password in DEMO_USERS:
            user = db.scalar(select(User).where(User.email == email))
            if user is None:
                db.add(User(
                    id=user_id,
                    email=email,
                    display_name=display_name,
                    hashed_password=password_hash.hash(password),
                    role_id=roles[role_name].id,
                ))
        db.commit()


@app.on_event("startup")
def on_startup() -> None:
    seed_database()


@app.on_event("startup")
async def start_data_sync() -> None:
    from .services import collection

    if collection.sync_enabled():
        app.state.data_sync_task = asyncio.create_task(collection.run_scheduler())


def _public_user(user: User) -> UserResponse:
    return UserResponse(id=user.id, email=user.email, display_name=user.display_name, role=user.role.name)


def _create_token(user: User, token_type: str, lifetime: timedelta) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": user.id,
        "email": user.email,
        "role": user.role.name,
        "type": token_type,
        "iat": now,
        "exp": now + lifetime,
        "jti": secrets.token_urlsafe(24),
    }
    return jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc
    if payload.get("type") != expected_type:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    if expected_type == "refresh" and payload.get("jti") in revoked_refresh_tokens:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token has been revoked")
    return payload


def _user_from_payload(payload: dict, db: Session) -> User:
    user = db.scalar(select(User).where(User.id == payload.get("sub")))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or unavailable")
    return user


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=REFRESH_TOKEN_DAYS * 24 * 60 * 60,
        path="/",
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    payload = _decode_token(credentials.credentials, "access")
    return _user_from_payload(payload, db)


def require_active_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")
    return user


def require_roles(*allowed_roles: str) -> Callable:
    def dependency(user: User = Depends(require_active_user)) -> User:
        if user.role.name not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return dependency


from .api.v1.routes import router as v1_router

app.include_router(v1_router)


@app.get("/health", tags=["system"])
def health_check(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(select(Role).limit(1))
    return {"status": "ok", "service": "gridsense-api", "database": "ok"}


@app.post("/api/auth/login", response_model=LoginResponse, tags=["auth"])
def login(request: LoginRequest, response: Response, db: Session = Depends(get_db)) -> LoginResponse:
    user = db.scalar(select(User).where(User.email == request.email.strip().lower()))
    if user is None or not user.is_active or not password_hash.verify(request.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    access_token = _create_token(user, "access", timedelta(minutes=ACCESS_TOKEN_MINUTES))
    refresh_token = _create_token(user, "refresh", timedelta(days=REFRESH_TOKEN_DAYS))
    _set_refresh_cookie(response, refresh_token)
    return LoginResponse(access_token=access_token, user=_public_user(user))


@app.post("/api/auth/refresh", response_model=LoginResponse, tags=["auth"])
def refresh(response: Response, refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME), db: Session = Depends(get_db)) -> LoginResponse:
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token required")
    payload = _decode_token(refresh_token, "refresh")
    revoked_refresh_tokens.add(payload["jti"])
    user = _user_from_payload(payload, db)
    access_token = _create_token(user, "access", timedelta(minutes=ACCESS_TOKEN_MINUTES))
    rotated_refresh_token = _create_token(user, "refresh", timedelta(days=REFRESH_TOKEN_DAYS))
    _set_refresh_cookie(response, rotated_refresh_token)
    return LoginResponse(access_token=access_token, user=_public_user(user))


@app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
def logout(response: Response, refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME)) -> None:
    if refresh_token:
        try:
            payload = _decode_token(refresh_token, "refresh")
            revoked_refresh_tokens.add(payload.get("jti", ""))
        except HTTPException:
            pass
    response.delete_cookie(key=REFRESH_COOKIE_NAME, path="/")


@app.get("/api/auth/me", response_model=UserResponse, tags=["auth"])
def me(user: User = Depends(require_active_user)) -> UserResponse:
    return _public_user(user)


@app.get("/api/protected-example", tags=["protected"])
def protected_example(user: User = Depends(require_active_user)) -> dict[str, str]:
    return {"message": f"Authenticated request accepted for {user.display_name}", "role": user.role.name}


@app.get("/api/admin-example", tags=["protected"])
def admin_example(user: User = Depends(require_roles("super_admin"))) -> dict[str, str]:
    return {"message": "Super Admin authorization accepted", "role": user.role.name}
