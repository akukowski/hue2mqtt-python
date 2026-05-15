"""Tests for the pydantic schema models."""
import pytest
from pydantic import ValidationError

from hue2mqtt.schema import (
    GroupInfo,
    GroupSetState,
    GroupState,
    LightInfo,
    LightSetState,
    LightState,
    SensorInfo,
)


class TestLightState:
    """Tests for LightState model."""

    def test_empty_state(self) -> None:
        """Test creating an empty (all-None) LightState."""
        state = LightState()
        assert state.on is None
        assert state.bri is None
        assert state.reachable is None

    def test_full_state(self) -> None:
        """Test creating a fully populated LightState."""
        state = LightState(on=True, bri=100.0, ct=300, hue=100, sat=150)
        assert state.on is True
        assert state.bri == 100.0
        assert state.ct == 300

    def test_serialise_excludes_none(self) -> None:
        """Test that None values are excluded from JSON serialisation."""
        state = LightState(on=True)
        data = state.model_dump_json(exclude_none=True)
        assert '"on":true' in data
        assert "bri" not in data


class TestLightSetState:
    """Tests for LightSetState model."""

    def test_minimal(self) -> None:
        """Test creating a minimal LightSetState."""
        state = LightSetState(on=True)
        assert state.on is True
        assert state.bri_inc is None

    def test_with_increments(self) -> None:
        """Test creating LightSetState with increment fields."""
        state = LightSetState(bri_inc=50, ct_inc=-20)
        assert state.bri_inc == 50
        assert state.ct_inc == -20

    def test_model_dump_exclude_none(self) -> None:
        """Test that model_dump(exclude_none=True) omits None values."""
        state = LightSetState(on=False, bri=100.0)
        dumped = state.model_dump(exclude_none=True)
        assert dumped == {"on": False, "bri": 100.0}


class TestGroupSetState:
    """Tests for GroupSetState model."""

    def test_with_scene(self) -> None:
        """Test creating a GroupSetState with a scene."""
        state = GroupSetState(on=True, scene="my-scene")
        assert state.scene == "my-scene"

    def test_without_scene(self) -> None:
        """Test that scene defaults to None."""
        state = GroupSetState(on=True)
        assert state.scene is None


class TestLightInfo:
    """Tests for LightInfo model."""

    def _make_light_data(self) -> dict:
        return {
            "id": "abc-123-def-456",
            "name": "Bedroom Lamp",
            "manufacturername": "Philips",
            "modelid": "LCA001",
            "productname": "Hue color lamp",
            "type": "light",
            "swversion": "1.90.1",
            "state": {"on": True, "bri": 100.0, "reachable": True},
        }

    def test_valid_light_info(self) -> None:
        """Test that a valid LightInfo can be constructed."""
        data = self._make_light_data()
        light = LightInfo(**data)
        assert light.name == "Bedroom Lamp"
        assert light.id == "abc-123-def-456"
        assert light.state is not None
        assert light.state.on is True

    def test_light_info_without_state(self) -> None:
        """Test that state is optional."""
        data = self._make_light_data()
        del data["state"]
        light = LightInfo(**data)
        assert light.state is None

    def test_serialise_by_alias(self) -> None:
        """Test JSON serialisation round-trip."""
        data = self._make_light_data()
        light = LightInfo(**data)
        json_str = light.model_dump_json(by_alias=True, exclude_none=True)
        assert "Bedroom Lamp" in json_str


class TestGroupInfo:
    """Tests for GroupInfo model."""

    def _make_group_data(self) -> dict:
        return {
            "id": "group-uuid-001",
            "name": "Living room",
            "lights": ["light-uuid-1", "light-uuid-2", "light-uuid-3"],
            "sensors": [],
            "type": "room",
            "state": {"all_on": False, "any_on": True},
            "class": "living_room",
            "action": {"on": True, "bri": 78.7},
        }

    def test_valid_group_info(self) -> None:
        """Test that a valid GroupInfo can be constructed."""
        data = self._make_group_data()
        group = GroupInfo(**data)
        assert group.name == "Living room"
        assert group.lights == ["light-uuid-1", "light-uuid-2", "light-uuid-3"]
        assert group.state.any_on is True

    def test_group_class_alias(self) -> None:
        """Test that group_class is populated from the 'class' alias."""
        data = self._make_group_data()
        group = GroupInfo(**data)
        assert group.group_class == "living_room"

    def test_group_class_serialise(self) -> None:
        """Test serialisation uses 'class' alias."""
        data = self._make_group_data()
        group = GroupInfo(**data)
        json_str = group.model_dump_json(by_alias=True, exclude_none=True)
        assert '"class"' in json_str


class TestGroupState:
    """Tests for GroupState model."""

    def test_all_on(self) -> None:
        """Test GroupState with all lights on."""
        state = GroupState(all_on=True, any_on=True)
        assert state.all_on is True
        assert state.any_on is True

    def test_none_on(self) -> None:
        """Test GroupState with all lights off."""
        state = GroupState(all_on=False, any_on=False)
        assert state.all_on is False
        assert state.any_on is False


class TestSensorInfo:
    """Tests for SensorInfo model."""

    def _make_sensor_data(self) -> dict:
        return {
            "id": "sensor-uuid-001",
            "name": "Hue motion sensor 1",
            "type": "motion",
            "modelid": "SML001",
            "manufacturername": "Signify Netherlands B.V.",
            "productname": "Hue Motion",
            "swversion": "2.0",
            "state": {"presence": True},
            "capabilities": {"certified": True},
        }

    def test_valid_sensor_info(self) -> None:
        """Test that a valid SensorInfo can be constructed."""
        data = self._make_sensor_data()
        sensor = SensorInfo(**data)
        assert sensor.name == "Hue motion sensor 1"
        assert sensor.id == "sensor-uuid-001"

    def test_sensor_state_presence(self) -> None:
        """Test that presence state is read correctly."""
        data = self._make_sensor_data()
        sensor = SensorInfo(**data)
        assert sensor.state.presence is True  # type: ignore[attr-defined]

    def test_sensor_without_swversion(self) -> None:
        """Test that swversion is optional."""
        data = self._make_sensor_data()
        del data["swversion"]
        sensor = SensorInfo(**data)
        assert sensor.swversion is None
