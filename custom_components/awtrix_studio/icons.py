"""Icons from the LaMetric gallery, served by Awtrix Studio.

An app can name its icon `lametric:<id>`. Awtrix Studio downloads that icon
once, keeps it in Home Assistant's storage and sends it inline with the app
(an 8x8 JPEG in Base64 for AWTRIX 3, a data URL for AWTRIX NG). Nothing has to
be uploaded to the clock first. AWTRIX 3 can only play animated icons from its
memory: they are stored there automatically (see clock_files), with their
first image as a fallback. AWTRIX NG plays an animated GIF sent inline.

In a layout (AWTRIX NG on a TC002), an icon whose box is at least 16x16 is
sent at double size, as a 16x16 GIF: next to text drawn at full resolution,
an 8x8 icon would look tiny. An animated icon stays animated when the payload
leaves room for it.

An icon named by a bare number (`2422`, as AWTRIX's own icon downloader names
its files) is a file of the clock when the clock has one by that name, and the
LaMetric icon of that number otherwise. Other names keep their usual meaning:
a file stored on the clock.
"""

from __future__ import annotations

import asyncio
import base64
from io import BytesIO
import json
import logging
import re
from typing import Any, Final

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from PIL import Image, ImageSequence

from .clock_files import ClockFiles
from .const import DOMAIN
from .drivers import FirmwareDriver

_LOGGER = logging.getLogger(__name__)

ICON_RE: Final = re.compile(r"^lametric:(\d{1,7})$")
NUMBER_RE: Final = re.compile(r"^\d{1,7}$")
SEARCH_URL: Final = "https://developer.lametric.com/api/v1/dev/preloadicons"
ICON_URL: Final = "https://developer.lametric.com/content/apps/icon_thumbs/{id}"
STORAGE_VERSION: Final = 1
MAX_ICON_BYTES: Final = 64 * 1024
TIMEOUT: Final = aiohttp.ClientTimeout(total=10)
PAGE_SIZE: Final = 48
SIZE: Final = 8
# Only these decoders ever see an icon file, and only up to this size, read
# from the header before anything is decoded (icons are 8x8).
FORMATS: Final = ("GIF", "JPEG", "PNG")
MAX_SIDE: Final = 64
# An icon stored on the clock at this size fills a TC002's icon area at full
# resolution: the preview draws it as is.
LARGE: Final = 16
# Layout boxes at least this large get the icon at double size.
DOUBLE_BOX: Final = 16
# How long a failed download is not retried, in seconds.
RETRY_AFTER: Final = 300


class IconError(Exception):
    """An icon that could not be downloaded or read."""


def icon_id(value: Any) -> int | None:
    """Return the LaMetric id of a `lametric:<id>` icon, else None."""
    if isinstance(value, str) and (match := ICON_RE.fullmatch(value)):
        return int(match.group(1))
    return None


def _first_frame(data: bytes) -> tuple[Image.Image, bool, str]:
    """Return an icon's first image, whether it is animated, and its format.

    Other formats and larger images are refused unread.
    """
    try:
        with Image.open(BytesIO(data), formats=FORMATS) as image:
            if image.width > MAX_SIDE or image.height > MAX_SIDE:
                raise IconError(f"image too large: {image.width}x{image.height}")
            animated = getattr(image, "n_frames", 1) > 1
            file_format = (image.format or "").lower()
            image.seek(0)
            return image.convert("RGBA"), animated, file_format
    except (OSError, ValueError, Image.DecompressionBombError) as err:
        raise IconError(f"unreadable icon: {err}") from err


def _rgb(frame: Image.Image) -> Image.Image:
    """Transparent pixels become black, the clock's "off"."""
    background = Image.new("RGBA", frame.size, (0, 0, 0, 255))
    return Image.alpha_composite(background, frame).convert("RGB")


def _pixels(rgb: Image.Image) -> list[int]:
    raw = rgb.tobytes()
    return [
        (raw[i] << 16) | (raw[i + 1] << 8) | raw[i + 2] for i in range(0, len(raw), 3)
    ]


