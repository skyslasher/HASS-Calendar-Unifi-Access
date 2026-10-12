"""Einrichtung beliebig vieler Kalender und Türgruppen."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
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
)
from .sync import AccessClient, list_door_groups

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


def _calendar_schema(door_groups: list[dict[str, str]]) -> vol.Schema:
    field, control = _door_group_schema(door_groups)
    return vol.Schema(
        {
            vol.Required("calendar"): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="calendar")
            ),
            field: control,
            vol.Required("add_another", default=True): bool,
        }
    )


def _calendar_select_schema(
    calendars: list[dict[str, str]], door_groups: list[dict[str, str]]
) -> vol.Schema:
    names = {group["id"]: group["name"] for group in door_groups}
    return vol.Schema(
        {
            vol.Required("calendar"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {
                            "value": item[CONF_ENTITY],
                            "label": (
                                f"{item[CONF_ENTITY]} → "
                                f"{names.get(item[CONF_DOOR_GROUP_ID], item[CONF_DOOR_GROUP_ID])}"
                            ),
                        }
                        for item in calendars
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        }
    )


def _validate_connection(user_input: dict[str, Any], *, token_required: bool) -> str | None:
    if token_required and not user_input.get(CONF_TOKEN, "").strip():
        return "missing_token"
    if not _clean_host(user_input[CONF_HOST]):
        return "missing_host"
    return None


def _summary(calendars: list[dict[str, str]], door_groups: list[dict[str, str]]) -> str:
    if not calendars:
        return "-"
    names = {group["id"]: group["name"] for group in door_groups}
    return "\n".join(
        f"{item[CONF_ENTITY]} → {names.get(item[CONF_DOOR_GROUP_ID], item[CONF_DOOR_GROUP_ID])}"
        for item in calendars
    )


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
    """Ersteinrichtung."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._connection: dict[str, Any] = {}
        self._calendars: list[dict[str, str]] = []
        self._door_groups: list[dict[str, str]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors: dict[str, str] = {}
        if user_input is not None:
            error = _validate_connection(user_input, token_required=True)
            if error:
                errors["base"] = error
            else:
                self._connection = {
                    CONF_HOST: _clean_host(user_input[CONF_HOST]),
                    CONF_TOKEN: user_input[CONF_TOKEN].strip(),
                    CONF_HORIZON_DAYS: int(user_input[CONF_HORIZON_DAYS]),
                }
                groups, error = await _fetch_door_groups(
                    self._connection[CONF_HOST], self._connection[CONF_TOKEN]
                )
                if error:
                    errors["base"] = error
                else:
                    self._door_groups = groups or []
                    return await self.async_step_calendar()
        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema(user_input),
            errors=errors,
        )

    async def async_step_calendar(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            error = self._append_calendar(user_input)
            if error:
                errors["base"] = error
            elif user_input["add_another"]:
                return await self.async_step_calendar()
            else:
                return self.async_create_entry(title="UniFi Access Gäste", data=self._entry_data())
        return self.async_show_form(
            step_id="calendar",
            data_schema=_calendar_schema(self._door_groups),
            errors=errors,
            description_placeholders={"calendars": _summary(self._calendars, self._door_groups)},
        )

    def _append_calendar(self, user_input: dict[str, Any]) -> str | None:
        entity = user_input["calendar"]
        door_group_id = str(user_input.get("door_group") or "").strip()
        error = _known_door_group(door_group_id, self._door_groups)
        if error:
            return error
        if any(item[CONF_ENTITY] == entity for item in self._calendars):
            return "duplicate_calendar"
        self._calendars.append(
            {CONF_ENTITY: entity, CONF_DOOR_GROUP_ID: door_group_id}
        )
        return None

    def _entry_data(self) -> dict[str, Any]:
        return {**self._connection, CONF_CALENDARS: self._calendars}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return UnifiGuestSyncOptionsFlow()


class UnifiGuestSyncOptionsFlow(OptionsFlow):
    """Kalender und Türgruppen nachträglich ändern."""

    def __init__(self) -> None:
        super().__init__()
        self._connection: dict[str, Any] = {}
        self._calendars: list[dict[str, str]] | None = None
        self._door_groups: list[dict[str, str]] = []
        self._edit_entity: str | None = None

    def _load(self) -> None:
        if self._calendars is not None:
            return
        current = {**self.config_entry.data, **self.config_entry.options}
        self._calendars = [dict(item) for item in current[CONF_CALENDARS]]
        self._connection = {
            CONF_HOST: current[CONF_HOST],
            CONF_TOKEN: current[CONF_TOKEN],
            CONF_HORIZON_DAYS: current[CONF_HORIZON_DAYS],
        }

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        self._load()
        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is None and not self._door_groups and self._connection.get(CONF_TOKEN):
            groups, _error = await _fetch_door_groups(
                self._connection[CONF_HOST], self._connection[CONF_TOKEN], quiet=True
            )
            if groups:
                self._door_groups = groups
        if user_input is not None:
            error = _validate_connection(user_input, token_required=False)
            if error:
                errors["base"] = error
            else:
                self._connection = {
                    CONF_HOST: _clean_host(user_input[CONF_HOST]),
                    CONF_TOKEN: user_input.get(CONF_TOKEN, "").strip() or current[CONF_TOKEN],
                    CONF_HORIZON_DAYS: int(user_input[CONF_HORIZON_DAYS]),
                }
                groups, error = await _fetch_door_groups(
                    self._connection[CONF_HOST], self._connection[CONF_TOKEN]
                )
                if error:
                    errors["base"] = error
                else:
                    self._door_groups = groups or []
                    return await self.async_step_menu()
        shown = user_input or current
        if user_input is not None and not user_input.get(CONF_TOKEN, "").strip():
            shown = {**user_input, CONF_TOKEN: ""}
        return self.async_show_form(
            step_id="init",
            data_schema=_connection_schema(shown, token_required=False),
            errors=errors,
            description_placeholders={
                "calendars": _summary(self._calendars or [], self._door_groups)
            },
        )

    async def async_step_menu(self, user_input: dict[str, Any] | None = None):
        options = ["add_calendar", "edit_calendar", "save"]
        if len(self._calendars or []) > 1:
            options.insert(2, "remove_calendar")
        return self.async_show_menu(
            step_id="menu",
            menu_options=options,
            description_placeholders={
                "calendars": _summary(self._calendars or [], self._door_groups)
            },
        )

    async def async_step_add_calendar(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            error = self._append_calendar(user_input)
            if error:
                errors["base"] = error
            elif user_input["add_another"]:
                return await self.async_step_add_calendar()
            else:
                return await self.async_step_menu()
        return self.async_show_form(
            step_id="add_calendar",
            data_schema=_calendar_schema(self._door_groups),
            errors=errors,
            description_placeholders={
                "calendars": _summary(self._calendars or [], self._door_groups)
            },
        )

    async def async_step_edit_calendar(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._edit_entity = user_input["calendar"]
            return await self.async_step_edit_door_group()
        return self.async_show_form(
            step_id="edit_calendar",
            data_schema=_calendar_select_schema(self._calendars or [], self._door_groups),
        )

    async def async_step_edit_door_group(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        current = next(
            item for item in self._calendars or [] if item[CONF_ENTITY] == self._edit_entity
        )
        if user_input is not None:
            door_group_id = str(user_input.get("door_group") or "").strip()
            error = _known_door_group(door_group_id, self._door_groups)
            if error:
                errors["base"] = error
            else:
                current[CONF_DOOR_GROUP_ID] = door_group_id
                return await self.async_step_menu()
        field, control = _door_group_schema(self._door_groups, current[CONF_DOOR_GROUP_ID])
        return self.async_show_form(
            step_id="edit_door_group",
            data_schema=vol.Schema({field: control}),
            errors=errors,
            description_placeholders={"calendar": self._edit_entity or ""},
        )

    async def async_step_remove_calendar(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            if len(self._calendars or []) <= 1:
                errors["base"] = "last_calendar"
            else:
                entity = user_input["calendar"]
                self._calendars = [
                    item for item in self._calendars or [] if item[CONF_ENTITY] != entity
                ]
                return await self.async_step_menu()
        return self.async_show_form(
            step_id="remove_calendar",
            data_schema=_calendar_select_schema(self._calendars or [], self._door_groups),
            errors=errors,
        )

    async def async_step_save(self, user_input: dict[str, Any] | None = None):
        if not self._calendars:
            return await self.async_step_menu()
        return self.async_create_entry(title="", data=self._entry_data())

    def _append_calendar(self, user_input: dict[str, Any]) -> str | None:
        entity = user_input["calendar"]
        door_group_id = str(user_input.get("door_group") or "").strip()
        error = _known_door_group(door_group_id, self._door_groups)
        if error:
            return error
        if any(item[CONF_ENTITY] == entity for item in self._calendars or []):
            return "duplicate_calendar"
        assert self._calendars is not None
        self._calendars.append({CONF_ENTITY: entity, CONF_DOOR_GROUP_ID: door_group_id})
        return None

    def _entry_data(self) -> dict[str, Any]:
        return {**self._connection, CONF_CALENDARS: self._calendars}
