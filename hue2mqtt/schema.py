"""Schemas for data about lights."""
from typing import Any, List, Optional, Tuple

from pydantic import BaseModel, Field, create_model


class LightBaseState(BaseModel):
    """The base attributes of a light state."""

    on: Optional[bool] = None

    alert: Optional[str] = None
    bri: Optional[float] = None
    ct: Optional[int] = None
    effect: Optional[str] = None
    hue: Optional[int] = None
    sat: Optional[int] = None
    xy: Optional[Tuple[float, float]] = None
    transitiontime: Optional[str] = None


class LightSetState(LightBaseState):
    """The settable states of a light."""

    bri_inc: Optional[int] = None
    sat_inc: Optional[int] = None
    hue_inc: Optional[int] = None
    ct_inc: Optional[int] = None
    xy_inc: Optional[Tuple[float, float]] = None


class GroupSetState(LightSetState):
    """The settable states of a group."""

    scene: Optional[str] = None


class LightState(LightBaseState):
    """The State of a light that we can read."""

    reachable: Optional[bool] = None
    color_mode: Optional[str] = None
    mode: Optional[str] = None


class LightInfo(BaseModel):
    """Information about a light."""

    id: str  # noqa: A003
    name: str
    state: Optional[LightState] = None

    manufacturername: str
    modelid: str
    productname: str
    type: str  # noqa: A003

    swversion: str


class GroupState(BaseModel):
    """The state of lights in a group."""

    all_on: bool
    any_on: bool


class GroupInfo(BaseModel):
    """Information about a light group."""

    id: str  # noqa: A003
    name: str
    lights: List[str]
    sensors: List[str]
    type: str  # noqa: A003
    state: GroupState

    group_class: Optional[str] = Field(default=None, alias="class")

    action: LightState


class GenericSensorState(BaseModel):
    """Information about the state of a sensor."""

    lastupdated: Optional[str] = None


class PresenceSensorState(GenericSensorState):
    """Information about the state of a sensor."""

    presence: Optional[bool] = None


class RotarySensorState(GenericSensorState):
    """Information about the state of a sensor."""

    rotaryevent: Optional[str] = None
    expectedrotation: Optional[str] = None
    expectedeventduration: Optional[str] = None


class SwitchSensorState(GenericSensorState):
    """Information about the state of a sensor."""

    buttonevent: Optional[int] = None


class LightLevelSensorState(GenericSensorState):
    """Information about the state of a sensor."""

    dark: Optional[bool] = None
    daylight: Optional[bool] = None
    lightlevel: Optional[int] = None


class TemperatureSensorState(GenericSensorState):
    """Information about the state of a sensor."""

    temperature: Optional[int] = None


class HumiditySensorState(GenericSensorState):
    """Information about the state of a sensor."""

    humidity: Optional[int] = None


class OpenCloseSensorState(GenericSensorState):
    """Information about the state of a sensor."""

    open: Optional[str] = None  # noqa: A003


SensorState = create_model(
    "SensorState",
    __base__=(
        LightLevelSensorState,
        PresenceSensorState,
        RotarySensorState,
        SwitchSensorState,
        TemperatureSensorState,
        HumiditySensorState,
        OpenCloseSensorState,
    ),
)


class SensorInfo(BaseModel):
    """Information about a sensor."""

    id: str  # noqa: A003
    name: str
    type: str  # noqa: A003
    modelid: str
    manufacturername: str

    productname: str
    swversion: Optional[str] = None

    state: SensorState  # type: ignore[valid-type]
    capabilities: Optional[Any] = None
