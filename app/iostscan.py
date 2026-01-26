"""
IOSTScan API client module.
Handles polling the IOSTScan actions API for wallet transactions.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import httpx

logger = logging.getLogger(__name__)


@dataclass
class Transaction:
    """Represents a parsed IOST transaction."""
    tx_hash: str
    action_name: str
    from_address: str
    to_address: str
    amount: str
    token: str
    timestamp: datetime
    status: str
    memo: str
    contract: str
    raw_data: dict  # Keep raw data for debugging
    
    @property
    def explorer_url(self) -> str:
        """Generate the IOSTScan explorer URL for this transaction."""
        return f"https://www.iostscan.com/tx/{self.tx_hash}"
    
    def is_incoming(self, wallet_address: str) -> bool:
        """Check if this is an incoming transaction for the given wallet."""
        return self.to_address.lower() == wallet_address.lower()
    
    def is_outgoing(self, wallet_address: str) -> bool:
        """Check if this is an outgoing transaction from the given wallet."""
        return self.from_address.lower() == wallet_address.lower()


class IOSTScanClient:
    """
    Async client for the IOSTScan actions API.
    Handles API calls, response parsing, and error handling.
    """
    
    def __init__(
        self,
        base_url: str = "https://www.iostscan.com",
        timeout: int = 30,
        max_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None
    
    async def __aenter__(self):
        """Create async HTTP client on context enter."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            headers={
                "Accept": "application/json",
                "User-Agent": "IOST-Wallet-Monitor/1.0",
            },
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Close async HTTP client on context exit."""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def fetch_actions(
        self,
        account: str,
        page: int = 1,
        size: int = 20,
    ) -> list[Transaction]:
        """
        Fetch account actions from IOSTScan API.
        
        Args:
            account: The IOST account/wallet address to query
            page: Page number for pagination (1-indexed)
            size: Number of results per page
            
        Returns:
            List of Transaction objects, newest first
        """
        if not self._client:
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")
        
        # Correct API endpoint: /api/account/{account}/actions
        url = f"{self.base_url}/api/account/{account}/actions"
        params = {
            "page": page,
            "size": size,
        }
        
        for attempt in range(self.max_retries):
            try:
                logger.debug(f"Fetching actions for {account}, attempt {attempt + 1}")
                logger.debug(f"URL: {url} with params: {params}")
                response = await self._client.get(url, params=params)
                
                # Handle various response codes
                if response.status_code == 204:
                    # No content - empty response, likely no transactions
                    logger.debug("Received 204 No Content - no transactions found")
                    return []
                
                if response.status_code == 200:
                    data = response.json()
                    logger.debug(f"Received response with {len(data.get('actions', []))} actions")
                    return self._parse_response(data)
                
                if response.status_code >= 500:
                    logger.warning(f"Server error {response.status_code}, retrying...")
                    continue
                
                # Client errors (4xx) - don't retry
                logger.error(f"Client error: {response.status_code} - {response.text}")
                return []
                
            except httpx.TimeoutException:
                logger.warning(f"Request timeout, attempt {attempt + 1}/{self.max_retries}")
            except httpx.RequestError as e:
                logger.warning(f"Request error: {e}, attempt {attempt + 1}/{self.max_retries}")
            except Exception as e:
                logger.error(f"Unexpected error fetching actions: {e}")
                return []
        
        logger.error(f"Failed to fetch actions after {self.max_retries} attempts")
        return []
    
    def _parse_response(self, response_data: dict) -> list[Transaction]:
        """
        Parse the API response into Transaction objects.
        
        Expected format:
        {
            "actions": [
                {
                    "tx_hash": "...",
                    "contract": "token.iost",
                    "action_name": "transfer",
                    "data": ["iost", "from_addr", "to_addr", "amount", "memo"],
                    "created_at": "2026-01-26T14:09:44.196Z"
                }
            ]
        }
        """
        transactions = []
        
        # Get the actions array
        if isinstance(response_data, list):
            actions = response_data
        elif isinstance(response_data, dict):
            actions = response_data.get("actions") or response_data.get("data") or []
        else:
            logger.warning(f"Unexpected response format: {type(response_data)}")
            return []
        
        for action in actions:
            try:
                tx = self._parse_action(action)
                if tx:
                    transactions.append(tx)
            except Exception as e:
                logger.warning(f"Failed to parse action: {e}", exc_info=True)
                continue
        
        return transactions
    
    def _parse_action(self, action: dict) -> Optional[Transaction]:
        """
        Parse a single action into a Transaction object.
        
        The data field is an array: ["token", "from", "to", "amount", "memo"]
        For token.iost::transfer actions.
        """
        # Extract tx_hash
        tx_hash = (
            action.get("tx_hash") or 
            action.get("txHash") or 
            action.get("hash") or
            action.get("trx_hash")
        )
        
        if not tx_hash:
            logger.debug(f"Action missing tx_hash: {action}")
            return None
        
        # Extract action name and contract
        action_name = (
            action.get("action_name") or 
            action.get("actionName") or 
            action.get("action") or
            "unknown"
        )
        
        contract = action.get("contract", "")
        
        # Parse the data array for transfer actions
        # Format: ["token", "from", "to", "amount", "memo"]
        data = action.get("data", [])
        
        # Handle both string JSON and actual list
        if isinstance(data, str):
            try:
                import json
                data = json.loads(data)
            except:
                data = []
        
        # Extract from data array if it's a transfer
        if isinstance(data, list) and len(data) >= 4:
            token = data[0] if len(data) > 0 else "IOST"
            from_address = data[1] if len(data) > 1 else ""
            to_address = data[2] if len(data) > 2 else ""
            amount = str(data[3]) if len(data) > 3 else "0"
            memo = data[4] if len(data) > 4 else ""
        else:
            # Fallback to direct fields
            from_address = action.get("from", action.get("from_address", ""))
            to_address = action.get("to", action.get("to_address", ""))
            amount = str(action.get("amount", action.get("value", "0")))
            token = action.get("token", action.get("symbol", "IOST"))
            memo = action.get("memo", "")
        
        # Normalize token name
        if token.lower() == "iost":
            token = "IOST"
        
        # Parse timestamp
        timestamp_raw = action.get("created_at") or action.get("timestamp") or action.get("time")
        if isinstance(timestamp_raw, (int, float)):
            # Assume Unix timestamp (seconds or milliseconds)
            if timestamp_raw > 1e12:  # Milliseconds
                timestamp = datetime.fromtimestamp(timestamp_raw / 1000)
            else:  # Seconds
                timestamp = datetime.fromtimestamp(timestamp_raw)
        elif isinstance(timestamp_raw, str):
            try:
                # Handle ISO format with Z suffix
                timestamp = datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
            except ValueError:
                timestamp = datetime.now()
        else:
            timestamp = datetime.now()
        
        # Extract status
        status = action.get("status", action.get("state", "success"))
        if isinstance(status, bool):
            status = "success" if status else "failed"
        
        return Transaction(
            tx_hash=tx_hash,
            action_name=action_name,
            from_address=from_address,
            to_address=to_address,
            amount=amount,
            token=token,
            timestamp=timestamp,
            status=str(status),
            memo=memo,
            contract=contract,
            raw_data=action,
        )
