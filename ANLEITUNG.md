# Gäste aus Beds24-Kalendern in UniFi Access

Home Assistant gleicht beliebig viele Kalender mit der lokalen UniFi-Access-Konsole ab. Jeder Kalender bedient eine Türgruppe. Aus jedem bestätigten Termin mit PIN wird ein Besucher. Geänderte Termine ändern denselben Besucher. Gelöschte, nicht mehr bestätigte und beendete Termine löschen ihn. Ein Termin mit `Privat` wird nur angelegt, wenn auf `PIN` ein Wert folgt.

Der Besucher und die PIN liegen danach in UniFi Access und werden an den Tür-Hub übertragen. Die Prüfung an der Tür läuft lokal. Eine bereits übertragene PIN bleibt gültig, wenn Internet oder Home Assistant ausfallen. Neu anlegen, ändern und löschen braucht den nächsten erfolgreichen Abgleich.

Die mitgelieferte Home-Assistant-Integration UniFi Access steuert Türen, Sperre und Evakuierung. Besucher legt diese Integration über die Access-API auf Port 12445 an.

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

Gelöscht werden nur Besucher mit dem Präfix des jeweiligen Kalenders. Manuell angelegte Besucher bleiben unverändert. Ein Termin aus einem Kalender entfernt keine Besucher eines anderen Kalenders.

## Terminformat

Schlüsselwörter und Standardzeiten stehen in `custom_components/unifi_guest_sync/const.py`:

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

Die PIN muss zur Länge in Access passen. Im Beispiel sind das 4 Ziffern. Dieselbe PIN darf zeitgleich nur einem Benutzer oder Besucher gehören, auch über alle Kalender hinweg.

## UniFi Access vorbereiten

1. In Access eine Türgruppe pro Kalender anlegen.
2. Unter **Einstellungen → Allgemein → API-Token** einen Token mit `view:space`, `view:visitor` und `edit:visitor` erzeugen. `view:space` braucht die Einrichtung, um die Türgruppen laden zu können.
3. Die PIN-Länge so einstellen, wie sie in den Kalendern steht.
4. Port 12445 im LAN erreichbar lassen. Home Assistant muss die Konsole direkt erreichen. Den Port nicht ins Internet stellen.

## Installation über HACS

Das Repository ist eine private HACS-Integration. In HACS einen GitHub-Token hinterlegen, der dieses Repository lesen darf. Danach unter **Benutzerdefinierte Repositories** die Repository-URL eintragen und die Kategorie **Integration** wählen. HACS installiert die Komponente nach `/config/custom_components/unifi_guest_sync`. Home Assistant neu starten.

Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen** **UniFi Access Gäste** einrichten:

- Host: IP oder Hostname der Access-Konsole, ohne `https://` und ohne Port
- API-Token aus Access
- Horizont in Tagen, Standard 120

Im nächsten Schritt jedes Paar aus Kalender und Türgruppe wählen. Die Türgruppen lädt die Einrichtung von der Konsole und zeigt sie als Liste. Den Haken **Weiteren Kalender hinzufügen** erst beim letzten Paar entfernen. Unter **Konfigurieren** lassen sich später weitere Kalender hinzufügen, Türgruppen ändern und Kalender entfernen. Ein entfernter Kalender verliert seine Besucher beim nächsten Abgleich. Der letzte Kalender bleibt erhalten. Ein leeres Token-Feld behält den gespeicherten Token. Fehlt diesem Token `view:space`, bricht der nächste Schritt mit einem Fehler ab. Dann den neuen Token eintragen.

Der Abgleich startet nach dem Einrichten, wiederholt sich alle fünf Minuten und lässt sich mit dem Dienst `unifi_guest_sync.sync` sofort ausführen.

## Ersteinrichtung prüfen

Unter **Entwicklerwerkzeuge → Aktionen** den Dienst `unifi_guest_sync.sync` ausführen.

Für einen bestätigten Termin ohne `Arrivo` und `Partenza` muss in Access der Besucher mit PIN und nur der Türgruppe seines Kalenders stehen. Der Zugang läuft von 15:00 am Anreisetag bis 11:00 am Abreisetag.

