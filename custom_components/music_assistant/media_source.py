"""Media Source Implementation."""
import logging
from typing import Tuple

from homeassistant.components.media_player import BrowseMedia
from homeassistant.components.media_player.const import (
    MEDIA_CLASS_ALBUM,
    MEDIA_CLASS_ARTIST,
    MEDIA_CLASS_DIRECTORY,
    MEDIA_CLASS_MUSIC,
    MEDIA_CLASS_PLAYLIST,
    MEDIA_CLASS_TRACK,
    MEDIA_TYPE_ALBUM,
    MEDIA_TYPE_ARTIST,
    MEDIA_TYPE_PLAYLIST,
    MEDIA_TYPE_TRACK,
)
from homeassistant.components.media_player.errors import BrowseError
from homeassistant.components.media_source.const import MEDIA_MIME_TYPES, URI_SCHEME
from homeassistant.components.media_source.error import MediaSourceError
from homeassistant.components.media_source.models import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
)
from homeassistant.core import HomeAssistant
from musicassistant_client import MusicAssistant

from .const import DEFAULT_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)


MEDIA_TYPE_RADIO = "radio"
CONTENT_TYPE_AUDIO = "audio"
MASS_URI_SCHEME = f"{URI_SCHEME}{DOMAIN}/"
ITEM_ID_SEPERATOR = "###"

PLAYABLE_MEDIA_TYPES = [
    MEDIA_TYPE_PLAYLIST,
    MEDIA_TYPE_ALBUM,
    MEDIA_TYPE_ARTIST,
    MEDIA_TYPE_RADIO,
    MEDIA_TYPE_TRACK,
]

LIBRARY_MAP = {
    "playlists": "Playlists",
    "artists": "Artists",
    "albums": "Albums",
    "tracks": "Tracks",
    "radios": "Radios",
}


CONTENT_TYPE_MEDIA_CLASS = {
    "playlists": {"parent": MEDIA_CLASS_DIRECTORY, "children": MEDIA_CLASS_PLAYLIST},
    "artists": {"parent": MEDIA_CLASS_DIRECTORY, "children": MEDIA_CLASS_ARTIST},
    "albums": {"parent": MEDIA_CLASS_DIRECTORY, "children": MEDIA_CLASS_ALBUM},
    "tracks": {"parent": MEDIA_CLASS_DIRECTORY, "children": MEDIA_CLASS_TRACK},
    "radios": {"parent": MEDIA_CLASS_DIRECTORY, "children": MEDIA_CLASS_MUSIC},
    MEDIA_TYPE_PLAYLIST: {
        "parent": MEDIA_CLASS_PLAYLIST,
        "children": MEDIA_CLASS_TRACK,
    },
    MEDIA_TYPE_ALBUM: {"parent": MEDIA_CLASS_ALBUM, "children": MEDIA_CLASS_TRACK},
    MEDIA_TYPE_ARTIST: {"parent": MEDIA_CLASS_ARTIST, "children": MEDIA_CLASS_ALBUM},
    MEDIA_TYPE_TRACK: {"parent": MEDIA_CLASS_DIRECTORY, "children": None},
    MEDIA_TYPE_RADIO: {"parent": MEDIA_CLASS_DIRECTORY, "children": None},
}


class MissingMediaInformation(BrowseError):
    """Missing media required information."""


class UnknownMediaType(BrowseError):
    """Unknown media type."""


class IncompatibleMediaSource(MediaSourceError):
    """Incompatible media source attributes."""


async def async_get_media_source(hass: HomeAssistant):
    """Set up media source."""
    return MusicAssistentSource(hass)


