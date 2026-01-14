"""
API routes for HLSS instance management.

Implements the HLSS OpenAPI specification for instance lifecycle,
input handling, and frame rendering.
"""

import hashlib
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from hlss.config import get_settings
from hlss.database import get_db
from hlss.models import ButtonType, Frame, InputEventType, Instance, LichessAccount, ScreenType
from hlss.models import InputEvent as InputEventModel
from hlss.schemas import (
    InputEventCreate,
    InstanceCreate,
    InstanceInitRequest,
    InstanceInitResponse,
    InstanceResponse,
    InstanceStatusResponse,
    RenderResponse,
)
from hlss.services.input_processor import InputProcessorService
from hlss.services.renderer import RendererService

router = APIRouter(prefix="/instances", tags=["instances"])

DbSession = Annotated[Session, Depends(get_db)]
settings = get_settings()


# ============================================================================
# HLSS OpenAPI Endpoints (called by LLSS)
# ============================================================================


@router.post("/init", response_model=InstanceInitResponse, tags=["hlss-api"])
def initialize_instance(
    data: InstanceInitRequest,
    db: DbSession,
) -> InstanceInitResponse:
    """
    Initialize a new HLSS instance.

    Called by LLSS when a new instance is created.
    Establishes trust, stores callbacks, and initializes state.

    This is the main entry point for LLSS to set up communication with HLSS.
    """
    # Check if instance already exists with this LLSS ID
    stmt = select(Instance).where(Instance.llss_instance_id == data.instance_id)
    existing = db.scalars(stmt).first()

    if existing:
        # Update existing instance with new callbacks
        existing.callback_frames = data.callbacks.frames
        existing.callback_inputs = data.callbacks.inputs
        existing.callback_notify = data.callbacks.notify
        existing.display_width = data.display.width
        existing.display_height = data.display.height
        existing.display_bit_depth = data.display.bit_depth
        existing.is_initialized = True
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        instance = existing
    else:
        # Create new instance
        instance = Instance(
            llss_instance_id=data.instance_id,
            name=f"Lichess Instance {data.instance_id[:8]}",
            instance_type="chess",
            callback_frames=data.callbacks.frames,
            callback_inputs=data.callbacks.inputs,
            callback_notify=data.callbacks.notify,
            display_width=data.display.width,
            display_height=data.display.height,
            display_bit_depth=data.display.bit_depth,
            is_initialized=True,
            is_ready=False,
            needs_configuration=True,
            current_screen=ScreenType.SETUP,
        )
        db.add(instance)
        db.commit()
        db.refresh(instance)

    # Check if we have any configured Lichess accounts
    accounts = db.scalars(select(LichessAccount).where(LichessAccount.is_enabled == True)).all()

    if accounts:
        # If we have accounts, mark as ready
        instance.is_ready = True
        instance.needs_configuration = False
        instance.current_screen = ScreenType.NEW_MATCH
        # Link to default account if available
        default_account = next((a for a in accounts if a.is_default), accounts[0])
        instance.linked_account_id = default_account.id
        db.commit()
        db.refresh(instance)

    # Generate configuration URL
    config_url = f"{settings.public_url}/configure/{instance.id}"
    instance.configuration_url = config_url
    db.commit()

    return InstanceInitResponse(
        status="initialized",
        needs_configuration=instance.needs_configuration,
        configuration_url=config_url if instance.needs_configuration else None,
    )


@router.post(
    "/{instance_id}/inputs",
    status_code=status.HTTP_200_OK,
    tags=["hlss-api", "inputs"],
)
def receive_instance_input(
    instance_id: str,
    data: InputEventCreate,
    background_tasks: BackgroundTasks,
    db: DbSession,
) -> dict:
    """
    Receive input event from LLSS.

    Receives abstract button events forwarded by LLSS.
    HLSS updates internal state and may render a new frame.

    The instance_id is the LLSS-assigned instance ID.
    """
    # Find instance by LLSS instance ID
    stmt = select(Instance).where(Instance.llss_instance_id == instance_id)
    instance = db.scalars(stmt).first()

    if not instance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Instance not found: {instance_id}",
        )

    # Store the input event
    event = InputEventModel(
        button=ButtonType(data.button.value),
        event_type=InputEventType(data.event_type.value),
        event_timestamp=data.timestamp,
        processed=False,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    # Process the input event synchronously
    processor = InputProcessorService(db)
    state_changed, error = processor.process_button(
        instance=instance,
        button=ButtonType(data.button.value),
    )

    # Mark event as processed
    event.processed = True
    event.processed_at = datetime.utcnow()
    db.commit()

    if state_changed:
        # Queue a render and frame submission to LLSS
        background_tasks.add_task(
            _render_and_submit_frame,
            instance_id=instance.id,
        )

    return {
        "status": "processed",
        "state_changed": state_changed,
        "error": error,
    }


@router.get(
    "/{instance_id}/status",
    response_model=InstanceStatusResponse,
    tags=["hlss-api"],
)
def get_instance_status(instance_id: str, db: DbSession) -> InstanceStatusResponse:
    """
    Get instance status.

    Returns the current status of the instance, including whether
    user configuration is required.

    The instance_id is the LLSS-assigned instance ID.
    """
    # Find instance by LLSS instance ID
    stmt = select(Instance).where(Instance.llss_instance_id == instance_id)
    instance = db.scalars(stmt).first()

    if not instance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Instance not found: {instance_id}",
        )

    # Determine active screen name
    active_screen = instance.current_screen.value
    if instance.current_game_id:
        active_screen = f"game_{instance.current_game_id}"

    return InstanceStatusResponse(
        instance_id=instance_id,
        ready=instance.is_ready,
        needs_configuration=instance.needs_configuration,
        configuration_url=instance.configuration_url if instance.needs_configuration else None,
        active_screen=active_screen,
    )


