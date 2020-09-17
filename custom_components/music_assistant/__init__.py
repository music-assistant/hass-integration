"""Music Assistant (music-assistant.github.io) integration."""
import asyncio
import logging
from functools import partial
from typing import Any

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
    ATTR_FRIENDLY_NAME,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    SERVICE_VOLUME_SET,
    STATE_OFF,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import HomeAssistantType
from musicassistant_client import (
    EVENT_CONNECTED,
    EVENT_PLAYER_ADDED,
    EVENT_PLAYER_CHANGED,
    EVENT_PLAYER_REMOVED,
    EVENT_QUEUE_UPDATED,
    MusicAssistant,
)

from .const import (
    DISPATCH_KEY_PLAYER_REMOVED,
    DISPATCH_KEY_PLAYERS,
    DISPATCH_KEY_QUEUE_UPDATE,
    DOMAIN,
)

SUBSCRIBE_EVENTS = (
    EVENT_CONNECTED,
    EVENT_PLAYER_ADDED,
    EVENT_PLAYER_CHANGED,
    EVENT_PLAYER_REMOVED,
    EVENT_QUEUE_UPDATED,
)

_LOGGER = logging.getLogger(__name__)

OFF_STATES = [STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN]


async def async_setup(hass, config):
    """Set up the platform."""
    hass.data[DOMAIN] = {}
    return True


