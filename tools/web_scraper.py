"""
Web Scraper Tool - DigiKey and Mouser API integration.

Scrapes component data from DigiKey and Mouser APIs for component search.
Caches results in ChromaDB for future searches.
"""

import logging
from typing import Optional, List
from datetime import datetime

import httpx

from config import settings
from schemas.component import Component

logger = logging.getLogger(__name__)


class WebScraperTool:
    """
    Web scraper for component distributor APIs.

    Supports:
    - DigiKey API v3 (OAuth2)
    - Mouser Search API v2 (key-based)
    """

    def __init__(self):
        self.digikey_token: Optional[str] = None
        self.digikey_token_expiry: Optional[datetime] = None
        self._mouser_session = None

    async def search_digikey(
        self,
        keyword: str,
        category: Optional[str] = None,
        limit: int = 10,
    ) -> List[Component]:
        """
        Search DigiKey API for components.

        Args:
            keyword: Search keyword
            category: Optional category filter
            limit: Max results

        Returns:
            List of Component objects
        """
        if not settings.digikey_client_id or not settings.digikey_client_secret:
            logger.warning("DigiKey API credentials not configured")
            return []

        try:
            # Ensure we have a valid token
            await self._ensure_digikey_token()

            async with httpx.AsyncClient(timeout=30.0) as client:
                # DigiKey Keyword Search API
                headers = {
                    "Authorization": f"Bearer {self.digikey_token}",
                    "X-DIGIKEY-Client-Id": settings.digikey_client_id,
                }

                params = {
                    "Keywords": keyword,
                    "Limit": limit,
                    "RecordCount": limit,
                }

                response = await client.get(
                    f"{settings.digikey_api_url}/Search/Keyword",
                    headers=headers,
                    params=params,
                )
                response.raise_for_status()
                data = response.json()

                # Parse products
                components = []
                products = data.get("Products", []) if data else []

                for product in products[:limit]:
                    # Extract key specs
                    specs = {}
                    parameters = product.get("Parameters", []) or []
                    for param in parameters:
                        if param.get("Parameter") and param.get("Value"):
                            specs[param["Parameter"]] = param["Value"]

                    component = Component(
                        part_number=product.get("ManufacturerPartNumber", ""),
                        manufacturer=product.get("Manufacturer", {}).get("Name", ""),
                        description=product.get("DetailedDescription", product.get("ProductDescription", "")),
                        category=product.get("Category", {}).get("Name", "Unknown"),
                        key_specs=specs,
                        datasheet_url=product.get("DatasheetUrl", ""),
                        lifecycle_status=product.get("ProductStatus", "unknown").lower(),
                        estimated_cost_usd=self._parse_digikey_pricing(product.get("ProductVariations", [])),
                    )
                    components.append(component)

                logger.info(f"DigiKey search '{keyword}': {len(components)} results")
                return components

        except httpx.HTTPStatusError as e:
            logger.error(f"DigiKey API error: {e.response.status_code} - {e.response.text}")
            return []
        except Exception as e:
            logger.error(f"DigiKey search failed: {e}")
            return []

    async def search_mouser(
        self,
        keyword: str,
        category: Optional[str] = None,
        limit: int = 10,
    ) -> List[Component]:
        """
        Search Mouser API for components.

        Args:
            keyword: Search keyword
            category: Optional category filter
            limit: Max results

        Returns:
            List of Component objects
        """
        if not settings.mouser_api_key:
            logger.warning("Mouser API key not configured")
            return []

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {
                    "Content-Type": "application/json",
                }

                # Mouser Search API
                body = {
                    "SearchByKeywordRequest": {
                        "keyword": keyword,
                        "records": limit,
                        "startingRecord": 0,
                        "searchOptions": "stringInclusive",
                        "searchWithYourSignUpInfo": True,
                    }
                }

                response = await client.post(
                    f"{settings.mouser_api_url}/search/keyword",
                    headers=headers,
                    params={"apiKey": settings.mouser_api_key},
                    json=body,
                )
                response.raise_for_status()
                data = response.json()

                # Parse parts
                components = []
                parts = data.get("SearchByKeywordResponse", {}).get("Parts", []) or []

                for part in parts[:limit]:
                    # Extract attributes as specs
                    specs = {}
                    for attr in part.get("Attributes", []) or []:
                        if attr.get("AttributeName") and attr.get("AttributeValue"):
                            specs[attr["AttributeName"]] = attr["AttributeValue"]

                    component = Component(
                        part_number=part.get("ManufacturerPartNumber", ""),
                        manufacturer=part.get("Manufacturer", ""),
                        description=part.get("Description", ""),
                        category=part.get("Category", "Unknown"),
                        key_specs=specs,
                        datasheet_url=part.get("DataSheetUrl", ""),
                        lifecycle_status=part.get("LifecycleStatus", "unknown").lower(),
                        estimated_cost_usd=self._parse_mouser_pricing(part.get("PriceBreaks", [])),
                    )
                    components.append(component)

                logger.info(f"Mouser search '{keyword}': {len(components)} results")
                return components

        except httpx.HTTPStatusError as e:
            logger.error(f"Mouser API error: {e.response.status_code} - {e.response.text}")
            return []
        except Exception as e:
            logger.error(f"Mouser search failed: {e}")
            return []

    async def search_all(
        self,
        keyword: str,
        category: Optional[str] = None,
        limit: int = 10,
    ) -> List[Component]:
        """
        Search both DigiKey and Mouser, combine results.

        Args:
            keyword: Search keyword
            category: Optional category filter
            limit: Max results per source

        Returns:
            Combined list of Component objects (deduplicated by part number)
        """
        import asyncio

        results = await asyncio.gather(
            self.search_digikey(keyword, category, limit),
            self.search_mouser(keyword, category, limit),
            return_exceptions=True,
        )

        all_components = []
        seen = set()

        for result in results:
            if isinstance(result, list):
                for comp in result:
                    if comp.part_number and comp.part_number not in seen:
                        all_components.append(comp)
                        seen.add(comp.part_number)

        return all_components

    async def _ensure_digikey_token(self):
        """Get or refresh DigiKey OAuth2 access token."""
        now = datetime.now()

        # Check if token is still valid (1 hour buffer)
        if self.digikey_token and self.digikey_token_expiry:
            if (self.digikey_token_expiry - now).total_seconds() > 3600:
                return

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # DigiKey token endpoint
                response = await client.post(
                    "https://api.digikey.com/v1/oauth2/token",
                    data={
                        "client_id": settings.digikey_client_id,
                        "client_secret": settings.digikey_client_secret,
                        "grant_type": "client_credentials",
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()
                data = response.json()

                self.digikey_token = data.get("access_token")
                expires_in = data.get("expires_in", 3600)
                self.digikey_token_expiry = datetime.fromtimestamp(
                    now.timestamp() + expires_in
                )

                logger.info("DigiKey access token refreshed")

        except Exception as e:
            logger.error(f"Failed to get DigiKey token: {e}")
            raise

    def _parse_digikey_pricing(self, variations: List) -> Optional[float]:
        """Parse pricing from DigiKey product variations."""
        if not variations:
            return None

        try:
            # Get unit price at quantity 1
            for variation in variations:
                if variation.get("Quantity") == 1:
                    prices = variation.get("StandardPricing", [])
                    if prices and isinstance(prices, list):
                        # Extract numeric price from string like "$1.23"
                        price_str = prices[0] if prices else ""
                        if price_str:
                            return float(price_str.replace("$", "").replace(",", "").strip())
            return None
        except Exception:
            return None

    def _parse_mouser_pricing(self, price_breaks: List) -> Optional[float]:
        """Parse pricing from Mouser price breaks."""
        if not price_breaks:
            return None

        try:
            # Get unit price at quantity 1
            for pb in price_breaks:
                qty = pb.get("Quantity", 0)
                price = pb.get("Price", "")
                if qty == 1 and price:
                    return float(price.replace("$", "").replace(",", "").strip())
            return None
        except Exception:
            return None