def large_pixels(data: bytes) -> list[int] | None:
    """Return the 16x16 pixels of an icon of that size, else None.

    Runs in the executor. Only the first image.
    """
    frame, _, _ = _first_frame(data)
    if frame.size != (LARGE, LARGE):
        return None
    return _pixels(_rgb(frame))


def convert(data: bytes) -> dict[str, Any]:
    """Turn a LaMetric icon file into pixels and an 8x8 Base64 JPEG.

    Runs in the executor (Pillow is blocking). Transparent pixels become black,
    the clock's "off". Other formats and larger images are refused unread.
    """
    frame, animated, file_format = _first_frame(data)
    if frame.size != (SIZE, SIZE):
        frame = frame.resize((SIZE, SIZE), Image.Resampling.NEAREST)
    rgb = _rgb(frame)
    pixels = _pixels(rgb)
    buffer = BytesIO()
    # Full quality and no chroma subsampling: an 8x8 image is a single block.
    rgb.save(buffer, format="JPEG", quality=100, subsampling=0)
    icon = {
        "pixels": pixels,
        "jpeg": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "animated": animated,
    }
    if animated and file_format == "gif":
        # Kept to be stored on the clock, which can play it.
        icon["gif"] = base64.b64encode(data).decode("ascii")
    return icon


def _double_frame(frame: Image.Image) -> Image.Image:
    rgba = frame.convert("RGBA")
    if rgba.size != (SIZE, SIZE):
        rgba = rgba.resize((SIZE, SIZE), Image.Resampling.NEAREST)
    background = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
    rgb = Image.alpha_composite(background, rgba).convert("RGB")
    return rgb.resize((2 * SIZE, 2 * SIZE), Image.Resampling.NEAREST)


def double(icon: dict[str, Any], *, animated: bool) -> str:
    """Return an icon at double size, as a Base64 GIF (lossless, unlike JPEG).

    Runs in the executor. Only the first image unless `animated` is set and
    the icon has a GIF; its frames keep their timing.
    """
    if animated and "gif" in icon:
        with Image.open(
            BytesIO(base64.b64decode(icon["gif"])), formats=("GIF",)
        ) as image:
            frames, durations = [], []
            for frame in ImageSequence.Iterator(image):
                frames.append(_double_frame(frame))
                durations.append(frame.info.get("duration", 100))
    else:
        image = Image.new("RGB", (SIZE, SIZE))
        image.putdata([(p >> 16 & 255, p >> 8 & 255, p & 255) for p in icon["pixels"]])
        frames, durations = [_double_frame(image)], [0]
    buffer = BytesIO()
    if len(frames) > 1:
        frames[0].save(
            buffer,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
        )
    else:
        frames[0].save(buffer, format="GIF")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _double_box(region: dict[str, Any]) -> bool:
    box = region.get("box")
    return (
        isinstance(box, list)
        and len(box) == 4
        and all(isinstance(v, int) and not isinstance(v, bool) for v in box)
        and box[2] >= DOUBLE_BOX
        and box[3] >= DOUBLE_BOX
    )


