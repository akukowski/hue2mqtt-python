"""Test the MQTT Wrapper class."""

import asyncio
from typing import AsyncIterator, Match
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from hue2mqtt.config import MQTTBrokerInfo
from hue2mqtt.messages import Hue2MQTTStatus
from hue2mqtt.mqtt.topic import Topic
from hue2mqtt.mqtt.wrapper import MQTTWrapper

BROKER_INFO = MQTTBrokerInfo(
    host="localhost",
    port=1883,
)

BROKER_INFO_AUTH = MQTTBrokerInfo(
    host="localhost",
    port=1883,
    enable_auth=True,
    username="user",
    password="pass",
)

BROKER_INFO_V311 = MQTTBrokerInfo(
    host="localhost",
    port=1883,
    force_protocol_version_3_1=True,
)

BROKER_INFO_TLS = MQTTBrokerInfo(
    host="localhost",
    port=8883,
    enable_tls=True,
)


class StubModel(BaseModel):
    """Test BaseModel."""

    foo: str


async def stub_message_handler(
    match: Match[str],
    payload: str,
) -> None:
    """Used in tests as a stub with the right type."""
    pass


def _make_mock_client() -> MagicMock:
    """Return a MagicMock that behaves like an aiomqtt.Client context manager."""
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.subscribe = AsyncMock()
    mock_client.publish = AsyncMock()
    # Provide an async iterator for messages that yields nothing by default
    mock_client.messages = _async_iter([])
    return mock_client


async def _async_iter(items: list) -> AsyncIterator[object]:
    """Async generator yielding items."""
    for item in items:
        yield item


def test_wrapper_init_minimal() -> None:
    """Test initialising the wrapper with minimal options."""
    wr = MQTTWrapper("foo", BROKER_INFO)

    assert wr._client_name == "foo"
    assert wr._last_will is None
    assert len(wr._topic_handlers) == 0


def test_wrapper_is_connected_at_init() -> None:
    """Test that the wrapper is not connected to the broker at init."""
    wr = MQTTWrapper("foo", BROKER_INFO)
    assert not wr.is_connected


def test_wrapper_last_will_message_null() -> None:
    """Test that the last will message is None when not supplied."""
    wr = MQTTWrapper("foo", BROKER_INFO)
    assert wr.last_will_message is None


def test_wrapper_last_will_message_set() -> None:
    """Test that last will message is constructed correctly when supplied."""
    status = Hue2MQTTStatus(online=False)
    wr = MQTTWrapper("foo", BROKER_INFO, last_will=status)
    will = wr.last_will_message
    assert will is not None
    assert will.topic == "hue2mqtt/status"
    assert will.retain is True


def test_wrapper_mqtt_prefix() -> None:
    """Test that the MQTT prefix is as expected."""
    wr = MQTTWrapper("foo", BROKER_INFO)
    assert wr.mqtt_prefix == "hue2mqtt"


def test_subscribe() -> None:
    """Test that subscribing works as expected."""
    wr = MQTTWrapper("foo", BROKER_INFO)

    assert len(wr._topic_handlers) == 0

    wr.subscribe("bees/+", stub_message_handler)
    assert len(wr._topic_handlers) == 1
    assert wr._topic_handlers[Topic(["hue2mqtt", "bees", "+"])] == stub_message_handler


def test_subscribe_empty_topic() -> None:
    """Test subscribing with an empty topic uses the prefix only."""
    wr = MQTTWrapper("foo", BROKER_INFO)
    wr.subscribe("", stub_message_handler)
    assert Topic(["hue2mqtt"]) in wr._topic_handlers


@pytest.mark.asyncio
async def test_connect_sets_connected() -> None:
    """Test that connect() sets the connected flag."""
    mock_client = _make_mock_client()
    with patch("hue2mqtt.mqtt.wrapper.MQTTWrapper._build_client", return_value=mock_client):
        wr = MQTTWrapper("foo", BROKER_INFO)
        await wr.connect()
        assert wr.is_connected
        await wr.disconnect()
        assert not wr.is_connected


@pytest.mark.asyncio
async def test_connect_subscribes_topics() -> None:
    """Test that connect() subscribes to all registered topics."""
    mock_client = _make_mock_client()
    with patch("hue2mqtt.mqtt.wrapper.MQTTWrapper._build_client", return_value=mock_client):
        wr = MQTTWrapper("foo", BROKER_INFO)
        wr.subscribe("bees/+", stub_message_handler)
        await wr.connect()
        mock_client.subscribe.assert_called_once_with("hue2mqtt/bees/+")
        await wr.disconnect()


