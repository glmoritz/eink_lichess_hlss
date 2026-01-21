"""Helper utilities for new match screen state."""

from __future__ import annotations

import json
from typing import Any

from hlss.models import Instance

NEW_MATCH_STATE_KEY = "new_match"
NEW_MATCH_COLORS = ["random", "white", "black"]


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
