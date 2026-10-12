# Gäste aus drei Beds24-Kalendern in UniFi Access

Home Assistant gleicht drei Kalender mit der lokalen UniFi-Access-Konsole ab. Jeder Kalender bedient eine Türgruppe. Aus jedem bestätigten Termin mit PIN wird ein Besucher. Geänderte Termine ändern denselben Besucher. Gelöschte, nicht mehr bestätigte und beendete Termine löschen ihn. Ein Termin mit `Privat` wird nur angelegt, wenn auf `PIN` ein Wert folgt.

Der Besucher und die PIN liegen danach in UniFi Access und werden an den Tür-Hub übertragen. Die Prüfung an der Tür läuft lokal. Eine bereits übertragene PIN bleibt gültig, wenn Internet oder Home Assistant ausfallen. Neu anlegen, ändern und löschen braucht den nächsten erfolgreichen Abgleich.

Die mitgelieferte Home-Assistant-Integration UniFi Access steuert Türen, Sperre und Evakuierung. Besucher legt dieses Skript über die Access-API auf Port 12445 an.

## Ablauf

Alle fünf Minuten und bei jedem Start von Home Assistant:

1. Termine ab jetzt bis zum Horizont lesen (Standard 120 Tage). Beendete Aufenthalte gehören nicht dazu.
2. Pro bestätigtem Termin einen Besucher anlegen oder aktualisieren. Ein laufender Aufenthalt, dessen Abreise noch aussteht, bleibt im Abgleich.
3. Besucher löschen, deren Zugang beendet ist, deren Termin fehlt, deren Status nicht mehr `Confirmed` ist, oder die als `Privat` keine PIN haben.

Die Verknüpfung steht im Bemerkungsfeld des Besuchers:

```text
ha-cal:<kalender-entität>:<termin-uid>
```

Beim Beispieltermin ist das:

```text
ha-cal:calendar.gaeste_eins:20270628150000-b89104524@beds24.com
```

Gelöscht werden nur Besucher mit dem Präfix des jeweiligen Kalenders. Manuell angelegte Besucher bleiben unverändert. Ein Termin aus Kalender 1 kann keinen Besucher von Kalender 2 entfernen.

## Terminformat

Schlüsselwörter und Standardzeiten stehen am Anfang von `pyscript/unifi_guest_sync.py`:

```python
DEFAULT_ARRIVAL_TIME = "15:00"
DEFAULT_DEPARTURE_TIME = "11:00"

KEYWORD_PIN = "PIN"
KEYWORD_STATUS = "Status"
KEYWORD_CONFIRMED = "Confirmed"
KEYWORD_ARRIVO = "Arrivo"
KEYWORD_PARTENZA = "Partenza"
KEYWORD_PRIVAT = "Privat"
SUMMARY_SEPARATOR = " - "
```

Das Datum kommt aus dem Termin. Die Uhrzeit ist 15:00 am Anreisetag und 11:00 am Abreisetag. Steht in der Beschreibung `Arrivo: 11:00` oder `Partenza: 19:00`, gilt diese Uhrzeit an demselben Tag. Die Zeitzone bleibt die des Termins, im Beispiel Europe/Rome.

Ein normaler Termin:

```text
SUMMARY:Ian McKenzie - 1 persone (via Booking.com)
DESCRIPTION: Status: Confirmed
             PIN: 9650
DTSTART;TZID=Europe/Rome:20270628T150000
DTEND;TZID=Europe/Rome:20270704T110000
```

Daraus wird der Besucher Ian McKenzie, PIN 9650, Zugang 28.06.2027 15:00 bis 04.07.2027 11:00. `Arrivo` und `Partenza` fehlen, deshalb gelten die Standardzeiten.

Ein privater Termin:

```text
SUMMARY:Ingrid & Andreas Skyhahn - 2 persone (via castellofai)
DESCRIPTION: Privat
             Status: Confirmed
             PIN:
             Arrivo: 11:00
             Partenza: 19:00
DTSTART;TZID=Europe/Rome:20261126T150000
DTEND;TZID=Europe/Rome:20261202T110000
```

`Privat` steht als eigene Zeile. Auf `PIN` folgt kein Wert, deshalb wird kein Besucher angelegt. Existiert zu diesem Termin schon einer, löscht der nächste Lauf ihn. Steht dort zum Beispiel `PIN: 9650`, entsteht der Besucher Ingrid & Andreas Skyhahn mit Zugang 26.11.2026 11:00 bis 02.12.2026 19:00.

`1 persone` und `(via …)` gehören nicht zum Namen. Fehlt bei einem Termin ohne `Privat` einmalig die PIN oder der Name, bleibt ein schon angelegter Besucher bestehen. Wechselt der Status weg von `Confirmed`, wird er gelöscht. Ist das Zugangsende erreicht, wird der Besucher in Access gelöscht und der Termin nicht erneut angelegt.

Die PIN muss zur Länge in Access passen. Im Beispiel sind das 4 Ziffern. Dieselbe PIN darf zeitgleich nur einem Benutzer oder Besucher gehören, auch über die drei Kalender hinweg.

## UniFi Access vorbereiten

