"""
Data Component base class.

A data component represents the common functionality between
State Managers and Consumers. It handles connecting to the broker
and managing the event loop.
"""
import asyncio
import json
import logging
import signal
import sys
from signal import SIGHUP, SIGINT, SIGTERM
from types import FrameType
from typing import Match, Optional

import aiohue
from aiohue.v1.groups import Group
from aiohue.v1.lights import Light
from aiohue.v1.sensors import GenericSensor
from aiohttp.client import ClientSession
from pydantic import TypeAdapter, ValidationError

from hue2mqtt import __version__
from hue2mqtt.messages import BridgeInfo, Hue2MQTTStatus
from hue2mqtt.schema import (
    GroupInfo,
    GroupSetState,
    LightInfo,
    LightSetState,
    SensorInfo,
)

from .config import Hue2MQTTConfig
from .mqtt.wrapper import MQTTWrapper

LOGGER = logging.getLogger(__name__)

# Polling interval in seconds for reading bridge state updates
POLL_INTERVAL = 5

loop = asyncio.get_event_loop()


class Hue2MQTT:
    """Hue to MQTT Bridge."""

    config: Hue2MQTTConfig

    def __init__(
        self,
        verbose: bool,
        config_file: Optional[str],
        *,
        name: str = "hue2mqtt",
    ) -> None:
        self.config = Hue2MQTTConfig.load(config_file)
        self.name = name

        self._setup_logging(verbose)
        self._setup_event_loop()
        self._setup_mqtt()

    def _setup_logging(self, verbose: bool, *, welcome_message: bool = True) -> None:
        if verbose:
            logging.basicConfig(
                level=logging.DEBUG,
                format=f"%(asctime)s {self.name} %(name)s %(levelname)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        else:
            logging.basicConfig(
                level=logging.INFO,
                format=f"%(asctime)s {self.name} %(levelname)s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

            # Suppress INFO messages from aiomqtt
            logging.getLogger("aiomqtt").setLevel(logging.WARNING)

        if welcome_message:
            LOGGER.info(f"Hue2MQTT v{__version__} - {self.__doc__}")

    def _setup_event_loop(self) -> None:
        loop.add_signal_handler(SIGHUP, self.halt)
        loop.add_signal_handler(SIGINT, self.halt)
        loop.add_signal_handler(SIGTERM, self.halt)

    def _setup_mqtt(self) -> None:
        self._mqtt = MQTTWrapper(
            self.name,
            self.config.mqtt,
            last_will=Hue2MQTTStatus(online=False),
        )

        self._mqtt.subscribe("light/+/set", self.handle_set_light)
        self._mqtt.subscribe("group/+/set", self.handle_set_group)

    def _exit(self, signals: signal.Signals, frame_type: FrameType) -> None:
        sys.exit(0)

    async def run(self) -> None:
        """Entrypoint for the data component."""
        await self._mqtt.connect()
        LOGGER.info("Connected to MQTT Broker")

        async with ClientSession() as websession:
            try:
                await self._setup_bridge(websession)
            except aiohue.Unauthorized:
                LOGGER.error("Bridge rejected username. Please use --discover")
                self.halt()
                return
            await self._publish_bridge_status()
            await self.main()

        LOGGER.info("Disconnecting from MQTT Broker")
        await self._publish_bridge_status(online=False)
        await self._mqtt.disconnect()

    def halt(self) -> None:
        """Stop the component."""
        sys.exit(-1)

    async def _setup_bridge(self, websession: ClientSession) -> None:
        """Connect to the Hue Bridge."""
        self._bridge = aiohue.HueBridgeV1(
            self.config.hue.ip,
            app_key=self.config.hue.username,
            websession=websession,
        )
        LOGGER.info(f"Connecting to Hue Bridge at {self.config.hue.ip}")
        await self._bridge.initialize()

    async def _publish_bridge_status(self, *, online: bool = True) -> None:
        """Publish info about the Hue Bridge."""
        if online:
            LOGGER.info(f"Bridge Name: {self._bridge.config.name}")
            LOGGER.info(f"Bridge MAC: {self._bridge.config.mac}")
            LOGGER.info(f"API Version: {self._bridge.config.apiversion}")

            info = BridgeInfo(
                name=self._bridge.config.name,
                mac_address=self._bridge.config.mac,
                api_version=self._bridge.config.apiversion,
            )
            message = Hue2MQTTStatus(online=online, bridge=info)
        else:
            message = Hue2MQTTStatus(online=online)

        self._mqtt.publish("status", message)

    def publish_light(self, light: LightInfo) -> None:
        """Publish information about a light to MQTT."""
        self._mqtt.publish(f"light/{light.uniqueid}", light, retain=True)

    def publish_group(self, group: GroupInfo) -> None:
        """Publish information about a group to MQTT."""
        self._mqtt.publish(f"group/{group.id}", group, retain=True)

    def publish_sensor(self, sensor: SensorInfo) -> None:
        """Publish information about a sensor to MQTT."""
        self._mqtt.publish(f"sensor/{sensor.uniqueid}", sensor, retain=True)

    async def handle_set_light(self, match: Match[str], payload: str) -> None:
        """Handle an update to a light."""
        uniqueid = match.group(1)

        # Find the light with that uniqueid
        for light_id in self._bridge.lights:
            light = self._bridge.lights[light_id]
            if light.uniqueid == uniqueid:
                try:
                    adapter: TypeAdapter[LightSetState] = TypeAdapter(LightSetState)
                    state = adapter.validate_python(json.loads(payload))
                    LOGGER.info(f"Updating {light.name}")
                    await light.set_state(**state.model_dump(exclude_none=True))
                except json.JSONDecodeError:
                    LOGGER.warning(f"Bad JSON on light request: {payload}")
                except TypeError:
                    LOGGER.warning(f"Expected dictionary, got: {payload}")
                except ValidationError as e:
                    LOGGER.warning(f"Invalid light state: {e}")
                return
        LOGGER.warning(f"Unknown light uniqueid: {uniqueid}")

    async def handle_set_group(self, match: Match[str], payload: str) -> None:
        """Handle an update to a group."""
        groupid = match.group(1)

        try:
            group = self._bridge.groups[groupid]
            adapter: TypeAdapter[GroupSetState] = TypeAdapter(GroupSetState)
            state = adapter.validate_python(json.loads(payload))
            LOGGER.info(f"Updating group {group.name}")
            await group.set_action(**state.model_dump(exclude_none=True))
        except IndexError:
            LOGGER.warning(f"Unknown group id: {groupid}")
        except json.JSONDecodeError:
            LOGGER.warning(f"Bad JSON on light request: {payload}")
        except TypeError:
            LOGGER.warning(f"Expected dictionary, got: {payload}")
        except ValidationError as e:
            LOGGER.warning(f"Invalid light state: {e}")

    async def main(self) -> None:
        """Main polling loop: publish initial state and then poll for updates."""
        # Publish initial state for all resources
        self._publish_all()

        # Track raw state for change detection
        prev_lights = {k: dict(v.raw) for k, v in self._bridge.lights._items.items()}
        prev_groups = {k: dict(v.raw) for k, v in self._bridge.groups._items.items()}
        prev_sensors = {
            k: dict(v.raw)
            for k, v in self._bridge.sensors._items.items()
            if "uniqueid" in v.raw and "productname" in v.raw
        }

        while True:
            await asyncio.sleep(POLL_INTERVAL)

            try:
                await self._bridge.lights.update()
                await self._bridge.groups.update()
                await self._bridge.sensors.update()
            except Exception as exc:
                LOGGER.warning(f"Failed to update bridge resources: {exc}")
                continue

            # Publish lights that changed
            for idx, light_raw in self._bridge.lights._items.items():
                raw = dict(light_raw.raw)
                if raw != prev_lights.get(idx):
                    light = LightInfo(id=int(idx), **light_raw.raw)
                    self.publish_light(light)
                    prev_lights[idx] = raw

            # Publish groups that changed
            for idx, group_raw in self._bridge.groups._items.items():
                raw = dict(group_raw.raw)
                if raw != prev_groups.get(idx):
                    group = GroupInfo(id=int(idx), **group_raw.raw)
                    self.publish_group(group)
                    prev_groups[idx] = raw

            # Publish sensors that changed
            for idx, sensor_raw in self._bridge.sensors._items.items():
                if "uniqueid" not in sensor_raw.raw or "productname" not in sensor_raw.raw:
                    LOGGER.debug(f"Ignoring virtual sensor: {sensor_raw.name}")
                    continue
                raw = dict(sensor_raw.raw)
                if raw != prev_sensors.get(idx):
                    sensor = SensorInfo(id=int(idx), **sensor_raw.raw)
                    self.publish_sensor(sensor)
                    prev_sensors[idx] = raw

    def _publish_all(self) -> None:
        """Publish initial info about all bridge resources."""
        for idx, light_raw in self._bridge.lights._items.items():
            light = LightInfo(id=int(idx), **light_raw.raw)
            self.publish_light(light)

        for idx, group_raw in self._bridge.groups._items.items():
            group = GroupInfo(id=int(idx), **group_raw.raw)
            self.publish_group(group)

        for idx, sensor_raw in self._bridge.sensors._items.items():
            if "uniqueid" in sensor_raw.raw and "productname" in sensor_raw.raw:
                sensor = SensorInfo(id=int(idx), **sensor_raw.raw)
                self.publish_sensor(sensor)
            else:
                LOGGER.debug(f"Ignoring virtual sensor: {sensor_raw.name}")
