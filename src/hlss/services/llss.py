"""
LLSS (Low Level Screen Service) integration service.
"""

import hashlib
from datetime import datetime
from io import BytesIO
from typing import Any

import httpx

from hlss.config import get_settings


class LLSSService:
    """Service for communicating with LLSS."""

    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.llss_base_url
        self.api_token = self.settings.llss_api_token

    def _get_headers(self) -> dict[str, str]:
        """Get authorization headers for LLSS requests."""
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

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
                headers=self._get_headers(),
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
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/instances/{instance_id}/frames",
                headers={
                    "Authorization": f"Bearer {self.api_token}" if self.api_token else "",
                    "Content-Type": "image/png",
                },
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
                headers=self._get_headers(),
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