class IconStore:
    """Downloaded LaMetric icons, kept in Home Assistant's storage."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the store (call async_load before use)."""
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.icons"
        )
        self._icons: dict[str, dict[str, Any]] = {}
        self._pending: dict[int, asyncio.Future[dict[str, Any] | None]] = {}
        self._failed: dict[int, float] = {}
        # Icons at double size, by (id, animated); not stored, cheap to redo.
        self._doubled: dict[tuple[int, bool], str] = {}

    async def async_load(self) -> None:
        """Read the icons downloaded earlier."""
        data = await self._store.async_load() or {}
        icons = data.get("icons", {})
        self._icons = {
            key: value
            for key, value in icons.items()
            if isinstance(value, dict)
            and isinstance(value.get("jpeg"), str)
            # Animated icons downloaded by 0.7 lack their GIF: fetch them again.
            and not (value.get("animated") and "gif" not in value)
        }

    def get(self, lametric_id: int) -> dict[str, Any] | None:
        """Return a downloaded icon, if any."""
        return self._icons.get(str(lametric_id))

    async def async_get(self, lametric_id: int) -> dict[str, Any] | None:
        """Return an icon, downloading it the first time. None if unavailable."""
        if (icon := self.get(lametric_id)) is not None:
            return icon
        if (pending := self._pending.get(lametric_id)) is not None:
            return await pending
        failed_at = self._failed.get(lametric_id)
        if failed_at is not None and self.hass.loop.time() - failed_at < RETRY_AFTER:
            return None

        future: asyncio.Future[dict[str, Any] | None] = self.hass.loop.create_future()
        self._pending[lametric_id] = future
        try:
            icon = await self._async_download(lametric_id)
        except IconError as err:
            _LOGGER.warning("LaMetric icon %s is not available: %s", lametric_id, err)
            self._failed[lametric_id] = self.hass.loop.time()
            icon = None
        else:
            self._icons[str(lametric_id)] = icon
            self._store.async_delay_save(lambda: {"icons": self._icons}, 5)
        finally:
            del self._pending[lametric_id]
        future.set_result(icon)
        return icon

    async def _async_download(self, lametric_id: int) -> dict[str, Any]:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                ICON_URL.format(id=lametric_id), timeout=TIMEOUT
            ) as response:
                if response.status != 200:
                    raise IconError(f"HTTP {response.status}")
                length = response.headers.get("Content-Length", "0")
                if length.isdigit() and int(length) > MAX_ICON_BYTES:
                    raise IconError("file too large")
                data = await response.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise IconError(str(err) or type(err).__name__) from err
        if len(data) > MAX_ICON_BYTES:
            raise IconError("file too large")
        return await self.hass.async_add_executor_job(convert, data)

    async def async_search(self, query: str, page: int) -> dict[str, Any]:
        """Search the LaMetric gallery. Only ids, names and a flag come back."""
        session = async_get_clientsession(self.hass)
        # The gallery's pages start at 0; without a query, show popular icons.
        params = {
            "page": str(page - 1),
            "category": "" if query else "Popular",
            "search": query,
            "count": str(PAGE_SIZE),
        }
        try:
            async with session.get(
                SEARCH_URL, params=params, timeout=TIMEOUT
            ) as response:
                if response.status != 200:
                    raise IconError(f"HTTP {response.status}")
                payload = json.loads(await response.text())
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise IconError(str(err) or type(err).__name__) from err

        icons = []
        for item in payload.get("icons", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict):
                continue
            lametric_id = item.get("id")
            if not isinstance(lametric_id, int) or not 0 < lametric_id < 10_000_000:
                continue
            name = item.get("name")
            icons.append(
                {
                    "id": lametric_id,
                    "name": name[:64] if isinstance(name, str) else "",
                    "animated": item.get("type") == 1,
                }
            )
        # A full page means there may be more.
        return {"icons": icons, "more": len(icons) >= PAGE_SIZE}

    async def async_resolve(
        self,
        payload: str,
        files: ClockFiles | None = None,
        driver: FirmwareDriver | None = None,
    ) -> str:
        """Replace a `lametric:<id>` icon in a page payload by the icon itself.

        A bare number names the same icon, unless the clock has a file by that
        name, or the gallery does not have it: it is then left to the clock.
        Animated icons are stored on the clock (when `files` can) and named by
        their number, so that the clock plays them. Otherwise the icon is sent
        inline, in the form the firmware takes: AWTRIX NG plays an inline GIF,
        AWTRIX 3 shows the first image. An inline GIF that would make the
        payload too large for the clock falls back to the first image.
        """
        if '"icon"' not in payload:
            return payload
        try:
            page = json.loads(payload)
        except ValueError:
            return payload
        if not isinstance(page, dict):
            return payload
        if "icon" in page:
            await self._resolve_page_icon(page, files, driver)
        layout = page.get("layout")
        if (
            driver is not None
            and isinstance(layout, dict)
            and isinstance(layout.get("regions"), list)
            and any(isinstance(r, dict) and "icon" in r for r in layout["regions"])
        ):
            await self._resolve_layout(page, layout, driver, files)
        return _dumps(page)

    async def _resolve_page_icon(
        self,
        page: dict[str, Any],
        files: ClockFiles | None,
        driver: FirmwareDriver | None,
    ) -> None:
        """Resolve the `icon` key of a page in place."""
        name = page.get("icon")
        if (lametric_id := await _gallery_id(name, files)) is None:
            return
        icon = await self.async_get(lametric_id)
        if icon is None:
            if icon_id(name) is not None:
                page.pop("icon")
            # A bare number: maybe a file the clock's listing did not show.
            return
        if (
            files is not None
            and "gif" in icon
            and await files.async_ensure(
                str(lametric_id), base64.b64decode(icon["gif"]), "gif"
            )
        ):
            page["icon"] = str(lametric_id)
        elif driver is None:
            page["icon"] = icon["jpeg"]
        else:
            page["icon"] = driver.inline_icon(icon["jpeg"], icon.get("gif"))
            limit = driver.max_payload
            if (
                "gif" in icon
                and limit is not None
                and len(_dumps(page).encode()) > limit
            ):
                page["icon"] = driver.inline_icon(icon["jpeg"], None)

    async def _resolve_layout(
        self,
        page: dict[str, Any],
        layout: dict[str, Any],
        driver: FirmwareDriver,
        files: ClockFiles | None,
    ) -> None:
        """Send the gallery icons of a layout inline, doubled in large boxes.

        Animated icons first; if the payload is then too large for the clock,
        their first image. An icon the gallery does not have leaves the layout,
        since a region shows exactly one thing.
        """
        found: dict[int, dict[str, Any] | None] = {}
        for region in layout["regions"]:
            if isinstance(region, dict) and "icon" in region:
                lametric_id = await _gallery_id(region["icon"], files)
                if lametric_id is not None and lametric_id not in found:
                    found[lametric_id] = await self.async_get(lametric_id)
        if not found:
            return
        for animated in (True, False):
            regions = []
            for region in layout["regions"]:
                resolved = region
                if isinstance(region, dict) and "icon" in region:
                    resolved = await self._resolve_region(
                        region, found, files, animated
                    )
                if resolved is not None:
                    regions.append(resolved)
            page["layout"] = {**layout, "regions": regions}
            limit = driver.max_payload
            if limit is None or len(_dumps(page).encode()) <= limit:
                return

    async def _resolve_region(
        self,
        region: dict[str, Any],
        found: dict[int, dict[str, Any] | None],
        files: ClockFiles | None,
        animated: bool,
    ) -> dict[str, Any] | None:
        name = region["icon"]
        lametric_id = await _gallery_id(name, files)
        if lametric_id is None:
            return region
        icon = found.get(lametric_id)
        if icon is None:
            return region if icon_id(name) is None else None
        if _double_box(region):
            key = (lametric_id, animated and "gif" in icon)
            if key not in self._doubled:
                self._doubled[key] = await self.hass.async_add_executor_job(
                    lambda: double(icon, animated=key[1])
                )
            return {**region, "icon": f"data:image/gif;base64,{self._doubled[key]}"}
        if animated and "gif" in icon:
            return {**region, "icon": f"data:image/gif;base64,{icon['gif']}"}
        return {**region, "icon": f"data:image/jpeg;base64,{icon['jpeg']}"}


async def _gallery_id(name: Any, files: ClockFiles | None) -> int | None:
    """Return the LaMetric id an icon name stands for, if any."""
    if (lametric_id := icon_id(name)) is not None:
        return lametric_id
    if isinstance(name, int) and not isinstance(name, bool):
        name = str(name)
    if not isinstance(name, str) or not NUMBER_RE.fullmatch(name):
        return None
    if files is not None and await files.async_has(name):
        return None
    return int(name)


def _dumps(page: dict[str, Any]) -> str:
    return json.dumps(page, ensure_ascii=False, separators=(",", ":"))
