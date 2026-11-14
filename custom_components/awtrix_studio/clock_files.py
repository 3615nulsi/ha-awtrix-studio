"""Requests to the clock's web server: animated icons and the app loop.

AWTRIX only animates icons stored in its own memory. For an animated icon of
the LaMetric gallery, Awtrix Studio drops the GIF into the clock's /ICONS
folder, once, and the app then names the icon by its number. AWTRIX 3 takes
it through its web file manager (the one the clock's web page uses), AWTRIX NG
through its files API; the driver knows which.

The clock's address comes from its MQTT stats and is limited to the RFC 1918
private IPv4 ranges (10/8, 172.16/12, 192.168/16). Files are only ever added,
never deleted. When the clock cannot be reached (or refuses the credentials),
the caller falls back to the icon's first image sent inline.

The clock's web page may ask for a user name and password (HTTP Basic auth, an
option of both firmwares): they are set by reconfiguring the clock, stored in
its config entry and sent with every request made here, never logged.

The /ICONS listing is kept for a few minutes, then read again when next
needed (no timer), and forgotten when the clock restarts: an icon deleted on
the clock does not stay "present" for ever.

The app loop order is also read and changed there: AWTRIX has no MQTT command
to reorder apps.

The panel's preview reads the icons already on the clock the same way, through
Home Assistant: only a file of the clock's /ICONS listing is ever fetched.
"""

from __future__ import annotations

from http import HTTPStatus
import json
import logging
import re
from typing import Final

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .drivers import FirmwareDriver

_LOGGER = logging.getLogger(__name__)

TIMEOUT: Final = aiohttp.ClientTimeout(total=5)
# How long a failed upload is not retried, in seconds.
RETRY_AFTER: Final = 300
# Names of the icon files the preview may read, and their size limit.
ICON_NAME_PATTERN: Final = r"^[A-Za-z0-9_-]{1,64}$"
ICON_NAME_RE: Final = re.compile(ICON_NAME_PATTERN)
ICON_EXTENSIONS: Final = ("gif", "jpg")
MAX_ICON_FILE_BYTES: Final = 64 * 1024
# How long the /ICONS listing is trusted before it is read again, in seconds.
LISTING_MAX_AGE: Final = 180
# Longest user name and password accepted for the clock's web page.
MAX_CREDENTIAL_LENGTH: Final = 64


def basic_auth(username: str | None, password: str | None) -> str | None:
    """Return the Authorization header for the clock's web page.

    None without a user name, or with one Basic auth cannot carry (a `:`).
    UTF-8, as typed on the clock's settings page.
    """
    if not username or not isinstance(username, str) or ":" in username:
        return None
    password = password if isinstance(password, str) else ""
    if (encode := getattr(aiohttp, "encode_basic_auth", None)) is not None:
        return encode(username, password, encoding="utf-8")
    # Older aiohttp, without encode_basic_auth() (BasicAuth is deprecated).
    return aiohttp.BasicAuth(username, password, encoding="utf-8").encode()


def _unauthorized(err: Exception) -> bool:
    return (
        isinstance(err, aiohttp.ClientResponseError)
        and err.status == HTTPStatus.UNAUTHORIZED
    )


def upload_form(path: str, data: bytes) -> aiohttp.FormData:
    """The multipart form of the clock's file manager.

    The file name is the destination path and must reach the clock as is:
    aiohttp would otherwise percent-encode its slashes.
    """
    form = aiohttp.FormData(quote_fields=False)
    form.add_field("data", data, filename=path, content_type="application/octet-stream")
    return form


async def read_limited(stream: aiohttp.StreamReader, limit: int) -> bytes:
    """Read a whole body, refusing it beyond `limit` bytes.

    `StreamReader.read(n)` only returns what has arrived so far: a file sent
    in several TCP segments would be cut short.
    """
    data = bytearray()
    async for chunk in stream.iter_chunked(16 * 1024):
        data += chunk
        if len(data) > limit:
            raise ValueError("file too large")
    return bytes(data)


