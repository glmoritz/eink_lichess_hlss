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
    api_token: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # Should be encrypted in production
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    games: Mapped[list["Game"]] = relationship("Game", back_populates="account")


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
    lichess_game_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("lichess.lichess_accounts.id"))

    # Game metadata
    player_color: Mapped[GameColor] = mapped_column(Enum(GameColor), nullable=False)
    opponent_username: Mapped[str] = mapped_column(String(255), nullable=True)
    status: Mapped[GameStatus] = mapped_column(Enum(GameStatus), default=GameStatus.CREATED)
    is_my_turn: Mapped[bool] = mapped_column(Boolean, default=False)

    # Game state
    fen: Mapped[str] = mapped_column(
        String(100), default="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    )
    last_move: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    moves: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Space-separated UCI moves

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    account: Mapped["LichessAccount"] = relationship("LichessAccount", back_populates="games")
    frames: Mapped[list["Frame"]] = relationship("Frame", back_populates="game")


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
    move_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON blob

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
