"""Database module for tele-convo.

This module provides SQLite database operations including schema
initialization, CRUD operations, pagination, and full-text search.
"""

import asyncio
import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import aiosqlite

from tele_convo.config import Config, load_config


# Database connection singleton
_db_connection: Optional[aiosqlite.Connection] = None
_init_lock = asyncio.Lock()


@dataclass
class Chat:
    """Chat entity representing a Telegram chat/channel.

    Attributes:
        id: Unique chat identifier.
        title: Chat title.
        username: Chat username (optional).
    """
    id: int
    title: str
    username: Optional[str] = None


@dataclass
class User:
    """User entity representing a Telegram user.

    Attributes:
        id: Unique user identifier.
        username: Username (optional).
        first_name: First name.
        last_name: Last name (optional).
    """
    id: int
    first_name: str
    username: Optional[str] = None
    last_name: Optional[str] = None


@dataclass
class Message:
    """Message entity representing a Telegram message.

    Attributes:
        id: Unique message identifier.
        chat_id: Chat identifier this message belongs to.
        sender_id: User identifier who sent the message.
        date: Message date/time.
        text: Message text content.
        reply_to_msg_id: Reply message ID (optional).
        is_forwarded: Whether message was forwarded.
        raw_json: Raw JSON representation of the message.
    """
    id: int
    chat_id: int
    sender_id: int
    date: datetime
    text: Optional[str] = None
    reply_to_msg_id: Optional[int] = None
    is_forwarded: bool = False
    raw_json: Optional[str] = None


@dataclass
class Media:
    """Media entity representing message media attachment.

    Attributes:
        msg_id: Message identifier.
        chat_id: Chat identifier.
        media_type: Type of media (photo, document, etc.).
        media_id: Telegram media identifier.
    """
    msg_id: int
    chat_id: int
    media_type: str
    media_id: str


@dataclass
class MessageCursor:
    """Cursor for paginating through messages.

    Attributes:
        last_id: Last message ID from previous page.
        last_date: Last message date from previous page.
    """
    last_id: int
    last_date: str


def encode_cursor(cursor: MessageCursor) -> str:
    """Encode a message cursor to base64 string for transmission.

    Args:
        cursor: The message cursor to encode.

    Returns:
        Base64 encoded cursor string.
    """
    data = {
        "last_id": cursor.last_id,
        "last_date": cursor.last_date
    }
    json_str = json.dumps(data)
    return base64.b64encode(json_str.encode()).decode()


def decode_cursor(cursor_str: str) -> Optional[MessageCursor]:
    """Decode a base64 cursor string to MessageCursor.

    Args:
        cursor_str: Base64 encoded cursor string.

    Returns:
        MessageCursor object or None if invalid.
    """
    try:
        json_bytes = base64.b64decode(cursor_str.encode())
        data = json.loads(json_bytes.decode())
        return MessageCursor(
            last_id=data["last_id"],
            last_date=data["last_date"]
        )
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


async def get_db_connection(config: Optional[Config] = None) -> aiosqlite.Connection:
    """Get or create the database connection singleton.

    Args:
        config: Configuration object. If not provided, loads from environment.

    Returns:
        Database connection instance.
    """
    global _db_connection

    if _db_connection is not None:
        return _db_connection

    async with _init_lock:
        if _db_connection is not None:
            return _db_connection

        if config is None:
            config = load_config()

        # Ensure the directory exists
        db_dir = os.path.dirname(config.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        _db_connection = await aiosqlite.connect(config.db_path)
        _db_connection.row_factory = aiosqlite.Row

        # Enable WAL mode for better concurrency
        await _db_connection.execute("PRAGMA journal_mode=WAL")
        await _db_connection.execute("PRAGMA synchronous=NORMAL")

        await _init_schema(_db_connection)

    return _db_connection


async def _init_schema(conn: aiosqlite.Connection) -> None:
    """Initialize database schema with all tables and FTS.

    Args:
        conn: Database connection.
    """
    # Create CHATS table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            username TEXT
        )
    """)

    # Create USERS table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT NOT NULL,
            last_name TEXT
        )
    """)

    # Create MESSAGES table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            text TEXT,
            reply_to_msg_id INTEGER,
            is_forwarded INTEGER DEFAULT 0,
            raw_json TEXT,
            FOREIGN KEY (chat_id) REFERENCES chats(id),
            FOREIGN KEY (sender_id) REFERENCES users(id)
        )
    """)

    # Create indexes for MESSAGES table
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_sender_id ON messages(sender_id)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(date)
    """)

    # Create MEDIA table
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS media (
            msg_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            media_type TEXT NOT NULL,
            media_id TEXT NOT NULL,
            PRIMARY KEY (msg_id, chat_id),
            FOREIGN KEY (msg_id) REFERENCES messages(id),
            FOREIGN KEY (chat_id) REFERENCES chats(id)
        )
    """)

    # Create FTS5 virtual table for full-text search
    await conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            text,
            content='messages',
            content_rowid='id'
        )
    """)

    # Create triggers to keep FTS in sync with messages
    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
        END
    """)

    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, text) VALUES('delete', old.id, old.text);
        END
    """)

    await conn.execute("""
        CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, text) VALUES('delete', old.id, old.text);
            INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
        END
    """)

    await conn.commit()


