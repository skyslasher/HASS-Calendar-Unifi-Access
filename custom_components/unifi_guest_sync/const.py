"""Konstanten für den Kalenderabgleich mit UniFi Access."""

from datetime import timedelta

DOMAIN = "unifi_guest_sync"

CONF_HOST = "host"
CONF_TOKEN = "token"
CONF_HORIZON_DAYS = "horizon_days"
CONF_CALENDARS = "calendars"
CONF_ENTITY = "entity"
CONF_DOOR_GROUP_ID = "door_group_id"
SUBENTRY_CALENDAR = "calendar"

DEFAULT_HORIZON_DAYS = 120
SCAN_INTERVAL = timedelta(minutes=5)
STORAGE_KEY = "unifi_guest_sync"
STORAGE_VERSION = 1
SERVICE_SYNC = "sync"
SERVICE_DOWNLOAD_QR = "download_qr_code"
QR_DIRECTORY = "unifi_guest_qr"

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

PREFIX = "ha-cal:"
PIN_LENGTH = (4, 8)
