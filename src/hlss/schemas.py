"""
Pydantic schemas for API request/response validation.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# Enums
# ============================================================================


class ButtonType(str, Enum):
    """Button identifiers matching LLSS specification."""

    BTN_1 = "BTN_1"
    BTN_2 = "BTN_2"
    BTN_3 = "BTN_3"
    BTN_4 = "BTN_4"
    BTN_5 = "BTN_5"
    BTN_6 = "BTN_6"
    BTN_7 = "BTN_7"
    BTN_8 = "BTN_8"
    ENTER = "ENTER"
    ESC = "ESC"
    HL_LEFT = "HL_LEFT"
    HL_RIGHT = "HL_RIGHT"


class InputEventType(str, Enum):
    """Input event types."""

    PRESS = "PRESS"
    LONG_PRESS = "LONG_PRESS"
    RELEASE = "RELEASE"


class GameColor(str, Enum):
    """Player color."""

    WHITE = "white"
    BLACK = "black"
    RANDOM = "random"


class ScreenType(str, Enum):
    """Screen types."""

    SETUP = "setup"
    NEW_MATCH = "new_match"
    PLAY = "play"
    GAME_LIST = "game_list"


# ============================================================================
# Lichess Account Schemas
# ============================================================================


class LichessAccountCreate(BaseModel):
    """Schema for creating a new Lichess account."""

    username: str = Field(..., min_length=1, max_length=255)
    api_token: str = Field(..., min_length=1)
    is_default: bool = False


class LichessAccountUpdate(BaseModel):
    """Schema for updating a Lichess account."""

    api_token: Optional[str] = None
    is_enabled: Optional[bool] = None
    is_default: Optional[bool] = None


class LichessAccountResponse(BaseModel):
    """Schema for Lichess account response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    is_enabled: bool
    is_default: bool
    created_at: datetime
    updated_at: datetime


# ============================================================================
# Game Schemas
# ============================================================================


class GameCreate(BaseModel):
    """Schema for creating a new game."""

    account_id: str
    color: GameColor = GameColor.RANDOM


class GameResponse(BaseModel):
    """Schema for game response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    lichess_game_id: str
    account_id: str
    player_color: str
    opponent_username: Optional[str]
    status: str
    is_my_turn: bool
    fen: str
    last_move: Optional[str]
    created_at: datetime
    updated_at: datetime


class GameListResponse(BaseModel):
    """Schema for list of games."""

    games: list[GameResponse]
    total: int


# ============================================================================
# Input Event Schemas (from LLSS)
# ============================================================================


class InputEventCreate(BaseModel):
    """Schema for input event from LLSS."""

    button: ButtonType
    event_type: InputEventType
    timestamp: datetime


class InputEventResponse(BaseModel):
    """Schema for input event response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    button: str
    event_type: str
    event_timestamp: datetime
    processed: bool


# ============================================================================
# Frame Schemas
# ============================================================================


class FrameResponse(BaseModel):
    """Schema for frame response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    screen_type: str
    image_hash: str
    width: int
    height: int
    llss_frame_id: Optional[str]
    submitted_at: Optional[datetime]
    created_at: datetime


class FrameCreateResponse(BaseModel):
    """Schema for frame creation response from LLSS."""

    frame_id: str
    hash: str
    created_at: datetime


# ============================================================================
# Instance Schemas
# ============================================================================


class InstanceCreate(BaseModel):
    """Schema for creating an instance."""

    name: str
    type: str = "chess"


class InstanceResponse(BaseModel):
    """Schema for instance response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    llss_instance_id: Optional[str]
    name: str
    instance_type: str
    current_screen: str
    current_game_id: Optional[str]
    created_at: datetime
    updated_at: datetime


# ============================================================================
# Move State Schemas
# ============================================================================


class MoveStateStep(str, Enum):
    """Current step in the move input workflow."""

    SELECT_PIECE = "select_piece"
    SELECT_FILE = "select_file"
    SELECT_RANK = "select_rank"
    DISAMBIGUATION = "disambiguation"
    CONFIRM = "confirm"


class MoveState(BaseModel):
    """State of the move input workflow."""

    step: MoveStateStep = MoveStateStep.SELECT_PIECE
    selected_piece: Optional[str] = None  # 'P', 'N', 'B', 'R', 'Q', 'K', 'O-O', 'O-O-O'
    selected_file: Optional[str] = None  # 'a' - 'h'
    selected_rank: Optional[int] = None  # 1 - 8
    disambiguation_options: list[str] = Field(default_factory=list)
    pending_move: Optional[str] = None  # UCI notation


# ============================================================================
# UI State Schemas
# ============================================================================


class ButtonAction(BaseModel):
    """Action associated with a button."""

    button: ButtonType
    label: str
    enabled: bool = True
    action: str  # Action identifier


class ScreenState(BaseModel):
    """Current screen state for rendering."""

    screen_type: ScreenType
    game_id: Optional[str] = None
    button_actions: list[ButtonAction] = Field(default_factory=list)
    move_state: Optional[MoveState] = None


# ============================================================================
# Health Check Schemas
# ============================================================================


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    version: str
    database: str = "connected"
