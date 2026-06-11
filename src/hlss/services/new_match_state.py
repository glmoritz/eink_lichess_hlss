"""Helper utilities for new match screen state."""

from __future__ import annotations

import json
from typing import Any

from hlss.models import Instance

NEW_MATCH_STATE_KEY = "new_match"
NEW_MATCH_COLORS = ["random", "white", "black"]

# Lichess AI (Stockfish) is offered as a selectable adversary alongside humans.
# AI options use synthetic, DB-less ids of the form "ai-<level>" so they can sit
# in the same selection cycle as real Adversary rows without a schema change.
AI_LEVELS = [1, 2, 3, 4, 5, 6, 7, 8]
AI_ID_PREFIX = "ai-"


def ai_adversary_id(level: int) -> str:
    """Synthetic adversary id for a Stockfish level."""
    return f"{AI_ID_PREFIX}{level}"


def ai_level_from_id(adversary_id: Any) -> int | None:
    """Return the Stockfish level if `adversary_id` is an AI sentinel, else None."""
    if isinstance(adversary_id, str) and adversary_id.startswith(AI_ID_PREFIX):
        try:
            level = int(adversary_id[len(AI_ID_PREFIX):])
        except ValueError:
            return None
        if level in AI_LEVELS:
            return level
    return None


def ai_adversary_label(level: int) -> str:
    """Human-readable name for a Stockfish level."""
    return f"Stockfish nível {level}"


def load_new_match_state(instance: Instance) -> dict[str, Any]:
    """Deserialize the new match state stored on an instance."""
    state = {"adversary_id": None, "color": NEW_MATCH_COLORS[0]}
    if not instance.new_match_state:
        return state

    try:
        payload = json.loads(instance.new_match_state)
        new_match = payload.get(NEW_MATCH_STATE_KEY)
        if isinstance(new_match, dict):
            state["adversary_id"] = new_match.get("adversary_id")
            color = new_match.get("color")
            if color in NEW_MATCH_COLORS:
                state["color"] = color
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    return state


def serialize_new_match_state(state: dict[str, Any]) -> str:
    """Serialize the new match state into JSON."""
    payload = {NEW_MATCH_STATE_KEY: state}
    return json.dumps(payload)
