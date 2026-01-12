"""
API routers package.
"""

from hlss.routers.accounts import router as accounts_router
from hlss.routers.games import router as games_router
from hlss.routers.inputs import router as inputs_router
from hlss.routers.frames import router as frames_router
from hlss.routers.instances import router as instances_router

__all__ = [
    "accounts_router",
    "games_router",
    "inputs_router",
    "frames_router",
    "instances_router",
]
