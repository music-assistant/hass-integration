"""Config flow for Music Assistant integration."""
import logging

import voluptuous as vol
from homeassistant import config_entries, core, exceptions
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import DiscoveryInfoType
from musicassistant_client import (
    ConnectionFailedError,
    InvalidCredentialsError,
    MusicAssistant,
)

from .const import DEFAULT_NAME, DOMAIN  # pylint: disable=unused-import

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA_DISCOVERY = vol.Schema(
    {
        vol.Optional(CONF_USERNAME, default="admin"): str,
        vol.Optional(CONF_PASSWORD, default=""): str,
    }
)
DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=8095): int,
        vol.Optional(CONF_USERNAME, default="admin"): str,
        vol.Optional(CONF_PASSWORD, default=""): str,
        vol.Optional(CONF_SSL, default=False): bool,
    }
)


async def authenticate(
    hass: core.HomeAssistant,
    host: str,
    port: int,
    username: str = "admin",
    password: str = "",
    ssl: bool = False,
):
    """Connect and authenticate home assistant."""
    http_session = async_get_clientsession(hass, verify_ssl=False)
    mass = MusicAssistant(host, port, username, password, ssl, hass.loop, http_session)
    try:
        await mass.async_connect()
        await mass.async_close()
    except (InvalidCredentialsError, ConnectionFailedError) as exc:
        raise InvalidAuth from exc
    return {
        CONF_HOST: host,
        CONF_PORT: port,
        CONF_USERNAME: username,
        CONF_PASSWORD: password,
        CONF_SSL: ssl,
    }


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Music Assistant."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    async def async_step_user(self, user_input=None):
        """Handle getting host details from the user."""

        errors = {}
        if user_input is not None:
            unique_id = user_input[CONF_HOST]
            await self.async_set_unique_id(unique_id)
            # try to authenticate
            try:
                info = await authenticate(
                    self.hass,
                    user_input[CONF_HOST],
                    user_input[CONF_PORT],
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    user_input[CONF_SSL],
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=DEFAULT_NAME, data=info)

        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)

    async def async_step_zeroconf(self, discovery_info: DiscoveryInfoType):
        """Handle discovery."""
        # pylint: disable=attribute-defined-outside-init
        unique_id = discovery_info["properties"]["id"]
        await self.async_set_unique_id(unique_id)
        self._host = discovery_info["properties"]["host"]
        self._port = discovery_info["properties"]["http_port"]
        self._name = discovery_info["properties"]["id"]
        server_info = {
            CONF_HOST: self._host,
            CONF_PORT: self._port,
        }
        self._abort_if_unique_id_configured(updates=server_info)
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(self, user_input=None):
        """Handle user-confirmation of discovered node."""
        errors = {}
        if user_input is not None:
            try:
                info = await authenticate(
                    self.hass,
                    self._host,
                    self._port,
                    user_input["username"],
                    user_input["password"],
                )
                # authentication was successfull, create the entry
                return self.async_create_entry(
                    title=DEFAULT_NAME,
                    data=info,
                )
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=DEFAULT_NAME, data=info)

        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=DATA_SCHEMA_DISCOVERY,
            description_placeholders={"name": self._name},
        )


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is invalid auth."""
