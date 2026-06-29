"""Per-instance PLAY-screen view mode (in-memory, not persisted).

The device toggles between board renderings (currently 3D perspective and
2D top-down) via HL_LEFT on the top button strip. State lives only in
this process — restarting HLSS resets everyone to the 3D default. The
trade-off is intentional: we don't want a migration for an ephemeral UI
preference, and the device repaints on the next /state heartbeat anyway.
"""

VIEW_3D = "3d"
VIEW_2D = "2d"
_VALID = {VIEW_3D, VIEW_2D}

_view_modes: dict[str, str] = {}


def get(instance_id: str) -> str:
    """Current view mode for the instance, defaulting to 3D."""
    return _view_modes.get(instance_id, VIEW_3D)


def set(instance_id: str, mode: str) -> None:
    """Explicit set. Silently ignores values outside the known set."""
    if mode in _VALID:
        _view_modes[instance_id] = mode


def toggle(instance_id: str) -> str:
    """Flip 3D ↔ 2D. Returns the new mode."""
    nxt = VIEW_2D if get(instance_id) == VIEW_3D else VIEW_3D
    _view_modes[instance_id] = nxt
    return nxt


def reset(instance_id: str) -> None:
    """Drop the stored mode so the instance falls back to the default."""
    _view_modes.pop(instance_id, None)
