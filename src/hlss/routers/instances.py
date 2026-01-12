"""
API routes for HLSS instance management.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from hlss.database import get_db
from hlss.models import Instance, ScreenType
from hlss.schemas import InstanceCreate, InstanceResponse

router = APIRouter(prefix="/instances", tags=["instances"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[InstanceResponse])
def list_instances(db: DbSession) -> list[Instance]:
    """List all HLSS instances."""
    stmt = select(Instance).order_by(Instance.created_at.desc())
    return list(db.scalars(stmt).all())


@router.get("/{instance_id}", response_model=InstanceResponse)
def get_instance(instance_id: str, db: DbSession) -> Instance:
    """Get a specific instance."""
    instance = db.get(Instance, instance_id)
    if not instance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Instance not found",
        )
    return instance


@router.post("", response_model=InstanceResponse, status_code=status.HTTP_201_CREATED)
def create_instance(data: InstanceCreate, db: DbSession) -> Instance:
    """
    Create a new HLSS instance.
    
    This creates a local instance record. Registration with LLSS
    is handled separately by the LLSS integration service.
    """
    instance = Instance(
        name=data.name,
        instance_type=data.type,
        current_screen=ScreenType.SETUP,
    )
    db.add(instance)
    db.commit()
    db.refresh(instance)

    # TODO: Register with LLSS via service
    return instance


@router.patch("/{instance_id}/screen", response_model=InstanceResponse)
def update_instance_screen(
    instance_id: str,
    screen_type: str,
    game_id: str | None = None,
    db: DbSession = Depends(get_db),
) -> Instance:
    """Update the current screen for an instance."""
    instance = db.get(Instance, instance_id)
    if not instance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Instance not found",
        )

    try:
        instance.current_screen = ScreenType(screen_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid screen type: {screen_type}",
        )

    instance.current_game_id = game_id
    db.commit()
    db.refresh(instance)

    return instance


@router.delete("/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_instance(instance_id: str, db: DbSession) -> None:
    """Delete an HLSS instance."""
    instance = db.get(Instance, instance_id)
    if not instance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Instance not found",
        )

    db.delete(instance)
    db.commit()
