"""
API routes for game management.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from hlss.database import get_db
from hlss.models import Game, GameStatus, LichessAccount
from hlss.schemas import GameCreate, GameListResponse, GameResponse

router = APIRouter(prefix="/games", tags=["games"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=GameListResponse)
def list_games(
    db: DbSession,
    account_id: str | None = None,
    active_only: bool = True,
) -> dict:
    """List games, optionally filtered by account and status."""
    stmt = select(Game)

    if account_id:
        stmt = stmt.where(Game.account_id == account_id)

    if active_only:
        # Filter to only ongoing games
        active_statuses = [GameStatus.CREATED, GameStatus.STARTED]
        stmt = stmt.where(Game.status.in_(active_statuses))

    stmt = stmt.order_by(Game.updated_at.desc())
    games = list(db.scalars(stmt).all())

    return {"games": games, "total": len(games)}


@router.get("/{game_id}", response_model=GameResponse)
def get_game(game_id: str, db: DbSession) -> Game:
    """Get a specific game by ID."""
    game = db.get(Game, game_id)
    if not game:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Game not found",
        )
    return game


@router.get("/lichess/{lichess_game_id}", response_model=GameResponse)
def get_game_by_lichess_id(lichess_game_id: str, db: DbSession) -> Game:
    """Get a game by its Lichess game ID."""
    game = db.scalar(select(Game).where(Game.lichess_game_id == lichess_game_id))
    if not game:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Game not found",
        )
    return game


@router.post("", response_model=GameResponse, status_code=status.HTTP_201_CREATED)
def create_game(data: GameCreate, db: DbSession) -> Game:
    """
    Create a new game request.
    
    This endpoint initiates a game creation on Lichess via the account's API token.
    The actual game creation is handled by the Lichess service.
    """
    # Verify account exists
    account = db.get(LichessAccount, data.account_id)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        )

    if not account.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account is disabled",
        )

    # TODO: Integrate with Lichess service to create actual game
    # For now, return a placeholder that will be updated by the service
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Game creation via Lichess API not yet implemented",
    )


@router.delete("/{game_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_game(game_id: str, db: DbSession) -> None:
    """Remove a game from tracking (does not affect Lichess)."""
    game = db.get(Game, game_id)
    if not game:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Game not found",
        )

    db.delete(game)
    db.commit()
