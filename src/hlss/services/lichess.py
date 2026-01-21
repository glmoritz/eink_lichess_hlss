"""
Lichess API integration service.
"""

from typing import Any, Optional

import berserk
import httpx

from hlss.config import get_settings


class LichessService:
    """Service for interacting with the Lichess API."""

    def __init__(self, api_token: str | None = None):
        self.settings = get_settings()
        self.api_token = api_token
        self._client: berserk.Client | None = None

    @property
    def client(self) -> berserk.Client:
        """Get or create a Lichess client."""
        if self._client is None:
            session = berserk.TokenSession(self.api_token) if self.api_token else None
            self._client = berserk.Client(session=session)
        return self._client

    def get_account(self) -> dict[str, Any]:
        """Get the authenticated user's account information."""
        return self.client.account.get()

    def get_ongoing_games(self) -> list[dict[str, Any]]:
        """Get list of ongoing games for the authenticated user."""
        return list(self.client.games.get_ongoing())

    def get_game(self, game_id: str) -> dict[str, Any]:
        """Get detailed information about a specific game."""
        return self.client.games.export(game_id)

    def get_friends(self) -> list[dict[str, Any]]:
        """Return the authenticated user\'s followed players (friends)."""
        return list(self.client.relations.get_users_followed())

    def get_game_stream(self, game_id: str):
        """
        Stream game updates for real-time position tracking.

        Returns an iterator that yields game state updates.
        """
        return self.client.board.stream_game_state(game_id)

    def make_move(self, game_id: str, move: str) -> bool:
        """
        Make a move in an ongoing game.

        Args:
            game_id: The Lichess game ID
            move: The move in UCI notation (e.g., 'e2e4')

        Returns:
            True if the move was accepted
        """
        try:
            self.client.board.make_move(game_id, move)
            return True
        except berserk.exceptions.ResponseError:
            return False

    def create_challenge(
        self,
        username: str | None = None,
        rated: bool = False,
        clock_limit: int | None = None,
        clock_increment: int | None = None,
        color: str = "random",
    ) -> dict[str, Any]:
        """
        Create a challenge (for correspondence or timed games).

        Args:
            username: Opponent username (None for open challenge)
            rated: Whether the game should be rated
            clock_limit: Initial time in seconds (None for correspondence)
            clock_increment: Time increment in seconds
            color: 'white', 'black', or 'random'

        Returns:
            Challenge information including game ID
        """
        # For correspondence games
        if clock_limit is None:
            days = 3  # Default correspondence time
            return self.client.challenges.create(
                username or "",
                rated=rated,
                color=color,
                days=days,
            )
        else:
            return self.client.challenges.create(
                username or "",
                rated=rated,
                color=color,
                clock_limit=clock_limit,
                clock_increment=clock_increment or 0,
            )

    def resign_game(self, game_id: str) -> bool:
        """Resign from an ongoing game."""
        try:
            self.client.board.resign_game(game_id)
            return True
        except berserk.exceptions.ResponseError:
            return False

    def offer_draw(self, game_id: str) -> bool:
        """Offer or accept a draw."""
        try:
            self.client.board.offer_draw(game_id)
            return True
        except berserk.exceptions.ResponseError:
            return False

    def abort_game(self, game_id: str) -> bool:
        """Abort a game (only possible in the first few moves)."""
        try:
            self.client.board.abort_game(game_id)
            return True
        except berserk.exceptions.ResponseError:
            return False

    @staticmethod
    async def validate_token(token: str) -> Optional[dict[str, Any]]:
        """
        Validate an API token and return account info if valid.

        This is an async method for validation during account setup.
        """
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {token}"}
            response = await client.get(
                "https://lichess.org/api/account",
                headers=headers,
            )
            if response.status_code == 200:
                return response.json()
            return None
