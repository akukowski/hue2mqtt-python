"""Tests for Hue2MQTT state read/write behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohue.v2 import EventType

import hue2mqtt.hue2mqtt as hue2mqtt_module
from hue2mqtt.hue2mqtt import Hue2MQTT
from hue2mqtt.messages import BridgeInfo, Hue2MQTTStatus


def _make_hue2mqtt() -> Hue2MQTT:
    """Create Hue2MQTT instance without running __init__ side-effects."""
    return Hue2MQTT.__new__(Hue2MQTT)


def _make_light(*, with_xy: bool = True) -> SimpleNamespace:
    """Create a light-like object for tests."""
    return SimpleNamespace(
        id="light-1",
        on=SimpleNamespace(on=True),
        dimming=SimpleNamespace(brightness=42.5),
        color_temperature=SimpleNamespace(mirek=301),
        color=SimpleNamespace(
            xy=SimpleNamespace(x=0.1234, y=0.5678) if with_xy else None,
        ),
        type=SimpleNamespace(value="light"),
    )


def _make_light_device() -> SimpleNamespace:
    """Create a light device-like object for tests."""
    return SimpleNamespace(
        metadata=SimpleNamespace(name="Desk Lamp"),
        product_data=SimpleNamespace(
            manufacturer_name="Signify",
            model_id="LCA001",
            product_name="Hue color lamp",
            software_version="1.90.1",
        ),
    )


def test_light_to_info_reads_xy_and_other_values() -> None:
    """_light_to_info should map on/bri/ct/xy values correctly."""
    hue2mqtt = _make_hue2mqtt()
    hue2mqtt._bridge = SimpleNamespace(
        lights=SimpleNamespace(get_device=MagicMock(return_value=_make_light_device())),
    )

    info = hue2mqtt._light_to_info(_make_light())

    assert info is not None
    assert info.state is not None
    assert info.state.on is True
    assert info.state.bri == 42.5
    assert info.state.ct == 301
    assert info.state.xy == (0.1234, 0.5678)


def test_light_to_info_handles_missing_xy_without_crash() -> None:
    """_light_to_info should handle missing xy payload gracefully."""
    hue2mqtt = _make_hue2mqtt()
    hue2mqtt._bridge = SimpleNamespace(
        lights=SimpleNamespace(get_device=MagicMock(return_value=_make_light_device())),
    )

    info = hue2mqtt._light_to_info(_make_light(with_xy=False))

    assert info is not None
    assert info.state is not None
    assert info.state.xy is None


def test_light_to_info_returns_none_when_device_missing() -> None:
    """_light_to_info should return None when device cannot be resolved."""
    hue2mqtt = _make_hue2mqtt()
    hue2mqtt._bridge = SimpleNamespace(
        lights=SimpleNamespace(get_device=MagicMock(return_value=None)),
    )

    assert hue2mqtt._light_to_info(_make_light()) is None


def test_group_to_info_maps_group_state_and_action() -> None:
    """_group_to_info should map group state fields correctly."""
    hue2mqtt = _make_hue2mqtt()
    grouped_light = SimpleNamespace(
        on=SimpleNamespace(on=True),
        dimming=SimpleNamespace(brightness=61.2),
    )
    group = SimpleNamespace(
        id="room-1",
        grouped_light="grouped-light-1",
        metadata=SimpleNamespace(
            name="Living Room",
            archetype=SimpleNamespace(value="living_room"),
        ),
        type=SimpleNamespace(value="room"),
    )
    hue2mqtt._bridge = SimpleNamespace(
        groups=SimpleNamespace(
            grouped_light=SimpleNamespace(get=MagicMock(return_value=grouped_light)),
            room=SimpleNamespace(get_lights=MagicMock(return_value=[SimpleNamespace(id="a")])),
            zone=SimpleNamespace(get_lights=MagicMock(return_value=[])),
        ),
    )

    info = hue2mqtt._group_to_info(group)

    assert info is not None
    assert info.state.all_on is True
    assert info.state.any_on is True
    assert info.action.on is True
    assert info.action.bri == 61.2
    assert info.group_class == "living_room"
    assert info.lights == ["a"]


@pytest.mark.asyncio
async def test_handle_set_light_sets_xy_and_other_values() -> None:
    """handle_set_light should pass on/bri/ct/xy into bridge state update."""
    hue2mqtt = _make_hue2mqtt()
    lights = SimpleNamespace(
        get=MagicMock(return_value=object()),
        set_state=AsyncMock(),
    )
    hue2mqtt._bridge = SimpleNamespace(lights=lights)
    match = MagicMock()
    match.group.return_value = "light-1"

    await hue2mqtt.handle_set_light(
        match,
        '{"on": true, "bri": 77.7, "ct": 222, "xy": [0.44, 0.55]}',
    )

    lights.set_state.assert_awaited_once_with(
        "light-1",
        on=True,
        brightness=77.7,
        color_xy=(0.44, 0.55),
        color_temp=222,
    )


@pytest.mark.asyncio
async def test_handle_set_light_invalid_payload_does_not_update() -> None:
    """handle_set_light should not update bridge state for invalid payload."""
    hue2mqtt = _make_hue2mqtt()
    lights = SimpleNamespace(
        get=MagicMock(return_value=object()),
        set_state=AsyncMock(),
    )
    hue2mqtt._bridge = SimpleNamespace(lights=lights)
    match = MagicMock()
    match.group.return_value = "light-1"

    await hue2mqtt.handle_set_light(match, "{")
    lights.set_state.assert_not_called()


@pytest.mark.asyncio
async def test_handle_set_light_non_dict_payload_does_not_update() -> None:
    """handle_set_light should ignore non-dict payloads."""
    hue2mqtt = _make_hue2mqtt()
    lights = SimpleNamespace(
        get=MagicMock(return_value=object()),
        set_state=AsyncMock(),
    )
    hue2mqtt._bridge = SimpleNamespace(lights=lights)
    match = MagicMock()
    match.group.return_value = "light-1"

    await hue2mqtt.handle_set_light(match, "null")
    lights.set_state.assert_not_called()


@pytest.mark.asyncio
async def test_handle_set_group_sets_xy_and_other_values() -> None:
    """handle_set_group should pass on/bri/ct/xy into grouped_light update."""
    hue2mqtt = _make_hue2mqtt()
    group = SimpleNamespace(
        grouped_light="grouped-light-1",
        metadata=SimpleNamespace(name="Living Room"),
    )
    grouped_light = SimpleNamespace(set_state=AsyncMock())
    groups = SimpleNamespace(
        room=SimpleNamespace(get=MagicMock(return_value=group)),
        zone=SimpleNamespace(get=MagicMock(return_value=None)),
        grouped_light=grouped_light,
    )
    hue2mqtt._bridge = SimpleNamespace(groups=groups)
    match = MagicMock()
    match.group.return_value = "group-1"

    await hue2mqtt.handle_set_group(
        match,
        '{"on": false, "bri": 12.3, "ct": 330, "xy": [0.1, 0.2]}',
    )

    grouped_light.set_state.assert_awaited_once_with(
        "grouped-light-1",
        on=False,
        brightness=12.3,
        color_xy=(0.1, 0.2),
        color_temp=330,
    )


@pytest.mark.asyncio
async def test_handle_set_group_without_grouped_light_does_not_update() -> None:
    """handle_set_group should skip updates if grouped_light is missing."""
    hue2mqtt = _make_hue2mqtt()
    group = SimpleNamespace(
        grouped_light=None,
        metadata=SimpleNamespace(name="Living Room"),
    )
    grouped_light = SimpleNamespace(set_state=AsyncMock())
    groups = SimpleNamespace(
        room=SimpleNamespace(get=MagicMock(return_value=group)),
        zone=SimpleNamespace(get=MagicMock(return_value=None)),
        grouped_light=grouped_light,
    )
    hue2mqtt._bridge = SimpleNamespace(groups=groups)
    match = MagicMock()
    match.group.return_value = "group-1"

    await hue2mqtt.handle_set_group(match, '{"on": true}')

    grouped_light.set_state.assert_not_called()


@pytest.mark.asyncio
async def test_handle_set_group_unknown_group_does_not_update() -> None:
    """handle_set_group should skip updates for unknown group ids."""
    hue2mqtt = _make_hue2mqtt()
    grouped_light = SimpleNamespace(set_state=AsyncMock())
    groups = SimpleNamespace(
        room=SimpleNamespace(get=MagicMock(return_value=None)),
        zone=SimpleNamespace(get=MagicMock(return_value=None)),
        grouped_light=grouped_light,
    )
    hue2mqtt._bridge = SimpleNamespace(groups=groups)
    match = MagicMock()
    match.group.return_value = "group-1"

    await hue2mqtt.handle_set_group(match, '{"on": true}')
    grouped_light.set_state.assert_not_called()


@pytest.mark.asyncio
async def test_publish_bridge_status_online() -> None:
    """_publish_bridge_status should publish online payload with bridge info."""
    hue2mqtt = _make_hue2mqtt()
    hue2mqtt._bridge = SimpleNamespace(
        config=SimpleNamespace(
            name="Bridge A",
            mac_address="00:11:22:33:44:55",
            software_version="1.2.3",
        ),
    )
    mqtt_publish = MagicMock()
    hue2mqtt._mqtt = SimpleNamespace(publish=mqtt_publish)  # type: ignore[assignment]

    await hue2mqtt._publish_bridge_status()
    first_call = mqtt_publish.call_args_list[0]

    assert first_call.args[0] == "status"
    assert first_call.args[1] == Hue2MQTTStatus(
        online=True,
        bridge=BridgeInfo(
            name="Bridge A",
            mac_address="00:11:22:33:44:55",
            api_version="1.2.3",
        ),
    )


@pytest.mark.asyncio
async def test_publish_bridge_status_offline() -> None:
    """_publish_bridge_status should publish offline payload without bridge info."""
    hue2mqtt = _make_hue2mqtt()
    hue2mqtt._mqtt = SimpleNamespace(publish=MagicMock())  # type: ignore[assignment]

    await hue2mqtt._publish_bridge_status(online=False)

    call = hue2mqtt._mqtt.publish.call_args_list[0]
    assert call.args[0] == "status"
    assert call.args[1] == Hue2MQTTStatus(online=False)


@pytest.mark.asyncio
async def test_event_handlers_publish_for_add_or_update_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Event handlers should publish converted resources for add/update events."""
    hue2mqtt = _make_hue2mqtt()
    light_info = object()
    group_info = object()
    sensor_info = object()
    hue2mqtt._light_to_info = MagicMock(return_value=light_info)  # type: ignore[method-assign]
    hue2mqtt._group_to_info = MagicMock(return_value=group_info)  # type: ignore[method-assign]
    hue2mqtt._sensor_to_info = MagicMock(return_value=sensor_info)  # type: ignore[method-assign]
    hue2mqtt.publish_light = MagicMock()  # type: ignore[method-assign]
    hue2mqtt.publish_group = MagicMock()  # type: ignore[method-assign]
    hue2mqtt.publish_sensor = MagicMock()  # type: ignore[method-assign]
    monkeypatch.setattr(hue2mqtt_module, "_SENSOR_TYPES", (object,))

    await hue2mqtt._on_light_event(EventType.RESOURCE_UPDATED, object())
    await hue2mqtt._on_group_event(EventType.RESOURCE_ADDED, object())
    await hue2mqtt._on_sensor_event(EventType.RESOURCE_UPDATED, object())

    hue2mqtt.publish_light.assert_called_once_with(light_info)
    hue2mqtt.publish_group.assert_called_once_with(group_info)
    hue2mqtt.publish_sensor.assert_called_once_with(sensor_info)


