"""Main entry point for tele-convo application.

This module orchestrates all components: config, database, telegram client,
and WebSocket server. It provides CLI interface for backfill, listen, serve,
and all operations.
"""

import argparse
import asyncio
import logging
import signal
import sys
from typing import Optional

from tele_convo.config import Config, load_config
from tele_convo.db import close_db, get_db_connection
from tele_convo.server import start_server as start_ws_server
from tele_convo.telegram import TelegramClientManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class TeleConvoApp:
    """Main application class that orchestrates all components."""

    def __init__(self, config: Config):
        """Initialize the application.

        Args:
            config: Configuration object.
        """
        self.config = config
        self.telegram_manager: Optional[TelegramClientManager] = None
        self.entity: Optional[any] = None
        self.shutdown_event = asyncio.Event()

    async def initialize(self) -> None:
        """Initialize database connection and Telegram client."""
        # Initialize database (this also creates the schema)
        logger.info("Initializing database...")
        await get_db_connection(self.config)
        logger.info(f"Database initialized at: {self.config.db_path}")

        # Create Telegram client manager
        self.telegram_manager = TelegramClientManager(self.config)
        logger.info("Telegram client manager created")

    async def connect_telegram(self) -> None:
        """Connect to Telegram and resolve the target entity."""
        if not self.telegram_manager:
            await self.initialize()

        # Connect to Telegram
        logger.info("Connecting to Telegram...")
        await self.telegram_manager.connect()

        # Get the target entity
        logger.info(f"Resolving entity: {self.config.group_url}")
        self.entity = await self.telegram_manager.get_entity(self.config.group_url)
        logger.info(f"Connected to: {getattr(self.entity, 'title', 'Unknown')}")

    async def run_backfill(self, limit: Optional[int] = None) -> int:
        """Run message backfill operation.

        Args:
            limit: Optional limit on number of messages to fetch.

        Returns:
            Number of messages fetched.
        """
        if not self.entity:
            await self.connect_telegram()

        def progress_callback(count: int) -> None:
            """Report backfill progress."""
            logger.info(f"Backfill progress: {count} messages fetched")

        logger.info(f"Starting backfill (limit: {limit or 'unlimited'})...")
        total = await self.telegram_manager.backfill_messages(
            self.entity,
            limit=limit,
            progress_callback=progress_callback
        )
        logger.info(f"Backfill complete: {total} messages fetched")

        return total

    async def run_listening(self) -> None:
        """Start live message listening."""
        if not self.entity:
            await self.connect_telegram()

        logger.info("Starting live message listening...")
        await self.telegram_manager.start_listening(self.entity)

        # This blocks until disconnected
        await self.telegram_manager.run()

    async def run_server(self) -> None:
        """Start the WebSocket JSON-RPC server."""
        logger.info(
            f"Starting WebSocket server on {self.config.ws_host}:{self.config.ws_port}"
        )
        await start_ws_server(self.config)

    async def run_all(
        self,
        limit: Optional[int] = None,
        no_listen: bool = False
    ) -> None:
        """Run all operations: backfill, listen, and server.

        Args:
            limit: Optional limit on number of messages to backfill.
            no_listen: If True, skip live listening.
        """
        # Run backfill first
        if limit is not None or no_listen is False:
            await self.run_backfill(limit)

        # Create tasks for listening and server
        tasks = []

        if not no_listen:
            # Start listening in background
            listening_task = asyncio.create_task(self.run_listening())
            tasks.append(listening_task)

        # Start server
        server_task = asyncio.create_task(self.run_server())
        tasks.append(server_task)

        # Wait for all tasks
        # Note: The server runs forever, so listening_task will run until disconnected
        if tasks:
            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                logger.info("Tasks cancelled")

    async def shutdown(self) -> None:
        """Gracefully shutdown the application."""
        logger.info("Shutting down...")

        # Disconnect Telegram
        if self.telegram_manager:
            try:
                await self.telegram_manager.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting Telegram: {e}")

        # Close database
        try:
            await close_db()
        except Exception as e:
            logger.warning(f"Error closing database: {e}")

        logger.info("Shutdown complete")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Telegram message scraper with SQLite storage and JSON-RPC WebSocket interface"
    )

    parser.add_argument(
        "mode",
        choices=["backfill", "listen", "serve", "all"],
        nargs="?",
        default="all",
        help="Operation mode: backfill (fetch historical), listen (live), serve (WebSocket server), all (default)"
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of messages to backfill"
    )

    parser.add_argument(
        "--no-listen",
        action="store_true",
        help="When using 'all' mode, skip starting live listening"
    )

    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> None:
    """Async main function.

    Args:
        args: Parsed command-line arguments.
    """
    # Load configuration
    try:
        config = load_config()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    # Create application
    app = TeleConvoApp(config)

    # Set up signal handler for graceful shutdown
    loop = asyncio.get_running_loop()
    shutdown_task: Optional[asyncio.Task] = None

    def signal_handler() -> None:
        """Handle shutdown signals."""
        nonlocal shutdown_task
        if shutdown_task is None or shutdown_task.done():
            logger.info("Received shutdown signal")
            shutdown_task = asyncio.create_task(app.shutdown())
            app.shutdown_event.set()

    # Register signal handlers
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        # Run the appropriate mode
        if args.mode == "backfill":
            await app.initialize()
            await app.connect_telegram()
            await app.run_backfill(args.limit)

        elif args.mode == "listen":
            await app.initialize()
            await app.connect_telegram()
            await app.run_listening()

        elif args.mode == "serve":
            await app.initialize()
            await app.run_server()

        elif args.mode == "all":
            await app.initialize()
            await app.run_all(limit=args.limit, no_listen=args.no_listen)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception(f"Error: {e}")
        sys.exit(1)
    finally:
        # Ensure cleanup
        await app.shutdown()


def main() -> None:
    """Main entry point."""
    args = parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