class ClockFiles:
    """Icon files of one clock."""

    def __init__(self, hass: HomeAssistant, driver: FirmwareDriver) -> None:
        """Initialize (the address is learned from the clock's stats)."""
        self.hass = hass
        self.driver = driver
        self.address: str | None = None
        self._present: set[str] | None = None
        self._listed_at = 0.0
        # The Authorization header sent with every request, if any.
        self._auth: str | None = None
        self._failed_at: dict[str, float] = {}
        self._list_failed_at: float | None = None
        self._warned = False
        self._warned_auth = False

    @property
    def supported(self) -> bool:
        """Return True if icons can be stored on this clock."""
        return (
            self.address is not None
            and self.driver.icon_file("x") is not None
            and self.driver.files_path is not None
            and self.driver.upload_path is not None
        )

    def set_address(self, address: str | None) -> bool:
        """Remember the clock's address; return True if it changed."""
        if address == self.address:
            return False
        self.address = address
        self._forget()
        return True

    @property
    def has_credentials(self) -> bool:
        """Return True if requests carry a user name and password."""
        return self._auth is not None

    def set_credentials(self, username: str | None, password: str | None) -> None:
        """Use these credentials for the clock's web page (None: no password)."""
        auth = basic_auth(username, password)
        if auth == self._auth:
            return
        self._auth = auth
        # Requests refused with the old credentials may now succeed.
        self._forget()
        self._warned = False
        self._warned_auth = False

    def forget_listing(self) -> None:
        """Read the icon files again when next needed (the clock restarted)."""
        self._present = None

    def _forget(self) -> None:
        self._present = None
        self._failed_at.clear()
        self._list_failed_at = None

    def _headers(self) -> dict[str, str]:
        return {} if self._auth is None else {"Authorization": self._auth}

    def _listing_fresh(self) -> bool:
        return (
            self._present is not None
            and self.hass.loop.time() - self._listed_at < LISTING_MAX_AGE
        )

    async def _async_refresh_listing(self) -> set[str]:
        """Return the /ICONS listing, read again if it is too old."""
        if self._present is None or not self._listing_fresh():
            self._present = await self._async_list()
            self._listed_at = self.hass.loop.time()
        return self._present

    def _warn(self, err: Exception) -> None:
        """Warn once that the clock's files cannot be used, and why."""
        if _unauthorized(err):
            self._warn_unauthorized()
            return
        if self._warned:
            return
        self._warned = True
        _LOGGER.warning(
            "Cannot store animated icons on the clock at %s (%s); their "
            "first image is shown instead",
            self.address,
            err,
        )

    def _warn_unauthorized(self) -> None:
        """Warn once that the clock's web page wants (other) credentials."""
        if self._warned_auth:
            return
        self._warned_auth = True
        _LOGGER.warning(
            "The web page of the clock at %s %s: animated icons show their first "
            "image and the app order is not applied. Set the user name and "
            "password of the clock's web page by reconfiguring the clock in "
            "Awtrix Studio",
            self.address,
            "refused the user name and password"
            if self._auth is not None
            else "asks for a password",
        )

    def _check(self, response: aiohttp.ClientResponse) -> None:
        """Raise for an error status, warning once about a password."""
        if response.status == HTTPStatus.UNAUTHORIZED:
            self._warn_unauthorized()
        response.raise_for_status()

    async def async_has(self, name: str) -> bool | None:
        """Return True if an icon file `<name>.*` is on the clock.

        None when the clock's files cannot be listed.
        """
        if not self.supported:
            return None
        if not self._listing_fresh():
            failed_at = self._list_failed_at
            if (
                failed_at is not None
                and self.hass.loop.time() - failed_at < RETRY_AFTER
            ):
                return None
        try:
            present = await self._async_refresh_listing()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            self._list_failed_at = self.hass.loop.time()
            # Other failures only mean the icon is sent inline: no warning.
            if _unauthorized(err):
                self._warn(err)
            return None
        self._list_failed_at = None
        return any(filename.rsplit(".", 1)[0] == name for filename in present)

    async def async_ensure(self, name: str, data: bytes, extension: str) -> bool:
        """Make sure `<name>.<extension>` is on the clock; return True if it is."""
        if not self.supported:
            return False
        filename = f"{name}.{extension}"
        failed_at = self._failed_at.get(filename)
        if failed_at is not None and self.hass.loop.time() - failed_at < RETRY_AFTER:
            return False
        try:
            present = await self._async_refresh_listing()
            if filename in present:
                return True
            await self._async_upload(filename, data)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            self._failed_at[filename] = self.hass.loop.time()
            self._warn(err)
            return False
        present.add(filename)
        return True

    def _url(self, path: str) -> str:
        return f"http://{self.address}{path}"

    async def _async_list(self) -> set[str]:
        folder = self.driver.icon_file("").rstrip("/")
        session = async_get_clientsession(self.hass)
        async with session.get(
            self._url(self.driver.files_path),
            params={"dir": folder},
            headers=self._headers(),
            timeout=TIMEOUT,
        ) as response:
            response.raise_for_status()
            names = self.driver.parse_files(await response.text())
        if names is None:
            raise ValueError("unexpected file list")
        return names

    async def _async_upload(self, filename: str, data: bytes) -> None:
        form = upload_form(self.driver.icon_file(filename), data)
        session = async_get_clientsession(self.hass)
        async with session.post(
            self._url(self.driver.upload_path),
            data=form,
            headers=self._headers(),
            timeout=TIMEOUT,
        ) as response:
            response.raise_for_status()
        _LOGGER.debug("Stored icon %s on the clock at %s", filename, self.address)

    async def async_get_text(self, path: str) -> str:
        """GET a path of the clock's web server and return the body."""
        if self.address is None:
            raise ValueError("address unknown")
        session = async_get_clientsession(self.hass)
        async with session.get(
            self._url(path), headers=self._headers(), timeout=TIMEOUT
        ) as response:
            self._check(response)
            return await response.text()

    async def async_post_json(self, path: str, body: object) -> None:
        """POST a JSON body to a path of the clock's web server."""
        if self.address is None:
            raise ValueError("address unknown")
        session = async_get_clientsession(self.hass)
        async with session.post(
            self._url(path),
            data=json.dumps(body, ensure_ascii=False),
            headers={**self._headers(), "Content-Type": "application/json"},
            timeout=TIMEOUT,
        ) as response:
            self._check(response)

    async def async_read_icon(self, name: str) -> bytes | None:
        """Return the icon file `<name>.*` of the clock; None if there is none.

        The listing is read again, so that a file added since shows up, and
        only a file it names is fetched. Raises when the clock cannot be read.
        The fresh listing also replaces the one kept for app icons.
        """
        if not self.supported:
            raise ValueError("icon files not supported")
        if not ICON_NAME_RE.fullmatch(name):
            return None
        present = await self._async_list()
        self._present, self._listed_at = set(present), self.hass.loop.time()
        filename = next(
            (
                f"{name}.{extension}"
                for extension in ICON_EXTENSIONS
                if f"{name}.{extension}" in present
            ),
            None,
        )
        if filename is None:
            return None
        session = async_get_clientsession(self.hass)
        async with session.get(
            self._url(self.driver.icon_file(filename)),
            headers=self._headers(),
            timeout=TIMEOUT,
        ) as response:
            response.raise_for_status()
            return await read_limited(response.content, MAX_ICON_FILE_BYTES)
