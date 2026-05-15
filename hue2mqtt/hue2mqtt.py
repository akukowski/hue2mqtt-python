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
from typing import Match, Optional, Union

import aiohue
from aiohue.v2 import EventType
from aiohue.v2.models.button import Button
from aiohue.v2.models.contact import Contact
from aiohue.v2.models.light import Light
from aiohue.v2.models.light_level import LightLevel
from aiohue.v2.models.motion import Motion
from aiohue.v2.models.relative_rotary import RelativeRotary
from aiohue.v2.models.room import Room
from aiohue.v2.models.temperature import Temperature
from aiohue.v2.models.zone import Zone
from pydantic import TypeAdapter, ValidationError

from hue2mqtt import __version__
from hue2mqtt.messages import BridgeInfo, Hue2MQTTStatus
from hue2mqtt.schema import (
    GroupInfo,
    GroupSetState,
    GroupState,
    LightInfo,
    LightSetState,
    LightState,
    SensorInfo,
    SensorState,
)

from .config import Hue2MQTTConfig
from .mqtt.wrapper import MQTTWrapper

LOGGER = logging.getLogger(__name__)

loop = asyncio.get_event_loop()

# Physical sensor types we want to publish
_SENSOR_TYPES = (Motion, Temperature, LightLevel, Button, RelativeRotary, Contact)
SensorResource = Union[Motion, Temperature, LightLevel, Button, RelativeRotary, Contact]


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

        try:
            await self._setup_bridge()
        except aiohue.Unauthorized:
            LOGGER.error("Bridge rejected username. Please use --discover")
            self.halt()
            return
        await self._publish_bridge_status()
        await self.main()

        LOGGER.info("Disconnecting from MQTT Broker")
        await self._publish_bridge_status(online=False)
        await self._bridge.close()
        await self._mqtt.disconnect()

    def halt(self) -> None:
        """Stop the component."""
        sys.exit(-1)

    async def _setup_bridge(self) -> None:
        """Connect to the Hue Bridge."""
        self._bridge = aiohue.HueBridgeV2(
            self.config.hue.ip,
            app_key=self.config.hue.username,
        )
        LOGGER.info(f"Connecting to Hue Bridge at {self.config.hue.ip}")
        await self._bridge.initialize()

    async def _publish_bridge_status(self, *, online: bool = True) -> None:
        """Publish info about the Hue Bridge."""
        if online:
            LOGGER.info(f"Bridge Name: {self._bridge.config.name}")
            LOGGER.info(
                f"Bridge Software Version: {self._bridge.config.software_version}"
            )

            info = BridgeInfo(
                name=self._bridge.config.name,
                mac_address=self._bridge.config.mac_address,
                api_version=self._bridge.config.software_version,
            )
            message = Hue2MQTTStatus(online=online, bridge=info)
        else:
            message = Hue2MQTTStatus(online=online)

        self._mqtt.publish("status", message)

    def publish_light(self, light: LightInfo) -> None:
        """Publish information about a light to MQTT."""
        self._mqtt.publish(f"light/{light.id}", light, retain=True)

    def publish_group(self, group: GroupInfo) -> None:
        """Publish information about a group to MQTT."""
        self._mqtt.publish(f"group/{group.id}", group, retain=True)

    def publish_sensor(self, sensor: SensorInfo) -> None:
        """Publish information about a sensor to MQTT."""
        self._mqtt.publish(f"sensor/{sensor.id}", sensor, retain=True)

    def _light_to_info(self, light: Light) -> Optional[LightInfo]:
        """Convert a V2 Light resource to a LightInfo schema object."""
        device = self._bridge.lights.get_device(light.id)
        if device is None:
            LOGGER.debug(f"No device found for light {light.id}, skipping")
            return None

        state = LightState(
            on=light.on.on if light.on is not None else None,
            bri=light.dimming.brightness if light.dimming is not None else None,
            ct=(
                light.color_temperature.mirek
                if light.color_temperature is not None
                else None
            ),
            xy=(
                (light.color.xy.x, light.color.xy.y)
                if light.color is not None
                else None
            ),
        )

        return LightInfo(
            id=light.id,
            name=device.metadata.name,
            state=state,
            manufacturername=device.product_data.manufacturer_name,
            modelid=device.product_data.model_id,
            productname=device.product_data.product_name,
            type=light.type.value,
            swversion=device.product_data.software_version,
        )

    def _group_to_info(self, group: Union[Room, Zone]) -> Optional[GroupInfo]:
        """Convert a V2 Room or Zone resource to a GroupInfo schema object."""
        grouped_light_id = group.grouped_light
        grouped_light = (
            self._bridge.groups.grouped_light.get(grouped_light_id)
            if grouped_light_id
            else None
        )

        any_on = bool(
            grouped_light is not None
            and grouped_light.on is not None
            and grouped_light.on.on
        )

        action = LightState(
            on=any_on,
            bri=(
                grouped_light.dimming.brightness
                if grouped_light is not None and grouped_light.dimming is not None
                else None
            ),
        )

        if group.type.value == "room":
            lights = self._bridge.groups.room.get_lights(group.id)
        else:
            lights = self._bridge.groups.zone.get_lights(group.id)

        light_ids = [light.id for light in lights]

        return GroupInfo(
            id=group.id,
            name=group.metadata.name,
            lights=light_ids,
            sensors=[],
            type=group.type.value,
            state=GroupState(all_on=any_on, any_on=any_on),
            group_class=(
                group.metadata.archetype.value
                if group.metadata.archetype is not None
                else None
            ),
            action=action,
        )

    def _sensor_to_info(self, sensor: SensorResource) -> Optional[SensorInfo]:
        """Convert a V2 sensor resource to a SensorInfo schema object."""
        device = self._bridge.sensors.get_device(sensor.id)
        if device is None:
            LOGGER.debug(f"No device found for sensor {sensor.id}, skipping")
            return None

        state_kwargs: dict = {}

        if isinstance(sensor, Motion):
            state_kwargs["presence"] = sensor.motion.motion
        elif isinstance(sensor, Temperature):
            state_kwargs["temperature"] = sensor.temperature.temperature
        elif isinstance(sensor, LightLevel):
            state_kwargs["lightlevel"] = sensor.light.light_level
            state_kwargs["dark"] = (
                sensor.light.light_level_valid and sensor.light.light_level < 10000
            )
        elif isinstance(sensor, Button):
            state_kwargs["buttonevent"] = (
                sensor.button.last_event.value
                if sensor.button is not None and sensor.button.last_event is not None
                else None
            )
        elif isinstance(sensor, RelativeRotary):
            state_kwargs["rotaryevent"] = (
                sensor.relative_rotary.last_event.action.value
                if sensor.relative_rotary is not None
                and sensor.relative_rotary.last_event is not None
                else None
            )

        sensor_state = SensorState(**state_kwargs)  # type: ignore[call-arg]

        return SensorInfo(
            id=sensor.id,
            name=device.metadata.name,
            type=sensor.type.value,
            modelid=device.product_data.model_id,
            manufacturername=device.product_data.manufacturer_name,
            productname=device.product_data.product_name,
            swversion=device.product_data.software_version,
            state=sensor_state,
        )

    async def handle_set_light(self, match: Match[str], payload: str) -> None:
        """Handle an update to a light."""
        light_id = match.group(1)

        light = self._bridge.lights.get(light_id)
        if light is None:
            LOGGER.warning(f"Unknown light id: {light_id}")
            return

        try:
            adapter: TypeAdapter[LightSetState] = TypeAdapter(LightSetState)
            state = adapter.validate_python(json.loads(payload))
            LOGGER.info(f"Updating light {light_id}")

            await self._bridge.lights.set_state(
                light_id,
                on=state.on,
                brightness=state.bri,
                color_xy=state.xy,
                color_temp=state.ct,
            )
        except json.JSONDecodeError:
            LOGGER.warning(f"Bad JSON on light request: {payload}")
        except TypeError:
            LOGGER.warning(f"Expected dictionary, got: {payload}")
        except ValidationError as e:
            LOGGER.warning(f"Invalid light state: {e}")

    async def handle_set_group(self, match: Match[str], payload: str) -> None:
        """Handle an update to a group."""
        group_id = match.group(1)

        room = self._bridge.groups.room.get(group_id)
        zone = self._bridge.groups.zone.get(group_id)
        group = room or zone

        if group is None:
            LOGGER.warning(f"Unknown group id: {group_id}")
            return

        grouped_light_id = group.grouped_light
        if grouped_light_id is None:
            LOGGER.warning(f"Group {group_id} has no grouped_light")
            return

        try:
            adapter: TypeAdapter[GroupSetState] = TypeAdapter(GroupSetState)
            state = adapter.validate_python(json.loads(payload))
            LOGGER.info(f"Updating group {group.metadata.name}")

            await self._bridge.groups.grouped_light.set_state(
                grouped_light_id,
                on=state.on,
                brightness=state.bri,
                color_xy=state.xy,
                color_temp=state.ct,
            )
        except IndexError:
            LOGGER.warning(f"Unknown group id: {group_id}")
        except json.JSONDecodeError:
            LOGGER.warning(f"Bad JSON on light request: {payload}")
        except TypeError:
            LOGGER.warning(f"Expected dictionary, got: {payload}")
        except ValidationError as e:
            LOGGER.warning(f"Invalid light state: {e}")

    async def _on_light_event(
        self,
        event_type: EventType,
        light: Light,
    ) -> None:
        """Handle a light event from the bridge."""
        if event_type in (EventType.RESOURCE_ADDED, EventType.RESOURCE_UPDATED):
            info = self._light_to_info(light)
            if info is not None:
                self.publish_light(info)

    async def _on_group_event(
        self,
        event_type: EventType,
        group: Union[Room, Zone],
    ) -> None:
        """Handle a room/zone event from the bridge."""
        if event_type in (EventType.RESOURCE_ADDED, EventType.RESOURCE_UPDATED):
            info = self._group_to_info(group)
            if info is not None:
                self.publish_group(info)

    async def _on_grouped_light_event(
        self,
        event_type: EventType,
        _item: object,
    ) -> None:
        """Handle a grouped_light event by republishing all groups."""
        if event_type == EventType.RESOURCE_UPDATED:
            for room in self._bridge.groups.room:
                info = self._group_to_info(room)
                if info is not None:
                    self.publish_group(info)
            for zone in self._bridge.groups.zone:
                info = self._group_to_info(zone)
                if info is not None:
                    self.publish_group(info)

    async def _on_sensor_event(
        self,
        event_type: EventType,
        sensor: SensorResource,
    ) -> None:
        """Handle a sensor event from the bridge."""
        if event_type in (EventType.RESOURCE_ADDED, EventType.RESOURCE_UPDATED):
            if not isinstance(sensor, _SENSOR_TYPES):
                return
            info = self._sensor_to_info(sensor)
            if info is not None:
                self.publish_sensor(info)

    async def main(self) -> None:
        """Main loop: publish initial state and subscribe to bridge events."""
        self._publish_all()

        # Subscribe to resource change events
        self._bridge.lights.subscribe(self._on_light_event)
        self._bridge.groups.room.subscribe(self._on_group_event)
        self._bridge.groups.zone.subscribe(self._on_group_event)
        self._bridge.groups.grouped_light.subscribe(self._on_grouped_light_event)
        self._bridge.sensors.motion.subscribe(self._on_sensor_event)
        self._bridge.sensors.temperature.subscribe(self._on_sensor_event)
        self._bridge.sensors.light_level.subscribe(self._on_sensor_event)
        self._bridge.sensors.button.subscribe(self._on_sensor_event)
        self._bridge.sensors.relative_rotary.subscribe(self._on_sensor_event)
        self._bridge.sensors.contact.subscribe(self._on_sensor_event)

        while True:
            await asyncio.sleep(1)

    def _publish_all(self) -> None:
        """Publish initial info about all bridge resources."""
        for light in self._bridge.lights:
            info = self._light_to_info(light)
            if info is not None:
                self.publish_light(info)

        for room in self._bridge.groups.room:
            info = self._group_to_info(room)
            if info is not None:
                self.publish_group(info)

        for zone in self._bridge.groups.zone:
            info = self._group_to_info(zone)
            if info is not None:
                self.publish_group(info)

        for controller in (
            self._bridge.sensors.motion,
            self._bridge.sensors.temperature,
            self._bridge.sensors.light_level,
            self._bridge.sensors.button,
            self._bridge.sensors.relative_rotary,
            self._bridge.sensors.contact,
        ):
            for sensor in controller:
                info = self._sensor_to_info(sensor)  # type: ignore[arg-type]
                if info is not None:
                    self.publish_sensor(info)
