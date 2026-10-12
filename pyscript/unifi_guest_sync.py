"""Gleich drei Kalender mit UniFi-Access-Besuchern ab.

Jeder Kalender bedient eine Türgruppe. Der Gastname steht im Titel vor
" - ". Zugangszeiten sind 15:00 am Anreisetag und 11:00 am Abreisetag,
sofern Arrivo und Partenza nichts anderes setzen. Eine Beschreibung mit
Privat ergibt nur dann einen Besucher, wenn auf PIN ein Wert folgt.
Beendete Aufenthalte werden nicht angelegt und in Access gelöscht.
"""

import json
import re
import ssl
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp
from homeassistant.util import dt as dt_util

# Standardzeiten am Anreise- und Abreisetag. Abweichungen stehen im Termin.
DEFAULT_ARRIVAL_TIME = "15:00"
DEFAULT_DEPARTURE_TIME = "11:00"

# Schlüsselwörter in Titel und Beschreibung der Beds24-Termine.
KEYWORD_PIN = "PIN"
KEYWORD_STATUS = "Status"
KEYWORD_CONFIRMED = "Confirmed"
KEYWORD_ARRIVO = "Arrivo"
KEYWORD_PARTENZA = "Partenza"
KEYWORD_PRIVAT = "Privat"
SUMMARY_SEPARATOR = " - "

STATE_PATH = Path("/config/unifi_guest_sync_state.json")
PREFIX = "ha-cal:"
PIN_LENGTH = (4, 8)


def _line_pattern(keyword):
    return re.compile(rf"(?im)^\s*{re.escape(keyword)}\s*[:=]\s*(.*?)\s*$")


def _flag_pattern(keyword):
    return re.compile(rf"(?im)^\s*{re.escape(keyword)}\s*$")


PIN_RE = _line_pattern(KEYWORD_PIN)
STATUS_RE = _line_pattern(KEYWORD_STATUS)
ARRIVO_RE = _line_pattern(KEYWORD_ARRIVO)
PARTENZA_RE = _line_pattern(KEYWORD_PARTENZA)
PRIVAT_RE = _flag_pattern(KEYWORD_PRIVAT)


def _cfg(name):
    return pyscript.app_config[name]


def _ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _api(method, path, body=None):
    url = f"https://{_cfg('host')}:12445{path}"
    headers = {
        "Authorization": f"Bearer {_cfg('token')}",
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


def _load_state():
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text())


def _save_state(state):
    temporary = STATE_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, indent=2))
    temporary.replace(STATE_PATH)


def _normalize(text):
    text = text or ""
    text = re.sub(r"\r?\n[ \t]", "", text)
    return (
        text.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
    )


def _field(text, pattern):
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1).strip()


def _guest_name(summary):
    name, _, _rest = (summary or "").strip().partition(SUMMARY_SEPARATOR)
    return name.strip()


def _split_name(summary):
    parts = _guest_name(summary).split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], "-"
    return parts[0], " ".join(parts[1:])


def _is_private(text):
    return PRIVAT_RE.search(text) is not None


def _pin_value(text):
    raw = _field(text, PIN_RE)
    if raw is None or raw == "":
        return None
    minimum, maximum = PIN_LENGTH
    if re.fullmatch(rf"\d{{{minimum},{maximum}}}", raw):
        return raw
    return None


def _is_confirmed(text):
    raw = _field(text, STATUS_RE)
    if raw is None:
        return True
    return raw.lower() == KEYWORD_CONFIRMED.lower()


def _clock(value):
    if value is None:
        return None
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def _resolve_clock(text, pattern, default):
    raw = _field(text, pattern)
    if raw is None or raw == "":
        return _clock(default), None
    parsed = _clock(raw)
    if parsed is None:
        return None, raw
    return parsed, None


