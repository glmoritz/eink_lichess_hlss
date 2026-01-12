"""
API routes for frame management.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from hlss.database import get_db
from hlss.models import Frame
from hlss.schemas import FrameResponse

router = APIRouter(prefix="/frames", tags=["frames"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[FrameResponse])
def list_frames(
    db: DbSession,
    game_id: str | None = None,
    limit: int = 10,
) -> list[Frame]:
    """List recent frames, optionally filtered by game."""
    stmt = select(Frame)

    if game_id:
        stmt = stmt.where(Frame.game_id == game_id)

    stmt = stmt.order_by(Frame.created_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())


@router.get("/{frame_id}", response_model=FrameResponse)
def get_frame(frame_id: str, db: DbSession) -> Frame:
    """Get frame metadata by ID."""
    frame = db.get(Frame, frame_id)
    if not frame:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Frame not found",
        )
    return frame


@router.get("/{frame_id}/image")
def get_frame_image(frame_id: str, db: DbSession) -> Response:
    """Get the raw frame image data."""
    frame = db.get(Frame, frame_id)
    if not frame:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Frame not found",
        )

    return Response(
        content=frame.image_data,
        media_type="image/png",
        headers={
            "X-Frame-Hash": frame.image_hash,
            "X-Frame-Width": str(frame.width),
            "X-Frame-Height": str(frame.height),
        },
    )


@router.delete("/{frame_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_frame(frame_id: str, db: DbSession) -> None:
    """Delete a frame."""
    frame = db.get(Frame, frame_id)
    if not frame:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Frame not found",
        )

    db.delete(frame)
    db.commit()
