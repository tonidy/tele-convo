# tele-convo

Telegram message scraper with SQLite storage and JSON-RPC WebSocket interface.

## Overview

tele-convo is a Telegram scraper that fetches historical messages (backfill) and listens for new messages in real-time. Messages are stored in a SQLite database with a normalized schema and can be queried via a JSON-RPC WebSocket API.

## Features

- **Telegram Integration**: Connect to Telegram using Telethon
  - Historical message backfill with chunked fetching
  - Real-time live message listening
  - Media extraction (photos, videos, audio, documents, stickers)
- **SQLite Storage**: Persistent local storage with normalized schema
  - Tables: `chats`, `users`, `messages`, `media`
  - Full-text search (FTS5) for efficient message searching
  - WAL mode for better concurrency
- **JSON-RPC WebSocket API**: Query stored messages programmatically
  - Get messages with filters and pagination
  - Search users and chats
  - Full-text message search

## Prerequisites

- Python 3.12 or higher
- Telegram API credentials (API ID and API Hash)

### Getting Telegram API Credentials

1. Visit [my.telegram.org](https://my.telegram.org)
2. Log in with your Telegram account
3. Click "API Development Tools"
4. Create a new application to get your `api_id` and `api_hash`

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/tele-convo.git
cd tele-convo

# Install uv if not present
https://docs.astral.sh/uv/getting-started/installation

# Install dependencies
uv sync
```

## Configuration

1. Copy the example environment file:

```bash
cp .env.example .env
```

2. Edit `.env` with your Telegram credentials:

```env
# Telegram API credentials (required)
API_ID=your_api_id_here
API_HASH=your_api_hash_here

# Telethon session name (optional)
SESSION_NAME=tele_convo

# Target group/channel URL (required)
GROUP_URL=https://t.me/your_group_here

# SQLite database path (optional)
DB_PATH=data/messages.db

# WebSocket server settings (optional)
WS_HOST=0.0.0.0
WS_PORT=8765
```

### Configuration Reference

| Variable       | Required | Default            | Description                          |
|----------------|----------|--------------------|--------------------------------------|
| `API_ID`       | Yes      | -                  | Telegram API ID (integer)            |
| `API_HASH`     | Yes      | -                  | Telegram API Hash string             |
| `SESSION_NAME` | No       | `tele_convo`       | Telethon session file name           |
| `GROUP_URL`    | Yes      | -                  | Target group/channel URL or username |
| `DB_PATH`      | No       | `data/messages.db` | SQLite database file path            |
| `WS_HOST`      | No       | `0.0.0.0`          | WebSocket server bind host           |
| `WS_PORT`      | No       | `8765`             | WebSocket server port                |

## Usage

### Command-Line Interface

tele-convo provides four operation modes:

```bash
# Run all operations (backfill + listen + serve)
uv run python -m tele_convo

# Or specify mode explicitly
uv run python -m tele_convo all

# Backfill historical messages only
uv run python -m tele_convo backfill

# Listen for new messages only
uv run python -m tele_convo listen

# Start WebSocket server only
uv run python -m tele_convo serve
```

### Command-Line Options

| Option        | Description                                                       |
|---------------|-------------------------------------------------------------------|
| `mode`        | Operation mode: `backfill`, `listen`, `serve`, or `all` (default) |
| `--limit`     | Limit the number of messages to backfill                          |
| `--no-listen` | When using `all` mode, skip live listening                        |

### Examples

```bash
# Fetch last 1000 messages from a group
uv run python -m tele_convo backfill --limit 1000

# Start server only (for API access)
uv run python -m tele_convo serve

# Run everything but skip live listening
uv run python -m tele_convo all --no-listen
```

## WebSocket API

The WebSocket server implements JSON-RPC 2.0 protocol. Connect to `ws://localhost:8765` (or your configured host:port).

### Connection

```python
import asyncio
import websockets
import json

async def main():
    uri = "ws://localhost:8765"
    async with websockets.connect(uri) as websocket:
        # Send request and receive response
        pass

asyncio.run(main())
```

### JSON-RPC Methods

#### getMessages

Get messages with optional filters and pagination.

**Parameters:**

| Parameter   | Type    | Required | Description                               |
|-------------|---------|----------|-------------------------------------------|
| `chat_id`   | integer | No       | Filter by chat ID                         |
| `sender_id` | integer | No       | Filter by sender ID                       |
| `keyword`   | string  | No       | Search keyword in message text            |
| `date_from` | string  | No       | Filter messages from this date (ISO 8601) |
| `date_to`   | string  | No       | Filter messages to this date (ISO 8601)   |
| `limit`     | integer | No       | Max results (default: 50, max: 200)       |
| `cursor`    | string  | No       | Pagination cursor from previous response  |

**Request:**

```json
{
  "jsonrpc": "2.0",
  "method": "getMessages",
  "params": {
    "limit": 10,
    "keyword": "hello"
  },
  "id": 1
}
```

**Response:**

```json
{
  "jsonrpc": "2.0",
  "result": {
    "messages": [
      {
        "id": 123,
        "chat_id": 456,
        "sender_id": 789,
        "date": "2024-01-15T10:30:00",
        "text": "Hello, world!",
        "reply_to_msg_id": null,
        "is_forwarded": false,
        "raw_json": "{...}"
      }
    ],
    "next_cursor": "base64_encoded_cursor",
    "has_more": true,
    "total_count": 150
  },
  "id":```

#### getCh 1
}
ats

