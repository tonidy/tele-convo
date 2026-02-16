"""Telegram client module for tele-convo.

This module provides Telegram client functionality using Telethon,
including historical message backfill and live message listening.
"""

import asyncio
import json
import logging
import random
from datetime import datetime
from typing import Any, Optional

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError
from telethon.tl import types
from telethon.tl.types import Message, User as TelethonUser, Chat as TelethonChat

from tele_convo.config import Config
from tele_convo.db import (
    Chat,
    User,
    Message as DBMessage,
    Media,
    insert_or_update_chat,
    insert_or_update_user,
    insert_message,
    insert_messages_batch,
    insert_media,
)

# Configure logging
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Constants
BACKFILL_CHUNK_SIZE = 100  # Messages per chunk
MIN_DELAY = 1  # Minimum delay in seconds
MAX_DELAY = 3  # Maximum delay in seconds
MAX_RETRIES = 5  # Maximum retries for failed requests


async def random_delay() -> None:
    """Add a random delay between API requests to avoid rate limiting."""
    delay = random.uniform(MIN_DELAY, MAX_DELAY)
    await asyncio.sleep(delay)


async def exponential_backoff(attempt: int, base_delay: float = 1.0) -> float:
    """Calculate exponential backoff delay.

    Args:
        attempt: Current retry attempt number.
        base_delay: Base delay in seconds.

    Returns:
        Exponential backoff delay in seconds.
    """
    return base_delay * (2 ** attempt)


