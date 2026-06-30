"""Per-instance "next-frame needs a full e-ink refresh" hint.

Partial refreshes on the SSD16xx are ~600 ms but accumulate ghosting
when adjacent frames are visually distinct. A small server-side hint
lets HLSS ask the device for a full refresh on the frames that benefit:

  - a confirmed move (board pieces moved or pieces removed)
  - a view-mode toggle (3D ↔ 2D — completely different layout)
  - any game-state transition the opponent stream applies

Everything else (composing a move, idle re-render, screensaver wake)
stays partial. The flag lives in-process; restart resets every
instance to "partial" which is the safe default.
"""

_pending: set[str] = set()


def mark(instance_id: str) -> None:
    """Mark the instance's next rendered frame as needing a full refresh."""
    _pending.add(instance_id)


def consume(instance_id: str) -> bool:
    """Return True if a full refresh was requested for this instance and
    clear the flag. Idempotent — multiple consumers see only the first."""
    try:
        _pending.remove(instance_id)
        return True
    except KeyError:
        return False
