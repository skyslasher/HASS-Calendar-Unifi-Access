"""Home-Assistant-Integration für UniFi-Access-Gäste aus Kalendern."""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, SCAN_INTERVAL, SERVICE_DOWNLOAD_QR, SERVICE_SYNC
from .sync import async_download_qr_code, async_sync_entry

_LOGGER = logging.getLogger(__name__)
_SYNC_LOCK = asyncio.Lock()


async def _run_sync(hass: HomeAssistant, entry: ConfigEntry) -> None:
    if _SYNC_LOCK.locked():
        _LOGGER.warning("Abgleich läuft bereits")
        return
    async with _SYNC_LOCK:
        await async_sync_entry(hass, entry)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Integration starten und den Abgleich regelmäßig ausführen."""

    async def _scheduled(_now) -> None:
        await _run_sync(hass, entry)

    entry.async_on_unload(async_track_time_interval(hass, _scheduled, SCAN_INTERVAL))
    entry.async_on_unload(entry.add_update_listener(_reload_entry))
    entry.async_create_background_task(
        hass, _run_sync(hass, entry), name="unifi_guest_sync"
    )

    async def _handle_sync(_call: ServiceCall) -> None:
        await _run_sync(hass, entry)

    async def _handle_download(call: ServiceCall) -> dict:
        return await async_download_qr_code(
            hass,
            entry,
            visitor_id=call.data.get("visitor_id"),
            calendar_entity=call.data.get("calendar_entity"),
            event_uid=call.data.get("event_uid"),
            publish=call.data.get("publish", False),
        )

    hass.services.async_register(DOMAIN, SERVICE_SYNC, _handle_sync)
    hass.services.async_register(
        DOMAIN,
        SERVICE_DOWNLOAD_QR,
        _handle_download,
        schema=vol.Schema(
            {
                vol.Optional("visitor_id"): cv.string,
                vol.Optional("calendar_entity"): cv.entity_id,
                vol.Optional("event_uid"): cv.string,
                vol.Optional("publish", default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Integration anhalten."""
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_SYNC)
        hass.services.async_remove(DOMAIN, SERVICE_DOWNLOAD_QR)
    return True


async def _reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
