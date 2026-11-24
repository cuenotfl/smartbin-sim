# Smart Bin – Software und Simulation

Jetson-Software der Smart Bin (Klassifikation, Fehlwurf-Entscheid, Ablaufsteuerung,
Statistik) plus eine Simulation der ganzen Station. Damit lässt sich die Software
**ohne Jetson und ohne Hardware** entwickeln und automatisch prüfen.

Architektur: siehe arc42-Dokument «Smart Bin – Softwarearchitektur» (Kap. 5, 6, 8, ADRs).

## Schnellstart

```bash
pip install -r requirements-dev.txt
python -m pytest -q                       # ~40 Tests, ein paar Sekunden
python -m sim.run --throws 1000           # simulierter Tag, Bericht zu MUSS-01..03
```

Beispielausgabe:

```
[PASS] MUSS-01   Klassifikation korrekt                 94.7 %  (target >= 90 %)
[PASS] MUSS-02   Erkannte Fehlwuerfe richtig umgelenkt  98.7 %  (target >= 90 %)
[PASS] MUSS-03   Rueckmeldung <= 2 s bei Fehlwurf      100.0 %  (target 100 %)
RESULT: PASS
```

## Aufbau

| Ordner | Inhalt | Läuft später auf |
| --- | --- | --- |
| `smartbin/core/` | Fachlicher Kern: Modell, Ports, Fehlwurf-Entscheid, Einwurf-Ablauf | Jetson (unverändert) |
| `smartbin/adapters/` | Protokoll, Serial-Gateway, SQLite, Konfiguration, ONNX-Klassifikator | Jetson (unverändert) |
| `smartbin/app.py` | Startpunkt für die echte Hardware, inkl. Dashboard | Jetson |
| `smartbin/adapters/dashboard.py` | Dashboard für den Hausdienst (ADR-005) | Jetson |
| `firmware/` | ESP32-CAM-Firmware (C++, PlatformIO) + Wokwi-Simulation, siehe `firmware/README.md` | ESP32 |
| `sim/` | Simulierte Station: ESP32-Zwilling, Serial-Leitungen, Physik, Nutzer:innen, Bericht | nur Laptop/CI |
| `sim/live.py`, `sim/viewer.html` | Interaktive 3D-Simulation im Browser (Einwurf von Hand) | nur Laptop |
| `sim/web.py`, `web/`, `tools/build_web.py` | Dieselbe Simulation als statische Website (Python im Browser) | Browser |
| `tests/` | Unit-, Vertrags- (Protokoll) und Szenario-Tests | Laptop/CI |
| `tools/evaluate_model.py` | Prüft ein trainiertes Modell gegen das Testset (MUSS-01) | Laptop/CI |
| `tools/wokwi_station.py` | Jetson-Software gegen die in Wokwi simulierte Firmware | Laptop |
| `config/` | Stationskonfigurationen (2er, 3er, zwei Mechanik-Varianten) | beides |

Der Kern kennt keine Technik (Ports and Adapters). In der Simulation werden nur die
Adapter ausgetauscht: Fake-Klassifikator statt Kamera+Modell, simulierte Leitungen
statt USB. **Kern, Protokoll und Datenbank sind derselbe Code wie auf dem Jetson.**

## Simulation steuern

```bash
python -m sim.run --config config/station-3.yaml            # Klappe kippt nur in Nachbarbehälter
python -m sim.run --config config/station-3-via-flaps.yaml  # Weitergabe von Klappe zu Klappe
python -m sim.run --config config/station-2.yaml            # 2er-Station
python -m sim.run --set classifier_accuracy=0.85            # schlechteres Modell
python -m sim.run --set baud=115200 --set image_bytes=30000 # langsame Leitung, grössere Bilder
python -m sim.run --set corrupt_rate=0.02                   # 2 % gestörte Frames
python -m sim.run --csv statistik.csv                       # Statistik wie fürs Facility Management
python -m sim.run --db demo.db                              # Statistik als SQLite, z. B. fürs Dashboard
python -m sim.run -v                                        # Log jedes Vorgangs
```

Alle Parameter: `SimParams` in `sim/station.py` und `ModuleParams` in `sim/module.py`.
Gleicher `--seed` = exakt gleicher Ablauf.

## 3D-Simulation (interaktiv)

```bash
python -m sim.live --open                                   # http://127.0.0.1:8090
python -m sim.live --config config/station-3.yaml --open    # andere Mechanik-Variante
```

Die Station läuft in Echtzeit im Browser (3D, ähnlich einer Roboter-Simulation): Klappen,
LEDs, PIR, ESP32-CAM, Leitungen zum Jetson mit den einzelnen Frames, Behälter.
Da die Bilderkennung nicht Teil dieser Software ist, gibt man **pro Einwurf von Hand ein,
was der Klassifikator meldet**: Öffnung, Fraktion (oder UNBEKANNT) und Konfidenz. Optional
auch, was der Gegenstand tatsächlich ist, um Fehlklassifikationen zu testen. Alles andere
ist derselbe Code wie in `sim.run`: echter Kern, Protokoll, Statistik und ESP32-Zwilling.

