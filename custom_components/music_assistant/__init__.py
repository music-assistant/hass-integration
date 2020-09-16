"""Music Assistant (music-assistant.github.io) integration."""
import asyncio
import logging
from typing import Any

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
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

    mass.register_event_callback(handle_mass_event, SUBSCRIBE_EVENTS)

    # connect to Music Assistant
    await mass.async_connect(auto_retry=True)

    return True


async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    mass = hass.data[DOMAIN].pop(entry.entry_id)
    return await mass.async_close()
