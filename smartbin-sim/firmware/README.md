# Smart Bin – Modul-Firmware (ESP32-CAM)

C++-Firmware für ein Modul (Kamera, PIR, Klappe, LED) mit PlatformIO.
Verhalten nach `sim/module.py` (ausführbare Spezifikation), Protokoll nach
`smartbin/adapters/protocol.py` + `tests/test_protocol.py` (Vertrag, ADR-004).

| Datei | Inhalt |
| --- | --- |
| `lib/smartbin/src/protocol.*` | Frames, CRC-8/CRC-16, Stream-Decoder – hardwareunabhängig |
| `lib/smartbin/src/module_fsm.*` | Zustandsautomat INIT → BEREIT → ERKANNT → WARTEN → SORTIEREN, STOERUNG, Sicherheitsregeln (ADR-003) – hardwareunabhängig |
| `src/main.cpp` | Nur Board-Anbindung: Pins, Kamera, Servo, LED, UART |
| `test/` | Unit-Tests auf dem PC (Protokoll-Vertrag, Zustandsautomat) |
| `diagram.json`, `wokwi.toml` | Schaltung für den Wokwi-Simulator |

## Befehle

```bash
pip install platformio
cd firmware
pio test -e native                  # Tests auf dem PC (braucht gcc; unter Windows MinGW oder WSL)
pio run -e esp32cam                 # echtes Modul bauen
pio run -e esp32cam -t upload       # flashen (MODULE_ID in platformio.ini pro Modul setzen!)
pio run -e wokwi                    # Build für den Simulator
```

Die CI baut beide Varianten und führt die Tests aus. `tests/test_firmware_contract.py`
(Python) prüft zusätzlich, dass Codes und Testvektoren mit `protocol.py` übereinstimmen.

## Ohne Hardware testen: Wokwi + echte Jetson-Software

1. VS Code-Erweiterung «Wokwi Simulator» installieren und eine (kostenlose) Lizenz aktivieren.
2. `pio run -e wokwi`, dann den Ordner `firmware/` in VS Code öffnen → «Wokwi: Start Simulator».
3. Im Repo-Hauptordner die Jetson-Seite starten (echter Kern, Gateway, SQLite, Dashboard;
   statt Kamera-Modell antwortet ein Skript):

   ```bash
   pip install pyserial -r requirements-dashboard.txt
   python tools/wokwi_station.py --detect ALU PET REST UNBEKANNT
   ```

   Die Software verbindet sich über `rfc2217://localhost:4000` (`config/station-wokwi.yaml`).
   Das simulierte Modul ist ALU (Mitte); PET links und REST rechts sind nicht angeschlossen.
4. In Wokwi: PIR anklicken (Hand in der Öffnung) und **Einwurf** drücken.

| Einwurf | Skript-Antwort | LED | Klappe |
| --- | --- | --- | --- |
| 1. | ALU | grün | DIREKT |
| 2. | PET | rot blinkend | LINKS |
| 3. | REST | rot blinkend | RECHTS |
| 4. | UNBEKANNT | gelb | RECHTS (REST) |

Weitere Fälle: PIR während der Bewegung auslösen → Klappe bleibt stehen;
Schiebeschalter auf Blockade → STOERUNG, im Dashboard (http://127.0.0.1:8080)
erscheint der Reset-Knopf; `wokwi_station.py` beenden → nach 3 s STOERUNG (Heartbeat).
Die Kamera gibt es in Wokwi nicht: das Modul schickt ein 15-KB-Platzhalterbild.

## Pinbelegung ESP32-CAM (AI-Thinker)

Mit laufender Kamera und ohne SD-Karte sind nur GPIO 2, 4, 12, 13, 14, 15 frei.

| GPIO | Funktion |
| --- | --- |
| 13 | PIR-Ausgang (HIGH = Hand) |
| 15 | Gegenstand-Sensor auf der Klappe (LOW = Gegenstand), z. B. IR-Reflexsensor |
| 14 | Servo Klappe (LEDC-Kanal 2, Timer 1; Kamera nutzt Timer 0) |
| 2 | Status-LED (1× WS2812) |
| 12 | Endschalter volle Neigung (HIGH = erreicht), **externer Pull-down**, nur mit `-DHAS_END_SWITCH` |
| 4 | Blitz-LED auf dem Board, als Licht beim Foto |
| 1/3 | UART0 zum Jetson, 921 600 Baud |

## Offene Punkte und Abweichungen von der Spezifikation (fürs arc42)

1. **Gegenstand-Sensor nötig.** Die Spezifikation setzt voraus, dass das Modul merkt, wenn etwas
   auf der Klappe liegt. Dafür braucht es einen Sensor (Annahme: IR-Reflexsensor an GPIO 15).
   Der PIR allein reicht nicht, weil Weitergaben ohne Hand ankommen.
2. **Blockade-Erkennung braucht einen Endschalter.** Ohne ihn meldet die Firmware nie BLOCKADE.
   GPIO 12 ist ein Strapping-Pin: beim Booten muss er LOW sein (externer Pull-down).
3. **Servo-Winkel sind Platzhalter**, bis die Mechanik (Variante, eine oder zwei Achsen) feststeht.
4. **PIR wird laufend geprüft**, nicht nur vor dem Start und auf halbem Weg wie in `sim/module.py`.
   Strenger als die Spezifikation, gleiche Regel.
5. **STOERUNG: Klappe zurück in Ruhelage nur bei freiem PIR.** Die Spezifikation sagt «Servo in
   Ruhelage» und «Klappe bewegt sich nur in SORTIEREN». Beides zugleich geht nicht, wenn die
   Störung mitten in der Bewegung kommt. Gewählt: zurückfahren, aber nur ohne Hand. Bitte im
   Team bestätigen.
6. **HELLO wird wiederholt**, solange das Modul in INIT ist (jede Sekunde mit dem Heartbeat).
   Sonst blieb ein Modul für immer in INIT, wenn HELLO oder CONFIG verloren ging oder der Jetson
   später startete. Auch in `sim/module.py` nachgezogen (Szenario-Test vorhanden). Das Protokoll
   (Format, Codes) ist unverändert.
7. **HALTEN:** Der Gegenstand bleibt liegen; erst wenn der Sensor ihn nicht mehr sieht
   (Nutzer:in hat ihn entnommen), zählt ein neuer Gegenstand. Die Simulation entfernt ihn sofort.
8. **Zwei Gegenstände übereinander:** Liegt nach dem Kippen noch etwas auf der Klappe, wird es
   als neuer Vorgang gemeldet. Ob es eine Weitergabe war, entscheidet der PIR in diesem Moment
   (ähnlich der bekannten Grenze 4 im Haupt-README).
