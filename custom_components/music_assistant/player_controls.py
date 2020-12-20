"""Support Home Assistant entities to be used as PlayerControl for Music Assistant."""
from functools import partial
from typing import Any, Dict, List

from homeassistant.components.input_boolean import DOMAIN as INPUT_BOOLEAN_DOMAIN
from homeassistant.components.media_player.const import (
    ATTR_INPUT_SOURCE,
    ATTR_INPUT_SOURCE_LIST,
    ATTR_MEDIA_VOLUME_LEVEL,
)
from homeassistant.components.media_player.const import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    SERVICE_VOLUME_SET,
    STATE_OFF,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.helpers.event import Event
from homeassistant.helpers.typing import HomeAssistantType
from musicassistant_client import MusicAssistant

from .const import CONF_POWER_CONTROL_ENTITIES, CONF_VOLUME_CONTROL_ENTITIES

OFF_STATES = [STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN]
CONTROL_TYPE_POWER = 0
CONTROL_TYPE_VOLUME = 1


async def async_get_playercontrol_entities(hass: HomeAssistantType) -> List[Dict]:
    """Return all entities that are suitable to be used as PlayerControl."""
    controls = []
    for entity in hass.states.async_all(
        [SWITCH_DOMAIN, MEDIA_PLAYER_DOMAIN, INPUT_BOOLEAN_DOMAIN]
    ):
        if entity.attributes.get("mass_player_id"):
            continue
        # PowerControl support
        source_list = entity.attributes.get(ATTR_INPUT_SOURCE_LIST, [""])
        # create PowerControl for each source (if exists)
        for source in source_list:
            if source:
                name = f"{entity.name} ({entity.entity_id}): {source}"
                control_id = f"{entity.entity_id}_power_{source}"
            else:
                name = f"{entity.name} ({entity.entity_id})"
                control_id = f"{entity.entity_id}_power"
            controls.append(
                {
                    "control_type": CONTROL_TYPE_POWER,
                    "control_id": control_id,
                    "provider_name": "Home Assistant",
                    "name": name,
                    "entity_id": entity.entity_id,
                    "source": source,
                }
            )

        # VolumeControl support
        if entity.domain == MEDIA_PLAYER_DOMAIN:
            control_id = f"{entity.entity_id}_volume"
            name = f"{entity.name} ({entity.entity_id})"
            controls.append(
                {
                    "control_type": CONTROL_TYPE_VOLUME,
                    "control_id": control_id,
                    "provider_name": "Home Assistant",
                    "name": name,
                    "entity_id": entity.entity_id,
                }
            )
    return controls


class HassPlayerControls:
    """Enable Home Assisant entities to be used as PlayerControls for MusicAssistant."""

    def __init__(
        self, hass: HomeAssistantType, mass: MusicAssistant, config_options: dict
    ) -> None:
        """Initialize class."""
        self.hass = hass
        self.mass = mass
        self.config_options = config_options
        self._registered_controls = {}
        # subscribe to HomeAssistant state changed events
        hass.bus.async_listen("state_changed", self.async_hass_state_event)

    async def async_set_player_control_state(
        self, control: dict, new_state: Any
    ) -> None:
        """Handle request from MusicAssistant to set a new state for a PlayerControl."""
        entity_id = control["entity_id"]
        entity = self.hass.states.get(entity_id)
        if not entity:
            return
        if control["control_type"] == CONTROL_TYPE_POWER and control["source"]:
            # power control with source support
            if new_state and entity.state == "off":
                # power on = select source
                service = "select_source"
                await self.hass.services.async_call(
                    entity.domain,
                    service,
                    {ATTR_ENTITY_ID: entity_id, ATTR_INPUT_SOURCE: control["source"]},
                )
            elif entity.attributes.get(ATTR_INPUT_SOURCE) == control["source"]:
                # power off (only if source matches)
                await self.hass.services.async_call(
                    entity.domain, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id}
                )
        elif control["control_type"] == CONTROL_TYPE_POWER:
            # power control with turn on/off
            service = SERVICE_TURN_ON if new_state else SERVICE_TURN_OFF
            await self.hass.services.async_call(
                entity.domain, service, {ATTR_ENTITY_ID: entity_id}
            )
        elif control["control_type"] == CONTROL_TYPE_VOLUME:
            # volume control
            await self.hass.services.async_call(
                entity.domain,
                SERVICE_VOLUME_SET,
                {ATTR_ENTITY_ID: entity_id, ATTR_MEDIA_VOLUME_LEVEL: new_state / 100},
            )

    async def async_hass_state_event(self, event: Event) -> None:
        """Handle hass state-changed events to update registered PlayerControls."""
        if event.data[ATTR_ENTITY_ID] not in self._registered_controls:
            return
        state_obj = event.data["new_state"]
        if not state_obj:
            return
        for control in self._registered_controls[state_obj.entity_id]:
            if control["control_type"] == CONTROL_TYPE_POWER and control["source"]:
                # power control with source select
                new_state = (
                    state_obj.attributes.get(ATTR_INPUT_SOURCE, "") == control["source"]
                )
            elif control["control_type"] == CONTROL_TYPE_POWER:
                # power control with source or new state off
                new_state = state_obj.state not in OFF_STATES
            elif control["control_type"] == CONTROL_TYPE_VOLUME:
                # volume control
                new_state = state_obj.attributes.get(ATTR_MEDIA_VOLUME_LEVEL, 0) * 100
            await self.mass.async_update_player_control(
                control["control_id"], new_state
            )

    async def async_register_player_controls(self):
        """Register (enabled) hass entities as player controls on Music Assistant."""
        enabled_power_controls = self.config_options.get(
            CONF_POWER_CONTROL_ENTITIES, []
        )
        enabled_volume_controls = self.config_options.get(
            CONF_VOLUME_CONTROL_ENTITIES, []
        )
        enabled_controls = enabled_power_controls + enabled_volume_controls

        for control in await async_get_playercontrol_entities(self.hass):

            # Only register controls that are enabled and available
            if control["control_id"] not in enabled_controls:
                continue
            entity = self.hass.states.get(control["entity_id"])
            if not entity:
                continue

            if control["control_type"] == CONTROL_TYPE_VOLUME:
                cur_state = entity.attributes.get(ATTR_MEDIA_VOLUME_LEVEL, 0) * 100
            else:
                cur_state = entity.state not in OFF_STATES

            await self.mass.async_register_player_control(
                control_type=control["control_type"],
                control_id=control["control_id"],
                provider_name="Home Assistant",
                name=control["name"],
                state=cur_state,
                cb_func=partial(self.async_set_player_control_state, control),
            )
            # store all controls belonging to an entity_id which helps us with updates from the state machine
            if entity.entity_id not in self._registered_controls:
                self._registered_controls[entity.entity_id] = []
            if control not in self._registered_controls[entity.entity_id]:
                self._registered_controls[entity.entity_id].append(control)