Weitere Knöpfe: Zeitraffer, Pause, «Hand bleibt in der Öffnung» (Sicherheitsstopp),
zufällige Nutzer:innen, Jetson-Ausfall, Hausdienst-Reset, Bericht (MUSS-01..03 des Laufs).
Nur lokal (`127.0.0.1`), ohne zusätzliche Pakete; three.js wird aus dem Internet (CDN) geladen.

### Als Website (ohne Installation)

Dieselbe 3D-Simulation lässt sich als statische Website veröffentlichen. Die echte
Python-Software (Kern, Protokoll, Statistik, ESP32-Zwilling) läuft dann **im Browser** der
Besucher:innen (Pyodide, Python als WebAssembly); es braucht keinen Server und keine Installation.

```bash
python tools/build_web.py                 # erzeugt site/
python -m http.server -d site 8000        # lokal testen: http://127.0.0.1:8000
```

`site/` kann auf jedem statischen Hosting liegen. Für GitHub Pages ist der Workflow
`.github/workflows/pages.yml` (im Wurzelverzeichnis des Repos) vorbereitet (einmalig: Settings → Pages → Source «GitHub Actions»);
er testet, baut und veröffentlicht bei jedem Push auf `master`. Die Konfiguration lässt sich auf
der Seite oben links wählen oder per Link teilen, z. B. `…/?config=config/station-3.yaml`.
Erster Aufruf: ca. 10–30 s Ladezeit (Python wird einmalig geladen, danach aus dem Cache).

## Erkenntnisse aus der Simulation (Stand 24.11.2026)

1. **Mechanik-Variante entscheidet MUSS-02.** Kippt die Klappe nur in den Nachbarbehälter,
   sind Fehlwürfe zwischen PET und REST nicht korrigierbar: nur ~65 % der Fehlwürfe werden
   umgelenkt (Ziel 90 %). Mit Weitergabe von Klappe zu Klappe: ~97 %.
2. **Serial-Geschwindigkeit:** Bei 115 200 Baud verletzt die Bildübertragung die 2-s-Grenze.
   Ab 460 800 Baud mit Bildern bis 30 KB ist MUSS-03 erfüllt.
3. **Protokoll braucht Robustheit:** Ohne Header-Prüfsumme, Wiederholungen und Status im
   Heartbeat brach das System bei 2 % gestörten Frames ein. Mit diesen Massnahmen hält es
   auch 5 %.
4. **Bekannte Grenze:** Landet eine Weitergabe genau dann auf einer Klappe, wenn jemand in diese
   Öffnung wirft, hält der PIR sie für einen neuen Einwurf. Der Gegenstand landet trotzdem
   richtig, die Statistik zählt den Vorgang aber als abgebrochen (~1 %).
5. **HELLO muss wiederholt werden.** Ging HELLO oder CONFIG verloren (oder startete der Jetson
   nach dem Modul), blieb das Modul für immer in INIT. Jetzt wiederholt es HELLO jede Sekunde,
   bis CONFIG kommt (Simulation und Firmware, Protokoll unverändert). Weitere Punkte aus der
   Firmware (Gegenstand-Sensor, Endschalter, Verhalten in STOERUNG): `firmware/README.md`.

## Echtes Modell (sobald Fotos da sind)

Die Bilderkennung entsteht separat. Für diese Software ist sie nur die Schnittstelle
`ClassifierPort` (`smartbin/core/ports.py`): Bild rein, PET / ALU / REST (oder UNBEKANNT) plus
Konfidenz raus. Liefert sie ein `.onnx`, passt der vorhandene `OnnxClassifier`; prüfen mit:

```bash
pip install -r requirements-ml.txt
python tools/evaluate_model.py --model model.onnx --testset testset --labels ALU PET REST
```

Das gleiche `.onnx` läuft später auf dem Jetson (optional als TensorRT-Engine).

## Dashboard für den Hausdienst

Läuft mit `smartbin.app` auf dem Jetson mit (Port 8080): Einwürfe pro Tag und Fraktion,
CSV-Download, Reset-Knopf für Module in STOERUNG. Ohne Station, nur lesend:

```bash
pip install -r requirements-dashboard.txt
python -m sim.run --throws 1000 --db demo.db
python -m smartbin.adapters.dashboard --db demo.db    # http://127.0.0.1:8080
```

Das Dashboard hat **kein Login**. Standardmässig nur auf `127.0.0.1`; für Zugriff im Netz
(`--dashboard-host`) braucht es vorher eine Absicherung (offener Punkt).

## Arbeiten mit KI

`CLAUDE.md` beschreibt Regeln und Befehle für einen KI-Assistenten (z. B. Claude Code), der
diesen Code weiterentwickelt. Jede Änderung läuft durch die CI (`.github/workflows/ci.yml`):
Tests + simulierter Tag + gestörte Leitung.
