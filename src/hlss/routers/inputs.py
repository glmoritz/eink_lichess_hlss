"""
API routes for handling input events from LLSS.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from hlss.database import get_db
from hlss.models import InputEvent as InputEventModel
from hlss.models import ButtonType, InputEventType
from hlss.schemas import InputEventCreate, InputEventResponse

router = APIRouter(prefix="/inputs", tags=["inputs"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=InputEventResponse, status_code=status.HTTP_202_ACCEPTED)
def receive_input_event(data: InputEventCreate, db: DbSession) -> InputEventModel:
    """
    Receive an input event forwarded from LLSS.
    
    This endpoint is called by LLSS when a device sends button input.
    The event is stored and processed asynchronously by the input processing service.
    """
    event = InputEventModel(
        button=ButtonType(data.button.value),
        event_type=InputEventType(data.event_type.value),
        event_timestamp=data.timestamp,
        processed=False,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    # TODO: Trigger async input processing
    # The input processor service will handle the event based on current screen state

    return event


@router.post("/process/{event_id}", response_model=InputEventResponse)
def mark_event_processed(event_id: str, db: DbSession) -> InputEventModel:
    """Mark an input event as processed (internal use)."""
    from fastapi import HTTPException

    event = db.get(InputEventModel, event_id)
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )

    event.processed = True
    event.processed_at = datetime.utcnow()
    db.commit()
    db.refresh(event)

    return event