1. In Access drei Türgruppen anlegen, eine pro Kalender.
2. Unter **Einstellungen → Allgemein → API-Token** einen Token mit `view:visitor` und `edit:visitor` erzeugen.
3. Die PIN-Länge so einstellen, wie sie in den Kalendern steht.
4. Port 12445 im LAN erreichbar lassen. Home Assistant muss die Konsole direkt erreichen. Den Port nicht ins Internet stellen.

Die Türgruppen-IDs liefert:

```bash
curl --insecure --silent \
  -H "Authorization: Bearer DEIN-TOKEN" \
  -H "Accept: application/json" \
  "https://192.168.1.10:12445/api/v1/developer/door_groups/topology"
```

Host und Token durch die eigenen Werte ersetzen. In der Antwort die `id` der jeweiligen Gruppe notieren.

## Dateien nach Home Assistant kopieren

Pyscript zuerst über HACS installieren: [custom-components/pyscript](https://github.com/custom-components/pyscript). Danach Home Assistant neu starten.

| Datei in diesem Projekt | Ziel auf dem Home-Assistant-Rechner |
| --- | --- |
| `pyscript/unifi_guest_sync.py` | `/config/pyscript/unifi_guest_sync.py` |
| `homeassistant/pyscript.yaml` | `/config/pyscript.yaml` |

In `/config/configuration.yaml` diese Zeile ergänzen:

```yaml
pyscript: !include pyscript.yaml
```

In `/config/secrets.yaml` den Token eintragen. Die Beispieldatei `homeassistant/secrets.yaml.example` zeigt den Schlüsselnamen:

```yaml
unifi_access_token: "der-token-aus-access"
```

In `/config/pyscript.yaml` danach vier Werte setzen:

- `host`: IP oder Hostname der Access-Konsole, ohne `https://` und ohne Port
- drei `entity`-Namen der Kalender, wie sie unter **Entwicklerwerkzeuge → Zustände** stehen
- drei `door_group_id`-Werte aus der Türgruppen-Abfrage

Die Reihenfolge der Liste ist die Zuordnung. Der erste Kalender bedient die erste Türgruppe, der zweite die zweite, der dritte die dritte.

```yaml
calendars:
  - entity: calendar.gaeste_eins
    door_group_id: "UUID-TUERGRUPPE-1"
  - entity: calendar.gaeste_zwei
    door_group_id: "UUID-TUERGRUPPE-2"
  - entity: calendar.gaeste_drei
    door_group_id: "UUID-TUERGRUPPE-3"
```

Home Assistant danach erneut neu starten.

## Ersteinrichtung prüfen

Unter **Entwicklerwerkzeuge → Aktionen** den Dienst `pyscript.unifi_guest_sync` ausführen.

Für einen bestätigten Termin ohne `Arrivo` und `Partenza` muss in Access der Besucher mit PIN und nur der Türgruppe seines Kalenders stehen. Der Zugang läuft von 15:00 am Anreisetag bis 11:00 am Abreisetag.

Danach nacheinander prüfen:

1. `Arrivo` oder `Partenza` setzen. Nach dem nächsten Lauf gelten diese Uhrzeiten am selben An- und Abreisetag.
2. Einen Termin mit der Zeile `Privat` und leerer `PIN` anlegen. In Access entsteht kein Besucher.
3. Bei demselben Termin eine PIN eintragen. Der Besucher wird angelegt.
4. Titel oder PIN ändern. Es bleibt derselbe Besucher, die Türgruppe bleibt die des Kalenders.
5. Den Termin löschen, den Status von `Confirmed` wegändern, oder das Zugangsende abwarten lassen. Der Besucher verschwindet.
6. Dieselbe PIN an der Tür eingeben, während Home Assistant gestoppt und die Internetverbindung getrennt ist. Der Zutritt muss funktionieren, solange der Zugang noch nicht beendet ist.

Das Protokoll steht unter **Einstellungen → System → Protokolle**, Quelle Pyscript. `/config/unifi_guest_sync_state.json` merkt sich die zuletzt gesetzte PIN, damit eine Änderung erkannt wird. Die Datei enthält PINs und bleibt auf dem Home-Assistant-Rechner.

## Was bei einem Ausfall gilt

Der Leser spricht nach der Übertragung nicht mit Home Assistant und nicht mit dem Internet. Solange der Hub Strom hat und die PIN schon synchronisiert wurde, geht die Tür auf.

Ein Termin, der während eines Ausfalls neu entsteht, sich ändert oder gelöscht wird, zieht erst beim nächsten erfolgreichen Abgleich nach. Dafür müssen Home Assistant, der Kalender und die Access-Konsole erreichbar sein. Ein Widerruf, der in den Ausfall fällt, gilt erst nach diesem Lauf. Der Horizont muss den am weitesten voraus liegenden Aufenthalt einschließen. Liegt ein Termin dahinter, wird er nicht angelegt.

Weist Access die PIN zurück, stimmt die PIN-Länge nicht, oder die PIN ist bereits vergeben. Die API-Dokumentation beschreibt die PIN als zuvor erzeugten Code. `PUT /visitors/{id}/pin_codes` übernimmt die Ziffern aus dem Kalender, sobald Länge und Eindeutigkeit passen. Der erste Testbesucher zeigt, ob die Console-Version das tut.
