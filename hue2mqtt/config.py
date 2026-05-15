"""
Configuration schema for Astoria.

Common to all components.
"""
import tomllib
from pathlib import Path
from typing import IO, Optional

from pydantic import BaseModel, TypeAdapter
from pydantic.config import ConfigDict


class HueBridgeInfo(BaseModel):
    """MQTT Broker Information."""

    model_config = ConfigDict(extra="forbid")

    ip: str
    username: str


class MQTTBrokerInfo(BaseModel):
    """MQTT Broker Information."""

    model_config = ConfigDict(extra="forbid")

    host: str
    port: int
    enable_auth: bool = False
    username: str = ""
    password: str = ""
    enable_tls: bool = False
    topic_prefix: str = "hue2mqtt"
    force_protocol_version_3_1: bool = False


class Hue2MQTTConfig(BaseModel):
    """Config schema for Hue2MQTT."""

    model_config = ConfigDict(extra="forbid")

    mqtt: MQTTBrokerInfo
    hue: HueBridgeInfo

    @classmethod
    def _get_config_path(cls, config_str: Optional[str] = None) -> Path:
        """Check for a config file or search the filesystem for one."""
        config_search_paths = [
            Path("hue2mqtt.toml"),
            Path("/etc/hue2mqtt.toml"),
        ]
        if config_str is None:
            for path in config_search_paths:
                if path.exists() and path.is_file():
                    return path
        else:
            path = Path(config_str)
            if path.exists() and path.is_file():
                return path
        raise FileNotFoundError("Unable to find config file.")

    @classmethod
    def load(cls, config_str: Optional[str] = None) -> "Hue2MQTTConfig":
        """Load the config."""
        config_path = cls._get_config_path(config_str)
        with config_path.open("rb") as fh:
            return cls.load_from_file(fh)

    @classmethod
    def load_from_file(cls, fh: IO[bytes]) -> "Hue2MQTTConfig":
        """Load the config from a file."""
        adapter: TypeAdapter[Hue2MQTTConfig] = TypeAdapter(cls)
        return adapter.validate_python(tomllib.load(fh))