@pytest.mark.asyncio
async def test_grouped_light_event_republishes_all_groups() -> None:
    """_on_grouped_light_event should republish all room and zone states."""
    hue2mqtt = _make_hue2mqtt()
    room = SimpleNamespace(id="room-1")
    zone = SimpleNamespace(id="zone-1")
    hue2mqtt._bridge = SimpleNamespace(
        groups=SimpleNamespace(
            room=[room],
            zone=[zone],
        ),
    )
    hue2mqtt._group_to_info = MagicMock(side_effect=[object(), object()])  # type: ignore[method-assign]
    hue2mqtt.publish_group = MagicMock()  # type: ignore[method-assign]

    await hue2mqtt._on_grouped_light_event(EventType.RESOURCE_UPDATED, object())

    assert hue2mqtt.publish_group.call_count == 2


def test_publish_all_publishes_each_resource_type() -> None:
    """_publish_all should publish lights, groups and sensors from all controllers."""
    hue2mqtt = _make_hue2mqtt()
    light = SimpleNamespace(id="light-1")
    room = SimpleNamespace(id="room-1")
    zone = SimpleNamespace(id="zone-1")
    motion = SimpleNamespace(id="motion-1")
    hue2mqtt._bridge = SimpleNamespace(
        lights=[light],
        groups=SimpleNamespace(room=[room], zone=[zone]),
        sensors=SimpleNamespace(
            motion=[motion],
            temperature=[],
            light_level=[],
            button=[],
            relative_rotary=[],
            contact=[],
        ),
    )
    hue2mqtt._light_to_info = MagicMock(return_value="light-info")  # type: ignore[method-assign]
    hue2mqtt._group_to_info = MagicMock(side_effect=["room-info", "zone-info"])  # type: ignore[method-assign]
    hue2mqtt._sensor_to_info = MagicMock(return_value="sensor-info")  # type: ignore[method-assign]
    hue2mqtt.publish_light = MagicMock()  # type: ignore[method-assign]
    hue2mqtt.publish_group = MagicMock()  # type: ignore[method-assign]
    hue2mqtt.publish_sensor = MagicMock()  # type: ignore[method-assign]

    hue2mqtt._publish_all()

    hue2mqtt.publish_light.assert_called_once_with("light-info")
    assert hue2mqtt.publish_group.call_count == 2
    hue2mqtt.publish_sensor.assert_called_once_with("sensor-info")
