"""
Storage module for persisting transaction state.
Uses JSON file storage to track last processed tx_hash.
This prevents duplicate alerts on application restarts.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Set
import aiofiles
import asyncio

logger = logging.getLogger(__name__)


class TransactionStorage:
    """
    Async-safe storage for tracking processed transactions.
    Persists to a JSON file to survive restarts.
    """
    
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self._last_tx_hash: Optional[str] = None
        self._processed_hashes: Set[str] = set()  # Track multiple recent tx hashes
        self._lock = asyncio.Lock()
        self._max_stored_hashes = 100  # Keep last 100 tx hashes to prevent memory growth
    
    async def load(self) -> None:
        """
        Load saved state from the JSON file.
        Creates the parent directory if it doesn't exist.
        """
        # Ensure parent directory exists
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        
        if not self.file_path.exists():
            logger.info(f"Storage file not found at {self.file_path}, starting fresh")
            return
        
        try:
            async with aiofiles.open(self.file_path, mode="r") as f:
                content = await f.read()
                if content.strip():
                    data = json.loads(content)
                    self._last_tx_hash = data.get("last_tx_hash")
                    self._processed_hashes = set(data.get("processed_hashes", []))
                    logger.info(f"Loaded last tx_hash: {self._last_tx_hash}")
                    logger.info(f"Loaded {len(self._processed_hashes)} processed hashes")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse storage file: {e}")
        except Exception as e:
            logger.error(f"Failed to load storage: {e}")
    
    async def save(self) -> None:
        """
        Persist current state to the JSON file.
        Uses atomic write pattern to prevent corruption.
        """
        async with self._lock:
            try:
                # Trim processed hashes if they exceed max
                if len(self._processed_hashes) > self._max_stored_hashes:
                    # Keep only the most recent ones (this is a simple trim)
                    self._processed_hashes = set(list(self._processed_hashes)[-self._max_stored_hashes:])
                
                data = {
                    "last_tx_hash": self._last_tx_hash,
                    "processed_hashes": list(self._processed_hashes),
                }
                
                # Write to temp file first, then rename for atomic operation
                temp_path = self.file_path.with_suffix(".tmp")
                async with aiofiles.open(temp_path, mode="w") as f:
                    await f.write(json.dumps(data, indent=2))
                
                # Atomic rename
                temp_path.rename(self.file_path)
                logger.debug(f"Saved state to {self.file_path}")
                
            except Exception as e:
                logger.error(f"Failed to save storage: {e}")
    
    def get_last_tx_hash(self) -> Optional[str]:
        """Return the last processed tx_hash."""
        return self._last_tx_hash
    
    def is_processed(self, tx_hash: str) -> bool:
        """Check if a transaction has already been processed."""
        return tx_hash in self._processed_hashes
    
    async def mark_processed(self, tx_hash: str) -> None:
        """
        Mark a transaction as processed and update the last tx_hash.
        Persists immediately to prevent data loss.
        """
        self._last_tx_hash = tx_hash
        self._processed_hashes.add(tx_hash)
        await self.save()
        logger.debug(f"Marked tx {tx_hash} as processed")
    
    async def mark_multiple_processed(self, tx_hashes: list[str]) -> None:
        """
        Mark multiple transactions as processed in one operation.
        More efficient than marking one at a time.
        """
        if not tx_hashes:
            return
        
        for tx_hash in tx_hashes:
            self._processed_hashes.add(tx_hash)
        
        # Update last tx hash to the most recent one (first in the list)
        self._last_tx_hash = tx_hashes[0]
        await self.save()
        logger.debug(f"Marked {len(tx_hashes)} transactions as processed")
