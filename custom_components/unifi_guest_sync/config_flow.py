"""Einrichtung beliebig vieler Kalender und Türgruppen."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigSubentryFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CALENDARS,
    CONF_DOOR_GROUP_ID,
    CONF_ENTITY,
    CONF_HORIZON_DAYS,
    CONF_HOST,
    CONF_TOKEN,
    DEFAULT_HORIZON_DAYS,
    DOMAIN,
    SUBENTRY_CALENDAR,
)
from .sync import AccessClient, list_door_groups, settings_from_entry

_LOGGER = logging.getLogger(__name__)


def _clean_host(host: str) -> str:
    value = host.strip()
    for prefix in ("https://", "http://"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value.split("/")[0].split(":")[0]


def _with_default(key: str, default: Any, *, required: bool = True):
    marker = vol.Required if required else vol.Optional
    if default is None:
        return marker(key)
    return marker(key, default=default)


def _connection_schema(defaults: dict[str, Any] | None = None, *, token_required: bool = True):
    defaults = defaults or {}
    return vol.Schema(
        {
            _with_default(CONF_HOST, defaults.get(CONF_HOST)): selector.TextSelector(),
            _with_default(
                CONF_TOKEN,
                None if token_required else "",
                required=token_required,
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            _with_default(
                CONF_HORIZON_DAYS,
                defaults.get(CONF_HORIZON_DAYS, DEFAULT_HORIZON_DAYS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=365, mode=selector.NumberSelectorMode.BOX, step=1
                )
            ),
        }
    )


def _door_group_options(door_groups: list[dict[str, str]]) -> list[dict[str, str]]:
    counts: dict[str, int] = {}
    for group in door_groups:
        counts[group["name"]] = counts.get(group["name"], 0) + 1
    options = []
    for group in door_groups:
        label = group["name"]
        if counts[group["name"]] > 1:
            label = f"{label} ({group['id'][:8]})"
        options.append({"value": group["id"], "label": label})
    return options


def _door_group_schema(door_groups: list[dict[str, str]], default: str | None = None):
    known = {group["id"] for group in door_groups}
    field = vol.Required("door_group")
    if default in known:
        field = vol.Required("door_group", default=default)
    return field, selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=_door_group_options(door_groups),
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _link_schema(
    door_groups: list[dict[str, str]],
    *,
    calendar: str | None = None,
    door_group: str | None = None,
) -> vol.Schema:
    calendar_field = vol.Required("calendar")
    if calendar:
        calendar_field = vol.Required("calendar", default=calendar)
    door_field, door_control = _door_group_schema(door_groups, door_group)
    return vol.Schema(
        {
            calendar_field: selector.EntitySelector(
                selector.EntitySelectorConfig(domain="calendar")
            ),
            door_field: door_control,
        }
    )


def _validate_connection(user_input: dict[str, Any], *, token_required: bool) -> str | None:
    if token_required and not user_input.get(CONF_TOKEN, "").strip():
        return "missing_token"
    if not _clean_host(user_input[CONF_HOST]):
        return "missing_host"
    return None


def _link_title(hass, entity: str, door_groups: list[dict[str, str]], door_group_id: str) -> str:
    state = hass.states.get(entity)
    calendar_name = state.name if state else entity
    door_name = next(
        (group["name"] for group in door_groups if group["id"] == door_group_id),
        door_group_id,
    )
    return f"{calendar_name} → {door_name}"


def _duplicate_calendar(
    entry: ConfigEntry, entity: str, *, ignore_subentry_id: str | None = None
) -> bool:
    for subentry in entry.subentries.values():
        if subentry.subentry_id == ignore_subentry_id:
            continue
        if subentry.unique_id == entity or subentry.data.get(CONF_ENTITY) == entity:
            return True
    return False


async def _fetch_door_groups(
    host: str, token: str, *, quiet: bool = False
) -> tuple[list[dict[str, str]] | None, str | None]:
    try:
        groups = await list_door_groups(AccessClient(host, token))
    except Exception:
        if quiet:
            _LOGGER.debug("Türgruppen von %s konnten nicht geladen werden", host, exc_info=True)
        else:
            _LOGGER.exception("Türgruppen von %s konnten nicht geladen werden", host)
        return None, "door_groups_unavailable"
    if not groups:
        return None, "no_door_groups"
    return groups, None


def _known_door_group(door_group_id: str, door_groups: list[dict[str, str]]) -> str | None:
    if door_group_id and any(group["id"] == door_group_id for group in door_groups):
        return None
    return "missing_door_group"


class UnifiGuestSyncConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ersteinrichtung der Konsole. Kalender werden auf der Integrationsseite angelegt."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors: dict[str, str] = {}
        if user_input is not None:
            error = _validate_connection(user_input, token_required=True)
            if error:
                errors["base"] = error
            else:
                host = _clean_host(user_input[CONF_HOST])
                token = user_input[CONF_TOKEN].strip()
                _groups, error = await _fetch_door_groups(host, token)
                if error:
                    errors["base"] = error
                else:
                    return self.async_create_entry(
                        title="UniFi Access Gäste",
                        data={
                            CONF_HOST: host,
                            CONF_TOKEN: token,
                            CONF_HORIZON_DAYS: int(user_input[CONF_HORIZON_DAYS]),
                            CONF_CALENDARS: [],
                        },
                    )
        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema(user_input),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        entry = self._get_reconfigure_entry()
        current = settings_from_entry(entry)
        errors: dict[str, str] = {}
        if user_input is not None:
            error = _validate_connection(user_input, token_required=False)
            if error:
                errors["base"] = error
            else:
                host = _clean_host(user_input[CONF_HOST])
                token = user_input.get(CONF_TOKEN, "").strip() or current[CONF_TOKEN]
                _groups, error = await _fetch_door_groups(host, token)
                if error:
                    errors["base"] = error
                else:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={
                            CONF_HOST: host,
                            CONF_TOKEN: token,
                            CONF_HORIZON_DAYS: int(user_input[CONF_HORIZON_DAYS]),
                            CONF_CALENDARS: [],
                        },
                        options={},
                    )
                    return self.async_abort(reason="reconfigure_successful")
        shown = {
            CONF_HOST: current.get(CONF_HOST, ""),
            CONF_HORIZON_DAYS: current.get(CONF_HORIZON_DAYS, DEFAULT_HORIZON_DAYS),
            CONF_TOKEN: "",
        }
        if user_input is not None:
            shown = {**user_input, CONF_TOKEN: ""}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_connection_schema(shown, token_required=False),
            errors=errors,
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_CALENDAR: CalendarSubentryFlow}


class CalendarSubentryFlow(ConfigSubentryFlow):
    """Einen Kalender mit einer Türgruppe verknüpfen."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        return await self._async_link(user_input, reconfigure=False)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        return await self._async_link(user_input, reconfigure=True)

    async def _door_groups(self) -> tuple[list[dict[str, str]] | None, str | None]:
        cached = getattr(self, "_groups", None)
        if cached:
            return cached, None
        settings = settings_from_entry(self._get_entry())
        groups, error = await _fetch_door_groups(settings[CONF_HOST], settings[CONF_TOKEN])
        if error:
            return None, error
        self._groups = groups
        return groups, None

    async def _async_link(self, user_input: dict[str, Any] | None, *, reconfigure: bool):
        groups, error = await self._door_groups()
        if error or not groups:
            return self.async_abort(reason=error or "door_groups_unavailable")
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry() if reconfigure else None
        errors: dict[str, str] = {}
        if user_input is not None:
            entity = user_input["calendar"]
            door_group_id = str(user_input.get("door_group") or "").strip()
            error = _known_door_group(door_group_id, groups)
            if error:
                errors["base"] = error
            elif _duplicate_calendar(
                entry,
                entity,
                ignore_subentry_id=subentry.subentry_id if subentry else None,
            ):
                errors["base"] = "duplicate_calendar"
            else:
                title = _link_title(self.hass, entity, groups, door_group_id)
                data = {CONF_ENTITY: entity, CONF_DOOR_GROUP_ID: door_group_id}
                if subentry is None:
                    return self.async_create_entry(title=title, data=data, unique_id=entity)
                return self.async_update_and_abort(
                    entry,
                    subentry,
                    title=title,
                    data=data,
                    unique_id=entity,
                )
        calendar = subentry.data.get(CONF_ENTITY) if subentry else None
        door_group = subentry.data.get(CONF_DOOR_GROUP_ID) if subentry else None
        return self.async_show_form(
            step_id="reconfigure" if reconfigure else "user",
            data_schema=_link_schema(groups, calendar=calendar, door_group=door_group),
            errors=errors,
            description_placeholders={"calendar": calendar or ""},
        )