async def close_db() -> None:
    """Close the database connection."""
    global _db_connection

    if _db_connection is not None:
        await _db_connection.close()
        _db_connection = None


# ==================== CHATS CRUD ====================


async def insert_or_update_chat(chat: Chat) -> None:
    """Insert or update a chat in the database.

    Args:
        chat: Chat entity to insert or update.
    """
    conn = await get_db_connection()
    await conn.execute("""
        INSERT INTO chats (id, title, username)
        VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            username = excluded.username
    """, (chat.id, chat.title, chat.username))
    await conn.commit()


async def get_chat_by_id(chat_id: int) -> Optional[Chat]:
    """Get a chat by its ID.

    Args:
        chat_id: Chat identifier.

    Returns:
        Chat entity or None if not found.
    """
    conn = await get_db_connection()
    cursor = await conn.execute(
        "SELECT id, title, username FROM chats WHERE id = ?",
        (chat_id,)
    )
    row = await cursor.fetchone()

    if row is None:
        return None

    return Chat(
        id=row["id"],
        title=row["title"],
        username=row["username"]
    )


async def get_all_chats() -> list[Chat]:
    """Get all chats from the database.

    Returns:
        List of Chat entities.
    """
    conn = await get_db_connection()
    cursor = await conn.execute(
        "SELECT id, title, username FROM chats ORDER BY title"
    )
    rows = await cursor.fetchall()

    return [
        Chat(
            id=row["id"],
            title=row["title"],
            username=row["username"]
        )
        for row in rows
    ]


# ==================== USERS CRUD ====================


async def insert_or_update_user(user: User) -> None:
    """Insert or update a user in the database.

    Args:
        user: User entity to insert or update.
    """
    conn = await get_db_connection()
    await conn.execute("""
        INSERT INTO users (id, username, first_name, last_name)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            last_name = excluded.last_name
    """, (user.id, user.username, user.first_name, user.last_name))
    await conn.commit()


async def get_user_by_id(user_id: int) -> Optional[User]:
    """Get a user by their ID.

    Args:
        user_id: User identifier.

    Returns:
        User entity or None if not found.
    """
    conn = await get_db_connection()
    cursor = await conn.execute(
        "SELECT id, username, first_name, last_name FROM users WHERE id = ?",
        (user_id,)
    )
    row = await cursor.fetchone()

    if row is None:
        return None

    return User(
        id=row["id"],
        username=row["username"],
        first_name=row["first_name"],
        last_name=row["last_name"]
    )


async def search_users(
    keyword: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[str] = None
) -> dict[str, Any]:
    """Search users by keyword in username, first_name, or last_name.

    Args:
        keyword: Search keyword.
        limit: Maximum number of results.
        cursor: Pagination cursor (not used for users currently).

    Returns:
        Dictionary with users list and pagination info.
    """
    conn = await get_db_connection()

    if keyword:
        search_pattern = f"%{keyword}%"
        cursor = await conn.execute("""
            SELECT id, username, first_name, last_name
            FROM users
            WHERE username LIKE ? OR first_name LIKE ? OR last_name LIKE ?
            ORDER BY first_name, last_name
            LIMIT ?
        """, (search_pattern, search_pattern, search_pattern, limit + 1))
    else:
        cursor = await conn.execute("""
            SELECT id, username, first_name, last_name
            FROM users
            ORDER BY first_name, last_name
            LIMIT ?
        """, (limit + 1,))

    rows = await cursor.fetchall()

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    users = [
        User(
            id=row["id"],
            username=row["username"],
            first_name=row["first_name"],
            last_name=row["last_name"]
        )
        for row in rows
    ]

    return {
        "users": users,
        "has_more": has_more,
        "next_cursor": None  # Users don't support cursor pagination yet
    }