class TelegramClientManager:
    """Telegram client manager for backfill and live listening.

    This class handles Telegram connection, historical message backfill,
    and real-time message listening with anti-ban best practices.
    """

    def __init__(self, config: Config):
        """Initialize the Telegram client manager.

        Args:
            config: Configuration object with Telegram API credentials.
        """
        self.config = config
        self.client: Optional[TelegramClient] = None
        self.entity: Optional[Any] = None

    async def connect(self) -> TelegramClient:
        """Connect to Telegram using Telethon.

        Returns:
            Connected TelegramClient instance.

        Raises:
            Exception: If connection fails.
        """
        self.client = TelegramClient(
            self.config.session_name,
            self.config.api_id,
            self.config.api_hash
        )
        await self.client.start()
        logger.warning(f"Connected to Telegram as session: {self.config.session_name}")
        return self.client

    async def disconnect(self) -> None:
        """Disconnect from Telegram."""
        if self.client:
            await self.client.disconnect()
            logger.warning("Disconnected from Telegram")

    async def get_entity(self, group_url: str) -> Any:
        """Get the entity (chat/channel) from Telegram.

        Args:
            group_url: Target group/channel URL or username.

        Returns:
            Entity object representing the chat/channel.

        Raises:
            Exception: If entity cannot be resolved.
        """
        if not self.client:
            await self.connect()

        # Resolve the entity (chat/channel)
        self.entity = await self.client.get_entity(group_url)
        logger.warning(f"Resolved entity: {getattr(self.entity, 'title', 'Unknown')} ({self.entity.id})")
        return self.entity

    async def _extract_media_info(self, message: Message) -> Optional[Media]:
        """Extract media information from a Telegram message.

        Args:
            message: Telethon Message object.

        Returns:
            Media entity if message has media, None otherwise.
        """
        media = message.media
        if media is None:
            return None

        # Determine media type
        media_type = "unknown"
        media_id = ""

        if isinstance(media, types.Photo):
            media_type = "photo"
            media_id = str(media.photo.id)
        elif isinstance(media, types.Document):
            # Get the document type (video, audio, voice, etc.)
            doc_type = "document"
            for attr in media.document.attributes:
                if isinstance(attr, types.DocumentAttributeVideo):
                    doc_type = "video"
                    break
                elif isinstance(attr, types.DocumentAttributeAudio):
                    doc_type = "audio"
                    break
                elif isinstance(attr, types.DocumentAttributeVoice):
                    doc_type = "voice"
                    break
                elif isinstance(attr, types.DocumentAttributeSticker):
                    doc_type = "sticker"
                    break
            media_type = doc_type
            media_id = str(media.document.id)
        elif isinstance(media, types.WebPage):
            media_type = "webpage"
            media_id = str(media.webpage.id)
        elif isinstance(media, types.Game):
            media_type = "game"
            media_id = str(media.game.id)
        elif isinstance(media, types.Invoice):
            media_type = "invoice"
            media_id = str(media.invoice.title)

        return Media(
            msg_id=message.id,
            chat_id=self.entity.id if self.entity else 0,
            media_type=media_type,
            media_id=media_id
        )

    async def _process_message(self, message: Message) -> Optional[DBMessage]:
        """Process a Telegram message and convert to DB Message entity.

        Args:
            message: Telethon Message object.

        Returns:
            DB Message entity or None if message should be skipped.
        """
        # Skip messages without valid sender
        if message.sender is None:
            return None

        # Extract sender information
        sender = message.sender
        sender_id = sender.id

        # Handle channel/supergroup messages (sender might be the chat itself)
        if isinstance(sender, (types.Channel, types.Chat)):
            sender_id = sender.id

        # Convert raw message to JSON
        raw_json = None
        try:
            # Get the message as a dictionary for storage
            raw_json = json.dumps(message.to_dict(), default=str)
        except Exception as e:
            logger.warning(f"Failed to serialize message to JSON: {e}")

        # Determine if message is forwarded
        is_forwarded = (
            message.fwd_from is not None or
            message.forward is not None
        )

        return DBMessage(
            id=message.id,
            chat_id=self.entity.id if self.entity else 0,
            sender_id=sender_id,
            date=message.date,
            text=message.text or message.message,
            reply_to_msg_id=message.reply_to_msg_id,
            is_forwarded=is_forwarded,
            raw_json=raw_json
        )

    async def _process_user(self, sender: Any) -> Optional[User]:
        """Process a Telegram user and convert to DB User entity.

        Args:
            sender: Telethon User or Channel object.

        Returns:
            DB User entity or None if not applicable.
        """
        if isinstance(sender, TelethonUser):
            return User(
                id=sender.id,
                username=sender.username,
                first_name=sender.first_name or "Unknown",
                last_name=sender.last_name
            )
        elif isinstance(sender, (types.Channel, types.Chat)):
            # For channels/chats, create a pseudo-user
            title = getattr(sender, 'title', 'Unknown')
            username = getattr(sender, 'username', None)
            return User(
                id=sender.id,
                username=username,
                first_name=title,
                last_name=None
            )
        return None

    async def _process_chat(self) -> Optional[Chat]:
        """Process the current entity and convert to DB Chat entity.

        Returns:
            DB Chat entity or None if entity is not set.
        """
        if self.entity is None:
            return None

        return Chat(
            id=self.entity.id,
            title=getattr(self.entity, 'title', 'Unknown'),
            username=getattr(self.entity, 'username', None)
        )

    async def backfill_messages(
        self,
        entity: Any,
        limit: Optional[int] = None,
        progress_callback: Optional[callable] = None,
        verbose: bool = False
    ) -> int:
        """Fetch historical messages from a chat/channel in chunks.

        Fetches messages in chunks of 100 with random delays between
        chunks to avoid rate limiting.

        Args:
            entity: The Telegram entity (chat/channel) to fetch from.
            limit: Maximum total messages to fetch (None for all).
            progress_callback: Optional callback function called with progress.

        Returns:
            Total number of messages fetched and stored.
        """
        if not self.client:
            await self.connect()

        # Ensure entity is set
        self.entity = entity

        # First, store the chat information
        chat = await self._process_chat()
        if chat:
            await insert_or_update_chat(chat)
            logger.warning(f"Stored chat: {chat.title}")

        total_fetched = 0
        all_messages: list[DBMessage] = []
        processed_users: set[int] = set()

        # Get messages in chunks
        offset_id = 0
        chunk_count = 0

        while True:
            chunk_count += 1

            # Calculate remaining messages to fetch
            remaining = None
            if limit is not None:
                remaining = limit - total_fetched
                if remaining <= 0:
                    break

            # Fetch chunk of messages
            chunk_size = min(BACKFILL_CHUNK_SIZE, remaining) if remaining else BACKFILL_CHUNK_SIZE

            try:
                messages = await self.client.get_messages(
                    entity,
                    limit=chunk_size,
                    offset_id=offset_id
                )

                if not messages:
                    logger.warning("No more messages to fetch")
                    break

                # Process each message
                for msg in messages:
                    # Process and store message
                    db_message = await self._process_message(msg)
                    if db_message:
                        all_messages.append(db_message)

                        # Process sender (user)
                        if msg.sender and msg.sender.id not in processed_users:
                            user = await self._process_user(msg.sender)
                            if user:
                                await insert_or_update_user(user)
                                processed_users.add(user.id)

                        # Process media if present
                        media = await self._extract_media_info(msg)
                        if media:
                            await insert_media(media)

                        # Verbose output
                        if verbose:
                            sender_name = getattr(msg.sender, 'first_name', 'Unknown')
                            text_preview = (msg.text or '')[:50]
                            logger.info(f"  [{msg.id}] {sender_name}: {text_preview}")

                # Update offset to last message ID for next chunk
                offset_id = messages[-1].id

                total_fetched += len(messages)
                logger.warning(f"Chunk {chunk_count}: fetched {len(messages)} messages (total: {total_fetched})")

                # Call progress callback if provided
                if progress_callback:
                    last_msg = messages[-1].text or '' if messages else ''
                    progress_callback(total_fetched, last_msg[:50] if verbose else None)

                # Add random delay between chunks to avoid rate limiting
                await random_delay()

            except FloodWaitError as e:
                # Handle FloodWaitError with exponential backoff
                wait_time = e.seconds
                logger.warning(f"FloodWaitError: Need to wait {wait_time} seconds")
                await asyncio.sleep(wait_time)
            except RPCError as e:
                logger.warning(f"RPCError during backfill: {e}")
                await random_delay()
            except Exception as e:
                logger.warning(f"Error during backfill: {e}")
                await random_delay()

            # Check if we've reached the limit
            if limit and total_fetched >= limit:
                break

        # Batch insert all messages
        if all_messages:
            await insert_messages_batch(all_messages)
            logger.warning(f"Batch inserted {len(all_messages)} messages")

        return total_fetched

    async def start_listening(
        self,
        entity: Any,
        message_handler: Optional[callable] = None,
        verbose: bool = False
    ) -> None:
        """Start listening for new messages in a chat/channel.

        Uses Telethon's event handler to listen for new messages
        in real-time and store them in the database.

        Args:
            entity: The Telegram entity (chat/channel) to listen to.
            message_handler: Optional custom message handler function.
            verbose: Enable verbose output.
        """
        if not self.client:
            await self.connect()

        # Ensure entity is set
        self.entity = entity

        # Store the chat information first
        chat = await self._process_chat()
        if chat:
            await insert_or_update_chat(chat)

        async def handle_new_message(event: events.NewMessage.Event) -> None:
            """Handle new message events.

            Args:
                event: NewMessage event from Telethon.
            """
            message = event.message

            # Skip messages without valid sender
            if message.sender is None:
                return

            try:
                # Add random delay to avoid rate limiting
                await random_delay()

                # Process and store the message
                db_message = await self._process_message(message)
                if db_message:
                    await insert_message(db_message)
                    
                    if verbose:
                        sender_name = getattr(message.sender, 'first_name', 'Unknown')
                        text_preview = (message.text or '')[:100]
                        logger.info(f"NEW MESSAGE [{message.id}] {sender_name}: {text_preview}")
                    else:
                        logger.warning(f"Stored new message: {message.id} from chat {db_message.chat_id}")

                # Process and store the sender
                user = await self._process_user(message.sender)
                if user:
                    await insert_or_update_user(user)

                # Process media if present
                media = await self._extract_media_info(message)
                if media:
                    await insert_media(media)
                    if verbose:
                        logger.info(f"  Media: {media.media_type}")

                # Call custom message handler if provided
                if message_handler:
                    message_handler(event)

            except FloodWaitError as e:
                wait_time = e.seconds
                logger.warning(f"FloodWaitError in listener: Need to wait {wait_time} seconds")
                await asyncio.sleep(wait_time)
            except RPCError as e:
                logger.warning(f"RPCError in listener: {e}")
            except Exception as e:
                logger.warning(f"Error handling new message: {e}")

        # Register the event handler
        self.client.add_event_handler(handle_new_message, events.NewMessage(chats=[entity]))
        logger.warning(f"Started listening for new messages in chat: {entity.id}")

    async def run(self) -> None:
        """Run the client (blocking call for live listening).

        This starts the Telegram client and begins listening for events.
        """
        if not self.client:
            await self.connect()

        logger.warning("Starting Telegram client run loop...")
        await self.client.run_until_disconnected()


