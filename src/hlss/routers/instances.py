"""
API routes for HLSS instance management.

Implements the HLSS OpenAPI specification for instance lifecycle,
input handling, and frame rendering.
"""

import hashlib
import json
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
    FrameMetadataResponse,
    FrameSendResponse,
    InputEventCreate,
    InstanceCreate,
    InstanceInitRequest,
    InstanceInitResponse,
    InstanceResponse,
    InstanceStatusResponse,
    RenderResponse,
)
from hlss.security import require_llss_auth
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
    background_tasks: BackgroundTasks,
    db: DbSession,
    _: dict = Depends(require_llss_auth),
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

    # Queue initial frame render
    background_tasks.add_task(
        _render_and_submit_frame,
        instance_id=instance.id,
    )

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
    _: dict = Depends(require_llss_auth),
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
def get_instance_status(
    instance_id: str,
    db: DbSession,
    _: dict = Depends(require_llss_auth),
) -> InstanceStatusResponse:
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
    _: dict = Depends(require_llss_auth),
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


@router.get(
    "/{instance_id}/frame",
    response_model=FrameMetadataResponse,
    tags=["hlss-api", "frames"],
)
def get_frame_metadata(
    instance_id: str,
    db: DbSession,
    _: dict = Depends(require_llss_auth),
) -> FrameMetadataResponse:
    """
    Get current frame metadata.

    Returns metadata about the current frame for this instance,
    including frame ID, hash, dimensions, and screen type.
    LLSS can use this to check if it has the latest frame.

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

    # Check if we have a frame - if not, render one first
    if not instance.last_frame_id:
        frame = _render_frame(instance, db)
    else:
        frame = db.get(Frame, instance.last_frame_id)
    if not frame:
        return FrameMetadataResponse(
            instance_id=instance_id,
            has_frame=False,
        )

    return FrameMetadataResponse(
        instance_id=instance_id,
        has_frame=True,
        frame_id=frame.id,
        frame_hash=frame.image_hash,
        screen_type=frame.screen_type.value if frame.screen_type else None,
        width=frame.width,
        height=frame.height,
        created_at=frame.created_at,
    )


@router.post(
    "/{instance_id}/frame/send",
    response_model=FrameSendResponse,
    tags=["hlss-api", "frames"],
)
def send_frame(
    instance_id: str,
    background_tasks: BackgroundTasks,
    db: DbSession,
    _: dict = Depends(require_llss_auth),
) -> FrameSendResponse:
    """
    Request HLSS to send (or re-send) the current frame to LLSS.

    If a frame exists, it will be submitted to LLSS via the callback URL.
    If no frame exists, a new one will be rendered and sent.

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

    # Check if we have a frame to send
    if instance.last_frame_id:
        frame = db.get(Frame, instance.last_frame_id)
        if frame:
            # Re-submit existing frame
            background_tasks.add_task(
                _submit_existing_frame,
                instance_id=instance.id,
                frame_id=frame.id,
            )
            return FrameSendResponse(
                status="sent",
                frame_id=frame.id,
            )

    # No frame exists, render a new one
    background_tasks.add_task(
        _render_and_submit_frame,
        instance_id=instance.id,
    )
    return FrameSendResponse(
        status="scheduled",
        frame_id=None,
    )


@router.delete(
    "/{instance_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["hlss-api"],
)
def delete_instance_by_llss_id(
    instance_id: str,
    db: DbSession,
    _: dict = Depends(require_llss_auth),
) -> None:
    """
    Delete an instance.

    Called by LLSS when an instance is deleted. HLSS cleans up
    all resources associated with this instance (state, cached frames, etc.).

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

    # Delete associated frames
    frames_stmt = select(Frame).where(Frame.id == instance.last_frame_id)
    frames = db.scalars(frames_stmt).all()
    for frame in frames:
        db.delete(frame)

    # Delete the instance
    db.delete(instance)
    db.commit()


# ============================================================================
# Helper Functions
# ============================================================================


def _render_frame(instance: Instance, db: Session) -> Frame:
    """
    Render a frame for the given instance based on its current screen state.

    Creates and stores the frame in the database, updating the instance's
    last_frame_id reference.

    Args:
        instance: The instance to render a frame for.
        db: Database session.

    Returns:
        The rendered Frame object.
    """
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

        selected_color = "random"
        if instance.move_state:
            try:
                data = json.loads(instance.move_state)
                if isinstance(data, dict):
                    new_match = data.get("new_match")
                    if isinstance(new_match, dict):
                        color = new_match.get("color")
                        if color in ["random", "white", "black"]:
                            selected_color = color
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        image_data = renderer.render_new_match_screen(
            selected_user=username,
            selected_color=selected_color,
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
                player_color = chess.WHITE if game.player_color.value == "white" else chess.BLACK

                image_data = renderer.render_play_screen(
                    board=board,
                    player_color=player_color,
                    opponent_name=game.opponent_username or "Unknown",
                    player_name=(
                        instance.linked_account.username if instance.linked_account else "Player"
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
    db.flush()  # Flush to get the frame ID
    instance.last_frame_id = frame.id
    db.commit()
    db.refresh(frame)

    return frame


async def _submit_frame(instance: Instance, frame: Frame, db: Session) -> Optional[str]:
    """
    Submit a frame to LLSS.

    Args:
        instance: The instance the frame belongs to.
        frame: The frame to submit.
        db: Database session.

    Returns:
        The frame ID if successful, None otherwise.
    """
    from hlss.services.llss import LLSSService

    if not instance.llss_instance_id:
        return None

    llss = LLSSService()
    try:
        result = await llss.submit_frame(
            instance_id=instance.llss_instance_id,
            image_data=frame.image_data,
        )

        # Update frame with LLSS response
        frame.llss_frame_id = result.get("frame_id")
        frame.submitted_at = datetime.utcnow()
        db.commit()

        return frame.id
    except Exception as e:
        # Log error but don't fail
        print(f"Failed to submit frame to LLSS: {e}")
        return frame.id


async def _render_and_submit_frame(instance_id: str) -> Optional[str]:
    """
    Render current screen and submit to LLSS.

    This is called as a background task after input processing or render request.

    Returns the frame ID if successful, None otherwise.
    """
    from hlss.database import SessionLocal

    db = SessionLocal()
    try:
        instance = db.get(Instance, instance_id)
        if not instance or not instance.llss_instance_id:
            return None

        frame = _render_frame(instance, db)
        return await _submit_frame(instance, frame, db)

    finally:
        db.close()


async def _submit_existing_frame(instance_id: str, frame_id: str) -> Optional[str]:
    """
    Submit an existing frame to LLSS.

    This is called as a background task when LLSS requests a frame re-send.

    Returns the frame ID if successful, None otherwise.
    """
    from hlss.database import SessionLocal

    db = SessionLocal()
    try:
        instance = db.get(Instance, instance_id)
        if not instance or not instance.llss_instance_id:
            return None

        frame = db.get(Frame, frame_id)
        if not frame:
            return None

        return await _submit_frame(instance, frame, db)

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