# ==================== MESSAGES CRUD ====================


async def insert_message(message: Message) -> None:
    """Insert a message into the database.

    Args:
        message: Message entity to insert.
    """
    conn = await get_db_connection()
    await conn.execute("""
        INSERT INTO messages (id, chat_id, sender_id, date, text, reply_to_msg_id, is_forwarded, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        message.id,
        message.chat_id,
        message.sender_id,
        message.date.isoformat(),
        message.text,
        message.reply_to_msg_id,
        1 if message.is_forwarded else 0,
        message.raw_json
    ))
    await conn.commit()


async def insert_messages_batch(messages: list[Message]) -> None:
    """Insert multiple messages in a single transaction.

    Args:
        messages: List of message entities to insert.
    """
    if not messages:
        return

    conn = await get_db_connection()
    data = [
        (
            m.id,
            m.chat_id,
            m.sender_id,
            m.date.isoformat(),
            m.text,
            m.reply_to_msg_id,
            1 if m.is_forwarded else 0,
            m.raw_json
        )
        for m in messages
    ]
    await conn.executemany("""
        INSERT INTO messages (id, chat_id, sender_id, date, text, reply_to_msg_id, is_forwarded, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, data)
    await conn.commit()


async def get_messages_by_chat(
    chat_id: int,
    limit: int = 50,
    cursor: Optional[str] = None
) -> dict[str, Any]:
    """Get messages for a specific chat with pagination.

    Args:
        chat_id: Chat identifier to filter by.
        limit: Maximum number of results.
        cursor: Base64 encoded pagination cursor.

    Returns:
        Dictionary with messages list and pagination info.
    """
    conn = await get_db_connection()

    # Decode cursor if provided
    cursor_obj = decode_cursor(cursor) if cursor else None

    if cursor_obj:
        # Get messages older than cursor
        cursor_query = await conn.execute("""
            SELECT m.id, m.chat_id, m.sender_id, m.date, m.text,
                   m.reply_to_msg_id, m.is_forwarded, m.raw_json
            FROM messages m
            WHERE m.chat_id = ? AND (m.date < ? OR (m.date = ? AND m.id < ?))
            ORDER BY m.date DESC, m.id DESC
            LIMIT ?
        """, (
            chat_id,
            cursor_obj.last_date,
            cursor_obj.last_date,
            cursor_obj.last_id,
            limit + 1
        ))
    else:
        cursor_query = await conn.execute("""
            SELECT m.id, m.chat_id, m.sender_id, m.date, m.text,
                   m.reply_to_msg_id, m.is_forwarded, m.raw_json
            FROM messages m
            WHERE m.chat_id = ?
            ORDER BY m.date DESC, m.id DESC
            LIMIT ?
        """, (chat_id, limit + 1))

    rows = await cursor_query.fetchall()

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    messages = [
        Message(
            id=row["id"],
            chat_id=row["chat_id"],
            sender_id=row["sender_id"],
            date=datetime.fromisoformat(row["date"]),
            text=row["text"],
            reply_to_msg_id=row["reply_to_msg_id"],
            is_forwarded=bool(row["is_forwarded"]),
            raw_json=row["raw_json"]
        )
        for row in rows
    ]

    # Create next cursor if there are more results
    next_cursor = None
    if has_more and messages:
        last_msg = messages[-1]
        next_cursor = encode_cursor(MessageCursor(
            last_id=last_msg.id,
            last_date=last_msg.date.isoformat()
        ))

    return {
        "messages": messages,
        "has_more": has_more,
        "next_cursor": next_cursor
    }


async def get_messages_with_filters(
    chat_id: Optional[int] = None,
    sender_id: Optional[int] = None,
    keyword: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[str] = None
) -> dict[str, Any]:
    """Get messages with various filters and pagination.

    Args:
        chat_id: Filter by chat ID.
        sender_id: Filter by sender ID.
        keyword: Search keyword in message text.
        date_from: Filter messages from this date (ISO format).
        date_to: Filter messages to this date (ISO format).
        limit: Maximum number of results.
        cursor: Base64 encoded pagination cursor.

    Returns:
        Dictionary with messages list and pagination info.
    """
    conn = await get_db_connection()

    # Decode cursor if provided
    cursor_obj = decode_cursor(cursor) if cursor else None

    # Build query dynamically
    conditions = []
    params = []

    if chat_id is not None:
        conditions.append("m.chat_id = ?")
        params.append(chat_id)

    if sender_id is not None:
        conditions.append("m.sender_id = ?")
        params.append(sender_id)

    if keyword:
        conditions.append("m.text LIKE ?")
        params.append(f"%{keyword}%")

    if date_from:
        conditions.append("m.date >= ?")
        params.append(date_from)

    if date_to:
        conditions.append("m.date <= ?")
        params.append(date_to)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    # Add cursor condition if provided
    if cursor_obj:
        where_clause += f" AND (m.date < ? OR (m.date = ? AND m.id < ?))"
        params.extend([cursor_obj.last_date, cursor_obj.last_date, cursor_obj.last_id])

    params.append(limit + 1)

    query = f"""
        SELECT m.id, m.chat_id, m.sender_id, m.date, m.text,
               m.reply_to_msg_id, m.is_forwarded, m.raw_json
        FROM messages m
        WHERE {where_clause}
        ORDER BY m.date DESC, m.id DESC
        LIMIT ?
    """

    cursor_query = await conn.execute(query, params)
    rows = await cursor_query.fetchall()

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    messages = [
        Message(
            id=row["id"],
            chat_id=row["chat_id"],
            sender_id=row["sender_id"],
            date=datetime.fromisoformat(row["date"]),
            text=row["text"],
            reply_to_msg_id=row["reply_to_msg_id"],
            is_forwarded=bool(row["is_forwarded"]),
            raw_json=row["raw_json"]
        )
        for row in rows
    ]

    # Create next cursor if there are more results
    next_cursor = None
    if has_more and messages:
        last_msg = messages[-1]
        next_cursor = encode_cursor(MessageCursor(
            last_id=last_msg.id,
            last_date=last_msg.date.isoformat()
        ))

    return {
        "messages": messages,
        "has_more": has_more,
        "next_cursor": next_cursor
    }


async def count_messages(
    chat_id: Optional[int] = None,
    sender_id: Optional[int] = None,
    keyword: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None
) -> int:
    """Count messages with the same filters as get_messages_with_filters.

    Args:
        chat_id: Filter by chat ID.
        sender_id: Filter by sender ID.
        keyword: Search keyword in message text.
        date_from: Filter messages from this date (ISO format).
        date_to: Filter messages to this date (ISO format).

    Returns:
        Total count of matching messages.
    """
    conn = await get_db_connection()

    conditions = []
    params = []

    if chat_id is not None:
        conditions.append("m.chat_id = ?")
        params.append(chat_id)

    if sender_id is not None:
        conditions.append("m.sender_id = ?")
        params.append(sender_id)

    if keyword:
        conditions.append("m.text LIKE ?")
        params.append(f"%{keyword}%")

    if date_from:
        conditions.append("m.date >= ?")
        params.append(date_from)

    if date_to:
        conditions.append("m.date <= ?")
        params.append(date_to)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    query = f"SELECT COUNT(*) as count FROM messages m WHERE {where_clause}"

    cursor = await conn.execute(query, params)
    row = await cursor.fetchone()

    return row["count"] if row else 0


# ==================== FULL-TEXT SEARCH ====================


async def search_messages_fulltext(
    query: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50
) -> dict[str, Any]:
    """Search messages using full-text search.

    Args:
        query: Search query string.
        date_from: Filter messages from this date (ISO format).
        date_to: Filter messages to this date (ISO format).
        limit: Maximum number of results.

    Returns:
        Dictionary with messages list and count.
    """
    conn = await get_db_connection()

    # Build conditions for FTS
    fts_conditions = ["messages_fts MATCH ?"]
    fts_params: list[Any] = [query]

    if date_from or date_to:
        fts_conditions.append("m.date >= ?")
        fts_params.append(date_from or "0001-01-01T00:00:00")

        fts_conditions.append("m.date <= ?")
        fts_params.append(date_to or "9999-12-31T23:59:59")

    fts_where = " AND ".join(fts_conditions)
    fts_params.append(limit)

    fts_query = f"""
        SELECT m.id, m.chat_id, m.sender_id, m.date, m.text,
               m.reply_to_msg_id, m.is_forwarded, m.raw_json
        FROM messages m
        JOIN messages_fts ON m.id = messages_fts.rowid
        WHERE {fts_where}
        ORDER BY m.date DESC, m.id DESC
        LIMIT ?
    """

    cursor = await conn.execute(fts_query, fts_params)
    rows = await cursor.fetchall()

    messages = [
        Message(
            id=row["id"],
            chat_id=row["chat_id"],
            sender_id=row["sender_id"],
            date=datetime.fromisoformat(row["date"]),
            text=row["text"],
            reply_to_msg_id=row["reply_to_msg_id"],
            is_forwarded=bool(row["is_forwarded"]),
            raw_json=row["raw_json"]
        )
        for row in rows
    ]

    return {
        "messages": messages,
        "count": len(messages),
        "has_more": len(messages) == limit
    }


# ==================== MEDIA CRUD ====================


async def insert_media(media: Media) -> None:
    """Insert media into the database.

    Args:
        media: Media entity to insert.
    """
    conn = await get_db_connection()
    await conn.execute("""
        INSERT OR REPLACE INTO media (msg_id, chat_id, media_type, media_id)
        VALUES (?, ?, ?, ?)
    """, (media.msg_id, media.chat_id, media.media_type, media.media_id))
    await conn.commit()


async def get_media_by_chat(
    chat_id: int,
    media_type: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[int] = None
) -> dict[str, Any]:
    """Get media for a specific chat.

    Args:
        chat_id: Chat identifier to filter by.
        media_type: Filter by media type (photo, document, etc.).
        limit: Maximum number of results.
        cursor: Last msg_id for pagination.

    Returns:
        Dictionary with media list and pagination info.
    """
    conn = await get_db_connection()

    conditions = ["chat_id = ?"]
    params: list[Any] = [chat_id]

    if media_type:
        conditions.append("media_type = ?")
        params.append(media_type)

    if cursor:
        conditions.append("msg_id < ?")
        params.append(cursor)

    where_clause = " AND ".join(conditions)
    params.append(limit + 1)

    query = f"""
        SELECT msg_id, chat_id, media_type, media_id
        FROM media
        WHERE {where_clause}
        ORDER BY msg_id DESC
        LIMIT ?
    """

    cursor_query = await conn.execute(query, params)
    rows = await cursor_query.fetchall()

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    media_list = [
        Media(
            msg_id=row["msg_id"],
            chat_id=row["chat_id"],
            media_type=row["media_type"],
            media_id=row["media_id"]
        )
        for row in rows
    ]

    next_cursor = None
    if has_more and media_list:
        next_cursor = media_list[-1].msg_id

    return {
        "media": media_list,
        "has_more": has_more,
        "next_cursor": next_cursor
    }


async def get_media_with_filters(
    chat_id: Optional[int] = None,
    media_type: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[int] = None
) -> dict[str, Any]:
    """Get media with optional filters.

    Args:
        chat_id: Filter by chat ID.
        media_type: Filter by media type.
        limit: Maximum number of results.
        cursor: Last msg_id for pagination.

    Returns:
        Dictionary with media list and pagination info.
    """
    conn = await get_db_connection()

    conditions = []
    params: list[Any] = []

    if chat_id is not None:
        conditions.append("chat_id = ?")
        params.append(chat_id)

    if media_type:
        conditions.append("media_type = ?")
        params.append(media_type)

    if cursor:
        conditions.append("msg_id < ?")
        params.append(cursor)

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit + 1)

    query = f"""
        SELECT msg_id, chat_id, media_type, media_id
        FROM media
        WHERE {where_clause}
        ORDER BY msg_id DESC
        LIMIT ?
    """

    cursor_query = await conn.execute(query, params)
    rows = await cursor_query.fetchall()

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    media_list = [
        Media(
            msg_id=row["msg_id"],
            chat_id=row["chat_id"],
            media_type=row["media_type"],
            media_id=row["media_id"]
        )
        for row in rows
    ]

    next_cursor = None
    if has_more and media_list:
        next_cursor = media_list[-1].msg_id

    return {
        "media": media_list,
        "has_more": has_more,
        "next_cursor": next_cursor
    }