@router.post(
    "/{instance_id}/render",
    response_model=RenderResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["hlss-api", "rendering"],
)
def force_render(
    instance_id: str,
    background_tasks: BackgroundTasks,
    db: DbSession,
) -> RenderResponse:
    """
    Force frame rendering.

    Optional endpoint allowing LLSS to request a fresh render
    (e.g. after reconnect or cache loss).

    The instance_id is the LLSS-assigned instance ID.
    """
    # Find instance by LLSS instance ID
    stmt = select(Instance).where(Instance.llss_instance_id == instance_id)
    instance = db.scalars(stmt).first()

    if not instance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Instance not found: {instance_id}",
        )

    # Queue a render task
    background_tasks.add_task(
        _render_and_submit_frame,
        instance_id=instance.id,
    )

    return RenderResponse(
        status="scheduled",
        frame_id=None,  # Will be available after background task completes
    )


# ============================================================================
# Helper Functions
# ============================================================================


async def _render_and_submit_frame(instance_id: str) -> Optional[str]:
    """
    Render current screen and submit to LLSS.

    This is called as a background task after input processing or render request.

    Returns the frame ID if successful, None otherwise.
    """
    from hlss.database import SessionLocal
    from hlss.services.llss import LLSSService

    db = SessionLocal()
    try:
        instance = db.get(Instance, instance_id)
        if not instance or not instance.llss_instance_id:
            return None

        renderer = RendererService()

        # Render based on current screen
        if instance.current_screen == ScreenType.SETUP:
            image_data = renderer.render_setup_screen(config_url=instance.configuration_url or "")
        elif instance.current_screen == ScreenType.NEW_MATCH:
            # Get linked account for new match screen
            username = "Not configured"
            if instance.linked_account_id:
                account = db.get(LichessAccount, instance.linked_account_id)
                if account:
                    username = account.username

            image_data = renderer.render_new_match_screen(
                selected_user=username,
                selected_color="random",
                button_actions=[],
            )
        elif instance.current_screen == ScreenType.PLAY:
            # Render play screen - requires game state
            import chess

            from hlss.models import Game

            if instance.current_game_id:
                game = db.get(Game, instance.current_game_id)
                if game:
                    board = chess.Board(game.fen)
                    player_color = (
                        chess.WHITE if game.player_color.value == "white" else chess.BLACK
                    )

                    image_data = renderer.render_play_screen(
                        board=board,
                        player_color=player_color,
                        opponent_name=game.opponent_username or "Unknown",
                        player_name=(
                            instance.linked_account.username
                            if instance.linked_account
                            else "Player"
                        ),
                        move_state=None,
                        button_actions=[],
                    )
                else:
                    # Fallback to new match screen
                    image_data = renderer.render_new_match_screen(
                        selected_user="Unknown",
                        selected_color="random",
                        button_actions=[],
                    )
            else:
                # No game selected, render new match
                image_data = renderer.render_new_match_screen(
                    selected_user="Unknown",
                    selected_color="random",
                    button_actions=[],
                )
        else:
            # Default to setup screen
            image_data = renderer.render_setup_screen(config_url=instance.configuration_url or "")

        # Compute hash
        image_hash = hashlib.sha256(image_data).hexdigest()

        # Store frame locally
        frame = Frame(
            screen_type=instance.current_screen,
            image_data=image_data,
            image_hash=image_hash,
            width=instance.display_width,
            height=instance.display_height,
        )
        db.add(frame)
        db.commit()
        db.refresh(frame)

        # Submit to LLSS
        llss = LLSSService()
        try:
            result = await llss.submit_frame(
                instance_id=instance.llss_instance_id,
                image_data=image_data,
            )

            # Update frame with LLSS response
            frame.llss_frame_id = result.get("frame_id")
            frame.submitted_at = datetime.utcnow()
            instance.last_frame_id = frame.id
            db.commit()

            return frame.id
        except Exception as e:
            # Log error but don't fail
            print(f"Failed to submit frame to LLSS: {e}")
            return frame.id

    finally:
        db.close()


# ============================================================================
# Internal Instance Management Endpoints
# ============================================================================


@router.get("", response_model=list[InstanceResponse])
def list_instances(db: DbSession) -> list[Instance]:
    """List all HLSS instances."""
    stmt = select(Instance).order_by(Instance.created_at.desc())
    return list(db.scalars(stmt).all())


@router.get("/by-id/{instance_id}", response_model=InstanceResponse)
def get_instance(instance_id: str, db: DbSession) -> Instance:
    """Get a specific instance by internal ID."""
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

    return instance


@router.patch("/by-id/{instance_id}/screen", response_model=InstanceResponse)
def update_instance_screen(
    instance_id: str,
    screen_type: str,
    db: DbSession,
    game_id: str | None = None,
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


@router.patch("/by-id/{instance_id}/link-account", response_model=InstanceResponse)
def link_account_to_instance(
    instance_id: str,
    account_id: str,
    db: DbSession,
) -> Instance:
    """Link a Lichess account to an instance."""
    instance = db.get(Instance, instance_id)
    if not instance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Instance not found",
        )

    account = db.get(LichessAccount, account_id)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
        )

    instance.linked_account_id = account_id
    instance.is_ready = True
    instance.needs_configuration = False
    instance.current_screen = ScreenType.NEW_MATCH
    db.commit()
    db.refresh(instance)

    return instance


@router.delete("/by-id/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
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
