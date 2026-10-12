"""Kalendertermine mit Besuchern in UniFi Access abgleichen."""

from __future__ import annotations

import json
import logging
import re
import ssl
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CALENDARS,
    CONF_DOOR_GROUP_ID,
    CONF_ENTITY,
    CONF_HORIZON_DAYS,
    CONF_HOST,
    CONF_TOKEN,
    SUBENTRY_CALENDAR,
    DEFAULT_ARRIVAL_TIME,
    DEFAULT_DEPARTURE_TIME,
    KEYWORD_ARRIVO,
    KEYWORD_CONFIRMED,
    KEYWORD_PARTENZA,
    KEYWORD_PIN,
    KEYWORD_PRIVAT,
    KEYWORD_STATUS,
    PIN_LENGTH,
    PREFIX,
    QR_DIRECTORY,
    STORAGE_KEY,
    STORAGE_VERSION,
    SUMMARY_SEPARATOR,
)

_LOGGER = logging.getLogger(__name__)

def _line_pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(rf"(?im)^\s*{re.escape(keyword)}\s*[:=]\s*(.*?)\s*$")


def _flag_pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(rf"(?im)^\s*{re.escape(keyword)}\s*$")


PIN_RE = _line_pattern(KEYWORD_PIN)
STATUS_RE = _line_pattern(KEYWORD_STATUS)
ARRIVO_RE = _line_pattern(KEYWORD_ARRIVO)
PARTENZA_RE = _line_pattern(KEYWORD_PARTENZA)
PRIVAT_RE = _flag_pattern(KEYWORD_PRIVAT)
_VISITOR_ID = re.compile(r"^[0-9a-fA-F-]{16,64}$")


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_DOOR_GROUP_TYPES = {"access", "building", "door_group"}


def door_groups_from_payload(data) -> list[dict[str, str]]:
    """Türgruppen mit id und name aus einer Listen-Antwort lesen."""
    if isinstance(data, dict):
        for key in ("hits", "list", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return []
    groups = []
    for item in data:
        if not isinstance(item, dict):
            continue
        group_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or item.get("group_name") or "").strip()
        if group_id and name:
            groups.append({"id": group_id, "name": name})
    return groups


