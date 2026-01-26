"""
IOST Wallet Monitor - FastAPI Application

A production-ready backend that monitors an IOST wallet using IOSTScan
and sends transaction alerts to a Discord channel.
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
from .storage import TransactionStorage

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Global state
discord_notifier: Optional[DiscordNotifier] = None
transaction_storage: Optional[TransactionStorage] = None
polling_task: Optional[asyncio.Task] = None
app_start_time: datetime = datetime.now()
last_poll_time: Optional[datetime] = None
total_alerts_sent: int = 0


async def poll_transactions() -> None:
    """
    Background task that polls IOSTScan for new transactions.
    Runs continuously until the application shuts down.
    """
    global last_poll_time, total_alerts_sent
    
    settings = get_settings()
    logger.info(f"Starting transaction polling for wallet: {settings.iost_wallet_address}")
    logger.info(f"Poll interval: {settings.poll_interval_seconds} seconds")
    
    async with IOSTScanClient(
        base_url=settings.iostscan_base_url,
        timeout=settings.iostscan_api_timeout,
        max_retries=settings.max_retries,
    ) as client:
        while True:
            try:
                last_poll_time = datetime.now()
                logger.debug("Polling IOSTScan for new transactions...")
                
                # Fetch recent transactions
                transactions = await client.fetch_actions(
                    account=settings.iost_wallet_address,
                    page=1,
                    size=20,
                )
                
                if not transactions:
                    logger.debug("No transactions found")
                else:
                    logger.debug(f"Fetched {len(transactions)} transactions")
                    
                    # Process transactions (newest first, but we want oldest new ones first for alerts)
                    new_transactions = []
                    for tx in transactions:
                        if not transaction_storage.is_processed(tx.tx_hash):
                            new_transactions.append(tx)
                    
                    if new_transactions:
                        logger.info(f"Found {len(new_transactions)} new transaction(s)")
                        
                        # Process in chronological order (oldest first)
                        for tx in reversed(new_transactions):
                            # Send Discord alert
                            if discord_notifier:
                                success = await discord_notifier.send_transaction_alert(tx)
                                if success:
                                    total_alerts_sent += 1
                            
                            # Mark as processed (even if alert fails, to prevent spam on retry)
                            await transaction_storage.mark_processed(tx.tx_hash)
                
            except asyncio.CancelledError:
                logger.info("Polling task cancelled, shutting down...")
                break
                
            except Exception as e:
                logger.error(f"Error during polling: {e}", exc_info=True)
            
            # Wait before next poll
            await asyncio.sleep(settings.poll_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Handles startup and shutdown of background services.
    """
    global discord_notifier, transaction_storage, polling_task, app_start_time
    
    settings = get_settings()
    app_start_time = datetime.now()
    
    logger.info("=" * 50)
    logger.info("IOST Wallet Monitor Starting")
    logger.info(f"Wallet: {settings.iost_wallet_address}")
    logger.info("=" * 50)
    
    # Initialize storage
    logger.info("Initializing transaction storage...")
    transaction_storage = TransactionStorage(settings.storage_file_path)
    await transaction_storage.load()
    
    # Initialize Discord bot
    logger.info("Initializing Discord bot...")
    discord_notifier = DiscordNotifier(
        bot_token=settings.discord_bot_token,
        channel_id=settings.discord_channel_id,
        wallet_address=settings.iost_wallet_address,
    )
    
    try:
        await discord_notifier.start()
        
        # Send startup message
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
    
    # Cancel polling task
    if polling_task:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    
    # Stop Discord bot
    if discord_notifier:
        await discord_notifier.stop()
    
    logger.info("Shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="IOST Wallet Monitor",
    description="Monitors an IOST wallet and sends Discord alerts for new transactions",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """
    Health check endpoint.
    Returns basic service health status.
    """
    return {"status": "healthy", "service": "iost-wallet-monitor"}


@app.get("/status")
async def get_status():
    """
    Detailed status endpoint.
    Returns information about the monitoring service.
    """
    settings = get_settings()
    
    uptime = datetime.now() - app_start_time
    uptime_str = str(timedelta(seconds=int(uptime.total_seconds())))
    
    return JSONResponse(content={
        "service": "iost-wallet-monitor",
        "status": "running",
        "wallet": settings.iost_wallet_address,
        "uptime": uptime_str,
        "last_poll": last_poll_time.isoformat() if last_poll_time else None,
        "total_alerts_sent": total_alerts_sent,
        "last_tx_hash": transaction_storage.get_last_tx_hash() if transaction_storage else None,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "discord_connected": discord_notifier._ready.is_set() if discord_notifier else False,
    })


@app.get("/")
async def root():
    """Root endpoint with basic service info."""
    return {
        "service": "IOST Wallet Monitor",
        "version": "1.0.0",
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
