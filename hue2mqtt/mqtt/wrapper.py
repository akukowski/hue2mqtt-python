"""MQTT Wrapper."""

import asyncio
import logging
import ssl
from typing import Any, Callable, Coroutine, Dict, Match, Optional

import aiomqtt
from pydantic import BaseModel

from hue2mqtt.config import MQTTBrokerInfo

from .topic import Topic

LOGGER = logging.getLogger(__name__)

Handler = Callable[[Match[str], str], Coroutine[Any, Any, None]]


class MQTTWrapper:
    """
    MQTT wrapper class.

    Wraps the functionality that we are using for MQTT, with extra
    sanity checks and validation to make sure that things are less
    likely to go wrong.
    """

    _client: Optional[aiomqtt.Client]
    _message_task: Optional[asyncio.Task[None]]

    def __init__(
        self,
        client_name: str,
        broker_info: MQTTBrokerInfo,
        *,
        last_will: Optional[BaseModel] = None,
    ) -> None:
        self._client_name = client_name
        self._broker_info = broker_info
        self._last_will = last_will

        self._topic_handlers: Dict[Topic, Handler] = {}
        self._client = None
        self._message_task = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Determine if the client connected to the broker."""
        return self._connected

    @property
    def last_will_message(self) -> Optional[aiomqtt.Will]:
        """Last will and testament message for this client."""
        if self._last_will is not None:
            return aiomqtt.Will(
                topic=self.mqtt_prefix + "/status",
                payload=self._last_will.model_dump_json(
                    by_alias=True,
                    exclude_none=True,
                ),
                retain=True,
            )
        return None

    @property
    def mqtt_prefix(self) -> str:
        """The topic prefix for MQTT."""
        return self._broker_info.topic_prefix

    def _build_client(self) -> aiomqtt.Client:
        """Build and return a configured aiomqtt Client."""
        protocol = aiomqtt.ProtocolVersion.V5
        if self._broker_info.force_protocol_version_3_1:
            protocol = aiomqtt.ProtocolVersion.V311

        tls_context: Optional[ssl.SSLContext] = None
        if self._broker_info.enable_tls:
            tls_context = ssl.create_default_context()

        kwargs: Dict[str, Any] = {
            "hostname": self._broker_info.host,
            "port": self._broker_info.port,
            "identifier": self._client_name,
            "protocol": protocol,
            "will": self.last_will_message,
            "tls_context": tls_context,
        }

        if self._broker_info.enable_auth:
            LOGGER.debug("MQTT Auth enabled")
            kwargs["username"] = self._broker_info.username
            kwargs["password"] = self._broker_info.password

        return aiomqtt.Client(**kwargs)

    async def connect(self) -> None:
        """Connect to the broker."""
        if self._connected:
            LOGGER.error("Attempting connection, but client is already connected.")
            return

        client = self._build_client()
        await client.__aenter__()
        self._client = client
        self._connected = True

        for topic in self._topic_handlers:
            LOGGER.debug(f"Subscribing to {topic}")
            await self._client.subscribe(str(topic))

        self._message_task = asyncio.ensure_future(self._message_loop())  # type: ignore[assignment]

    async def disconnect(self) -> None:
        """Disconnect from the broker."""
        if not self._connected:
            LOGGER.error(
                "Attempting disconnection, but client is already disconnected.",
            )
            return

        if self._message_task is not None:
            self._message_task.cancel()
            try:
                await self._message_task
            except asyncio.CancelledError:
                pass
            self._message_task = None

        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None

        self._connected = False

    async def _message_loop(self) -> None:
        """Process incoming MQTT messages in the background."""
        assert self._client is not None
        async for message in self._client.messages:
            topic = str(message.topic)
            payload = message.payload
            payload_str = payload.decode() if isinstance(payload, (bytes, bytearray)) else str(payload)
            LOGGER.debug(f"Message received on {topic} with payload: {payload!r}")
            for t, handler in self._topic_handlers.items():
                match = t.match(topic)
                if match:
                    LOGGER.debug(f"Calling {handler.__name__} to handle {topic}")
                    asyncio.ensure_future(handler(match, payload_str))

    def publish(
        self,
        topic: str,
        payload: BaseModel,
        *,
        retain: bool = False,
        auto_prefix_topic: bool = True,
    ) -> None:
        """Publish a payload to the broker."""
        if not self._connected:
            LOGGER.error(
                "Attempted to publish message, but client is not connected.",
            )

        prefix = self._broker_info.topic_prefix

        if len(topic) == 0:
            topic_complete = Topic.parse(prefix)
        elif auto_prefix_topic:
            topic_complete = Topic.parse(f"{prefix}/{topic}")
        else:
            topic_complete = Topic.parse(topic)

        if not topic_complete.is_publishable:
            raise ValueError(f"Cannot publish to MQTT topic: {topic_complete}")

        assert self._client is not None
        asyncio.ensure_future(
            self._client.publish(
                str(topic_complete),
                payload.model_dump_json(by_alias=True, exclude_none=True),
                qos=1,
                retain=retain,
            ),
        )

    def subscribe(
        self,
        topic: str,
        callback: Handler,
    ) -> None:
        """
        Subscribe to an MQTT Topic.

        Callback is called when a message arrives.

        Should be called before the MQTT wrapper is connected.
        """
        if len(topic) == 0:
            topic_complete = Topic.parse(self.mqtt_prefix)
        else:
            topic_complete = Topic.parse(f"{self._broker_info.topic_prefix}/{topic}")

        self._topic_handlers[topic_complete] = callback
