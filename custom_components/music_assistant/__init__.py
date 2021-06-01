"""Music Assistant (music-assistant.github.io) integration."""

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import HomeAssistantType
from musicassistant_client import (
    EVENT_CONNECTED,
    EVENT_PLAYER_ADDED,
    EVENT_PLAYER_CHANGED,
    EVENT_PLAYER_REMOVED,
    EVENT_QUEUE_TIME_UPDATED,
    EVENT_QUEUE_UPDATED,
    MusicAssistant,
)

from .const import (
    DISPATCH_KEY_PLAYER_REMOVED,
    DISPATCH_KEY_PLAYERS,
    DISPATCH_KEY_QUEUE_TIME_UPDATE,
    DISPATCH_KEY_QUEUE_UPDATE,
    DOMAIN,
)
from .player_controls import HassPlayerControls

SUBSCRIBE_EVENTS = (
    EVENT_CONNECTED,
    EVENT_PLAYER_ADDED,
    EVENT_PLAYER_CHANGED,
    EVENT_PLAYER_REMOVED,
    EVENT_QUEUE_UPDATED,
    EVENT_QUEUE_TIME_UPDATED,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass, config):
    """Set up the platform."""
    hass.data[DOMAIN] = {}
    return True


async def async_setup_entry(hass: HomeAssistantType, entry: ConfigEntry):
    """Set up from a config entry."""
    address = entry.data["address"]
    token_info = entry.data["token_info"]
    http_session = async_get_clientsession(hass, verify_ssl=False)
    mass = MusicAssistant(
        address, token_info["token"], loop=hass.loop, client_session=http_session
    )
    hass.data[DOMAIN][entry.entry_id] = mass

    # initialize media_player platform
    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(entry, "media_player")
    )
    player_controls = HassPlayerControls(hass, mass, entry.options)

    # register callbacks
    async def handle_mass_event(event: str, event_details: Any):
        """Handle an incoming event from Music Assistant."""
        if event in [EVENT_PLAYER_ADDED, EVENT_PLAYER_CHANGED]:
            async_dispatcher_send(hass, DISPATCH_KEY_PLAYERS, event_details)
        elif event == EVENT_QUEUE_UPDATED:
            async_dispatcher_send(hass, DISPATCH_KEY_QUEUE_UPDATE, event_details)
        elif event == EVENT_QUEUE_TIME_UPDATED:
            async_dispatcher_send(hass, DISPATCH_KEY_QUEUE_TIME_UPDATE, event_details)
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
    hass.async_create_task(mass.async_connect())

    async def async_options_updated(hass: HomeAssistantType, entry: ConfigEntry):
        """Handle options update."""
        await player_controls.async_register_player_controls()

    entry.add_update_listener(async_options_updated)

    return True


async def async_options_updated(hass: HomeAssistantType, entry: ConfigEntry):
    """Handle options update."""
    _LOGGER.info("Configuration options changed, reloading integration...")
    asyncio.create_task(hass.config_entries.async_reload(entry.entry_id))


async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    mass = hass.data[DOMAIN].pop(entry.entry_id)
    await mass.async_close()
    return True
