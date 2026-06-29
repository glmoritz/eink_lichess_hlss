"""
SQLAlchemy models for the HLSS application.
"""

import enum
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hlss.database import Base


def generate_uuid() -> str:
    """Generate a UUID string."""
    return str(uuid4())


class LichessAccount(Base):
    """A Lichess account linked to the HLSS instance."""

    __tablename__ = "lichess_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    api_token: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # null for local accounts; should be encrypted in production
    # "lichess" (Lichess-backed, has api_token) or "local" (offline play, no token)
    backend: Mapped[str] = mapped_column(
        String(16), nullable=False, default="lichess", server_default="lichess"
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    last_games_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    games: Mapped[list["Game"]] = relationship("Game", back_populates="account")
    challenges: Mapped[list["LichessChallenge"]] = relationship(
        "LichessChallenge",
        back_populates="account",
        cascade="all, delete-orphan",
    )
    adversaries: Mapped[list["Adversary"]] = relationship(
        "Adversary",
        back_populates="account",
        cascade="all, delete-orphan",
    )


class GameStatus(enum.Enum):
    """Status of a chess game."""

    CREATED = "created"
    STARTED = "started"
    ABORTED = "aborted"
    MATE = "mate"
    RESIGN = "resign"
    STALEMATE = "stalemate"
    TIMEOUT = "timeout"
    DRAW = "draw"
    OUT_OF_TIME = "outoftime"
    CHEAT = "cheat"
    NO_START = "noStart"
    UNKNOWN_FINISH = "unknownFinish"
    VARIANT_END = "variantEnd"


class GameColor(enum.Enum):
    """Player color in a game."""

    WHITE = "white"
    BLACK = "black"


class Game(Base):
    """A Lichess game being tracked by HLSS."""

    __tablename__ = "games"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    # For local games this is a synthetic "local-<uuid>" id (col is unique+not-null).
    lichess_game_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("lichess.lichess_accounts.id"))
    # "lichess" or "local" — selects the GameBackend that drives this game.
    backend: Mapped[str] = mapped_column(
        String(16), nullable=False, default="lichess", server_default="lichess"
    )
    # Local human-vs-human: the two mirrored rows of one match share match_id, and
    # each row's instance_id is the HLSS instance that plays that side (for frame relay).
    match_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    instance_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # Game metadata
    player_color: Mapped[GameColor] = mapped_column(Enum(GameColor), nullable=False)
    opponent_username: Mapped[str] = mapped_column(String(255), nullable=True)
    status: Mapped[GameStatus] = mapped_column(Enum(GameStatus), default=GameStatus.CREATED)
    is_my_turn: Mapped[bool] = mapped_column(Boolean, default=False)

    # Game state
    fen: Mapped[str] = mapped_column(
        String(100), default="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    )
    # Initial FEN for the stored move sequence (if provided by Lichess)
    initial_fen: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    # Raw JSON payload returned by Lichess for debugging/inspection
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_move: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    moves: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Space-separated UCI moves
    move_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    account: Mapped["LichessAccount"] = relationship("LichessAccount", back_populates="games")
    frames: Mapped[list["Frame"]] = relationship("Frame", back_populates="game")


class LichessChallenge(Base):
    """An incoming Lichess challenge for a linked account."""

    __tablename__ = "lichess_challenges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    lichess_challenge_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("lichess.lichess_accounts.id"))

    # Challenge metadata
    challenger_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    challenger_title: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    rated: Mapped[bool] = mapped_column(Boolean, default=False)
    color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    variant: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    speed: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Time control
    time_control_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    time_control_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    time_control_increment: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    time_control_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Raw payload for debugging/inspection
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    account: Mapped["LichessAccount"] = relationship("LichessAccount", back_populates="challenges")


class ScreenType(enum.Enum):
    """Types of screens rendered by HLSS."""

    SETUP = "setup"  # Initial setup with QR code
    NEW_MATCH = "new_match"  # New game creation screen
    PLAY = "play"  # Active game play screen
    GAME_LIST = "game_list"  # List of ongoing games


class Frame(Base):
    """A rendered frame stored by HLSS."""

    __tablename__ = "frames"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    game_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("lichess.games.id"), nullable=True
    )

    screen_type: Mapped[ScreenType] = mapped_column(Enum(ScreenType), nullable=False)

    # Frame data
    image_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    image_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA256 hash
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)

    # Optional pressed-state strips for the LLSS press-feedback cache. PNG
    # bytes of width × top_strip_height / width × bottom_strip_height, with
    # every button slot drawn in pressed visual state. The device extracts
    # only the actually-pressed slot's column range and overlays it on the
    # captured frame band. Omitted when the screen has no usable buttons.
    top_pressed_data: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    bottom_pressed_data: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)

    # 8-bit per-band mask of enabled (pressable) slots. Independent of the
    # rendered strip image so a frame where labels are unchanged but a
    # button toggles enabled-state can reuse the same strip id and only
    # change the mask. Sent to LLSS as a form field on /instances/{id}/frames
    # and forwarded to the device in DeviceStateResponse / InputProcessResponse.
    top_enabled_mask: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bottom_enabled_mask: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # LLSS integration
    llss_frame_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    game: Mapped[Optional["Game"]] = relationship("Game", back_populates="frames")


class InputEventType(enum.Enum):
    """Types of input events."""

    PRESS = "PRESS"
    LONG_PRESS = "LONG_PRESS"
    RELEASE = "RELEASE"


class ButtonType(enum.Enum):
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


class InputEvent(Base):
    """Record of input events received from devices."""

    __tablename__ = "input_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    button: Mapped[ButtonType] = mapped_column(Enum(ButtonType), nullable=False)
    event_type: Mapped[InputEventType] = mapped_column(Enum(InputEventType), nullable=False)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Processing info
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Instance(Base):
    """HLSS instance registered with LLSS."""

    __tablename__ = "instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    llss_instance_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    instance_type: Mapped[str] = mapped_column(String(50), default="chess")

    # LLSS callback URLs (set during initialization by LLSS)
    callback_frames: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    callback_inputs: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    callback_notify: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # Display capabilities (set during initialization by LLSS)
    display_width: Mapped[int] = mapped_column(Integer, default=800)
    display_height: Mapped[int] = mapped_column(Integer, default=480)
    display_bit_depth: Mapped[int] = mapped_column(Integer, default=1)

    # Instance readiness state
    is_initialized: Mapped[bool] = mapped_column(Boolean, default=False)
    is_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_configuration: Mapped[bool] = mapped_column(Boolean, default=True)
    configuration_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # Current state
    current_screen: Mapped[ScreenType] = mapped_column(Enum(ScreenType), default=ScreenType.SETUP)
    current_game_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    # Linked Lichess account
    linked_account_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("lichess.lichess_accounts.id"), nullable=True
    )

    # Navigation state for move input
    new_match_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Last frame tracking
    last_frame_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    linked_account: Mapped[Optional["LichessAccount"]] = relationship(
        "LichessAccount", foreign_keys=[linked_account_id]
    )


class Adversary(Base):
    """A Lichess friend/adversary associated with an account."""

    __tablename__ = "adversaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("lichess.lichess_accounts.id"))
    lichess_username: Mapped[str] = mapped_column(String(255), nullable=False)
    friendly_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    account: Mapped["LichessAccount"] = relationship("LichessAccount", back_populates="adversaries")