async def async_setup_entry(hass, entry):
    """Set up from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    use_ssl = entry.data[CONF_SSL]
    http_session = async_get_clientsession(hass, verify_ssl=False)
    mass = MusicAssistant(host, port, username, password, use_ssl, hass.loop, http_session)
    hass.data[DOMAIN][entry.entry_id] = mass

    # initialize media_player platform
    hass.async_create_task(hass.config_entries.async_forward_entry_setup(entry, "media_player"))
    player_controls = HassPlayerControls(hass, mass)

    # register callbacks
    async def handle_mass_event(event: str, event_details: Any):
        """Handle an incoming event from Music Assistant."""
        if event in [EVENT_PLAYER_ADDED, EVENT_PLAYER_CHANGED]:
            async_dispatcher_send(hass, DISPATCH_KEY_PLAYERS, event_details)
        elif event == EVENT_QUEUE_UPDATED:
            async_dispatcher_send(hass, DISPATCH_KEY_QUEUE_UPDATE, event_details)
        elif event == EVENT_PLAYER_REMOVED:
            async_dispatcher_send(hass, DISPATCH_KEY_PLAYER_REMOVED, event_details)
        elif event == EVENT_CONNECTED:
            _LOGGER.debug("Music Assistant is connected!")
            # request all players once at startup
            for player in await mass.async_get_players():
                async_dispatcher_send(hass, DISPATCH_KEY_PLAYERS, player)
            # register player controls
            await player_controls.async_register_player_controls()

    mass.register_event_callback(handle_mass_event, SUBSCRIBE_EVENTS)

    # connect to Music Assistant in a background task with auto reconnects etc.
    hass.async_create_task(mass.async_connect(auto_retry=True))

    return True


class HassPlayerControls:
    """Allow Home Assisant entities to be used as PlayerControls for MusicAssistant."""

    def __init__(self, hass: HomeAssistantType, mass: MusicAssistant):
        """Initialize class."""
        self.hass = hass
        self.mass = mass
        self._registered_controls = {}
        self._watched_entities = set()
        # subscribe to HomeAssistant state changed events
        hass.bus.async_listen("state_changed", self.async_hass_state_event)

    async def async_set_player_control_state(self, control_id: str, new_state: Any):
        """Handle request from MusicAssistant to set a new state for a PlayerControl."""
        control = self._registered_controls[control_id]
        entity_id = control[ATTR_ENTITY_ID]
        domain = entity_id.split(".")[0]

        if control["control_type"] == 0 and control["source"] and new_state:
            # power control with source or new state off
            service = "select_source"
            await self.hass.services.async_call(
                domain,
                service,
                {ATTR_ENTITY_ID: entity_id, ATTR_INPUT_SOURCE: control["source"]},
            )
        elif control["control_type"] == 0:
            # power control with turn on/off
            service = SERVICE_TURN_ON if new_state else SERVICE_TURN_OFF
            await self.hass.services.async_call(domain, service, {ATTR_ENTITY_ID: entity_id})
        elif control["control_type"] == 1:
            # volume control
            await self.hass.services.async_call(
                domain,
                SERVICE_VOLUME_SET,
                {ATTR_ENTITY_ID: entity_id, ATTR_MEDIA_VOLUME_LEVEL: new_state / 100},
            )

    async def async_hass_state_event(self, event):
        """Handle hass state-changed events to update registered PlayerControls."""
        if event.data[ATTR_ENTITY_ID] not in self._watched_entities:
            return
        state_obj = event.data["new_state"]
        for control_id, control in self._registered_controls.items():
            if state_obj.entity_id != control[ATTR_ENTITY_ID]:
                continue
            if control["control_type"] == 0 and control[ATTR_INPUT_SOURCE]:
                # power control with source select
                new_state = state_obj["attributes"].get(ATTR_INPUT_SOURCE, "") == control["source"]
            elif control["control_type"] == 0:
                # power control with source or new state off
                new_state = state_obj.state not in OFF_STATES
            elif control["control_type"] == 1:
                # volume control
                new_state = state_obj.attributes.get(ATTR_MEDIA_VOLUME_LEVEL, 0) * 100
            await self.mass.async_update_player_control(control_id, new_state)

    async def async_register_player_controls(self):
        """Register hass entities as player controls on Music Assistant."""
        # TODO: create a user configurable filter which entities may be published?
        for entity in self.hass.states.async_all(
            [SWITCH_DOMAIN, MEDIA_PLAYER_DOMAIN, INPUT_BOOLEAN_DOMAIN]
        ):
            if entity.attributes.get("mass_player_id"):
                continue
            # PowerControl support
            source_list = entity.attributes.get(ATTR_INPUT_SOURCE_LIST, [""])
            # create PowerControl for each source (if exists)
            for source in source_list:
                if source:
                    cur_state = entity.attributes.get(ATTR_INPUT_SOURCE) == source
                    name = f"{entity.name}: {source}"
                    control_id = f"{entity.entity_id}_power_{source}"
                else:
                    cur_state = entity.state not in OFF_STATES
                    name = entity.name
                    control_id = f"{entity.entity_id}_power"
                control = {
                    "control_type": 0,
                    "control_id": control_id,
                    "provider_name": "Home Assistant",
                    "name": name,
                }
                await self.mass.async_register_player_control(
                    **control,
                    state=cur_state,
                    cb_func=partial(self.async_set_player_control_state, control_id),
                )
                control[ATTR_INPUT_SOURCE] = source
                control[ATTR_ENTITY_ID] = entity.entity_id
                self._watched_entities.add(entity.entity_id)
                self._registered_controls[control_id] = control
            # VolumeControl support
            if entity.domain == MEDIA_PLAYER_DOMAIN:
                control_id = f"{entity.entity_id}_volume"
                cur_state = entity.attributes.get(ATTR_MEDIA_VOLUME_LEVEL, 0) * 100
                control = {
                    "control_type": 1,
                    "control_id": control_id,
                    "provider_name": "Home Assistant",
                    "name": entity.name,
                }
                await self.mass.async_register_player_control(
                    **control,
                    state=cur_state,
                    cb_func=partial(self.async_set_player_control_state, control_id),
                )
                control[ATTR_ENTITY_ID] = entity.entity_id
                self._watched_entities.add(entity.entity_id)
                self._registered_controls[control_id] = control


async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    mass = hass.data[DOMAIN].pop(entry.entry_id)
    await mass.async_close()
    return True
