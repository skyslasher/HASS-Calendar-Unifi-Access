"""Home-Assistant-Integration für UniFi-Access-Gäste aus Kalendern."""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_CALENDARS,
    CONF_DOOR_GROUP_ID,
    CONF_ENTITY,
    CONF_HORIZON_DAYS,
    CONF_HOST,
    CONF_TOKEN,
    DEFAULT_HORIZON_DAYS,
    DOMAIN,
    SCAN_INTERVAL,
    SERVICE_DOWNLOAD_QR,
    SERVICE_SYNC,
    SUBENTRY_CALENDAR,
)
from .sync import async_download_qr_code, async_sync_entry, settings_from_entry

_LOGGER = logging.getLogger(__name__)
_SYNC_LOCK = asyncio.Lock()


async def _run_sync(hass: HomeAssistant, entry: ConfigEntry) -> None:
    if _SYNC_LOCK.locked():
        _LOGGER.warning("Abgleich läuft bereits")
        return
    async with _SYNC_LOCK:
        await async_sync_entry(hass, entry)


def _migrate_calendar_subentries(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Frühere Kalenderliste in Einträge auf der Integrationsseite übernehmen."""
    if not hasattr(entry, "subentries"):
        return
    raw = {**entry.data, **entry.options}
    settings = settings_from_entry(entry)
    known = {
        subentry.unique_id or subentry.data.get(CONF_ENTITY)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_CALENDAR
    }
    for calendar in raw.get(CONF_CALENDARS) or []:
        entity = calendar.get(CONF_ENTITY)
        door_group_id = calendar.get(CONF_DOOR_GROUP_ID)
        if not entity or not door_group_id or entity in known:
            continue
        hass.config_entries.async_add_subentry(
            entry,
            ConfigSubentry(
                data={CONF_ENTITY: entity, CONF_DOOR_GROUP_ID: door_group_id},
                subentry_type=SUBENTRY_CALENDAR,
                title=entity,
                unique_id=entity,
            ),
        )
        known.add(entity)
    new_data = {
        CONF_HOST: settings[CONF_HOST],
        CONF_TOKEN: settings[CONF_TOKEN],
        CONF_HORIZON_DAYS: settings.get(CONF_HORIZON_DAYS, DEFAULT_HORIZON_DAYS),
        CONF_CALENDARS: [],
    }
    if dict(entry.data) != new_data or dict(entry.options):
        hass.config_entries.async_update_entry(entry, data=new_data, options={})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Integration starten und den Abgleich regelmäßig ausführen."""
    _migrate_calendar_subentries(hass, entry)

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
