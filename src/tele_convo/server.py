"""JSON-RPC WebSocket server module for tele-convo.

This module provides a WebSocket server that handles JSON-RPC 2.0 requests
for querying messages, chats, users, media, and full-text search.
"""

import asyncio
import json
import logging
from typing import Any, Optional

import websockets

from tele_convo.config import Config, load_config
from tele_convo.db import (
    Chat,
    Media,
    Message,
    User,
    count_messages,
    get_all_chats,
    get_media_with_filters,
    get_messages_with_filters,
    search_messages_fulltext,
    search_users,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# JSON-RPC error codes
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

# Default limits
DEFAULT_MESSAGES_LIMIT = 50
MAX_MESSAGES_LIMIT = 200
DEFAULT_USERS_LIMIT = 50
DEFAULT_MEDIA_LIMIT = 50
DEFAULT_SEARCH_LIMIT = 50


class JSONRPCError:
    """JSON-RPC error object."""

    def __init__(self, code: int, message: str, data: Optional[Any] = None):
        self.code = code
        self.message = message
        self.data = data

    def to_dict(self) -> dict[str, Any]:
        error = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return error


def create_error_response(
    error: JSONRPCError, request_id: Optional[Any] = None
) -> dict[str, Any]:
    """Create a JSON-RPC error response.

    Args:
        error: The JSONRPCError object: The request ID.
        request_id from the original request.

    Returns:
        JSON-RPC error response dictionary.
    """
    response = {
        "jsonrpc": "2.0",
        "error": error.to_dict(),
    }
    if request_id is not None:
        response["id"] = request_id
    return response


def create_success_response(
    result: Any, request_id: Optional[Any] = None
) -> dict[str, Any]:
    """Create a JSON-RPC success response.

 result: The result data.
        request    Args:
       _id: The request ID from the original request.

    Returns:
        JSON-RPC success response dictionary.
    """
    response = {
        "jsonrpc": "2.0",
        "result": result,
    }
    if request_id is not None:
        response["id"] = request_id
    return response


def serialize_message(message: Message) -> dict[str, Any]:
    """Serialize a Message object to dictionary.

    Args:
        message: Message object to serialize.

    Returns:
        Dictionary representation of the message.
    """
    return {
        "id": message.id,
        "chat_id": message.chat_id,
        "sender_id": message.sender_id,
        "date": message.date.isoformat() if message.date else None,
        "text": message.text,
        "reply_to_msg_id": message.reply_to_msg_id,
        "is_forwarded": message.is_forwarded,
        "raw_json": message.raw_json,
    }


def serialize_chat(chat: Chat) -> dict[str, Any]:
    """Serialize a Chat object to dictionary.

    Args:
        chat: Chat object to serialize.

    Returns:
        Dictionary representation of the chat.
    """
    return {
        "id": chat.id,
        "title": chat.title,
        "username": chat.username,
    }


def serialize_user(user: User) -> dict[str, Any]:
    """Serialize a User object to dictionary.

    Args:
        user: User object to serialize.

    Returns:
        Dictionary representation of the user.
    """
    return {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }


def serialize_media(media: Media) -> dict[str, Any]:
    """Serialize a Media object to dictionary.

    Args:
        media: Media object to serialize.

    Returns:
        Dictionary representation of the media.
    """
    return {
        "msg_id": media.msg_id,
        "chat_id": media.chat_id,
        "media_type": media.media_type,
        "media_id": media.media_id,
    }


async def handle_get_messages(params: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Handle getMessages JSON-RPC method.

    Parameters:
        chat_id (int, optional): Filter by chat ID.
        sender_id (int, optional): Filter by sender ID.
        keyword (string, optional): Search keyword in message text.
        date_from (string, optional): Filter messages from this date.
        date_to (string, optional): Filter messages to this date.
        limit (int, optional, default: 50, max: 200): Maximum results.
        cursor (string, optional): Pagination cursor.

    Returns:
        Dictionary with messages array, next_cursor, has_more, total_count.
    """
    # Extract parameters
    chat_id = params.get("chat_id") if params else None
    sender_id = params.get("sender_id") if params else None
    keyword = params.get("keyword") if params else None
    date_from = params.get("date_from") if params else None
    date_to = params.get("date_to") if params else None
    limit = params.get("limit", DEFAULT_MESSAGES_LIMIT) if params else DEFAULT_MESSAGES_LIMIT
    cursor = params.get("cursor") if params else None

    # Validate limit
    if not isinstance(limit, int):
        raise JSONRPCError(
            JSONRPC_INVALID_PARAMS,
            "limit must be an integer"
        )
    limit = min(limit, MAX_MESSAGES_LIMIT)
    if limit <= 0:
        raise JSONRPCError(
            JSONRPC_INVALID_PARAMS,
            "limit must be greater than 0"
        )

    # Validate cursor if provided
    if cursor is not None and not isinstance(cursor, str):
        raise JSONRPCError(
            JSONRPC_INVALID_PARAMS,
            "cursor must be a string"
        )

    # Get messages from database
    result = await get_messages_with_filters(
        chat_id=chat_id,
        sender_id=sender_id,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        cursor=cursor
    )

    # Get total count
    total_count = await count_messages(
        chat_id=chat_id,
        sender_id=sender_id,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to
    )

    return {
        "messages": [serialize_message(msg) for msg in result["messages"]],
        "next_cursor": result["next_cursor"],
        "has_more": result["has_more"],
        "total_count": total_count
    }


async def handle_get_chats(_params: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Handle getChats JSON-RPC method.

    Returns:
        Dictionary with chats array.
    """
    chats = await get_all_chats()
    return {
        "chats": [serialize_chat(chat) for chat in chats]
    }


async def handle_get_users(params: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Handle getUsers JSON-RPC method.

    Parameters:
        keyword (string, optional): Search keyword.
        limit (int, optional): Maximum results.
        cursor (string, optional): Pagination cursor.

    Returns:
        Dictionary with users array, next_cursor, has_more.
    """
    # Extract parameters
    keyword = params.get("keyword") if params else None
    limit = params.get("limit", DEFAULT_USERS_LIMIT) if params else DEFAULT_USERS_LIMIT
    cursor = params.get("cursor") if params else None

    # Validate limit
    if not isinstance(limit, int):
        raise JSONRPCError(
            JSONRPC_INVALID_PARAMS,
            "limit must be an integer"
        )
    if limit <= 0:
        raise JSONRPCError(
            JSONRPC_INVALID_PARAMS,
            "limit must be greater than 0"
        )

    result = await search_users(
        keyword=keyword,
        limit=limit,
        cursor=cursor
    )

    return {
        "users": [serialize_user(user) for user in result["users"]],
        "next_cursor": result.get("next_cursor"),
        "has_more": result["has_more"]
    }


async def handle_get_media(params: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Handle getMedia JSON-RPC method.

    Parameters:
        chat_id (int, optional): Filter by chat ID.
        media_type (string, optional): Filter by media type.
        limit (int, optional): Maximum results.
        cursor (string, optional): Pagination cursor.

    Returns:
        Dictionary with media array, next_cursor, has_more.
    """
    # Extract parameters
    chat_id = params.get("chat_id") if params else None
    media_type = params.get("media_type") if params else None
    limit = params.get("limit", DEFAULT_MEDIA_LIMIT) if params else DEFAULT_MEDIA_LIMIT
    cursor_str = params.get("cursor") if params else None

    # Convert cursor string to int if provided
    cursor = None
    if cursor_str is not None:
        try:
            cursor = int(cursor_str)
        except ValueError:
            raise JSONRPCError(
                JSONRPC_INVALID_PARAMS,
                "cursor must be a valid integer string"
            )

    # Validate limit
    if not isinstance(limit, int):
        raise JSONRPCError(
            JSONRPC_INVALID_PARAMS,
            "limit must be an integer"
        )
    if limit <= 0:
        raise JSONRPCError(
            JSONRPC_INVALID_PARAMS,
            "limit must be greater than 0"
        )

    result = await get_media_with_filters(
        chat_id=chat_id,
        media_type=media_type,
        limit=limit,
        cursor=cursor
    )

    return {
        "media": [serialize_media(media) for media in result["media"]],
        "next_cursor": str(result["next_cursor"]) if result["next_cursor"] else None,
        "has_more": result["has_more"]
    }


async def handle_search(params: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Handle search JSON-RPC method.

    Parameters:
        query (string, required): Search query string.
        date_from (string, optional): Filter messages from this date.
        date_to (string, optional): Filter messages to this date.
        limit (int, optional): Maximum results.

    Returns:
        Dictionary with results array, has_more.
    """
    # Validate params
    if not params:
        raise JSONRPCError(
            JSONRPC_INVALID_PARAMS,
            "query parameter is required"
        )

    query = params.get("query")
    if not query:
        raise JSONRPCError(
            JSONRPC_INVALID_PARAMS,
            "query parameter is required"
        )

    if not isinstance(query, str):
        raise JSONRPCError(
            JSONRPC_INVALID_PARAMS,
            "query must be a string"
        )

    date_from = params.get("date_from")
    date_to = params.get("date_to")
    limit = params.get("limit", DEFAULT_SEARCH_LIMIT)

    # Validate limit
    if not isinstance(limit, int):
        raise JSONRPCError(
            JSONRPC_INVALID_PARAMS,
            "limit must be an integer"
        )
    if limit <= 0:
        raise JSONRPCError(
            JSONRPC_INVALID_PARAMS,
            "limit must be greater than 0"
        )

    result = await search_messages_fulltext(
        query=query,
        date_from=date_from,
        date_to=date_to,
        limit=limit
    )

    return {
        "results": [serialize_message(msg) for msg in result["messages"]],
        "has_more": result["has_more"]
    }


# Method handlers map
METHOD_HANDLERS = {
    "getMessages": handle_get_messages,
    "getChats": handle_get_chats,
    "getUsers": handle_get_users,
    "getMedia": handle_get_media,
    "search": handle_search,
}


async def handle_jsonrpc_request(request: dict[str, Any]) -> dict[str, Any]:
    """Handle a single JSON-RPC request.

    Args:
        request: JSON-RPC request dictionary.

    Returns:
        JSON-RPC response dictionary.
    """
    # Validate JSON-RPC version
    if request.get("jsonrpc") != "2.0":
        return create_error_response(
            JSONRPCError(JSONRPC_INVALID_REQUEST, "Invalid JSON-RPC version"),
            request.get("id")
        )

    # Validate method exists
    method = request.get("method")
    if not method or method not in METHOD_HANDLERS:
        return create_error_response(
            JSONRPCError(JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}"),
            request.get("id")
        )

    # Get params
    params = request.get("params")

    # Validate params is object or null
    if params is not None and not isinstance(params, dict):
        return create_error_response(
            JSONRPCError(JSONRPC_INVALID_PARAMS, "Params must be an object or null"),
            request.get("id")
        )

    # Call the method handler
    try:
        handler = METHOD_HANDLERS[method]
        result = await handler(params)
        return create_success_response(result, request.get("id"))
    except JSONRPCError as e:
        return create_error_response(e, request.get("id"))
    except Exception as e:
        logger.exception(f"Error handling method {method}")
        return create_error_response(
            JSONRPCError(JSONRPC_INTERNAL_ERROR, str(e)),
            request.get("id")
        )


async def handle_websocket_client(websocket: websockets.WebSocketServerProtocol) -> None:
    """Handle a WebSocket client connection.

    Args:
        websocket: WebSocket connection.
    """
    client_addr = websocket.remote_address
    logger.info(f"Client connected: {client_addr}")

    try:
        async for message in websocket:
            logger.debug(f"Received message from {client_addr}: {message}")

            # Parse the incoming JSON
            try:
                request = json.loads(message)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON from {client_addr}: {e}")
                error_response = create_error_response(
                    JSONRPCError(JSONRPC_PARSE_ERROR, f"Invalid JSON: {str(e)}"),
                    None
                )
                await websocket.send(json.dumps(error_response))
                continue

            # Handle batch requests (array)
            if isinstance(request, list):
                responses = []
                for req in request:
                    if isinstance(req, dict):
                        responses.append(await handle_jsonrpc_request(req))
                if responses:
                    await websocket.send(json.dumps(responses))
                else:
                    error_response = create_error_response(
                        JSONRPCError(
                            JSONRPC_INVALID_REQUEST,
                            "Invalid batch request: all items must be objects"
                        ),
                        None
                    )
                    await websocket.send(json.dumps(error_response))

            # Handle single request (object)
            elif isinstance(request, dict):
                response = await handle_jsonrpc_request(request)
                await websocket.send(json.dumps(response))

            else:
                error_response = create_error_response(
                    JSONRPCError(JSONRPC_INVALID_REQUEST, "Request must be an object or array"),
                    None
                )
                await websocket.send(json.dumps(error_response))

    except websockets.exceptions.ConnectionClosed:
        logger.info(f"Client disconnected: {client_addr}")
    except Exception as e:
        logger.exception(f"Error handling client {client_addr}: {e}")
    finally:
        logger.info(f"Connection closed: {client_addr}")


async def start_server(config: Config) -> None:
    """Start the WebSocket JSON-RPC server.

    Args:
        config: Configuration object with server settings.
    """
    logger.info(f"Starting WebSocket server on {config.ws_host}:{config.ws_port}")

    async with websockets.serve(
        handle_websocket_client,
        config.ws_host,
        config.ws_port
    ):
        logger.info(f"Server started successfully on ws://{config.ws_host}:{config.ws_port}")
        # Keep the server running
        await asyncio.Future()  # Run forever


async def run_server() -> None:
    """Run the WebSocket server with configuration from environment."""
    try:
        config = load_config()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return

    await start_server(config)


if __name__ == "__main__":
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
