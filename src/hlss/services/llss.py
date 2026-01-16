"""
LLSS (Low Level Screen Service) integration service.
"""

import hashlib
from typing import Any

import httpx

from hlss.config import get_settings
from hlss.security import create_llss_token


class LLSSService:
    """Service for communicating with LLSS."""

    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.llss_base_url
        self.api_token = self.settings.llss_api_token

    def _build_headers(
        self,
        content_type: str,
        token_type: str | None = None,
        subject: str | None = None,
    ) -> dict[str, str]:
        """Build headers for LLSS requests with shared-key JWTs."""
        headers = {"Content-Type": content_type}

        token: str | None = None
        if self.settings.hlss_shared_key and token_type:
            token = create_llss_token(token_type=token_type, subject=subject)
        elif self.api_token:
            token = self.api_token

        if token:
            headers["Authorization"] = f"Bearer {token}"

        return headers

    def _get_orchestrator_headers(self) -> dict[str, str]:
        return self._build_headers(
            content_type="application/json",
            token_type="llss_admin",
            subject=self.settings.app_name,
        )

    def _get_instance_headers(self, instance_id: str) -> dict[str, str]:
        return self._build_headers(
            content_type="application/json",
            token_type="instance_access",
            subject=instance_id,
        )

    async def create_instance(self, name: str, instance_type: str = "chess") -> dict[str, Any]:
        """
        Create a new instance in LLSS.

        Args:
            name: Human-readable name for the instance
            instance_type: Type of instance (e.g., 'chess')

        Returns:
            Instance creation response including instance_id
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/instances",
                headers=self._get_orchestrator_headers(),
                json={"name": name, "type": instance_type},
            )
            response.raise_for_status()
            return response.json()

    async def submit_frame(
        self,
        instance_id: str,
        image_data: bytes,
    ) -> dict[str, Any]:
        """
        Submit a rendered frame to LLSS.

        Args:
            instance_id: The LLSS instance ID
            image_data: PNG image data

        Returns:
            Frame creation response including frame_id and hash
        """
        headers = self._build_headers(
            content_type="image/png",
            token_type="instance_access",
            subject=instance_id,
        )

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/instances/{instance_id}/frames",
                headers=headers,
                content=image_data,
            )
            response.raise_for_status()
            return response.json()

    async def notify_state_change(self, instance_id: str) -> bool:
        """
        Notify LLSS that the instance state has changed.

        Args:
            instance_id: The LLSS instance ID

        Returns:
            True if notification was accepted
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/instances/{instance_id}/notify",
                headers=self._get_instance_headers(instance_id),
            )
            return response.status_code == 202

    async def health_check(self) -> bool:
        """Check if LLSS is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/health")
                return response.status_code == 200
        except httpx.RequestError:
            return False

    @staticmethod
    def compute_frame_hash(image_data: bytes) -> str:
        """Compute SHA256 hash of frame data."""
        return hashlib.sha256(image_data).hexdigest()
