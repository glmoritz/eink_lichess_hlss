"""
HLSS - High Level Screen Service for Lichess e-Ink Client

FastAPI application entry point.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from hlss import __version__
from hlss.config import get_settings
from hlss.database import init_db
from hlss.routers import (
    accounts_router,
    frames_router,
    games_router,
    inputs_router,
    instances_router,
)
from hlss.schemas import HealthResponse

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    # Startup
    if settings.is_development:
        # Auto-create tables in development
        init_db()
    yield
    # Shutdown
    pass


app = FastAPI(
    title="HLSS - Lichess e-Ink Client",
    description="""
High Level Screen Service for playing Lichess games on e-Ink devices.

This service:
- Integrates with the Lichess API
- Renders complete e-Ink user interfaces server-side
- Exposes frames to LLSS (Low Level Screen Service)
- Processes input events from devices

All UI rendering is performed on the server using Pillow and python-chess.
    """,
    version=__version__,
    lifespan=lifespan,
)

# CORS middleware for web configuration interface
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.is_development else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(accounts_router, prefix="/api")
app.include_router(games_router, prefix="/api")
app.include_router(frames_router, prefix="/api")
app.include_router(inputs_router, prefix="/api")
app.include_router(instances_router, prefix="/api")


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health_check() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(
        status="ok",
        version=__version__,
        database="connected",
    )


@app.get("/", tags=["root"])
def root():
    """Root endpoint with API information."""
    return {
        "name": "HLSS - Lichess e-Ink Client",
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
    }
