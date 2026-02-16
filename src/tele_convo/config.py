"""Configuration module for tele-convo.

This module provides configuration loading from environment variables
using python-dotenv.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass
class Config:
    """Configuration settings for tele-convo.

    Attributes:
        api_id: Telegram API ID (integer).
        api_hash: Telegram API Hash (string).
        session_name: Telethon session name.
        group_url: Target group/channel URL.
        db_path: SQLite database path.
        ws_host: WebSocket server host.
        ws_port: WebSocket server port.
    """
    api_id: int
    api_hash: str
    session_name: str
    group_url: str
    db_path: str
    ws_host: str
    ws_port: int


def load_config() -> Config:
    """Load configuration from environment variables.

    Loads environment variables from .env file, validates required
    variables, and returns a Config instance.

    Returns:
        Config: A configuration instance with all settings.

    Raises:
        ValueError: If required environment variables are missing or invalid.
    """
    load_dotenv()

    # Required variables
    api_id_str = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")
    group_url = os.getenv("GROUP_URL")

    # Validate required variables
    missing_vars = []
    if not api_id_str:
        missing_vars.append("API_ID")
    if not api_hash:
        missing_vars.append("API_HASH")
    if not group_url:
        missing_vars.append("GROUP_URL")

    if missing_vars:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing_vars)}"
        )

    # Parse API_ID as integer
    try:
        api_id = int(api_id_str)  # type: ignore[arg-type]
    except ValueError:
        raise ValueError(
            f"API_ID must be an integer, got: '{api_id_str}'"
        )

    # Optional variables with defaults
    session_name = os.getenv("SESSION_NAME", "tele_convo")
    db_path = os.getenv("DB_PATH", "data/messages.db")
    ws_host = os.getenv("WS_HOST", "0.0.0.0")

    # Parse WS_PORT as integer
    ws_port_str = os.getenv("WS_PORT", "8765")
    try:
        ws_port = int(ws_port_str)
    except ValueError:
        raise ValueError(
            f"WS_PORT must be an integer, got: '{ws_port_str}'"
        )

    return Config(
        api_id=api_id,
        api_hash=api_hash,  # type: ignore[arg-type]
        session_name=session_name,
        group_url=group_url,  # type: ignore[arg-type]
        db_path=db_path,
        ws_host=ws_host,
        ws_port=ws_port,
    )
