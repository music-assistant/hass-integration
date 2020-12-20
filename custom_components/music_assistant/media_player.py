"""MediaPlayer platform for Music Assistant integration."""
import logging

from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.const import (
    ATTR_MEDIA_ENQUEUE,
    MEDIA_TYPE_PLAYLIST,
    SUPPORT_BROWSE_MEDIA,
    SUPPORT_CLEAR_PLAYLIST,
    SUPPORT_NEXT_TRACK,
    SUPPORT_PAUSE,
    SUPPORT_PLAY,
    SUPPORT_PLAY_MEDIA,
    SUPPORT_PREVIOUS_TRACK,
    SUPPORT_SHUFFLE_SET,
    SUPPORT_STOP,
    SUPPORT_TURN_OFF,
    SUPPORT_TURN_ON,
    SUPPORT_VOLUME_MUTE,
    SUPPORT_VOLUME_SET,
    SUPPORT_VOLUME_STEP,
)
from homeassistant.components.media_player.errors import BrowseError
from homeassistant.const import STATE_IDLE, STATE_OFF, STATE_PAUSED, STATE_PLAYING
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.util.dt import utcnow
from musicassistant_client import MusicAssistant

from .const import (
    DEFAULT_NAME,
    DISPATCH_KEY_PLAYER_REMOVED,
    DISPATCH_KEY_PLAYER_UPDATE,
    DISPATCH_KEY_PLAYERS,
    DISPATCH_KEY_QUEUE_TIME_UPDATE,
    DISPATCH_KEY_QUEUE_UPDATE,
    DOMAIN,
)
from .media_source import (
    ITEM_ID_SEPERATOR,
    MASS_URI_SCHEME,
    PLAYABLE_MEDIA_TYPES,
    async_create_item_listing,
    async_create_server_listing,
    async_parse_uri,
)