def door_groups_from_topology(data) -> list[dict[str, str]]:
    """Türgruppen aus der Topologie lesen, Etagen und einzelne Türen auslassen."""
    nodes = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    found: list[dict[str, str]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if str(node.get("type") or "") in _DOOR_GROUP_TYPES:
            found.extend(door_groups_from_payload([node]))
        nested = node.get("resource_topologies")
        if nested:
            found.extend(door_groups_from_topology(nested))
    return found


def merge_door_groups(*collections: list[dict[str, str]]) -> list[dict[str, str]]:
    """Gruppen nach ID zusammenführen. Der erste Name einer ID bleibt erhalten."""
    by_id: dict[str, dict[str, str]] = {}
    for groups in collections:
        for item in groups:
            by_id.setdefault(item["id"], item)
    return sorted(by_id.values(), key=lambda item: item["name"].casefold())


class AccessClient:
    """Lokale UniFi-Access-API auf Port 12445."""

    def __init__(self, host: str, token: str) -> None:
        self._host = host
        self._token = token

    async def request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"https://{self._host}:12445{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method, url, headers=headers, json=body, ssl=_ssl_context()
            ) as resp:
                text = await resp.text()
                try:
                    payload = json.loads(text) if text else {}
                except json.JSONDecodeError:
                    payload = {"raw": text}
                if resp.status >= 300 or payload.get("code") not in (None, "SUCCESS"):
                    raise RuntimeError(f"{method} {path} -> {resp.status} {text[:500]}")
                return payload

    async def request_bytes(self, method: str, path: str) -> bytes:
        url = f"https://{self._host}:12445{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "*/*",
        }
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, url, headers=headers, ssl=_ssl_context()) as resp:
                data = await resp.read()
                if resp.status >= 300 or data[:1] in (b"{", b"["):
                    preview = data[:500].decode(errors="replace")
                    raise RuntimeError(f"{method} {path} -> {resp.status} {preview}")
                return data


def _normalize(text: str) -> str:
    text = text or ""
    text = re.sub(r"\r?\n[ \t]", "", text)
    return (
        text.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
    )


def _field(text: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1).strip()


def _guest_name(summary: str) -> str:
    name, _, _rest = (summary or "").strip().partition(SUMMARY_SEPARATOR)
    return name.strip()


def _split_name(summary: str) -> tuple[str | None, str | None]:
    parts = _guest_name(summary).split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], "-"
    return parts[0], " ".join(parts[1:])


def _is_private(text: str) -> bool:
    return PRIVAT_RE.search(text) is not None


def _pin_value(text: str) -> str | None:
    raw = _field(text, PIN_RE)
    if raw is None or raw == "":
        return None
    minimum, maximum = PIN_LENGTH
    if re.fullmatch(rf"\d{{{minimum},{maximum}}}", raw):
        return raw
    return None


def _is_confirmed(text: str) -> bool:
    raw = _field(text, STATUS_RE)
    if raw is None:
        return True
    return raw.lower() == KEYWORD_CONFIRMED.lower()


def _clock(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def _resolve_clock(
    text: str, pattern: re.Pattern[str], default: str
) -> tuple[tuple[int, int] | None, str | None]:
    raw = _field(text, pattern)
    if raw is None or raw == "":
        return _clock(default), None
    parsed = _clock(raw)
    if parsed is None:
        return None, raw
    return parsed, None


def _event_datetime(value) -> datetime:
    if isinstance(value, dict):
        if value.get("dateTime"):
            return _event_datetime(value["dateTime"])
        if value.get("date"):
            return _event_datetime(value["date"])
        raise ValueError(f"Zeitpunkt nicht lesbar: {value}")
    if isinstance(value, datetime):
        moment = value
    else:
        raw = str(value).strip()
        if len(raw) == 10:
            moment = datetime.fromisoformat(raw)
        else:
            moment = dt_util.parse_datetime(raw)
            if moment is None:
                raise ValueError(f"Zeitpunkt nicht lesbar: {raw}")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt_util.now().tzinfo)
    return moment


def _access_window(event: dict, text: str) -> tuple[datetime, datetime]:
    arrival, arrival_raw = _resolve_clock(text, ARRIVO_RE, DEFAULT_ARRIVAL_TIME)
    departure, departure_raw = _resolve_clock(text, PARTENZA_RE, DEFAULT_DEPARTURE_TIME)
    if arrival is None:
        raise ValueError(f"{KEYWORD_ARRIVO} ist keine Uhrzeit: {arrival_raw}")
    if departure is None:
        raise ValueError(f"{KEYWORD_PARTENZA} ist keine Uhrzeit: {departure_raw}")
    start = _event_datetime(event["start"]).replace(
        hour=arrival[0], minute=arrival[1], second=0, microsecond=0
    )
    end = _event_datetime(event["end"]).replace(
        hour=departure[0], minute=departure[1], second=0, microsecond=0
    )
    if end <= start:
        raise ValueError("Zugangsende liegt vor dem Zugangsbeginn")
    return start, end


def _event_key(event: dict) -> str | None:
    uid = event.get("uid")
    if not uid:
        return None
    recurrence = event.get("recurrence_id")
    return f"{uid}#{recurrence}" if recurrence else uid


def _prefix(entity: str) -> str:
    return f"{PREFIX}{entity}:"


def _parse_events(response: dict, entity_id: str, now: datetime):
    if entity_id not in response:
        raise RuntimeError(f"{entity_id}: Kalenderantwort ohne Terminliste")
    block = response.get(entity_id) or {}
    found = []
    keep_keys = set()
    for event in block.get("events") or []:
        summary = event.get("summary")
        key = _event_key(event)
        if not key:
            _LOGGER.warning("%s: Termin ohne UID übersprungen: %s", entity_id, summary)
            continue
        text = _normalize(event.get("description") or "")
        if not _is_confirmed(text):
            _LOGGER.info(
                "%s: Status nicht %s, Besucher wird entfernt: %s",
                entity_id,
                KEYWORD_CONFIRMED,
                summary,
            )
            continue
        pin = _pin_value(text)
        if _is_private(text) and not pin:
            _LOGGER.info(
                "%s: %s ohne %s, kein Besucher: %s",
                entity_id,
                KEYWORD_PRIVAT,
                KEYWORD_PIN,
                summary,
            )
            continue
        first, last = _split_name(summary)
        if not pin or not first:
            _LOGGER.warning(
                "%s: %s oder Name fehlt, bestehender Besucher bleibt: %s",
                entity_id,
                KEYWORD_PIN,
                summary,
            )
            keep_keys.add(key)
            continue
        try:
            start, end = _access_window(event, text)
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.warning(
                "%s: Zeitraum nicht lesbar, bestehender Besucher bleibt: %s (%s)",
                entity_id,
                summary,
                err,
            )
            keep_keys.add(key)
            continue
        if end <= now:
            _LOGGER.info("%s: Aufenthalt beendet, kein Besucher: %s", entity_id, summary)
            continue
        found.append(
            {
                "key": key,
                "first": first,
                "last": last,
                "pin": pin,
                "start": int(dt_util.as_utc(start).timestamp()),
                "end": int(dt_util.as_utc(end).timestamp()),
            }
        )
    return found, keep_keys


def _visitor_body(guest: dict, entity: str, door_group_id: str) -> dict:
    return {
        "first_name": guest["first"],
        "last_name": guest["last"],
        "start_time": guest["start"],
        "end_time": guest["end"],
        "visit_reason": "Others",
        "remarks": _prefix(entity) + guest["key"],
        "resources": [{"id": door_group_id, "type": "door_group"}],
    }


async def list_door_groups(client: AccessClient) -> list[dict[str, str]]:
    """Alle Türgruppen der Konsole laden."""
    listed = await client.request("GET", "/api/v1/developer/door_groups")
    groups = door_groups_from_payload(listed.get("data"))
    try:
        topology = await client.request("GET", "/api/v1/developer/door_groups/topology")
    except Exception:
        _LOGGER.debug("Türgruppen-Topologie nicht verfügbar", exc_info=True)
        topology = {}
    return merge_door_groups(groups, door_groups_from_topology(topology.get("data")))


async def _list_visitors(client: AccessClient) -> list:
    page = 1
    visitors = []
    while True:
        payload = await client.request(
            "GET", f"/api/v1/developer/visitors?page_num={page}&page_size=100"
        )
        batch = payload.get("data") or []
        if isinstance(batch, dict):
            batch = batch.get("hits") or batch.get("list") or []
        visitors.extend(batch)
        total = (payload.get("pagination") or {}).get("total")
        if not batch or (total is not None and page * 100 >= int(total)):
            return visitors
        page += 1


async def _assign_pin(client: AccessClient, visitor_id: str, pin: str) -> None:
    try:
        await client.request("DELETE", f"/api/v1/developer/visitors/{visitor_id}/pin_codes")
    except Exception:
        pass
    await client.request(
        "PUT",
        f"/api/v1/developer/visitors/{visitor_id}/pin_codes",
        {"pin_code": pin},
    )


async def _sync_calendar(hass, client, calendar, visitors, state, now, window_end) -> None:
    entity = calendar[CONF_ENTITY]
    door_group_id = calendar[CONF_DOOR_GROUP_ID]
    if not entity or not door_group_id:
        _LOGGER.error("Kalender ohne Entität oder Türgruppe: %s", calendar)
        return

    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": entity,
            "start_date_time": now,
            "end_date_time": window_end,
        },
        blocking=True,
        return_response=True,
    )
    guests, keep_keys = _parse_events(response, entity, now)
    by_key = {guest["key"]: guest for guest in guests}
    prefix = _prefix(entity)
    managed = {}
    for visitor in visitors:
        remarks = visitor.get("remarks") or ""
        if remarks.startswith(prefix):
            managed[remarks[len(prefix) :]] = visitor

    cal_state = state.setdefault(entity, {})
    for key, guest in by_key.items():
        fingerprint = (
            f"{guest['first']}|{guest['last']}|{guest['start']}|"
            f"{guest['end']}|{guest['pin']}|{door_group_id}"
        )
        visitor = managed.get(key)
        previous = cal_state.get(key) or {}
        try:
            if visitor is None:
                created = await client.request(
                    "POST",
                    "/api/v1/developer/visitors",
                    _visitor_body(guest, entity, door_group_id),
                )
                visitor_id = created["data"]["id"]
                await _assign_pin(client, visitor_id, guest["pin"])
                _LOGGER.info(
                    "%s: Besucher angelegt: %s %s", entity, guest["first"], guest["last"]
                )
            else:
                visitor_id = visitor["id"]
                if previous.get("fingerprint") != fingerprint:
                    await client.request(
                        "PUT",
                        f"/api/v1/developer/visitors/{visitor_id}",
                        _visitor_body(guest, entity, door_group_id),
                    )
                    if previous.get("pin") != guest["pin"]:
                        await _assign_pin(client, visitor_id, guest["pin"])
                    _LOGGER.info(
                        "%s: Besucher aktualisiert: %s %s",
                        entity,
                        guest["first"],
                        guest["last"],
                    )
            cal_state[key] = {
                "visitor_id": visitor_id,
                "pin": guest["pin"],
                "fingerprint": fingerprint,
            }
        except Exception as err:
            _LOGGER.error(
                "%s: Sync fehlgeschlagen für %s %s: %s",
                entity,
                guest["first"],
                guest["last"],
                err,
            )

    now_ts = int(now.timestamp())
    window_end_ts = int(window_end.timestamp())
    for key, visitor in managed.items():
        if key in by_key:
            continue
        end = int(visitor.get("end_time") or 0)
        start = int(visitor.get("start_time") or 0)
        ended = end > 0 and end <= now_ts
        if key in keep_keys and not ended:
            continue
        beyond_horizon = start >= window_end_ts and end >= window_end_ts
        if beyond_horizon and not ended:
            continue
        reason = "Aufenthalt beendet" if ended else "Termin ohne Zugang"
        try:
            await client.request(
                "DELETE",
                f"/api/v1/developer/visitors/{visitor['id']}?is_force=true",
            )
            cal_state.pop(key, None)
            _delete_qr_files(hass, visitor["id"])
            _LOGGER.info(
                "%s: Besucher gelöscht (%s): %s %s",
                entity,
                reason,
                visitor.get("first_name"),
                visitor.get("last_name"),
            )
        except Exception as err:
            _LOGGER.error("%s: Löschen fehlgeschlagen für %s: %s", entity, key, err)

    for key in list(cal_state):
        if key not in by_key and key not in managed and key not in keep_keys:
            cal_state.pop(key, None)


async def _load_state(hass: HomeAssistant, store: Store) -> dict:
    stored = await store.async_load()
    if isinstance(stored, dict):
        return stored
    legacy = Path(hass.config.path("unifi_guest_sync_state.json"))
    if legacy.exists():
        loaded = json.loads(legacy.read_text())
        if isinstance(loaded, dict):
            return loaded
    return {}


def settings_from_entry(entry) -> dict:
    """Konfiguration aus Ersteinrichtung und Kalender-Einträgen zusammenführen."""
    settings = {**entry.data, **entry.options}
    links = []
    for subentry in getattr(entry, "subentries", {}).values():
        if subentry.subentry_type != SUBENTRY_CALENDAR:
            continue
        entity = subentry.data.get(CONF_ENTITY)
        door_group_id = subentry.data.get(CONF_DOOR_GROUP_ID)
        if entity and door_group_id:
            links.append({CONF_ENTITY: entity, CONF_DOOR_GROUP_ID: door_group_id})
    if not links:
        links = list(settings.get(CONF_CALENDARS) or [])
    settings[CONF_CALENDARS] = links
    return settings


def _qr_directories(hass: HomeAssistant) -> tuple[Path, Path]:
    return (
        Path(hass.config.path(QR_DIRECTORY)),
        Path(hass.config.path(f"www/{QR_DIRECTORY}")),
    )


def _delete_qr_files(hass: HomeAssistant, visitor_id: str) -> None:
    if not _VISITOR_ID.fullmatch(visitor_id or ""):
        return
    for directory in _qr_directories(hass):
        for path in directory.glob(f"{visitor_id}.*"):
            path.unlink(missing_ok=True)


def _image_extension(image: bytes) -> str:
    if image.startswith(b"\x89PNG"):
        return "png"
    if image.startswith(b"\xff\xd8"):
        return "jpg"
    return "png"


def _write_qr_file(directory: Path, visitor_id: str, extension: str, image: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{visitor_id}.{extension}"
    temporary = target.with_suffix(f".{extension}.tmp")
    temporary.write_bytes(image)
    temporary.replace(target)
    return target


async def async_download_qr_code(
    hass: HomeAssistant,
    entry,
    *,
    visitor_id: str | None = None,
    calendar_entity: str | None = None,
    event_uid: str | None = None,
    publish: bool = False,
) -> dict:
    """QR-Code eines Besuchers laden und als Bilddatei speichern."""
    settings = settings_from_entry(entry)
    client = AccessClient(settings[CONF_HOST], settings[CONF_TOKEN])
    visitors = await _list_visitors(client)
    visitor_id = (visitor_id or "").strip() or None
    calendar_entity = (calendar_entity or "").strip() or None
    event_uid = (event_uid or "").strip() or None

    if visitor_id:
        visitor = next((item for item in visitors if item.get("id") == visitor_id), None)
    elif calendar_entity and event_uid:
        remarks = _prefix(calendar_entity) + event_uid
        visitor = next((item for item in visitors if (item.get("remarks") or "") == remarks), None)
    else:
        raise HomeAssistantError(
            "visitor_id oder calendar_entity zusammen mit event_uid angeben"
        )
    if visitor is None:
        raise HomeAssistantError("Kein passender Besucher in UniFi Access")

    visitor_id = visitor["id"]
    if not _VISITOR_ID.fullmatch(visitor_id):
        raise HomeAssistantError("Die Besucher-ID ist ungültig")

    try:
        await client.request("PUT", f"/api/v1/developer/visitors/{visitor_id}/qr_codes")
    except Exception as err:
        _LOGGER.debug("QR-Code war bereits zugeordnet oder die Zuweisung meldet: %s", err)

    try:
        image = await client.request_bytes(
            "GET", f"/api/v1/developer/credentials/qr_codes/download/{visitor_id}"
        )
    except Exception as err:
        raise HomeAssistantError(
            "QR-Code konnte nicht geladen werden. Der Leser muss QR-Codes unterstützen "
            f"und Access mindestens 3.3.10 sein. {err}"
        ) from err

    extension = _image_extension(image)
    private = _write_qr_file(_qr_directories(hass)[0], visitor_id, extension, image)
    local_url = None
    if publish:
        _write_qr_file(_qr_directories(hass)[1], visitor_id, extension, image)
        local_url = f"/local/{QR_DIRECTORY}/{visitor_id}.{extension}"

    return {
        "visitor_id": visitor_id,
        "first_name": visitor.get("first_name"),
        "last_name": visitor.get("last_name"),
        "path": str(private),
        "filename": private.name,
        "local_url": local_url,
    }


def _entity_from_remarks(remarks: str) -> str | None:
    if not remarks.startswith(PREFIX):
        return None
    entity, separator, _key = remarks[len(PREFIX) :].partition(":")
    if not separator or not entity:
        return None
    return entity


async def _remove_unconfigured(
    hass: HomeAssistant, client: AccessClient, visitors: list, state: dict, configured: set[str]
) -> None:
    """Besucher löschen, deren Kalender nicht mehr eingerichtet ist."""
    failed: set[str] = set()
    removed: set[str] = set()
    for visitor in visitors:
        entity = _entity_from_remarks(visitor.get("remarks") or "")
        if entity is None or entity in configured:
            continue
        try:
            await client.request(
                "DELETE",
                f"/api/v1/developer/visitors/{visitor['id']}?is_force=true",
            )
            removed.add(entity)
            _delete_qr_files(hass, visitor["id"])
            _LOGGER.info(
                "%s: Besucher gelöscht (Kalender entfernt): %s %s",
                entity,
                visitor.get("first_name"),
                visitor.get("last_name"),
            )
        except Exception as err:
            failed.add(entity)
            _LOGGER.error("%s: Löschen fehlgeschlagen für entfernten Kalender: %s", entity, err)
    for entity in removed - failed:
        state.pop(entity, None)


async def async_sync_entry(hass: HomeAssistant, entry) -> None:
    """Einen Konfigurationseintrag vollständig abgleichen."""
    settings = settings_from_entry(entry)
    client = AccessClient(settings[CONF_HOST], settings[CONF_TOKEN])
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    now = dt_util.now()
    window_end = now + timedelta(days=int(settings[CONF_HORIZON_DAYS]))
    visitors = await _list_visitors(client)
    state = await _load_state(hass, store)
    for calendar in settings[CONF_CALENDARS]:
        try:
            await _sync_calendar(hass, client, calendar, visitors, state, now, window_end)
        except Exception as err:
            _LOGGER.error(
                "%s: Kalender-Sync fehlgeschlagen: %s",
                calendar.get(CONF_ENTITY),
                err,
            )
    configured = {
        calendar[CONF_ENTITY]
        for calendar in settings[CONF_CALENDARS]
        if calendar.get(CONF_ENTITY)
    }
    known = set(state.get("_managed_calendars") or [])
    if configured or known:
        await _remove_unconfigured(hass, client, visitors, state, configured)
    state["_managed_calendars"] = sorted(configured)
    await store.async_save(state)
