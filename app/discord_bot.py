"""
Discord bot module for sending transaction alerts and handling slash commands.
Uses discord.py for integration with Discord API.
"""

import asyncio
import logging
from typing import Optional, Set, TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from .config import get_settings
from .iostscan import Transaction

if TYPE_CHECKING:
    from .storage import UserStorage

logger = logging.getLogger(__name__)


class DiscordNotifier:
    """
    Discord bot client for sending transaction alerts and handling registrations.
    """
    
    def __init__(self, bot_token: str, channel_id: int):
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.user_storage: Optional["UserStorage"] = None
        
        # Setup Discord intents
        intents = discord.Intents.default()
        intents.message_content = False
        
        # Create bot instance
        self.bot = commands.Bot(command_prefix="!", intents=intents)
        self._ready = asyncio.Event()
        self._channel: Optional[discord.TextChannel] = None
        
        # Rate limit handling
        self._rate_limit_delay = 1.0
        self._max_rate_limit_retries = 5
        
        # Register event handlers and commands
        self._setup_events()
        self._setup_commands()
    
    def set_user_storage(self, storage: "UserStorage") -> None:
        """Set the user storage for wallet registrations."""
        self.user_storage = storage
    
    def _setup_events(self) -> None:
        """Register Discord bot event handlers."""
        
        @self.bot.event
        async def on_ready():
            logger.info(f"Discord bot connected as {self.bot.user}")
            
            # Get the target channel
            channel = self.bot.get_channel(self.channel_id)
            if channel is None:
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
            
            # Sync slash commands
            try:
                synced = await self.bot.tree.sync()
                logger.info(f"Synced {len(synced)} slash command(s)")
            except Exception as e:
                logger.error(f"Failed to sync commands: {e}")
            
            self._ready.set()
        
        @self.bot.event
        async def on_disconnect():
            logger.warning("Discord bot disconnected")
            self._ready.clear()
        
        @self.bot.event
        async def on_resumed():
            logger.info("Discord bot connection resumed")
            self._ready.set()
    
    def _setup_commands(self) -> None:
        """Register slash commands."""
        
        @self.bot.tree.command(name="register", description="Register your IOST wallet for transaction alerts")
        @app_commands.describe(wallet="Your IOST wallet address (e.g., inwaliost)")
        async def register(interaction: discord.Interaction, wallet: str):
            settings = get_settings()
            
            # Check if command is used in the registration channel
            if interaction.channel_id != settings.discord_registration_channel_id:
                await interaction.response.send_message(
                    "❌ Please use the wallet registration channel.",
                    ephemeral=True
                )
                return
            
            # Validate wallet input
            wallet = wallet.strip().lower()
            if not wallet:
                await interaction.response.send_message(
                    "❌ Wallet address cannot be empty.",
                    ephemeral=True
                )
                return
            
            # Check if user storage is available
            if self.user_storage is None:
                await interaction.response.send_message(
                    "❌ Registration system is not available. Please try again later.",
                    ephemeral=True
                )
                logger.error("User storage not initialized for registration")
                return
            
            # Register the user
            try:
                user_id = str(interaction.user.id)
                success = await self.user_storage.register_user(user_id, wallet)
                
                if success:
                    await interaction.response.send_message(
                        f"✅ Wallet `{wallet}` registered successfully.",
                        ephemeral=True
                    )
                    logger.info(f"User {interaction.user.name} ({user_id}) registered wallet: {wallet}")
                else:
                    await interaction.response.send_message(
                        "❌ Failed to register wallet. Please try again.",
                        ephemeral=True
                    )
            except Exception as e:
                logger.error(f"Error during registration: {e}")
                await interaction.response.send_message(
                    "❌ An error occurred. Please try again later.",
                    ephemeral=True
                )
        
        @self.bot.tree.command(name="mywallet", description="Check your registered wallet")
        async def mywallet(interaction: discord.Interaction):
            if self.user_storage is None:
                await interaction.response.send_message(
                    "❌ Registration system is not available.",
                    ephemeral=True
                )
                return
            
            user_id = str(interaction.user.id)
            wallet = self.user_storage.get_user_wallet(user_id)
            
            if wallet:
                await interaction.response.send_message(
                    f"📋 Your registered wallet: `{wallet}`",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ You haven't registered a wallet yet. Use `/register wallet:<address>` to register.",
                    ephemeral=True
                )
    
    async def start(self) -> None:
        """Start the Discord bot in the background."""
        logger.info("Starting Discord bot...")
        asyncio.create_task(self._run_bot())
        
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=30.0)
            logger.info("Discord bot is ready")
        except asyncio.TimeoutError:
            logger.error("Discord bot failed to connect within 30 seconds")
            raise RuntimeError("Discord bot connection timeout")
    
    async def _run_bot(self) -> None:
        """Run the Discord bot."""
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
        """Wait until the bot is ready."""
        await self._ready.wait()
    
    def format_transaction_message(self, tx: Transaction, wallet: str) -> str:
        """Format a transaction into a Discord message."""
        if tx.is_incoming(wallet):
            emoji = "🚨"
            direction = "Received"
        elif tx.is_outgoing(wallet):
            emoji = "📤"
            direction = "Sent"
        else:
            emoji = "🔔"
            direction = "Activity"
        
        try:
            amount_float = float(tx.amount)
            if amount_float == int(amount_float):
                amount_str = str(int(amount_float))
            else:
                amount_str = f"{amount_float:.4f}".rstrip('0').rstrip('.')
        except (ValueError, TypeError):
            amount_str = tx.amount
        
        lines = [
            f"{emoji} **IOST {direction}!**",
            f"**Wallet:** `{wallet}`",
            f"**From:** `{tx.from_address or 'N/A'}`",
            f"**To:** `{tx.to_address or 'N/A'}`",
            f"**Amount:** {amount_str} {tx.token}",
            f"**Action:** {tx.action_name}",
            f"**Tx:** {tx.explorer_url}",
        ]
        
        return "\n".join(lines)
    
    async def send_user_alert(self, tx: Transaction, user_ids: Set[str], wallet: str) -> int:
        """
        Send transaction alert to channel with user mentions.
        Returns number of successful sends.
        """
        if not self._ready.is_set() or self._channel is None:
            return 0
        
        message = self.format_transaction_message(tx, wallet)
        
        # Build mentions string for all registered users
        mentions = " ".join([f"<@{user_id}>" for user_id in user_ids])
        full_message = f"{mentions}\n{message}" if mentions else message
        
        try:
            await self._channel.send(full_message)
            logger.info(f"Sent channel alert for tx {tx.tx_hash[:16]}... (mentioned {len(user_ids)} users)")
            await asyncio.sleep(self._rate_limit_delay)
            return len(user_ids)
        except Exception as e:
            logger.error(f"Failed to send channel alert: {e}")
            return 0
    
    async def _mention_user_in_channel(self, user_id: str, message: str) -> bool:
        """Mention a user in the alert channel with the transaction message."""
        if self._channel is None:
            return False
        
        try:
            mention_message = f"<@{user_id}>\n{message}"
            await self._channel.send(mention_message)
            logger.info(f"Mentioned user {user_id} in channel")
            return True
        except Exception as e:
            logger.error(f"Failed to mention user in channel: {e}")
            return False
    
    async def _send_message_to_channel(self, message: str) -> bool:
        """Send a message to the alert channel."""
        if self._channel is None:
            return False
        
        for attempt in range(self._max_rate_limit_retries):
            try:
                await self._channel.send(message)
                await asyncio.sleep(self._rate_limit_delay)
                return True
            except discord.RateLimited as e:
                logger.warning(f"Rate limited, waiting {e.retry_after:.2f}s")
                await asyncio.sleep(e.retry_after)
            except discord.Forbidden:
                logger.error("Bot doesn't have permission to send to channel")
                return False
            except discord.HTTPException as e:
                logger.error(f"Discord HTTP error: {e}")
                if attempt < self._max_rate_limit_retries - 1:
                    await asyncio.sleep(self._rate_limit_delay * (attempt + 1))
            except Exception as e:
                logger.error(f"Failed to send message: {e}")
                return False
        
        return False
    
    async def send_startup_message(self) -> bool:
        """Send a startup notification."""
        if self._channel is None:
            return False
        
        try:
            registered_count = len(self.user_storage.get_all_users()) if self.user_storage else 0
            wallets_count = len(self.user_storage.get_all_wallets()) if self.user_storage else 0
            
            await self._channel.send(
                f"🟢 **IOST Wallet Monitor Started**\n"
                f"📊 Registered users: {registered_count}\n"
                f"👛 Monitored wallets: {wallets_count}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send startup message: {e}")
            return False