class MusicAssistentSource(MediaSource):
    """Provide Music Assistent Media Items as media sources."""

    name: str = DEFAULT_NAME

    def __init__(self, hass: HomeAssistant):
        """Initialize Music Assistent source."""
        super().__init__(DOMAIN)
        self.hass = hass

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve media to a url."""
        media = await async_parse_uri(item.identifier)
        for mass_instance in self.hass.data[DOMAIN].values():
            if mass_instance.host != media["mass_host"]:
                continue
            if media["media_type"] in ["track", "radio"]:
                url = f"{mass_instance.base_url}/stream_media/"
                url += f'{media["media_type"]}/{media["provider"]}/{media["item_id"]}'
                return PlayMedia(url, "audio/flac")
            else:
                return PlayMedia(item.identifier, "application/musicassistant")
        raise BrowseError("Invalid Music Assistance instance")

    async def async_browse_media(
        self, item: MediaSourceItem, media_types: Tuple[str] = MEDIA_MIME_TYPES
    ) -> BrowseMediaSource:
        """Return library media for each Music Assistent server."""
        if item.identifier is None:
            return await async_create_root_listing(self.hass)
        elif item.identifier.endswith("/root"):
            # got request for the main listing of a specific mass instance
            mass_id = item.identifier.split("/")[1]
            for mass_instance in self.hass.data[DOMAIN].values():
                if mass_instance.server_id == mass_id:
                    return await async_create_server_listing(mass_instance)
            raise BrowseError("Invalid Music Assistance instance")

        else:
            # sublevel requested
            media_item = await async_parse_uri(item.identifier)
            for mass_instance in self.hass.data[DOMAIN].values():
                if mass_instance.server_id != media_item["mass_id"]:
                    continue
                return await async_create_item_listing(mass_instance, media_item)
            raise BrowseError("Invalid Music Assistance instance")


async def async_create_root_listing(hass: HomeAssistant):
    """Create the root media source."""
    if len(hass.data[DOMAIN].keys()) == 1:
        # we only have one Music Assistant instance, skip root listing
        for mass_instance in hass.data[DOMAIN].values():
            return await async_create_server_listing(mass_instance)
    else:
        # we create a server listing for each server
        root_source = BrowseMediaSource(
            domain=DOMAIN,
            identifier="root",
            title="Root",
            media_class=MEDIA_CLASS_DIRECTORY,
            media_content_type=CONTENT_TYPE_AUDIO,
            can_play=False,
            can_expand=True,
            children=[],
        )
        for mass_instance in hass.data[DOMAIN].values():
            root_source.children.append(
                await async_create_server_listing(mass_instance)
            )
        return root_source


async def async_create_server_listing(mass: MusicAssistant):
    """Create the Library sources (main listing) for a Music Assistant instance."""
    parent_source = BrowseMediaSource(
        domain=DOMAIN,
        identifier=f"{mass.server_id}/root",
        title=f"{DEFAULT_NAME} ({mass.host})",
        media_class=MEDIA_CLASS_DIRECTORY,
        media_content_type=CONTENT_TYPE_AUDIO,
        can_play=False,
        can_expand=True,
        children=[],
    )
    for media_type, title in LIBRARY_MAP.items():
        child_source = BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{mass.server_id}/{media_type}",
            title=title,
            media_class=MEDIA_CLASS_DIRECTORY,
            media_content_type=CONTENT_TYPE_AUDIO,
            can_play=False,
            can_expand=True,
        )
        parent_source.children.append(child_source)
    return parent_source


async def async_create_item_listing(mass: MusicAssistant, media_item: dict):
    """Create BrowseMediaSource payload for the (parsed) media item."""
    source = None
    items = []
    if media_item["media_type"] == "playlists":
        items = await mass.async_get_library_playlists()
    elif media_item["media_type"] == "artists":
        items = await mass.async_get_library_artists()
    elif media_item["media_type"] == "albums":
        items = await mass.async_get_library_albums()
    elif media_item["media_type"] == "tracks":
        items = await mass.async_get_library_tracks()
    elif media_item["media_type"] == "radios":
        items = await mass.async_get_library_radios()
    elif media_item["media_type"] == MEDIA_TYPE_PLAYLIST:
        # playlist tracks
        source = await async_create_media_item_source(
            mass,
            await mass.async_get_playlist(
                media_item["item_id"], media_item["provider"]
            ),
        )
        items = await mass.async_get_playlist_tracks(
            media_item["item_id"], media_item["provider"]
        )
    elif media_item["media_type"] == MEDIA_TYPE_ALBUM:
        # album tracks
        source = await async_create_media_item_source(
            mass,
            await mass.async_get_album(media_item["item_id"], media_item["provider"]),
        )
        items = await mass.async_get_album_tracks(
            media_item["item_id"], media_item["provider"]
        )
    elif media_item["media_type"] == MEDIA_TYPE_ARTIST:
        # artist albums
        source = await async_create_media_item_source(
            mass,
            await mass.async_get_artist(media_item["item_id"], media_item["provider"]),
        )
        items = await mass.async_get_artist_albums(
            media_item["item_id"], media_item["provider"]
        )
    if not source:
        # create generic source
        source = await async_create_generic_source(mass, media_item)
    # attach source childs
    for item in items:
        try:
            child_item = await async_create_media_item_source(mass, item)
            source.children.append(child_item)
        except (MissingMediaInformation, UnknownMediaType):
            continue

    return source


async def async_create_generic_source(mass: MusicAssistant, media_item: dict):
    """Create a BrowseMedia source for a generic (root folder) item."""
    media_class = CONTENT_TYPE_MEDIA_CLASS[media_item["media_type"]]
    title = LIBRARY_MAP.get(media_item["content_id"])
    image = ""
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=f'{mass.server_id}/{media_item["media_type"]}/{media_item["content_id"]}',
        title=title,
        media_class=media_class["parent"],
        children_media_class=media_class["children"],
        media_content_type=CONTENT_TYPE_AUDIO,
        can_play=media_item["media_type"] in PLAYABLE_MEDIA_TYPES,
        children=[],
        can_expand=True,
        thumbnail=image,
    )


async def async_create_media_item_source(mass: MusicAssistant, media_item: dict):
    """Convert Music Assistant media_item into a BrowseMedia item."""
    # get media_type and class
    media_type = media_item["media_type"]
    media_class = CONTENT_TYPE_MEDIA_CLASS[media_type]

    # get image url
    image = await mass.async_get_media_item_image_url(media_item)
    # create title
    if media_type == "album":
        title = f'{media_item["artist"]["name"]} - {media_item["name"]}'
    if media_type == "track":
        artist_names = [i["name"] for i in media_item["artists"]]
        artist_names_str = " / ".join(artist_names)
        title = f'{artist_names_str} - {media_item["name"]}'
    else:
        title = media_item["name"]

    # create media_content_id from provider/item_id combination
    media_item_id = (
        f'{media_item["provider"]}{ITEM_ID_SEPERATOR}{media_item["item_id"]}'
    )

    # we're constructing the identifier and media_content_id manually
    # this way we're compatible with both BrowseMedia and BrowseMediaSource
    identifier = f"{mass.server_id}/{media_type}/{media_item_id}"
    media_content_id = f"{MASS_URI_SCHEME}{identifier}"
    src = BrowseMedia(
        title=title,
        media_class=media_class["parent"],
        children_media_class=media_class["children"],
        media_content_id=media_content_id,
        media_content_type=CONTENT_TYPE_AUDIO,
        can_play=media_type in PLAYABLE_MEDIA_TYPES,
        children=[],
        can_expand=media_type not in [MEDIA_TYPE_TRACK, MEDIA_TYPE_RADIO],
        thumbnail=image,
    )
    # set these manually so we're compatible with BrowseMediaSource
    src.identifier = identifier
    src.domain = DOMAIN
    return src


async def async_parse_uri(uri: str) -> dict:
    """Parse uri (item identifier) to some values we can understand."""
    content_id = ""
    provider = ""
    item_id = ""
    if uri.startswith(MASS_URI_SCHEME):
        uri = uri.split(MASS_URI_SCHEME)[1]
    if uri.startswith("/"):
        uri = uri[1:]
    mass_id = uri.split("/")[0]
    media_type = uri.split("/")[1]
    # music assistant needs a provider and item_id combination for all media items
    # we've mangled both in the content_id, used by Hass internally
    if len(uri.split("/")) > 2:
        content_id = uri.split("/")[2]
        if content_id:
            provider, item_id = content_id.split(ITEM_ID_SEPERATOR)
    # return a dict that is (partly) compatible with the Music Assistant MediaItem structure
    return {
        "item_id": item_id,
        "provider": provider,
        "media_type": media_type,
        "mass_id": mass_id,
        "content_id": content_id,
    }
