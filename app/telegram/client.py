import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional

from fastapi import HTTPException, status
from telethon import TelegramClient, events, utils
from telethon.tl.types import Channel, PeerChannel

from app.settings import settings
from app.telegram.base import TelegramMonitorProtocol

# Set up logging to help with debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SESSIONS_DIR = Path(__file__).parent.parent / "sessions"


# Class to manage our Telegram client and channel monitoring
class TelegramMonitor(TelegramMonitorProtocol):
    def __init__(self) -> None:
        self.client: Optional[TelegramClient] = None
        self.monitored_channels: Dict[int, str] = {}
        # Add a way to store the verification code
        self._verification_code: Optional[str] = None
        self._verification_event = asyncio.Event()

    async def start(self) -> None:
        """Initialize and start the Telegram client"""
        if self.client is None:
            logger.info("Starting Telegram client...")
            self.client = TelegramClient(
                f"{SESSIONS_DIR}/session",
                settings.telegram.API_ID,
                settings.telegram.API_HASH,
            )

            start_options = {"code_callback": self._code_callback}
            if settings.telegram.BOT_TOKEN:
                start_options["bot_token"] = settings.telegram.BOT_TOKEN
            else:
                start_options["phone"] = settings.telegram.PHONE

            await self.client.start(**start_options)

            logger.info(
                f"Telegram client started successfully. adding channels {settings.telegram.CHANNELS}"
            )
            channels = settings.telegram.CHANNELS.split(",")
            for channel_username in channels:
                await self.add_channel(channel_username)

            # Set up event handler for new messages
            @self.client.on(events.NewMessage())
            async def handle_new_message(event):
                logger.info(f"Received new message: {event.text[:50]}...")
                await self._process_event(event, "new")

            @self.client.on(events.MessageEdited())
            async def handle_message_edited(event):
                logger.info(f"Received edited message: {event.text[:50]}...")
                await self._process_event(event, "edit")

            @self.client.on(events.MessageDeleted())
            async def handle_message_deleted(event):
                logger.info(f"Received deleted message: {event.chat_id}")
                await self._process_event(event, "delete")

    async def _process_event(self, event, event_type):
        try:
            chat = await event.get_chat()
            if chat is None:
                logger.info(
                    f"Received event({event_type}) from unknown channel({event.chat_id})"
                )
                return
            real_id, peer_type = utils.resolve_id(event.chat_id)
            logger.info(
                f"Received event({event_type}) from channel({chat.title}) => real_id: {real_id}, peer_type: {peer_type}"
            )

            if real_id == [
                settings.telegram.TARGET_CHANNEL_ID,
                settings.telegram.TARGET_CHANNEL_ID_UNMONITORED,
            ]:
                logger.info(f"Received event({event_type}) from target channel")
                return

            if real_id not in self.monitored_channels:
                logger.info(f"unmonitored channel({chat.title})")
                await self.forward_message(
                    event.message, settings.telegram.TARGET_CHANNEL_ID_UNMONITORED
                )
                return

            # chat = await event.get_chat()
            # logger.info(f"chat: {chat}")
            # sender = await event.get_sender()
            # logger.info(f"sender: {sender}")
            # message = event.message
            # logger.info(f"message: {message}")
            #
            # message_data = {
            #     'event_type': event_type,
            #     'source_channel_id': event.chat_id,
            #     'message_id': event.id,
            #     'text': event.text,
            #     'timestamp': event.date,
            #     'sender_id': sender.id if sender else None,
            #     'sender_name': sender.username if sender else None
            # }
            #
            # logger.info(f"new Event:{'-' * 200}")
            # logger.info(json.dumps(message_data, default=str))
            # logger.info(f"{'-' * 200}")

            if settings.telegram.FORWARD:
                await self.forward_message(
                    event.message, settings.telegram.TARGET_CHANNEL_ID
                )
            else:
                await self.send_message(
                    event.message, settings.telegram.TARGET_CHANNEL_ID
                )
        except Exception as e:
            logger.error(f"Error processing event: {str(e)}")

    async def add_channel(self, channel_username: str):
        """Add a new channel to monitor"""
        try:
            channel_entity = channel_username
            if channel_username.isnumeric():
                channel_entity = PeerChannel(channel_id=int(channel_username))
            channel = await self.client.get_entity(channel_entity)
            if isinstance(channel, Channel):
                self.monitored_channels[channel.id] = channel_username
                logger.info(f"Added channel: {channel_username}")
                return {"status": "success", "channel_id": channel.id}
            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Not a valid channel"
                )
        except Exception as e:
            logger.error(f"Error adding channel {channel_username}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            )

    async def remove_channel(self, channel_username: str):
        """Remove a channel from monitoring"""
        channel_id = None
        for cid, cusername in self.monitored_channels.items():
            if cusername == channel_username:
                channel_id = cid
                break

        if channel_id:
            del self.monitored_channels[channel_id]
            logger.info(f"Removed channel: {channel_username}")
            return {"status": "success", "channel": channel_username}
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found"
            )

    def get_monitored_channels(self):
        """Get list of currently monitored channels"""
        return self.monitored_channels

    async def get_all_channels(self):
        """Fetch all channels the bot is a member of"""
        channels = await self.client.get_dialogs()
        return [
            {
                "id": channel.id,
                "name": channel.name,
                "username": (
                    channel.entity.username
                    if isinstance(channel.entity, Channel)
                    else None
                ),
            }
            for channel in channels
            if isinstance(channel.entity, Channel)
        ]

    async def send_message(self, message, target_channel_id):
        try:
            await self.client.send_message(PeerChannel(target_channel_id), message)
        except Exception as e:
            logger.error(f"Error sending message: {str(e)}")

    async def forward_message(self, message, target_channel_id):
        try:
            await self.client.forward_messages(PeerChannel(target_channel_id), message)
        except Exception as e:
            logger.error(f"Error forwarding message: {str(e)}")

    async def _code_callback(self):
        """Wait for verification code to be provided via API"""
        logger.info("Waiting for verification code to be provided via API...")
        await self._verification_event.wait()  # Wait for code to be set
        code = self._verification_code
        self._verification_code = None  # Clear the code
        self._verification_event.clear()  # Reset the event
        return code

    async def provide_verification_code(self, code: str):
        """Method to provide verification code through API"""
        self._verification_code = code
        self._verification_event.set()  # Signal that code is available
        logger.info("Verification code provided")

    async def stop(self) -> None:
        if self.client is None:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail="Telegram client is not running",
            )
        try:
            await self.client.disconnect()
            self.client = None
            logger.info("Telegram client stopped successfully")
        except Exception as e:
            logger.error(f"Error stopping Telegram client: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            )

    async def is_running(self) -> bool:
        if self.client is None:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail="Telegram client is not running",
            )

        try:
            return await self.client.is_user_authorized()
        except Exception as e:
            logger.error(f"Error checking if Telegram client is running: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            )