async def get_entity(client: TelegramClient, group_url: str) -> Any:
    """Get the entity (chat/channel) from Telegram.

    Args:
        client: Connected TelegramClient instance.
        group_url: Target group/channel URL or username.

    Returns:
        Entity object representing the chat/channel.
    """
    entity = await client.get_entity(group_url)
    return entity


async def start_client(config: Config) -> TelegramClientManager:
    """Create and connect a Telegram client manager.

    Args:
        config: Configuration object with Telegram API credentials.

    Returns:
        Connected TelegramClientManager instance.
    """
    manager = TelegramClientManager(config)
    await manager.connect()
    return manager


async def run_backfill(config: Config, progress_callback: Optional[callable] = None) -> int:
    """Run historical message backfill.

    Args:
        config: Configuration object.
        progress_callback: Optional progress callback function.

    Returns:
        Total number of messages fetched.
    """
    manager = TelegramClientManager(config)
    await manager.connect()

    # Get the entity (chat/channel)
    entity = await manager.get_entity(config.group_url)

    # Run backfill
    total = await manager.backfill_messages(entity, progress_callback=progress_callback)

    # Disconnect when done
    await manager.disconnect()

    return total


async def run_live_listener(
    config: Config,
    message_handler: Optional[callable] = None
) -> None:
    """Run live message listener.

    Args:
        config: Configuration object.
        message_handler: Optional custom message handler.
    """
    manager = TelegramClientManager(config)
    await manager.connect()

    # Get the entity (chat/channel)
    entity = await manager.get_entity(config.group_url)

    # Start listening
    await manager.start_listening(entity, message_handler)

    # Run the client (blocking)
    await manager.run()
