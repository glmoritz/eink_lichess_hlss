"""
API routes for Lichess account management.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from hlss.database import get_db
from hlss.models import LichessAccount
from hlss.schemas import (
    LichessAccountCreate,
    LichessAccountResponse,
    LichessAccountUpdate,
)

router = APIRouter(prefix="/accounts", tags=["accounts"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[LichessAccountResponse])
def list_accounts(db: DbSession) -> list[LichessAccount]:
    """List all configured Lichess accounts."""
    stmt = select(LichessAccount).order_by(LichessAccount.created_at.desc())
    return list(db.scalars(stmt).all())


@router.get("/{account_id}", response_model=LichessAccountResponse)
def get_account(account_id: str, db: DbSession) -> LichessAccount:
    """Get a specific Lichess account."""
    account = db.get(LichessAccount, account_id)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        )
    return account


@router.post("", response_model=LichessAccountResponse, status_code=status.HTTP_201_CREATED)
def create_account(data: LichessAccountCreate, db: DbSession) -> LichessAccount:
    """Create a new Lichess account configuration."""
    # Check if username already exists
    existing = db.scalar(select(LichessAccount).where(LichessAccount.username == data.username))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account with this username already exists",
        )

    # If this is the first account or marked as default, ensure only one default
    if data.is_default:
        db.execute(select(LichessAccount).where(LichessAccount.is_default == True))  # noqa: E712
        for acc in db.scalars(
            select(LichessAccount).where(LichessAccount.is_default == True)  # noqa: E712
        ).all():
            acc.is_default = False

    account = LichessAccount(
        username=data.username,
        api_token=data.api_token,
        is_default=data.is_default,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.patch("/{account_id}", response_model=LichessAccountResponse)
def update_account(
    account_id: str,
    data: LichessAccountUpdate,
    db: DbSession,
) -> LichessAccount:
    """Update a Lichess account configuration."""
    account = db.get(LichessAccount, account_id)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        )

    update_data = data.model_dump(exclude_unset=True)

    # Handle default account logic
    if update_data.get("is_default"):
        for acc in db.scalars(
            select(LichessAccount).where(LichessAccount.is_default == True)  # noqa: E712
        ).all():
            acc.is_default = False

    for field, value in update_data.items():
        setattr(account, field, value)

    db.commit()
    db.refresh(account)
    return account


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(account_id: str, db: DbSession) -> None:
    """Delete a Lichess account configuration."""
    account = db.get(LichessAccount, account_id)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        )

    db.delete(account)
    db.commit()
