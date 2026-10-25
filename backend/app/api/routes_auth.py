from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password, verify_password
from app.db.database import get_db
from app.db.models import Organization, User
from app.schemas.schemas import RegisterRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    if await db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(400, "Email already registered")

    org = await db.scalar(select(Organization).where(Organization.name == body.org_name))
    role = "member"
    if not org:
        org = Organization(name=body.org_name)
        db.add(org)
        await db.flush()
        role = "admin"  # first user in an org becomes its admin

    user = User(org_id=org.id, email=body.email,
                hashed_password=hash_password(body.password), role=role)
    db.add(user)
    await db.commit()
    return TokenResponse(access_token=create_access_token(user.id, org.id, user.role))


@router.post("/login", response_model=TokenResponse)
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.email == form.username))
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(401, "Invalid credentials")
    return TokenResponse(access_token=create_access_token(user.id, user.org_id, user.role))