Danach nacheinander prüfen:

1. `Arrivo` oder `Partenza` setzen. Nach dem nächsten Lauf gelten diese Uhrzeiten am selben An- und Abreisetag.
2. Einen Termin mit der Zeile `Privat` und leerer `PIN` anlegen. In Access entsteht kein Besucher.
3. Bei demselben Termin eine PIN eintragen. Der Besucher wird angelegt.
4. Titel oder PIN ändern. Es bleibt derselbe Besucher, die Türgruppe bleibt die des Kalenders.
5. Den Termin löschen, den Status von `Confirmed` wegändern, oder das Zugangsende abwarten lassen. Der Besucher verschwindet.
6. Dieselbe PIN an der Tür eingeben, während Home Assistant gestoppt und die Internetverbindung getrennt ist. Der Zutritt muss funktionieren, solange der Zugang noch nicht beendet ist.

Das Protokoll steht unter **Einstellungen → System → Protokolle**, Logger `custom_components.unifi_guest_sync`. Der Abgleich merkt sich die zuletzt gesetzte PIN in `.storage/unifi_guest_sync`. Die Datei enthält PINs und bleibt auf dem Home-Assistant-Rechner.

## QR-Code herunterladen

Die Aktion `unifi_guest_sync.download_qr_code` ordnet dem Besucher bei Bedarf einen QR-Code zu, speichert das Bild und gibt den Pfad zurück. Dieselbe Aktion gilt für **Entwicklerwerkzeuge → Aktionen** und für Automatisierungen. Der Leser muss QR-Codes als Zugangsmethode erlauben. Dafür ist UniFi Access 3.3.10 oder neuer nötig.

Die Aktion sucht den Besucher entweder über `visitor_id` oder über `calendar_entity` und `event_uid`. Die UID ist die des Kalendertermins, zum Beispiel `20261126150000-b94003821@beds24.com`.

```yaml
actions:
  - action: unifi_guest_sync.download_qr_code
    data:
      calendar_entity: calendar.gaeste_eins
      event_uid: "20261126150000-b94003821@beds24.com"
    response_variable: qr
  - action: notify.benachrichtigung
    data:
      message: "QR-Code: {{ qr.path }}"
```

`qr.path` zeigt auf `/config/unifi_guest_qr/<besucher-id>.png`. Mit `publish: true` liegt dieselbe Datei zusätzlich unter `/config/www/unifi_guest_qr/` und ist im Browser über `qr.local_url` erreichbar, zum Beispiel `/local/unifi_guest_qr/<besucher-id>.png`. Diese Adresse verlangt kein Login. Wird der Besucher gelöscht, werden die Bilddateien mit entfernt.

## Was bei einem Ausfall gilt

Der Leser spricht nach der Übertragung nicht mit Home Assistant und nicht mit dem Internet. Solange der Hub Strom hat und die PIN schon synchronisiert wurde, geht die Tür auf.

Ein Termin, der während eines Ausfalls neu entsteht, sich ändert oder gelöscht wird, zieht erst beim nächsten erfolgreichen Abgleich nach. Dafür müssen Home Assistant, der Kalender und die Access-Konsole erreichbar sein. Ist die Kalender-Entität nicht verfügbar, bricht der Abgleich für diesen Kalender ab und löscht keine Besucher. Eine erfolgreich geladene, aber leere Kalenderdatei gilt dagegen als echte Löschung. Ein Widerruf, der in den Ausfall fällt, gilt erst nach dem nächsten erfolgreichen Lauf. Der Horizont muss den am weitesten voraus liegenden Aufenthalt einschließen. Liegt ein Termin dahinter, wird er nicht angelegt.

Weist Access die PIN zurück, stimmt die PIN-Länge nicht, oder die PIN ist bereits vergeben. Die API-Dokumentation beschreibt die PIN als zuvor erzeugten Code. `PUT /visitors/{id}/pin_codes` übernimmt die Ziffern aus dem Kalender, sobald Länge und Eindeutigkeit passen. Der erste Testbesucher zeigt, ob die Console-Version das tut.
