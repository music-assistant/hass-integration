"""Config flow for Music Assistant integration."""
import logging

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.typing import DiscoveryInfoType
from musicassistant_client import async_get_token

from .const import (
    CONF_POWER_CONTROL_ENTITIES,
    CONF_VOLUME_CONTROL_ENTITIES,
    DEFAULT_NAME,
    DOMAIN,
)
from .player_controls import (
    CONTROL_TYPE_POWER,
    CONTROL_TYPE_VOLUME,
    async_get_playercontrol_entities,
)

_LOGGER = logging.getLogger(__name__)


DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=8095): int,
        vol.Optional(CONF_USERNAME, default="admin"): str,
        vol.Optional(CONF_PASSWORD, default=""): str,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Music Assistant."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input=None):
        """Handle getting host details from the user."""

        errors = {}
        if user_input is not None:

            # try to authenticate
            try:
                token_info = await async_get_token(
                    server=user_input[CONF_HOST],
                    username=user_input[CONF_USERNAME],
                    password=user_input[CONF_PASSWORD],
                    app_id="HomeAssistant",
                    port=user_input[CONF_PORT],
                )
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "invalid_auth"
            else:
                unique_id = token_info["server_id"]
                await self.async_set_unique_id(unique_id)
                data = {
                    CONF_HOST: user_input[CONF_HOST],
                    CONF_PORT: user_input[CONF_PORT],
                    "token_info": token_info,
                }
                return self.async_create_entry(title=DEFAULT_NAME, data=data)

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    async def async_step_zeroconf(self, discovery_info: DiscoveryInfoType):
        """Handle discovery."""
        # pylint: disable=attribute-defined-outside-init
        unique_id = discovery_info["properties"]["id"]
        await self.async_set_unique_id(unique_id)
        self._host = discovery_info["properties"]["host"]
        self._port = discovery_info["properties"]["port"]
        self._name = discovery_info["properties"]["id"]
        server_info = {CONF_HOST: self._host, CONF_PORT: self._port}
        self._abort_if_unique_id_configured(updates=server_info)
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(self, user_input=None):
        """Handle user-confirmation of discovered node."""
        errors = {}
        if user_input is not None:
            try:
                token_info = await async_get_token(
                    server=self._host,
                    username=user_input[CONF_USERNAME],
                    password=user_input[CONF_PASSWORD],
                    app_id="HomeAssistant",
                    port=self._port,
                )
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "invalid_auth"
            else:
                data = {
                    CONF_HOST: self._host,
                    CONF_PORT: self._port,
                    "token_info": token_info,
                }
                return self.async_create_entry(title=DEFAULT_NAME, data=data)

        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_USERNAME, default="admin"): str,
                    vol.Optional(CONF_PASSWORD, default=""): str,
                }
            ),
            description_placeholders={"name": self._name},
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """OptionsFlow handler."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        errors = {}

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # get all available playercontrol entities
        control_entities = await async_get_playercontrol_entities(self.hass)
        power_controls = {
            x["control_id"]: x["name"]
            for x in control_entities
            if x["control_type"] == CONTROL_TYPE_POWER
        }
        volume_controls = {
            x["control_id"]: x["name"]
            for x in control_entities
            if x["control_type"] == CONTROL_TYPE_VOLUME
        }
        # show form with the selection boxes
        # by default no controls are enabled
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_POWER_CONTROL_ENTITIES,
                        default=self.config_entry.options.get(
                            CONF_POWER_CONTROL_ENTITIES, []
                        ),
                    ): cv.multi_select(power_controls),
                    vol.Optional(
                        CONF_VOLUME_CONTROL_ENTITIES,
                        default=self.config_entry.options.get(
                            CONF_VOLUME_CONTROL_ENTITIES, []
                        ),
                    ): cv.multi_select(volume_controls),
                }
            ),
            errors=errors,
        )
