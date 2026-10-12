# UniFi Access Gäste

Jeder Remote-Kalender legt Besucher in UniFi Access an und bedient eine Türgruppe. Die Anzahl der Paare ist frei wählbar. Name und PIN kommen aus dem Termin. Die Zugangsdaten liegen danach auf der lokalen Access-Konsole, damit die Tür auch ohne Internet und ohne Home Assistant öffnet.

## Privates Repository in HACS

Das GitHub-Repository bleibt privat. In HACS unter **Einstellungen** einen GitHub-Token hinterlegen, der dieses Repository lesen darf. Bei einem klassischen Token ist das der Bereich `repo`. Bei einem fein abgestuften Token reicht der Lesezugriff auf dieses eine Repository.

Danach in HACS das Dreipunktmenü öffnen, **Benutzerdefinierte Repositories** wählen und die Repository-URL eintragen. Die Kategorie ist **Integration**. HACS lädt `custom_components/unifi_guest_sync` nach `/config/custom_components/unifi_guest_sync`. Home Assistant neu starten.

Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen** die Integration **UniFi Access Gäste** wählen. Dort nur die Konsolen-Adresse, den API-Token und den Horizont eintragen. Der Token braucht `view:space`, `view:visitor` und `edit:visitor`. Der Host ist nur die Adresse, ohne `https://` und ohne Port. Kalender folgen danach unter **Konfigurieren**, jeweils eine Verknüpfung aus Kalender und Türgruppe. Die Türgruppen kommen von der Konsole. Eine bestehende Verknüpfung lässt sich dort bearbeiten.

Der Abgleich startet danach von selbst, wiederholt sich alle fünf Minuten und lässt sich mit dem Dienst `unifi_guest_sync.sync` sofort ausführen. Der QR-Code eines Besuchers lässt sich mit `unifi_guest_sync.download_qr_code` manuell oder aus einer Automatisierung laden. Änderungen an Kalendern und Türgruppen stehen unter **Konfigurieren**. Ein leeres Token-Feld behält den gespeicherten Token.

Die Terminformate, Standardzeiten und das Verhalten bei Ausfällen stehen in [ANLEITUNG.md](ANLEITUNG.md).