Get all stored chats.

**Parameters:** None

**Request:**

```json
{
  "jsonrpc": "2.0",
  "method": "getChats",
  "params": null,
  "id": 2
}
```

**Response:**

```json
{
  "jsonrpc": "2.0",
  "result": {
    "chats": [
      {
        "id": 456,
        "title": "My Telegram Group",
        "username": "my_group"
      }
    ]
  },
  "id": 2
}
```

#### getUsers

Get users with optional search.

**Parameters:**

| Parameter | Type    | Required | Description                       |
|-----------|---------|----------|-----------------------------------|
| `keyword` | string  | No       | Search keyword (username or name) |
| `limit`   | integer | No       | Max results (default: 50)         |
| `cursor`  | string  | No       | Pagination cursor                 |

**Request:**

```json
{
  "jsonrpc": "2.0",
  "method": "getUsers",
  "params": {
    "keyword": "john",
    "limit": 10
  },
  "id": 3
}
```

#### getMedia

Get media attachments with optional filters.

**Parameters:**

| Parameter    | Type    | Required | Description                                                          |
|--------------|---------|----------|----------------------------------------------------------------------|
| `chat_id`    | integer | No       | Filter by chat ID                                                    |
| `media_type` | string  | No       | Filter by media type (photo, video, audio, voice, sticker, document) |
| `limit`      | integer | No       | Max results (default: 50)                                            |
| `cursor`     | string  | No       | Pagination cursor                                                    |

**Request:**

```json
{
  "jsonrpc": "2.0",
  "method": "getMedia",
  "params": {
    "media_type": "photo",
    "limit": 20
  },
  "id": 4
}
```

#### search

Full-text search across all messages.

**Parameters:**

| Parameter   | Type    | Required | Description                    |
|-------------|---------|----------|--------------------------------|
| `query`     | string  | Yes      | Search query string            |
| `date_from` | string  | No       | Filter messages from this date |
| `date_to`   | string  | No       | Filter messages to this date   |
| `limit`     | integer | No       | Max results (default: 50)      |

**Request:**

```json
{
  "jsonrpc": "2.0",
  "method": "search",
  "params": {
    "query": "important meeting",
    "limit": 20
  },
  "id": 5
}
```

**Response:**

```json
{
  "jsonrpc": "2.0",
  "result": {
    "results": [...],
    "has_more": false
  },
  "id": 5
}
```

### Complete Python Client Example

```python
import asyncio
import websockets
import json

async def call_method(websocket, method, params=None):
    request = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
        "id": 1
    }
    await websocket.send(json.dumps(request))
    response = json.loads(await websocket.recv())
    return response["result"]

async def main():
    uri = "ws://localhost:8765"
    
    async with websockets.connect(uri) as websocket:
        # Get recent messages
        messages = await call_method(websocket, "getMessages", {"limit": 10})
        print(f"Found {messages['total_count']} messages")
        for msg in messages["messages"]:
            print(f"  [{msg['date']}] {msg['text'][:50]}...")
        
        # Search for messages
        results = await call_method(websocket, "search", {"query": "hello"})
        print(f"Found {len(results['results'])} search results")
        
        # Get all chats
        chats = await call_method(websocket, "getChats")
        print(f"Stored {len(chats['chats'])} chats")
        
        # Get users
        users = await call_method(websocket, "getUsers", {"limit": 10})
        print(f"Found {len(users['users'])} users")

if __name__ == "__main__":
    asyncio.run(main())
```

