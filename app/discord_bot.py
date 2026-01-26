"""
Discord bot module for sending transaction alerts.
Uses discord.py for integration with Discord API.
"""

import asyncio
import logging
from typing import Optional

import discord
from discord.ext import commands

from .iostscan import Transaction

logger = logging.getLogger(__name__)


class DiscordNotifier:
    """
    Discord bot client for sending transaction alerts.
    Manages connection lifecycle and message sending.
    """
    
    def __init__(self, bot_token: str, channel_id: int, wallet_address: str):
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.wallet_address = wallet_address
        
        # Setup Discord intents (minimal - we only need to send messages)
        intents = discord.Intents.default()
        intents.message_content = False  # We don't need to read messages
        
        # Create bot instance
        self.bot = commands.Bot(command_prefix="!", intents=intents)
        self._ready = asyncio.Event()
        self._channel: Optional[discord.TextChannel] = None
        
        # Rate limit handling
        self._rate_limit_delay = 1.0  # Base delay between messages
        self._max_rate_limit_retries = 5
        
        # Register event handlers
        self._setup_events()
    
    def _setup_events(self) -> None:
        """Register Discord bot event handlers."""
        
        @self.bot.event
        async def on_ready():
            logger.info(f"Discord bot connected as {self.bot.user}")
            
            # Get the target channel
            channel = self.bot.get_channel(self.channel_id)
            if channel is None:
                # Try fetching if not in cache
                try:
                    channel = await self.bot.fetch_channel(self.channel_id)
                except discord.NotFound:
                    logger.error(f"Channel {self.channel_id} not found!")
                except discord.Forbidden:
                    logger.error(f"Bot doesn't have access to channel {self.channel_id}")
            
            if channel and isinstance(channel, discord.TextChannel):
                self._channel = channel
                logger.info(f"Target channel: #{channel.name} in {channel.guild.name}")
            else:
                logger.error(f"Could not access channel {self.channel_id}")
            
            self._ready.set()
        
        @self.bot.event
        async def on_disconnect():
            logger.warning("Discord bot disconnected")
            self._ready.clear()
        
        @self.bot.event
        async def on_resumed():
            logger.info("Discord bot connection resumed")
            self._ready.set()
    
    async def start(self) -> None:
        """
        Start the Discord bot in the background.
        Returns immediately, bot runs in a separate task.
        """
        logger.info("Starting Discord bot...")
        
        # Start bot in background task
        asyncio.create_task(self._run_bot())
        
        # Wait for bot to be ready (with timeout)
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=30.0)
            logger.info("Discord bot is ready")
        except asyncio.TimeoutError:
            logger.error("Discord bot failed to connect within 30 seconds")
            raise RuntimeError("Discord bot connection timeout")
    
    async def _run_bot(self) -> None:
        """Run the Discord bot (blocking call wrapped in task)."""
        try:
            await self.bot.start(self.bot_token)
        except discord.LoginFailure:
            logger.error("Invalid Discord bot token!")
            raise
        except Exception as e:
            logger.error(f"Discord bot error: {e}")
            raise
    
    async def stop(self) -> None:
        """Gracefully stop the Discord bot."""
        logger.info("Stopping Discord bot...")
        await self.bot.close()
    
    async def wait_until_ready(self) -> None:
        """Wait until the bot is ready to send messages."""
        await self._ready.wait()
    
    def format_transaction_message(self, tx: Transaction) -> str:
        """
        Format a transaction into a Discord message.
        
        Format:
        🚨 IOST [Received/Sent]!
        Wallet: inwaliost
        From: {from_address}
        To: {to_address}
        Amount: {amount} {token}
        Tx: https://www.iostscan.com/tx/{tx_hash}
        """
        # Determine transaction direction
        if tx.is_incoming(self.wallet_address):
            emoji = "🚨"
            direction = "Received"
        elif tx.is_outgoing(self.wallet_address):
            emoji = "📤"
            direction = "Sent"
        else:
            emoji = "🔔"
            direction = "Activity"
        
        # Format amount nicely
        try:
            amount_float = float(tx.amount)
            if amount_float == int(amount_float):
                amount_str = str(int(amount_float))
            else:
                amount_str = f"{amount_float:.4f}".rstrip('0').rstrip('.')
        except (ValueError, TypeError):
            amount_str = tx.amount
        
        # Build message
        lines = [
            f"{emoji} **IOST {direction}!**",
            f"**Wallet:** `{self.wallet_address}`",
            f"**From:** `{tx.from_address or 'N/A'}`",
            f"**To:** `{tx.to_address or 'N/A'}`",
            f"**Amount:** {amount_str} {tx.token}",
            f"**Action:** {tx.action_name}",
            f"**Tx:** {tx.explorer_url}",
        ]
        
        return "\n".join(lines)
    
    async def send_transaction_alert(self, tx: Transaction) -> bool:
        """
        Send a formatted transaction alert to the Discord channel.
        
        Args:
            tx: Transaction to alert about
            
        Returns:
            True if message was sent successfully, False otherwise
        """
        if not self._ready.is_set():
            logger.warning("Discord bot not ready, cannot send alert")
            return False
        
        if self._channel is None:
            logger.error("No target channel configured")
            return False
        
        message = self.format_transaction_message(tx)
        
        for attempt in range(self._max_rate_limit_retries):
            try:
                await self._channel.send(message)
                logger.info(f"Sent alert for tx {tx.tx_hash[:16]}...")
                
                # Small delay to avoid rate limits
                await asyncio.sleep(self._rate_limit_delay)
                return True
                
            except discord.RateLimited as e:
                # Handle rate limiting with exponential backoff
                retry_after = e.retry_after
                logger.warning(f"Rate limited, waiting {retry_after:.2f}s")
                await asyncio.sleep(retry_after)
                
            except discord.Forbidden:
                logger.error(f"Bot doesn't have permission to send to channel")
                return False
                
            except discord.HTTPException as e:
                logger.error(f"Discord HTTP error: {e}")
                if attempt < self._max_rate_limit_retries - 1:
                    await asyncio.sleep(self._rate_limit_delay * (attempt + 1))
                    
            except Exception as e:
                logger.error(f"Failed to send Discord message: {e}")
                return False
        
        logger.error(f"Failed to send alert after {self._max_rate_limit_retries} attempts")
        return False
    
    async def send_startup_message(self) -> bool:
        """Send a startup notification to confirm bot is working."""
        if self._channel is None:
            return False
        
        try:
            await self._channel.send(
                f"🟢 **IOST Wallet Monitor Started**\n"
                f"Monitoring wallet: `{self.wallet_address}`"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send startup message: {e}")
            return False