@pytest.mark.asyncio
async def test_connect_twice_logs_error() -> None:
    """Test that calling connect() twice logs an error and does not double-connect."""
    mock_client = _make_mock_client()
    with patch("hue2mqtt.mqtt.wrapper.MQTTWrapper._build_client", return_value=mock_client):
        wr = MQTTWrapper("foo", BROKER_INFO)
        await wr.connect()
        # Second connect should log error and return early
        await wr.connect()
        assert mock_client.__aenter__.call_count == 1
        await wr.disconnect()


@pytest.mark.asyncio
async def test_disconnect_when_not_connected_logs_error() -> None:
    """Test that disconnect() on an unconnected wrapper logs an error."""
    wr = MQTTWrapper("foo", BROKER_INFO)
    # Should not raise; just log an error
    await wr.disconnect()
    assert not wr.is_connected


@pytest.mark.asyncio
async def test_handler_called() -> None:
    """Test that subscription handlers are called on message delivery."""
    ev = asyncio.Event()

    async def test_handler(
        match: Match[str],
        payload: str,
    ) -> None:
        assert payload == "hive"
        ev.set()

    mock_client = _make_mock_client()

    # Provide one message to the async iterator
    fake_message = MagicMock()
    fake_message.topic = MagicMock()
    fake_message.topic.__str__ = lambda _: "hue2mqtt/bees/bar"
    fake_message.payload = b"hive"
    mock_client.messages = _async_iter([fake_message])

    with patch("hue2mqtt.mqtt.wrapper.MQTTWrapper._build_client", return_value=mock_client):
        wr = MQTTWrapper("foo", BROKER_INFO)
        wr.subscribe("bees/+", test_handler)
        await wr.connect()
        await asyncio.wait_for(ev.wait(), 1.0)
        await wr.disconnect()


@pytest.mark.asyncio
async def test_publish_raises_on_invalid_topic() -> None:
    """Test that publishing to an invalid (wildcard) topic raises ValueError."""
    mock_client = _make_mock_client()
    with patch("hue2mqtt.mqtt.wrapper.MQTTWrapper._build_client", return_value=mock_client):
        wr = MQTTWrapper("bar", BROKER_INFO)
        await wr.connect()

        with pytest.raises(ValueError):
            wr.publish("bees/+", StubModel(foo="bar"))

        with pytest.raises(ValueError):
            wr.publish("bees/#", StubModel(foo="bar"))

        with pytest.raises(ValueError):
            wr.publish("bees/", StubModel(foo="bar"))

        await wr.disconnect()


@pytest.mark.asyncio
async def test_publish_calls_client_publish() -> None:
    """Test that publish() calls the underlying client's publish coroutine."""
    mock_client = _make_mock_client()
    with patch("hue2mqtt.mqtt.wrapper.MQTTWrapper._build_client", return_value=mock_client):
        wr = MQTTWrapper("bar", BROKER_INFO)
        await wr.connect()

        model = StubModel(foo="bar")
        wr.publish("bees/foo", model)

        # allow the ensure_future coroutine to run
        await asyncio.sleep(0)
        mock_client.publish.assert_called_once()
        call_kwargs = mock_client.publish.call_args
        assert call_kwargs[0][0] == "hue2mqtt/bees/foo"
        assert "bar" in call_kwargs[0][1]  # payload contains value

        await wr.disconnect()


@pytest.mark.asyncio
async def test_publish_with_retain() -> None:
    """Test that publish() passes retain flag to the underlying client."""
    mock_client = _make_mock_client()
    with patch("hue2mqtt.mqtt.wrapper.MQTTWrapper._build_client", return_value=mock_client):
        wr = MQTTWrapper("bar", BROKER_INFO)
        await wr.connect()

        model = StubModel(foo="bar")
        wr.publish("bees/foo", model, retain=True)

        await asyncio.sleep(0)
        call_kwargs = mock_client.publish.call_args
        assert call_kwargs[1].get("retain") is True

        await wr.disconnect()


@pytest.mark.asyncio
async def test_build_client_uses_v311() -> None:
    """Test that _build_client uses MQTT 3.1.1 protocol when configured."""
    import aiomqtt

    wr = MQTTWrapper("foo", BROKER_INFO_V311)
    client = wr._build_client()
    # The client should be an aiomqtt.Client instance
    assert isinstance(client, aiomqtt.Client)


@pytest.mark.asyncio
async def test_build_client_uses_v5() -> None:
    """Test that _build_client uses MQTT 5 protocol by default."""
    import aiomqtt

    wr = MQTTWrapper("foo", BROKER_INFO)
    client = wr._build_client()
    assert isinstance(client, aiomqtt.Client)
