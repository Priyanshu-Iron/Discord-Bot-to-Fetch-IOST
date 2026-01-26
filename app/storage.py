"""
Storage module for persisting transaction state and user registrations.
Uses JSON file storage for persistence across restarts.
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Set, Dict
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
        self._processed_hashes: Set[str] = set()
        self._lock = asyncio.Lock()
        self._max_stored_hashes = 100
    
    async def load(self) -> None:
        """Load saved state from the JSON file."""
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
        """Persist current state to the JSON file with atomic write."""
        async with self._lock:
            try:
                if len(self._processed_hashes) > self._max_stored_hashes:
                    self._processed_hashes = set(list(self._processed_hashes)[-self._max_stored_hashes:])
                
                data = {
                    "last_tx_hash": self._last_tx_hash,
                    "processed_hashes": list(self._processed_hashes),
                }
                
                temp_path = self.file_path.with_suffix(".tmp")
                async with aiofiles.open(temp_path, mode="w") as f:
                    await f.write(json.dumps(data, indent=2))
                
                temp_path.rename(self.file_path)
                logger.debug(f"Saved state to {self.file_path}")
                
            except Exception as e:
                logger.error(f"Failed to save storage: {e}")
    
    def get_last_tx_hash(self) -> Optional[str]:
        return self._last_tx_hash
    
    def is_processed(self, tx_hash: str) -> bool:
        return tx_hash in self._processed_hashes
    
    async def mark_processed(self, tx_hash: str) -> None:
        self._last_tx_hash = tx_hash
        self._processed_hashes.add(tx_hash)
        await self.save()
        logger.debug(f"Marked tx {tx_hash} as processed")
    
    async def mark_multiple_processed(self, tx_hashes: list[str]) -> None:
        if not tx_hashes:
            return
        
        for tx_hash in tx_hashes:
            self._processed_hashes.add(tx_hash)
        
        self._last_tx_hash = tx_hashes[0]
        await self.save()
        logger.debug(f"Marked {len(tx_hashes)} transactions as processed")


class UserStorage:
    """
    Async-safe storage for user wallet registrations.
    Structure: { "discord_user_id": { "wallet": "...", "registered_at": "..." } }
    """
    
    def __init__(self, file_path: str = "data/users.json"):
        self.file_path = Path(file_path)
        self._users: Dict[str, dict] = {}
        self._wallet_to_users: Dict[str, Set[str]] = {}  # wallet -> set of user IDs
        self._lock = asyncio.Lock()
    
    async def load(self) -> None:
        """Load user registrations from JSON file."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        
        if not self.file_path.exists():
            logger.info(f"User storage not found at {self.file_path}, starting fresh")
            return
        
        try:
            async with aiofiles.open(self.file_path, mode="r") as f:
                content = await f.read()
                if content.strip():
                    self._users = json.loads(content)
                    self._rebuild_wallet_index()
                    logger.info(f"Loaded {len(self._users)} registered users")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse user storage: {e}")
        except Exception as e:
            logger.error(f"Failed to load user storage: {e}")
    
    def _rebuild_wallet_index(self) -> None:
        """Rebuild the wallet -> users lookup index."""
        self._wallet_to_users.clear()
        for user_id, data in self._users.items():
            wallet = data.get("wallet", "").lower()
            if wallet:
                if wallet not in self._wallet_to_users:
                    self._wallet_to_users[wallet] = set()
                self._wallet_to_users[wallet].add(user_id)
    
    async def save(self) -> None:
        """Persist user registrations to JSON file with atomic write."""
        async with self._lock:
            try:
                temp_path = self.file_path.with_suffix(".tmp")
                async with aiofiles.open(temp_path, mode="w") as f:
                    await f.write(json.dumps(self._users, indent=2))
                
                temp_path.rename(self.file_path)
                logger.debug(f"Saved user storage to {self.file_path}")
                
            except Exception as e:
                logger.error(f"Failed to save user storage: {e}")
    
    async def register_user(self, user_id: str, wallet: str) -> bool:
        """
        Register or update a user's wallet.
        Returns True if successful.
        """
        wallet = wallet.strip().lower()
        
        if not wallet:
            return False
        
        # Remove user from old wallet index if re-registering
        old_wallet = self._users.get(user_id, {}).get("wallet")
        if old_wallet and old_wallet in self._wallet_to_users:
            self._wallet_to_users[old_wallet].discard(user_id)
        
        # Store new registration
        self._users[user_id] = {
            "wallet": wallet,
            "registered_at": datetime.utcnow().isoformat() + "Z",
        }
        
        # Update wallet index
        if wallet not in self._wallet_to_users:
            self._wallet_to_users[wallet] = set()
        self._wallet_to_users[wallet].add(user_id)
        
        await self.save()
        logger.info(f"Registered user {user_id} with wallet {wallet}")
        return True
    
    def get_user_wallet(self, user_id: str) -> Optional[str]:
        """Get a user's registered wallet."""
        return self._users.get(user_id, {}).get("wallet")
    
    def get_users_for_wallet(self, wallet: str) -> Set[str]:
        """Get all user IDs registered for a wallet."""
        return self._wallet_to_users.get(wallet.lower(), set())
    
    def get_all_wallets(self) -> Set[str]:
        """Get all unique registered wallets."""
        return set(self._wallet_to_users.keys())
    
    def get_all_users(self) -> Dict[str, dict]:
        """Get all user registrations."""
        return self._users.copy()