def _event_datetime(value):
    if isinstance(value, dict):
        if value.get("dateTime"):
            return _event_datetime(value["dateTime"])
        if value.get("date"):
            return _event_datetime(value["date"])
        raise ValueError(f"Zeitpunkt nicht lesbar: {value}")
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if len(raw) == 10:
            dt = datetime.fromisoformat(raw)
        else:
            dt = dt_util.parse_datetime(raw)
            if dt is None:
                raise ValueError(f"Zeitpunkt nicht lesbar: {raw}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_util.now().tzinfo)
    return dt


def _access_window(event, text):
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


def _event_key(event):
    uid = event.get("uid")
    if not uid:
        return None
    recurrence = event.get("recurrence_id")
    return f"{uid}#{recurrence}" if recurrence else uid


def _prefix(entity):
    return f"{PREFIX}{entity}:"


def _parse_events(response, entity_id, now):
    block = response.get(entity_id) or {}
    found = []
    keep_keys = set()
    for event in block.get("events") or []:
        summary = event.get("summary")
        key = _event_key(event)
        if not key:
            log.warning(f"{entity_id}: Termin ohne UID übersprungen: {summary}")
            continue
        text = _normalize(event.get("description") or "")
        if not _is_confirmed(text):
            log.info(
                f"{entity_id}: Status nicht {KEYWORD_CONFIRMED}, Besucher wird entfernt: {summary}"
            )
            continue
        pin = _pin_value(text)
        if _is_private(text) and not pin:
            log.info(f"{entity_id}: {KEYWORD_PRIVAT} ohne {KEYWORD_PIN}, kein Besucher: {summary}")
            continue
        first, last = _split_name(summary)
        if not pin or not first:
            log.warning(
                f"{entity_id}: {KEYWORD_PIN} oder Name fehlt, bestehender Besucher bleibt: {summary}"
            )
            keep_keys.add(key)
            continue
        try:
            start, end = _access_window(event, text)
        except (KeyError, TypeError, ValueError) as err:
            log.warning(
                f"{entity_id}: Zeitraum nicht lesbar, bestehender Besucher bleibt: {summary} ({err})"
            )
            keep_keys.add(key)
            continue
        if end <= now:
            log.info(f"{entity_id}: Aufenthalt beendet, kein Besucher: {summary}")
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


async def _list_visitors():
    page = 1
    visitors = []
    while True:
        payload = await _api(
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


def _visitor_body(guest, entity, door_group_id):
    return {
        "first_name": guest["first"],
        "last_name": guest["last"],
        "start_time": guest["start"],
        "end_time": guest["end"],
        "visit_reason": "Others",
        "remarks": _prefix(entity) + guest["key"],
        "resources": [{"id": door_group_id, "type": "door_group"}],
    }


async def _assign_pin(visitor_id, pin):
    try:
        await _api("DELETE", f"/api/v1/developer/visitors/{visitor_id}/pin_codes")
    except Exception:
        pass
    await _api(
        "PUT",
        f"/api/v1/developer/visitors/{visitor_id}/pin_codes",
        {"pin_code": pin},
    )


async def _sync_calendar(calendar, visitors, state, now, window_end):
    entity = calendar["entity"]
    door_group_id = calendar["door_group_id"]
    if not entity or not door_group_id:
        log.error(f"Kalender ohne Entität oder Türgruppe: {calendar}")
        return

    response = service.call(
        "calendar",
        "get_events",
        entity_id=entity,
        start_date_time=now.isoformat(),
        end_date_time=window_end.isoformat(),
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
                created = await _api(
                    "POST",
                    "/api/v1/developer/visitors",
                    _visitor_body(guest, entity, door_group_id),
                )
                visitor_id = created["data"]["id"]
                await _assign_pin(visitor_id, guest["pin"])
                log.info(
                    f"{entity}: Besucher angelegt: {guest['first']} {guest['last']}"
                )
            else:
                visitor_id = visitor["id"]
                if previous.get("fingerprint") != fingerprint:
                    await _api(
                        "PUT",
                        f"/api/v1/developer/visitors/{visitor_id}",
                        _visitor_body(guest, entity, door_group_id),
                    )
                    if previous.get("pin") != guest["pin"]:
                        await _assign_pin(visitor_id, guest["pin"])
                    log.info(
                        f"{entity}: Besucher aktualisiert: {guest['first']} {guest['last']}"
                    )
            cal_state[key] = {
                "visitor_id": visitor_id,
                "pin": guest["pin"],
                "fingerprint": fingerprint,
            }
        except Exception as err:
            log.error(
                f"{entity}: Sync fehlgeschlagen für {guest['first']} {guest['last']}: {err}"
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
            await _api(
                "DELETE",
                f"/api/v1/developer/visitors/{visitor['id']}?is_force=true",
            )
            cal_state.pop(key, None)
            log.info(
                f"{entity}: Besucher gelöscht ({reason}): "
                f"{visitor.get('first_name')} {visitor.get('last_name')}"
            )
        except Exception as err:
            log.error(f"{entity}: Löschen fehlgeschlagen für {key}: {err}")

    for key in list(cal_state):
        if key not in by_key and key not in managed and key not in keep_keys:
            cal_state.pop(key, None)


@service
@time_trigger("startup")
@time_trigger("cron(*/5 * * * *)")
async def unifi_guest_sync():
    """Kalendertermine als UniFi-Access-Besucher anlegen, ändern und löschen."""
    now = dt_util.now()
    horizon = int(_cfg("horizon_days"))
    window_end = now + timedelta(days=horizon)
    visitors = await _list_visitors()
    state = _load_state()
    for calendar in _cfg("calendars"):
        try:
            await _sync_calendar(calendar, visitors, state, now, window_end)
        except Exception as err:
            log.error(f"{calendar.get('entity')}: Kalender-Sync fehlgeschlagen: {err}")
    _save_state(state)
