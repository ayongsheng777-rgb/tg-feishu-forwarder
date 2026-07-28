"""Telegram client manager built on Telethon.

Handles:
  - Session management (interactive login via phone + code)
  - Listing joined channels/chats
  - Listening to new messages in monitored channels
  - Downloading media from messages
"""
import asyncio
import os
import logging
from typing import Any

from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat, PeerChannel
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.functions.channels import GetChannelsRequest

from app.core.config import settings

logger = logging.getLogger(__name__)

# Session files are stored alongside the SQLite DB in /data
SESSION_DIR = os.path.dirname(settings.db_url.replace("sqlite+aiosqlite://", "")) or "/data"


class TelegramManager:
    """Singleton Telethon client wrapper."""

    _instance: "TelegramManager | None" = None
    _client: TelegramClient | None = None
    _listener_task: asyncio.Task | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ------------------------------------------------------------------
    #  Client lifecycle
    # ------------------------------------------------------------------

    @property
    def session_path(self) -> str:
        return os.path.join(SESSION_DIR, settings.tg_session_name)

    def _get_client(self) -> TelegramClient:
        if self._client is None:
            kwargs: dict[str, Any] = {}
            if settings.tg_api_id and settings.tg_api_hash:
                kwargs["api_id"] = settings.tg_api_id
                kwargs["api_hash"] = settings.tg_api_hash
            else:
                # Allow reading from DB settings at runtime
                kwargs["api_id"] = 0
                kwargs["api_hash"] = ""

            proxy = settings.proxy_tuple
            if proxy:
                kwargs["proxy"] = proxy

            self._client = TelegramClient(self.session_path, **kwargs)
        return self._client

    async def connect(self) -> bool:
        """Connect and return True if a valid session exists."""
        client = self._get_client()
        if not client.is_connected():
            await client.connect()
        return await client.is_user_authorized()

    async def disconnect(self):
        client = self._get_client()
        if client and client.is_connected():
            await client.disconnect()

    @property
    def is_connected(self) -> bool:
        client = self._client
        return client is not None and client.is_connected()

    # ------------------------------------------------------------------
    #  Authentication (interactive)
    # ------------------------------------------------------------------

    async def start_login(self, phone: str) -> str:
        """Send a login code to the given phone number. Returns a status string."""
        client = self._get_client()
        if not client.is_connected():
            await client.connect()
        await client.send_code_request(phone)
        return "code_sent"

    async def complete_login(self, phone: str, code: str, password: str = "") -> dict:
        """Complete login with the code (and 2FA password if required)."""
        client = self._get_client()
        try:
            await client.sign_in(phone=phone, code=code, password=password or None)
            return {"success": True, "message": "Login successful"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    async def get_login_status(self) -> dict:
        """Return current session status."""
        client = self._get_client()
        if not client.is_connected():
            await client.connect()
        authorized = await client.is_user_authorized()
        me = None
        if authorized:
            try:
                me = await client.get_me()
            except Exception:
                pass
        return {
            "authorized": authorized,
            "connected": self.is_connected,
            "user": {
                "id": me.id,
                "first_name": me.first_name,
                "username": me.username,
                "phone": me.phone,
            } if me else None,
        }

    # ------------------------------------------------------------------
    #  Channel discovery
    # ------------------------------------------------------------------

    async def list_dialogs(self) -> list[dict]:
        """List all dialogs (channels, chats, users) the account has joined."""
        client = self._get_client()
        if not client.is_connected():
            await client.connect()
        result = []
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            is_channel = isinstance(entity, (Channel, Chat))
            result.append({
                "id": str(dialog.id),
                "title": dialog.title or entity.username or str(dialog.id),
                "username": getattr(entity, "username", None),
                "is_channel": is_channel,
                "type": type(entity).__name__,
            })
        return result

    # ------------------------------------------------------------------
    #  Message listener
    # ------------------------------------------------------------------

    async def start_listener(self, channel_ids: list[str], on_new_message):
        """Register an event handler for new messages in the given channels.

        ``on_new_message`` is an async callable that receives the Telethon
        event.  This method is meant to be run as a background task.
        """
        client = self._get_client()
        if not client.is_connected():
            await client.connect()

        # Build a set of int channel IDs for fast lookup
        cid_set = set()
        for cid in channel_ids:
            try:
                cid_set.add(int(cid))
            except ValueError:
                pass

        @client.on(events.NewMessage())
        async def _handler(event):
            chat_id = event.chat_id
            # Only process messages from monitored channels
            if cid_set and chat_id not in cid_set:
                return
            try:
                await on_new_message(event)
            except Exception:
                logger.exception("Error in message handler for chat %s", chat_id)

        logger.info("TG listener registered for channels: %s", channel_ids)
        # Keep the client running
        await client.run_until_disconnected()

    async def stop_listener(self):
        """Stop the background listener."""
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        self._listener_task = None

    async def download_media(self, message, file_path: str | None = None) -> str | None:
        """Download media from a message. Returns the local file path or None."""
        client = self._get_client()
        if file_path is None:
            file_path = settings.media_cache_dir
        os.makedirs(file_path, exist_ok=True)
        result = await client.download_media(message, file=file_path)
        return result


# Module-level singleton
tg_manager = TelegramManager()
