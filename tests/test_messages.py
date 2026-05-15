"""Tests for the MQTT message models."""

from hue2mqtt.messages import BridgeInfo, Hue2MQTTStatus


class TestBridgeInfo:
    """Tests for BridgeInfo model."""

    def test_valid(self) -> None:
        """Test creating a valid BridgeInfo."""
        info = BridgeInfo(
            name="My Bridge",
            mac_address="00:11:22:33:44:55",
            api_version="1.56.0",
        )
        assert info.name == "My Bridge"
        assert info.mac_address == "00:11:22:33:44:55"
        assert info.api_version == "1.56.0"

    def test_serialise(self) -> None:
        """Test JSON serialisation of BridgeInfo."""
        info = BridgeInfo(
            name="Bridge",
            mac_address="aa:bb:cc:dd:ee:ff",
            api_version="1.60.0",
        )
        json_str = info.model_dump_json()
        assert "Bridge" in json_str
        assert "aa:bb:cc:dd:ee:ff" in json_str


class TestHue2MQTTStatus:
    """Tests for Hue2MQTTStatus model."""

    def test_online_with_bridge(self) -> None:
        """Test creating an online status with bridge info."""
        bridge_info = BridgeInfo(
            name="My Bridge",
            mac_address="00:11:22:33:44:55",
            api_version="1.56.0",
        )
        status = Hue2MQTTStatus(online=True, bridge=bridge_info)
        assert status.online is True
        assert status.bridge is not None
        assert status.bridge.name == "My Bridge"

    def test_offline_no_bridge(self) -> None:
        """Test creating an offline status (no bridge info)."""
        status = Hue2MQTTStatus(online=False)
        assert status.online is False
        assert status.bridge is None

    def test_serialise_excludes_none(self) -> None:
        """Test that None fields are excluded from JSON output."""
        status = Hue2MQTTStatus(online=False)
        json_str = status.model_dump_json(exclude_none=True)
        assert "bridge" not in json_str
        assert '"online":false' in json_str

    def test_serialise_online_with_bridge(self) -> None:
        """Test JSON output includes bridge when present."""
        bridge_info = BridgeInfo(
            name="My Bridge",
            mac_address="00:11:22:33:44:55",
            api_version="1.56.0",
        )
        status = Hue2MQTTStatus(online=True, bridge=bridge_info)
        json_str = status.model_dump_json(exclude_none=True)
        assert "My Bridge" in json_str
        assert '"online":true' in json_str