## Project Structure

```
tele-convo/
├── src/
│   └── tele_convo/
│       ├── __init__.py        # Package initialization
│       ├── __main__.py        # Module entry point
│       ├── config.py          # Configuration loader
│       ├── db.py              # SQLite database operations
│       ├── telegram.py        # Telegram client (backfill + listening)
│       ├── server.py          # JSON-RPC WebSocket server
│       └── main.py           # CLI entry point and app orchestration
├── .env.example              # Example environment configuration
├── pyproject.toml            # Project dependencies and metadata
└── README.md                  # This file
```

### Module Description

| File                                        | Description                                           |
|---------------------------------------------|-------------------------------------------------------|
| [`config.py`](src/tele_convo/config.py)     | Loads configuration from environment variables        |
| [`db.py`](src/tele_convo/db.py)             | SQLite operations with normalized schema, FTS5 search |
| [`telegram.py`](src/tele_convo/telegram.py) | Telethon client with backfill and live listening      |
| [`server.py`](src/tele_convo/server.py)     | JSON-RPC 2.0 WebSocket server                         |
| [`main.py`](src/tele_convo/main.py)         | CLI interface and application orchestration           |

## Database Schema

### Tables

**chats**
- `id` (INTEGER PRIMARY KEY) - Chat ID
- `title` (TEXT) - Chat title
- `username` (TEXT) - Chat username

**users**
- `id` (INTEGER PRIMARY KEY) - User ID
- `username` (TEXT) - Username
- `first_name` (TEXT) - First name
- `last_name` (TEXT) - Last name

**messages**
- `id` (INTEGER PRIMARY KEY) - Message ID
- `chat_id` (INTEGER) - Chat ID (foreign key)
- `sender_id` (INTEGER) - Sender user ID (foreign key)
- `date` (TEXT) - Message date (ISO 8601)
- `text` (TEXT) - Message text content
- `reply_to_msg_id` (INTEGER) - Reply message ID
- `is_forwarded` (INTEGER) - Whether message is forwarded
- `raw_json` (TEXT) - Raw Telegram message JSON

**media**
- `msg_id` (INTEGER) - Message ID (primary key part)
- `chat_id` (INTEGER) - Chat ID (primary key part)
- `media_type` (TEXT) - Media type (photo, video, audio, etc.)
- `media_id` (TEXT) - Telegram media identifier

### Indexes

- `idx_messages_chat_id` - Messages by chat
- `idx_messages_sender_id` - Messages by sender
- `idx_messages_date` - Messages by date

### Full-Text Search

- `messages_fts` - FTS5 virtual table for full-text search on message text

## Anti-Ban Best Practices

When scraping Telegram, it's important to follow best practices to avoid rate limiting and account restrictions:

### Rate Limiting

- **Random Delays**: The client adds random delays (1-3 seconds) between API requests
- **Chunked Fetching**: Messages are fetched in chunks of 100
- **Exponential Backoff**: Failed requests are retried with exponential backoff

### FloodWaitError Handling

When Telegram returns a `FloodWaitError`, the client automatically:
1. Extracts the wait time from the error
2. Pauses execution for the specified duration
3. Retries the request after waiting

### Session Management

- **Session Persistence**: Telethon sessions are saved locally to avoid re-authentication
- **Graceful Disconnection**: The client properly disconnects on shutdown
- **Single Session**: Use a dedicated session for scraping to avoid conflicts

### Recommendations

1. **Use a test account first** - Don't use your primary Telegram account for scraping
2. **Start with small limits** - Begin with `--limit 100` to test
3. **Run during off-peak hours** - Avoid peak usage times
4. **Monitor for errors** - Check logs for `FloodWaitError` messages
5. **Space out sessions** - Don't run continuous backfills for extended periods
6. **Respect Telegram's Terms of Service** - Use responsibly

## License

MIT License - See LICENSE file for details.
