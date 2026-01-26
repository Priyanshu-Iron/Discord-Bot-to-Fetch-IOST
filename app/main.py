"""
IOST Wallet Monitor - FastAPI Application

A production-ready backend that monitors IOST wallets for registered users
and sends transaction alerts via Discord DM or channel mention.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import get_settings
from .discord_bot import DiscordNotifier
from .iostscan import IOSTScanClient, Transaction
from .storage import TransactionStorage, UserStorage

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Global state
discord_notifier: Optional[DiscordNotifier] = None
transaction_storage: Optional[TransactionStorage] = None
user_storage: Optional[UserStorage] = None
polling_task: Optional[asyncio.Task] = None
app_start_time: datetime = datetime.now()
last_poll_time: Optional[datetime] = None
total_alerts_sent: int = 0


async def poll_transactions() -> None:
    """
    Background task that polls IOSTScan for new transactions.
    Monitors only registered wallets.
    """
    global last_poll_time, total_alerts_sent
    
    settings = get_settings()
    logger.info(f"Poll interval: {settings.poll_interval_seconds} seconds")
    
    async with IOSTScanClient(
        base_url=settings.iostscan_base_url,
        timeout=settings.iostscan_api_timeout,
        max_retries=settings.max_retries,
    ) as client:
        while True:
            try:
                last_poll_time = datetime.now()
                
                # Get all unique registered wallets only
                wallets = user_storage.get_all_wallets() if user_storage else set()
                
                if not wallets:
                    logger.debug("No registered wallets to monitor")
                else:
                    logger.debug(f"Polling {len(wallets)} wallet(s): {wallets}")
                    
                    for wallet in wallets:
                        await poll_wallet(client, wallet)
                
            except asyncio.CancelledError:
                logger.info("Polling task cancelled, shutting down...")
                break
                
            except Exception as e:
                logger.error(f"Error during polling: {e}", exc_info=True)
            
            await asyncio.sleep(settings.poll_interval_seconds)


async def poll_wallet(client: IOSTScanClient, wallet: str) -> None:
    """Poll a single wallet for new transactions."""
    global total_alerts_sent
    
    try:
        transactions = await client.fetch_actions(account=wallet, page=1, size=20)
        
        if not transactions:
            return
        
        logger.debug(f"Fetched {len(transactions)} transactions for {wallet}")
        
        # Find new transactions
        new_transactions = []
        for tx in transactions:
            if not transaction_storage.is_processed(tx.tx_hash):
                new_transactions.append(tx)
        
        if not new_transactions:
            return
        
        logger.info(f"Found {len(new_transactions)} new transaction(s) for {wallet}")
        
        # Process in chronological order (oldest first)
        for tx in reversed(new_transactions):
            await process_transaction(tx, wallet)
            await transaction_storage.mark_processed(tx.tx_hash)
            
    except Exception as e:
        logger.error(f"Error polling wallet {wallet}: {e}")


async def process_transaction(tx: Transaction, wallet: str) -> None:
    """Process a single transaction and send alerts."""
    global total_alerts_sent
    
    if not discord_notifier:
        return
    
    # Log transaction details
    logger.info(f"Processing tx: {tx.tx_hash[:16]}... | From: {tx.from_address} -> To: {tx.to_address} | Amount: {tx.amount} {tx.token}")
    
    # Get users registered for this wallet
    user_ids = user_storage.get_users_for_wallet(wallet) if user_storage else set()
    
    if user_ids:
        # Send channel alerts with user mentions
        sent_count = await discord_notifier.send_user_alert(tx, user_ids, wallet)
        total_alerts_sent += sent_count
        logger.info(f"Sent channel alert for wallet {wallet} (mentioned {sent_count} user(s))")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global discord_notifier, transaction_storage, user_storage, polling_task, app_start_time
    
    settings = get_settings()
    app_start_time = datetime.now()
    
    logger.info("=" * 50)
    logger.info("IOST Wallet Monitor Starting")
    logger.info("=" * 50)
    
    # Initialize transaction storage
    logger.info("Initializing transaction storage...")
    transaction_storage = TransactionStorage(settings.storage_file_path)
    await transaction_storage.load()
    
    # Initialize user storage
    logger.info("Initializing user storage...")
    user_storage = UserStorage("data/users.json")
    await user_storage.load()
    
    # Initialize Discord bot
    logger.info("Initializing Discord bot...")
    discord_notifier = DiscordNotifier(
        bot_token=settings.discord_bot_token,
        channel_id=settings.discord_channel_id,
    )
    discord_notifier.set_user_storage(user_storage)
    
    try:
        await discord_notifier.start()
        await discord_notifier.send_startup_message()
    except Exception as e:
        logger.error(f"Failed to start Discord bot: {e}")
        raise
    
    # Start polling task
    logger.info("Starting transaction polling...")
    polling_task = asyncio.create_task(poll_transactions())
    
    logger.info("Application startup complete!")
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    
    if polling_task:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    
    if discord_notifier:
        await discord_notifier.stop()
    
    logger.info("Shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="IOST Wallet Monitor",
    description="Monitors IOST wallets and sends Discord alerts for new transactions",
    version="1.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "iost-wallet-monitor"}


@app.get("/status")
async def get_status():
    """Detailed status endpoint."""
    settings = get_settings()
    
    uptime = datetime.now() - app_start_time
    uptime_str = str(timedelta(seconds=int(uptime.total_seconds())))
    
    registered_users = len(user_storage.get_all_users()) if user_storage else 0
    monitored_wallets = list(user_storage.get_all_wallets()) if user_storage else []
    
    return JSONResponse(content={
        "service": "iost-wallet-monitor",
        "status": "running",
        "version": "1.1.0",
        "uptime": uptime_str,
        "last_poll": last_poll_time.isoformat() if last_poll_time else None,
        "total_alerts_sent": total_alerts_sent,
        "last_tx_hash": transaction_storage.get_last_tx_hash() if transaction_storage else None,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "discord_connected": discord_notifier._ready.is_set() if discord_notifier else False,
        "registered_users": registered_users,
        "monitored_wallets": monitored_wallets,
    })


@app.get("/users")
async def get_users():
    """Get list of registered users (admin endpoint)."""
    if not user_storage:
        return {"users": {}}
    
    users = user_storage.get_all_users()
    return {"count": len(users), "users": users}


@app.get("/")
async def root():
    """Root endpoint with basic service info."""
    return {
        "service": "IOST Wallet Monitor",
        "version": "1.1.0",
        "docs": "/docs",
        "health": "/health",
        "status": "/status",
    }


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