SUPPORTED_FEATURES = (
    SUPPORT_PAUSE
    | SUPPORT_VOLUME_SET
    | SUPPORT_STOP
    | SUPPORT_PREVIOUS_TRACK
    | SUPPORT_NEXT_TRACK
    | SUPPORT_SHUFFLE_SET
    | SUPPORT_TURN_ON
    | SUPPORT_TURN_OFF
    | SUPPORT_VOLUME_MUTE
    | SUPPORT_PLAY
    | SUPPORT_PLAY_MEDIA
    | SUPPORT_VOLUME_STEP
    | SUPPORT_CLEAR_PLAYLIST
    | SUPPORT_BROWSE_MEDIA
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up Music Assistant MediaPlayer(s) from Config Entry."""
    mass = hass.data[DOMAIN][config_entry.entry_id]
    media_players = {}

    async def async_update_media_player(player_data):
        """Add or update Music Assistant MediaPlayer."""
        player_id = player_data["player_id"]
        if player_id not in media_players:
            # new player!
            if not player_data["available"]:
                return  # we don't add unavailable players
            media_player = MassPlayer(mass, player_data)
            media_players[player_id] = media_player
            async_add_entities([media_player])
        else:
            # update for existing player
            async_dispatcher_send(
                hass, f"{DISPATCH_KEY_PLAYER_UPDATE}_{player_id}", player_data
            )

    async def async_remove_media_player(player_id):
        """Handle player removal."""
        for player in media_players.values():
            if player.player_id != player_id:
                continue
            await player.async_mark_unavailable()

    # start listening for players to be added or changed by the server component
    async_dispatcher_connect(hass, DISPATCH_KEY_PLAYERS, async_update_media_player)
    async_dispatcher_connect(
        hass, DISPATCH_KEY_PLAYER_REMOVED, async_remove_media_player
    )


class MassPlayer(MediaPlayerEntity):
    """Representation of Music Assistant player."""

    def __init__(self, mass: MusicAssistant, player_data: dict):
        """Initialize MediaPlayer entity."""
        self._mass = mass
        self._player_data = player_data
        self._queue_data = {}
        self._queue_cur_item = {}
        self._cur_image = None

    async def async_added_to_hass(self):
        """Register callbacks."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DISPATCH_KEY_PLAYER_UPDATE}_{self.player_id}",
                self.async_update_callback,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DISPATCH_KEY_QUEUE_UPDATE}",
                self.async_update_queue_callback,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DISPATCH_KEY_QUEUE_TIME_UPDATE}",
                self.async_update_queue_time_callback,
            )
        )
        # fetch queue state once
        queue_data = await self._mass.async_get_player_queue(self.player_id)
        self._queue_data = queue_data
        if queue_data["cur_item"] is not None:
            self._queue_cur_item = queue_data["cur_item"]

    async def async_update_callback(self, player_data):
        """Handle player updates."""
        self._player_data = player_data
        self.async_write_ha_state()

    async def async_mark_unavailable(self):
        """Handle player removal, mark player as unavailable (as it might come back)."""
        self._player_data["available"] = False
        self.async_write_ha_state()

    async def async_update_queue_callback(self, queue_data):
        """Handle player queue updates."""
        if queue_data["queue_id"] == self._player_data["active_queue"]:
            # received queue update for this player (or it's parent)
            queue_data["updated_at"] = utcnow()
            self._queue_data = queue_data
            if queue_data["cur_item"] is not None:
                self._queue_cur_item = queue_data["cur_item"]
            else:
                self._queue_cur_item = {}
            self._cur_image = await self._mass.async_get_media_item_image_url(
                self._queue_cur_item
            )
            self.async_write_ha_state()

    async def async_update_queue_time_callback(self, queue_data):
        """Handle player queue time updates."""
        if queue_data["queue_id"] == self._player_data["active_queue"]:
            # received queue time update for this player (or it's parent)
            self._queue_data["cur_item_time"] = queue_data["cur_item_time"]
            self._queue_data["updated_at"] = utcnow()
            self.async_write_ha_state()

    @property
    def device_state_attributes(self) -> dict:
        """Return device specific state attributes."""
        return {"mass_player_id": self.player_id}

    @property
    def available(self):
        """Return True if entity is available."""
        return self._player_data.get("available")

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return SUPPORTED_FEATURES

    @property
    def device_info(self):
        """Return the device info."""
        manufacturer = self._player_data.get("device_info", {}).get(
            "manufacturer", DEFAULT_NAME
        )
        model = self._player_data.get("device_info", {}).get("model", "")

        return {
            "identifiers": {(DOMAIN, self.unique_id)},
            "name": self.name,
            "manufacturer": manufacturer,
            "model": model,
            "via_hub": (DOMAIN, self._mass.server_id),
        }

    @property
    def media_position_updated_at(self):
        """When was the position of the current playing media valid."""
        return self._queue_data.get("updated_at")

    @property
    def player_id(self):
        """Return the id of this player."""
        return self._player_data["player_id"]

    @property
    def unique_id(self):
        """Return a unique id for this media player."""
        return f"mass_{self.player_id}"

    @property
    def should_poll(self):
        """Return True if entity has to be polled for state."""
        return False

    @property
    def name(self):
        """Return device name."""
        return self._player_data["name"]

    @property
    def media_content_id(self):
        """Content ID of current playing media."""
        if self._queue_cur_item:
            return f'{self._queue_cur_item["provider"]}{ITEM_ID_SEPERATOR}{self._queue_cur_item["item_id"]}'
        return None

    @property
    def media_content_type(self):
        """Content type of current playing media."""
        return self._queue_cur_item.get("media_type")

    @property
    def media_title(self):
        """Return title currently playing."""
        return self._queue_cur_item.get("name")

    @property
    def media_album_name(self):
        """Album name of current playing media (Music track only)."""
        if self._queue_cur_item and self._queue_cur_item.get("album"):
            return self._queue_cur_item["album"]["name"]
        return None

    @property
    def media_artist(self):
        """Artist of current playing media (Music track only)."""
        if self._queue_cur_item and self._queue_cur_item.get("artists"):
            artist_names = (i["name"] for i in self._queue_cur_item["artists"])
            return "/".join(artist_names)
        return None

    @property
    def media_album_artist(self):
        """Album artist of current playing media (Music track only)."""
        if self._queue_cur_item and self._queue_cur_item.get("album"):
            if self._queue_cur_item["album"].get("artist"):
                return self._queue_cur_item["album"]["artist"]["name"]
        return None

    @property
    def media_image_url(self):
        """Image url of current playing media."""
        return self._cur_image

    @property
    def media_position(self):
        """Return position currently playing."""
        return self._queue_data.get("cur_item_time")

    @property
    def media_duration(self):
        """Return total runtime length."""
        return self._queue_cur_item.get("duration")

    @property
    def volume_level(self):
        """Return current volume level."""
        return self._player_data["volume_level"] / 100

    @property
    def is_volume_muted(self):
        """Return mute state."""
        return self._player_data["muted"]

    @property
    def state(self):
        """Return current playstate of the device."""
        if not self._player_data["powered"]:
            return STATE_OFF
        if self._player_data["state"] == "playing":
            return STATE_PLAYING
        if self._player_data["state"] == "paused":
            return STATE_PAUSED
        return STATE_IDLE

    @property
    def shuffle(self):
        """Boolean if shuffle is enabled."""
        return self._queue_data.get("shuffle_enabled")

    async def async_media_play(self):
        """Send play command to device."""
        await self._mass.async_player_command(self.player_id, "play")

    async def async_media_pause(self):
        """Send pause command to device."""
        await self._mass.async_player_command(self.player_id, "pause")

    async def async_media_stop(self):
        """Send stop command to device."""
        await self._mass.async_player_command(self.player_id, "stop")

    async def async_media_next_track(self):
        """Send next track command to device."""
        await self._mass.async_player_command(self.player_id, "next")

    async def async_media_previous_track(self):
        """Send previous track command to device."""
        await self._mass.async_player_command(self.player_id, "previous")

    async def async_set_volume_level(self, volume):
        """Send new volume_level to device."""
        volume = int(volume * 100)
        await self._mass.async_player_command(self.player_id, "volume_set", volume)

    async def async_mute_volume(self, mute=True):
        """Send mute/unmute to device."""
        await self._mass.async_player_command(self.player_id, "volume_mute", mute)

    async def async_volume_up(self):
        """Send new volume_level to device."""
        await self._mass.async_player_command(self.player_id, "volume_up")

    async def async_volume_down(self):
        """Send new volume_level to device."""
        await self._mass.async_player_command(self.player_id, "volume_down")

    async def async_turn_on(self):
        """Turn on device."""
        await self._mass.async_player_command(self.player_id, "power_on")

    async def async_turn_off(self):
        """Turn off device."""
        await self._mass.async_player_command(self.player_id, "power_off")

    async def async_set_shuffle(self, shuffle: bool):
        """Set shuffle state."""
        await self._mass.async_player_queue_cmd_set_shuffle(self.player_id, shuffle)

    async def async_clear_playlist(self):
        """Clear players playlist."""
        await self._mass.async_player_queue_cmd_clear(self.player_id)

    async def async_play_media(self, media_type, media_id, **kwargs):
        """Send the play_media command to the media player."""
        queue_opt = "add" if kwargs.get(ATTR_MEDIA_ENQUEUE) else "play"
        if media_id.startswith(MASS_URI_SCHEME):
            # got uri from source/media browser
            media = await async_parse_uri(media_id)
            await self._mass.async_play_media(self.player_id, dict(media), queue_opt)
        elif media_type in PLAYABLE_MEDIA_TYPES and ITEM_ID_SEPERATOR in media_id:
            # direct media item
            provider, item_id = media_id.split(ITEM_ID_SEPERATOR)
            await self._mass.async_play_media(
                self.player_id,
                {"media_type": media_type, "item_id": item_id, "provider": provider},
                queue_opt,
            )
        elif media_type in PLAYABLE_MEDIA_TYPES and ITEM_ID_SEPERATOR in media_id:
            # direct media item
            provider, item_id = media_id.split(ITEM_ID_SEPERATOR)
            await self._mass.async_play_media(
                self.player_id,
                {"media_type": media_type, "item_id": item_id, "provider": provider},
                queue_opt,
            )
        elif media_type == MEDIA_TYPE_PLAYLIST:
            # library playlist by name
            for playlist in await self._mass.async_get_library_playlists():
                if playlist["name"] == media_id:
                    await self._mass.async_play_media(
                        self.player_id, playlist, queue_opt
                    )
                    break
        elif media_id.startswith("http"):
            # plain url
            await self._mass.async_cmd_play_uri(self.player_id, media_id)
        else:
            _LOGGER.warning("Unsupported media: %s - %s", media_type, media_id)

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        """Implement the websocket media browsing helper."""

        if media_content_type in [None, "library"] or media_content_id.endswith(
            "/root"
        ):
            # main/library listing requested (for this mass instance)
            return await async_create_server_listing(self._mass)

        if media_content_id.startswith(MASS_URI_SCHEME):
            # sublevel requested
            media_item = await async_parse_uri(media_content_id)
            if self._mass.server_id != media_item["mass_id"]:
                # should not happen, but just in case
                raise BrowseError("Invalid Music Assistance instance")
            return await async_create_item_listing(self._mass, media_item)
