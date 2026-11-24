# Smart Bin – Softwarearchitektur (arc42)

Nov 24, 2026 · @Fluffi · Team: Elia Amani, Julian Baumann, Flavian Cuenot

## 1 Einführung und Ziele

Smart Bin ist ein nachrüstbares System für eine FHNW-Recyclingstation (PET · Alu · Rest), das jeden Einwurf per Kamera klassifiziert, Fehlwürfe per Klappe in den richtigen Behälter umlenkt, die Person innerhalb von 2 s per LED informiert und anonyme Statistikdaten speichert.

### 1.1 Aufgabenstellung

Kernablauf gemäss Projektsteckbrief:

1. Gegenstand wird in eine Öffnung eingeworfen und auf der Klappe des Moduls gehalten.
2. Das System bestimmt die Fraktion (PET, ALU, REST).
3. Die erkannte Fraktion wird mit der gewählten Öffnung verglichen.
4. Bei einem Fehlwurf erhält die Nutzer:in einen Hinweis.
5. Die Klappe kippt den Gegenstand in den richtigen Behälter.
6. Der Vorgang wird abgeschlossen und als Statistik-Eintrag gespeichert.

Nicht klassifizierbare Gegenstände werden zurückgehalten bzw. der Restfraktion zugeführt.

### 1.2 Qualitätsziele (Top 4)

| Prio | Qualitätsziel (ISO 25010) | Bedeutung für Smart Bin | Quelle |
| --- | --- | --- | --- |
| 1 | Betriebssicherheit (Safety) | Bei Störung geht jede Bewegung in einen sicheren Zustand; keine Bewegung bei Eingriff in die Öffnung | MUSS-05 |
| 2 | Funktionale Eignung (Korrektheit) | ≥ 90 % des Testsets korrekt klassifiziert, ≥ 90 % der Fehlwürfe korrekt umgelenkt | MUSS-01, MUSS-02 |
| 3 | Effizienz (Antwortzeit) | Rückmeldung bei Fehlwurf ≤ 2 s nach Einwurf | MUSS-03 |
| 4 | Wartbarkeit / Flexibilität | 2er- und 3er-Station sowie weitere Konfigurationen ohne Codeänderung; Modell austauschbar | MUSS-04, KANN-03 |

### 1.3 Stakeholder

| Rolle | Erwartung an die Software |
| --- | --- |
| Nutzer:innen (Studierende, Mitarbeitende, Gäste) | Sofort verständlicher Hinweis, keine Wartezeit, keine Gefährdung |
| Hausdienst | Störungen klar erkennbar, einfacher Neustart, Reinigung ohne Software-Eingriff |
| Facility Management | Auswertbare Statistik (Fehlwürfe pro Tag und Fraktion), Export als CSV |
| Projektteam (Amani, Baumann, Cuenot) | Klare Aufteilung Jetson / ESP32, testbar ohne Hardware, Modell einfach neu trainierbar |
| Dozierende | Nachvollziehbare Architektur nach arc42, begründete Entscheidungen (ADRs) |

## 2 Randbedingungen

Die Hardware ist durch das Funktionsdiagramm V2 weitgehend festgelegt; die Software muss sich daran anpassen.

| Art | Randbedingung | Konsequenz für die Architektur |
| --- | --- | --- |
| Technisch | Zentralrechner NVIDIA Jetson Nano Dev Kit im Mother-Modul (PET) | Klassifikation (GPU) und Ablaufsteuerung laufen zentral; Linux, Python |
| Technisch | Pro Modul ein ESP32-CAM mit integrierter Kamera, PIR-Sensor, SMD-LED, 2 Servos | Echtzeitnahe Ansteuerung (PWM, GPIO, Safety) auf dem ESP32; Firmware in C++ |
| Technisch | Verbindung Jetson ↔ ESP32 je über USB (Serial), sternförmig, 3 Kanäle | Eigenes Nachrichtenprotokoll über UART nötig; Bildtransfer über Serial begrenzt die Bandbreite |
| Technisch | Batteriebetrieb, kein Netzkabel (Typ/Spannung offen) | Energiesparende Ruhezustände; Datenpersistenz muss Spannungsabfall überstehen (KANN-05) |
| Technisch | Alle drei Module identisch aufgebaut | Eine Firmware für alle ESP32, Unterschied nur über Konfiguration (Modul-ID, Fraktion) |
| Organisatorisch | Team von 3 Personen, ein Semester (HS 2026), ein Prototyp an einer Station | Einfache Architektur ohne Cloud oder Microservices; ein Deployment |
| Organisatorisch | Abnahmetest mit definiertem Testset | Klassifikation nur für Testset-Gegenstände garantiert; Testset als Artefakt versioniert |
| Konventionen | Datenschutz (revDSG): keine Personenerkennung, keine personenbezogenen Daten | Bilder werden nur flüchtig verarbeitet, nicht gespeichert; Statistik ohne Personenbezug |
| Konventionen | Dokumentation nach arc42, Entscheidungen als ADR (Nygard) | Docs-as-Code im Repository (`docs/arc42`, `docs/adr`) |

## 3 Kontextabgrenzung

Smart Bin arbeitet autark an der Station: Nutzer:innen und Hausdienst interagieren direkt am Gerät, das Facility Management nur über ein lokales Web-Dashboard.

### 3.1 Fachlicher Kontext

&#91;embedded content: Fachlicher Kontext · Smart Bin als Blackbox\]

Die Personenerkennung ist bewusst ausserhalb des Systems: die Kamera sieht nur den Gegenstand auf der Klappe.

### 3.2 Technischer Kontext

| Nachbar | Kanal / Protokoll | Daten |
| --- | --- | --- |
| Nutzer:in | Mechanische Öffnung; SMD-LED (optional Summer) | Gegenstand rein; Farbsignal raus |
| Hausdienst | Taster am Mother-Modul bzw. Dashboard | Störung quittieren, Neustart |
| Facility Management | WLAN, HTTP (Browser) | Dashboard-Ansicht, CSV-Download |
| Recyclingbehälter | Mechanisch (Klappe kippt) | Gegenstand fällt in Behälter |
| Batterie / DC-DC-Regler | 5 V Jetson und ESP32, 5–6 V Servos | Energie |

## 4 Lösungsstrategie

Die Software ist ein zweistufiges System: ein modularer Monolith auf dem Jetson entscheidet, drei identische ESP32-Firmwares führen aus und sichern ab.

| Qualitätsziel | Lösungsansatz | Details in |
| --- | --- | --- |
| Betriebssicherheit | Safety-Logik lokal auf jedem ESP32 (Watchdog, Endlage, PIR-Sperre, Timeout); Jetson kann Bewegungen nur anfordern, nicht erzwingen | Kap. 8.2, ADR-003 |
| Korrektheit der Klassifikation | Bildklassifikation mit vortrainiertem CNN (Transfer Learning) auf dem Jetson-GPU; Konfidenzschwelle, darunter «UNBEKANNT» → REST | Kap. 5.2, ADR-002 |
| Antwortzeit ≤ 2 s | Ereignisgesteuerter Ablauf: ESP32 meldet Einwurf sofort und sendet 1 komprimiertes JPEG; Modell bleibt im Speicher geladen | Kap. 6, ADR-004 |
| Wartbarkeit / Austauschbarkeit | Ports and Adapters im Jetson-Kern: Klassifikator, Modul-Kommunikation, Speicher, Dashboard sind Adapter hinter Ports | Kap. 5.2, ADR-001 |
| Flexibilität 2er/3er-Station | Stationskonfiguration als Datei (`station.yaml`): Anzahl Module, Fraktion pro Modul, Nachbarschaft für Kipprichtung | Kap. 8.6 |
| Zuverlässige Datenerfassung | Ereignisse sofort in SQLite (WAL-Modus) schreiben; CSV-Export auf Anfrage | Kap. 8.3, ADR-005 |
| Datenschutz | Bilder nur im RAM, nach Klassifikation verworfen; Statistik enthält nur Zeit, Modul, Fraktionen, Ergebnis | Kap. 8.5 |

Architekturmuster im Überblick:

- **Ports and Adapters** für die Jetson-Anwendung (Fachlogik ohne Abhängigkeit zu Kamera, Serial oder Datenbank).
- **Pipes and Filters** innerhalb der Klassifikation (Dekodieren → Vorverarbeiten → Inferenz → Entscheid).
- **Zustandsautomat** für den Einwurf-Ablauf, sowohl im Jetson (pro Vorgang) als auch im ESP32 (pro Modul).
- **Bewusst kein Microservice-Ansatz**: ein Gerät, ein Team, keine unabhängige Skalierung nötig.

## 5 Bausteinsicht

Das System besteht aus zwei Software-Bausteinen: der Jetson-Anwendung (entscheidet) und der Modul-Firmware (führt aus, dreimal identisch deployt).

### 5.1 Ebene 1 – Whitebox Smart Bin

| Baustein | Verantwortung | Technologie | Schnittstellen |
| --- | --- | --- | --- |
| Jetson-Anwendung `smartbin-core` | Klassifikation, Fehlwurf-Entscheid, Ablaufsteuerung über alle Module, Statistik, Dashboard | Python 3, PyTorch/TensorRT, FastAPI, SQLite | Serial-Protokoll (Kap. 8.7), HTTP |
| Modul-Firmware `smartbin-module` | Bild aufnehmen, Klappe/Servos ansteuern, LED, PIR, lokale Safety | C++ (Arduino-Framework / ESP-IDF) | Serial-Protokoll, GPIO, PWM |
| Klassifikationsmodell (Artefakt) | Trainierte Gewichte, versioniert mit Testset-Ergebnis | ONNX → TensorRT-Engine | Datei, geladen vom Klassifikator-Adapter |
| Trainings-Pipeline (offline) | Bilder sammeln, Modell trainieren, gegen Testset prüfen | Python, Jupyter, Laptop/Colab | Erzeugt das Modell-Artefakt |

### 5.2 Ebene 2 – Whitebox Jetson-Anwendung

&#91;embedded content: Bausteinsicht Ebene 2 · Jetson-Anwendung als Ports and Adapters\]

Der Kern hängt nur von seinen Ports ab; für Tests ersetzen Fake-Adapter Kamera, Serial und Datenbank (ADR-001). Das Serial-Gateway ist ein Baustein, der beide Rollen übernimmt: es treibt den Kern mit eingehenden Ereignissen und implementiert den `ModulePort` für ausgehende Befehle.

| Baustein | Verantwortung |
| --- | --- |
| Einwurf-Ablauf | Startet pro `ITEM_DETECTED` einen Vorgang, fordert Klassifikation an, löst Hinweis und Sortieren aus, schliesst ab |
| Fehlwurf-Entscheid | Vergleicht gewählte und erkannte Fraktion; unter der Konfidenzschwelle → `UNBEKANNT`/REST; bestimmt die Route (Richtung) aus der Stationskonfiguration |
| Statistik | Erzeugt ein Ereignis pro Vorgang, liefert Auswertungen pro Tag und Fraktion |
| Klassifikator-Adapter | Pipes and Filters: JPEG dekodieren → zuschneiden/normalisieren → Inferenz → Fraktion + Konfidenz |
| Konfigurations-Adapter | Liest `station.yaml` beim Start (Port `StationConfigPort`) |

### 5.3 Ebene 2 – Whitebox Modul-Firmware

| Baustein | Verantwortung |
| --- | --- |
| Zustandsautomat | Steuert den Ablauf im Modul (Kap. 8.1) |
| Safety-Monitor | Prüft PIR, Heartbeat, Timeouts, Servo-Endlage; darf jede Bewegung sperren |
| Kamera-Treiber | Nimmt ein JPEG auf Anforderung bzw. nach Einwurf auf |
| Servo-Treiber | PWM für 2 Servos, Kipp-Profile `DIREKT`, `LINKS`, `RECHTS`, Grundstellung |
| LED-Treiber | Signalmuster (OK, Fehlwurf, Störung) |
| Protokoll-Link | Frames kodieren/dekodieren, CRC, Heartbeat |

## 6 Laufzeitsicht

Der wichtigste Ablauf ist der Fehlwurf: Hinweis (Schritt 8) kommt vor der Bewegung, damit die 2-s-Grenze nicht vom Kippen abhängt.

### 6.1 Fehlwurf mit Umlenkung

&#91;embedded content: Laufzeitsicht · Fehlwurf mit Umlenkung, 12 Schritte\]

Die `seq` aus Schritt 2 begleitet alle Nachrichten und Log-Einträge des Vorgangs. Liegt die Ziel-Fraktion nicht neben dem Modul, wiederholen sich Schritte 9–11 beim Nachbarmodul (siehe offene Punkte).

### 6.2 Weitere Szenarien

| Szenario | Abweichung vom Ablauf 6.1 | Ergebnis in Statistik |
| --- | --- | --- |
| Korrekter Einwurf | Schritt 6: gewählt = erkannt; Schritt 7 `SIGNAL(OK)` (grün kurz); Schritt 9 `SORT(DIREKT)` | `KORREKT` |
| Unbekannter Gegenstand | Schritt 5: Konfidenz unter Schwelle → `UNBEKANNT`; Gegenstand wird in REST gekippt bzw. bei `HALTEN` zurückgehalten | `UNBEKANNT` |
| Hand in der Öffnung | Schritt 10: PIR meldet Bewegung; ESP32 wartet, kippt erst nach Freigabe; nach Timeout `FAULT` | ggf. `ABGEBROCHEN` |
| Blockade | Servo erreicht Endlage nicht; ESP32 fährt in sicheren Zustand, sendet `FAULT`; LED Störung bis Quittierung | `ABGEBROCHEN` |
| Jetson-Ausfall | Heartbeat fehlt 3 s; ESP32 geht in sicheren Zustand, danach Notbetrieb «durchlassen» (kippt in eigenen Behälter) | keine Einträge bis Neustart |
| Systemstart | Jetson lädt Konfiguration und Modell, wartet auf `HELLO` aller Module, sendet `CONFIG`; Module melden `BEREIT` | – |

## 7 Verteilungssicht

Alle Software läuft an der Station selbst: ein Jetson Nano im Mother-Modul und eine identische Firmware auf drei ESP32-CAM.

&#91;embedded content: Verteilungssicht · 4 Knoten an einer Station\]

| Knoten | Deployte Artefakte | Deployment-Weg |
| --- | --- | --- |
| Jetson Nano | `smartbin-core`, `model.engine`, `station.yaml`, `smartbin.db` | Git-Checkout + Installationsskript; Start als systemd-Dienst beim Booten |
| ESP32-CAM (×3) | `smartbin-module` (gleiches Binary), Modul-ID im NVS gespeichert | Flashen über USB (PlatformIO) |
| Laptop/Handy | keine (nur Browser) | – |

Umgebungen: Entwicklung auf Laptops mit Fake-Adaptern, Labor-Funktionsmuster mit einem Modul, Abnahme an der FHNW-Station mit drei Modulen.

## 8 Querschnittliche Konzepte

Diese Regeln gelten für alle Bausteine und werden einmal entschieden, damit Jetson-Anwendung und Firmware konsistent bleiben.

### 8.1 Ablaufsteuerung als Zustandsautomat

Jedes Modul (ESP32) führt einen expliziten Zustandsautomaten; der Jetson führt pro Einwurf einen Vorgang mit eigener ID (`seq`). Zustände werden nie implizit über Flags abgebildet, sondern als Enum mit erlaubten Übergängen. Jeder Zustand mit Bewegung oder Warten hat ein Timeout.

&#91;embedded content: Zustandsautomat der Modul-Firmware · 6 Zustände\]

Aus ERKANNT, WARTEN und SORTIEREN führt jeder Fehler in STOERUNG; nur `RESET_FAULT` führt zurück. Fällt der Jetson dauerhaft aus, wechselt das Modul nach der Quittierung in den Notbetrieb «durchlassen».

### 8.2 Betriebssicherheit und Fehlerbehandlung

- **Safety lokal:** Der ESP32 bewegt die Servos nur im Zustand `SORTIEREN` und nur, wenn der PIR-Sensor keine Hand in der Öffnung meldet. Diese Prüfung hängt nicht vom Jetson ab.
- **Sicherer Zustand:** Servos fahren in die Grundstellung (Klappe waagrecht, hält den Gegenstand) bzw. werden bei Blockade stromlos geschaltet; LED zeigt Störung.
- **Watchdogs:** Hardware-Watchdog im ESP32; Heartbeat Jetson ↔ ESP32 jede Sekunde. Bleibt der Heartbeat 3 s aus, geht das Modul in `STOERUNG` (Annahme, im Test zu kalibrieren).
- **Fachliche Fehler** (z. B. Klassifikation unsicher): kein Fehler, sondern reguläres Ergebnis `UNBEKANNT` → Restfraktion.
- **Technische Fehler** (Bild unlesbar, Serial-Timeout, Servo-Endlage nicht erreicht): Fehlercode im `FAULT`-Telegramm, Eintrag im Log, Vorgang mit Ergebnis `ABGEBROCHEN` in der Statistik.
- **Quittierung:** Eine Störung wird nur durch Hausdienst (Taster bzw. Dashboard-Button) oder Neustart quittiert, nie automatisch.

### 8.3 Persistenz

- Eine SQLite-Datenbank auf dem Jetson (Tabelle `einwurf`), Modus WAL, jeder Vorgang wird sofort nach Abschluss geschrieben (KANN-05).
- Gespeichert werden: Zeitstempel, Modul-ID, gewählte Fraktion, erkannte Fraktion, Konfidenz, Ergebnis (`KORREKT`, `FEHLWURF_UMGELENKT`, `UNBEKANNT`, `ABGEBROCHEN`), Dauer in ms.
- Keine Bilder. Für Training und Fehleranalyse gibt es einen separaten, standardmässig ausgeschalteten Sammelmodus, der nur im Labor aktiviert wird.
- Export: CSV (Semikolon, UTF-8, ISO-8601-Zeit) für Tabellenkalkulation (KANN-04).

### 8.4 Logging und Diagnose

- Jetson: strukturiertes Logging (JSON-Zeilen) mit `seq` als Correlation-ID; jede Nachricht von und zu einem Modul enthält dieselbe `seq`.
- ESP32: Fehlercodes und Zustandswechsel werden an den Jetson gemeldet und dort mitgeloggt; zusätzlich Debug-Ausgabe nur im Entwicklungsmodus.
- Dashboard zeigt Zustand jedes Moduls und die letzten Fehler.

### 8.5 Datenschutz

Kamerabilder zeigen nur das Innere der Einwurföffnung. Sie werden im RAM klassifiziert und danach verworfen. Die Statistik enthält keinen Personenbezug. Das Dashboard ist nur im lokalen WLAN erreichbar und mit einem Passwort geschützt.

### 8.6 Konfiguration

`station.yaml` auf dem Jetson beschreibt die Station: Liste der Module mit Modul-ID, Serial-Port, Fraktion, Position (links → rechts) sowie Konfidenzschwelle und Timeouts. Beim Start sendet der Jetson jedem Modul ein `CONFIG`-Telegramm. Eine 2er-Station (Allgemeiner Abfall + PET) unterscheidet sich nur durch diese Datei (MUSS-04, KANN-03).

### 8.7 Kommunikationsprotokoll Jetson ↔ ESP32

Binäre Frames über USB-Serial: `0xA5 | Typ u8 | seq u16 | Länge u32 | CRC8 Header | Nutzdaten | CRC16`. Die Header-Prüfsumme (CRC-8) erkennt eine verfälschte Länge sofort; ohne sie verschluckte der Empfänger in der Simulation nachfolgende Frames. Nach Postel: der Sender hält sich streng an das Format, der Empfänger ignoriert unbekannte Typen und verwirft Frames mit falscher Prüfsumme. Protokollversion wird im `HELLO` ausgetauscht. Die verbindlichen Testvektoren für die Firmware stehen in `tests/test_protocol.py`.

Robustheit (in der Simulation bis 5 % gestörte Frames geprüft):

- Befehle sind pro (Modul, `seq`) idempotent; der Jetson darf `SIGNAL` und `SORT` erneut senden.
- Bekommt ein Modul 0,6 s nach dem Bild kein `SORT`, sendet es `ITEM_DETECTED` und `IMAGE` erneut.
- `SORT` enthält zusätzlich das LED-Muster, damit ein verlorenes `SIGNAL` die Rückmeldung nicht verhindert.
- Der Heartbeat des Moduls enthält Zustand und letzten abgeschlossenen `SORT`; verlorene `FAULT`- oder `SORT_DONE`-Frames werden so innert 1 s erkannt.

| Richtung | Nachricht | Inhalt / Bedeutung |
| --- | --- | --- |
| ESP32 → Jetson | `HELLO` | Modul-ID, Firmware- und Protokollversion |
| ESP32 → Jetson | `ITEM_DETECTED` | Gegenstand liegt auf der Klappe (neue `seq`); Quelle: Öffnung oder Übergabe vom Nachbarn |
| ESP32 → Jetson | `IMAGE` | JPEG-Bild zur `seq` |
| ESP32 → Jetson | `SORT_DONE` | Klappe hat gekippt und ist zurück in Grundstellung |
| ESP32 → Jetson | `FAULT` | Fehlercode, aktueller Zustand |
| ESP32 → Jetson | `HEARTBEAT` | Jede Sekunde: Zustand, letzter abgeschlossener `SORT` |
| Jetson → ESP32 | `HEARTBEAT` | Lebenszeichen jede Sekunde |
| Jetson → ESP32 | `CONFIG` | Fraktion, Position, Routing-Variante |
| Jetson → ESP32 | `SORT` | Ziel (`DIREKT`, `LINKS`, `RECHTS`, `HALTEN`) und LED-Muster |
| Jetson → ESP32 | `SIGNAL` | LED-Muster (z. B. grün kurz, rot blinkend) |
| Jetson → ESP32 | `RESET_FAULT` | Störung quittieren |

### 8.8 Testkonzept

- **Fachlogik:** Unit-Tests gegen Fake-Adapter (Fake-Klassifikator, Fake-Modul), läuft ohne Hardware auf jedem Laptop.
- **Klassifikator:** Offline-Auswertung gegen das versionierte Testset; Treffergenauigkeit pro Fraktion als Kennzahl (Ziel ≥ 90 %).
- **Protokoll:** Contract-Tests für Frames (Encoder/Decoder in Python und C++ gegen dieselben Testvektoren).
- **Firmware:** Safety-Fälle (PIR aktiv, Timeout, Blockade) auf dem Funktionsmuster prüfen.
- **System:** Abnahmetest mit Testset an der Station, Messung von Erkennungsrate, Sortierrate und Reaktionszeit.

### 8.9 Simulation ohne Jetson

Solange kein Jetson verfügbar ist, läuft die Jetson-Anwendung unverändert auf dem Laptop gegen eine simulierte Station (Repository `smartbin-sim`, ADR-006). Ersetzt werden nur Adapter:

| Echt (Jetson) | In der Simulation |
| --- | --- |
| ONNX/TensorRT-Klassifikator | Fake-Klassifikator mit einstellbarer Genauigkeit; für echte Fotos dasselbe `.onnx` mit ONNX Runtime auf der CPU |
| USB-Serial (pyserial) | Simulierte Leitung mit Baudrate und Fehlerinjektion |
| 3 × ESP32-Firmware | Digitaler Zwilling der Firmware (gleicher Zustandsautomat, gleiche Safety-Regeln, echtes Protokoll) |
| Station, Personen | Physik (Klappen, Behälter, PIR) und zufällige Einwürfe |

Die Simulation läuft ereignisgesteuert: 1000 Einwürfe (ca. 2 h Stationszeit) dauern etwa 1 s. Ein Bericht prüft MUSS-01 bis MUSS-03 automatisch. Die CI (GitHub Actions) führt bei jedem Push die Tests, einen simulierten Tag und einen Tag mit gestörter Leitung aus. Weiterentwickelt wird der Code KI-gestützt; die Regeln dafür stehen in `CLAUDE.md`.

Erkenntnisse (Stand 24.11.2026):

| Frage | Ergebnis | Konsequenz |
| --- | --- | --- |
| Reicht «Klappe kippt in Nachbarbehälter»? | Nur \~65 % der Fehlwürfe korrigierbar (PET ↔ REST unerreichbar) | MUSS-02 nur mit Weitergabe von Klappe zu Klappe (\~97 %) oder anderer Mechanik |
| Welche Baudrate? | 115 200 Baud verletzt die 2-s-Grenze | ≥ 460 800 Baud, Bilder ≤ 30 KB |
| Wie viel Inferenzzeit ist erlaubt? | Bis ca. 0,3 s unkritisch, ab ca. 0,8 s Grenzfälle über 2 s | Auf dem Jetson messen |
| Störungen auf der Leitung? | Ohne Wiederholungen brach das System bei 2 % Störungen ein | Robustheitsregeln in Kap. 8.7 |

## 9 Architekturentscheidungen

Sechs Entscheidungen prägen die Struktur; jede ist im Repository als eigene Datei unter `docs/adr/` abgelegt (Format nach Nygard).

| ADR | Titel | Status |
| --- | --- | --- |
| ADR-001 | Ports and Adapters für die Jetson-Anwendung | Akzeptiert |
| ADR-002 | Klassifikation zentral auf dem Jetson, nicht auf dem ESP32 | Akzeptiert |
| ADR-003 | Safety-Logik lokal im ESP32 | Akzeptiert |
| ADR-004 | Binäres Frame-Protokoll über USB-Serial | Vorgeschlagen |
| ADR-005 | SQLite als lokaler Datenspeicher mit Web-Dashboard | Akzeptiert |
| ADR-006 | Entwicklung ohne Jetson gegen eine simulierte Station | Akzeptiert |

### ADR-001: Ports and Adapters für die Jetson-Anwendung

**Kontext:** Klassifikator, Kommunikation und Speicher werden während des Projekts mehrfach geändert (Modellwechsel, Protokolländerungen). Die Fachlogik soll ohne Hardware testbar sein.

**Entscheidung:** Wir trennen den fachlichen Kern (Einwurf-Ablauf, Fehlwurf-Entscheid, Statistik) von der Technik. Der Kern definiert Ports (`ClassifierPort`, `ModulePort`, `EventStorePort`); Adapter implementieren sie.

**Konsequenzen:**

- \+ Kern mit Fake-Adaptern testbar, Modell austauschbar
- \+ Klare Arbeitsteilung im Team
- − Mehr Schnittstellen und Klassen als ein einfaches Skript

**Alternativen:** Klassische Schichtenarchitektur – verworfen, weil die Fachlogik dann von der Serial- und Datenbankschicht abhängt.

### ADR-002: Klassifikation zentral auf dem Jetson

**Kontext:** Ziel ≥ 90 % Erkennung auch bei verformten oder verschmutzten Gegenständen. Der ESP32 hat wenig RAM und keine GPU; der Jetson Nano hat eine GPU.

**Entscheidung:** Der ESP32 nimmt nur das Bild auf und sendet es; die Inferenz läuft auf dem Jetson mit einem vortrainierten CNN (z. B. MobileNet via Transfer Learning, beschleunigt mit TensorRT).

**Konsequenzen:**

- \+ Grössere, genauere Modelle möglich; ein Modell für alle Module
- \+ Modell-Update an einer Stelle
- − Bild muss über Serial übertragen werden (Latenz)
- − Jetson ist Single Point of Failure für die Klassifikation

**Alternativen:** TinyML direkt auf dem ESP32-CAM – verworfen wegen voraussichtlich zu geringer Genauigkeit; bleibt Fallback-Option.

### ADR-003: Safety-Logik lokal im ESP32

**Kontext:** MUSS-05 verlangt einen sicheren Zustand bei Störung. Der Jetson (Linux) ist nicht echtzeitfähig und kann abstürzen oder hängen.

**Entscheidung:** Jeder ESP32 entscheidet selbst, ob eine Bewegung zulässig ist (PIR frei, Zustand `SORTIEREN`, Heartbeat vorhanden) und überwacht Timeouts. Der Jetson kann Bewegungen nur anfordern.

**Konsequenzen:**

- \+ Sicherer Zustand auch bei Ausfall von Jetson oder Kabel
- − Safety-Logik muss in C++ sorgfältig getestet werden

**Alternativen:** Zentrale Steuerung aller Bewegungen durch den Jetson – verworfen.

### ADR-004: Binäres Frame-Protokoll über USB-Serial

**Kontext:** Die Hardware sieht drei USB-Serial-Verbindungen vor. Über dieselbe Leitung laufen Steuerbefehle und Bilder.

**Entscheidung:** Wir nutzen ein eigenes binäres Frame-Format mit Längenfeld und CRC16 (Kap. 8.7) statt Textzeilen.

**Konsequenzen:**

- \+ Bilder und Befehle auf einem Kanal, Übertragungsfehler erkennbar
- − Eigener Encoder/Decoder in Python und C++ nötig

**Alternativen:** JSON-Zeilen mit Base64-Bild – verworfen (ca. 33 % mehr Daten); WLAN/MQTT – verworfen, da Hardware USB vorsieht. Status «vorgeschlagen», bis die Bildübertragungszeit gemessen ist.

### ADR-005: SQLite lokal mit Web-Dashboard

**Kontext:** Statistik muss persistent sein und als Tabelle auswertbar; das Facility Management will Daten ohne Kabel abrufen.

**Entscheidung:** Wir speichern Ereignisse in SQLite auf dem Jetson. Ein kleiner Webserver (z. B. FastAPI) zeigt ein Dashboard im lokalen WLAN und bietet CSV-Download.

**Konsequenzen:**

- \+ Keine Cloud, keine externe Infrastruktur, Datenschutz einfach
- − Daten nur im Netz der Station erreichbar; WLAN-Zugang an der FHNW klären

**Alternativen:** Nur CSV-Datei auf USB-Stick – verworfen (unbequem); Cloud-Backend – verworfen (Out of Scope, Datenschutz).

### ADR-006: Entwicklung ohne Jetson gegen eine simulierte Station

**Kontext:** Das Team hat keinen Zugriff auf einen Jetson Nano, und die Mechanik existiert noch nicht. Trotzdem sollen Fachlogik, Protokoll und die MUSS-Kriterien früh geprüft werden.

**Entscheidung:** Wir entwickeln die Jetson-Anwendung auf dem Laptop und testen sie gegen eine ereignisgesteuerte Simulation der Station (Firmware-Zwilling, simulierte Leitungen, Physik, Nutzer:innen). Kern, Protokoll und Datenbank sind derselbe Code wie später auf dem Jetson; nur Adapter werden ersetzt. Code und Tests werden KI-gestützt gepflegt, die CI prüft jede Änderung.

**Konsequenzen:**

- \+ Frühe, wiederholbare Aussagen zu MUSS-01 bis MUSS-03 und zu Hardware-Parametern (Baudrate, Bildgrösse)
- \+ Der Firmware-Zwilling ist eine ausführbare Spezifikation für die C++-Firmware
- − Echte Jetson-Latenz und reale Gegenstände (Verformung, Nässe) bildet die Simulation nicht ab
- − Simulation und Firmware können auseinanderlaufen; Contract-Tests für das Protokoll sind Pflicht

**Alternativen:** Warten auf Hardware – verworfen (zu spät). Roboter-Simulator (Gazebo, Webots) – verworfen, für eine Klappe mit zwei Servos unverhältnismässig.

## 10 Qualitätsanforderungen

Die Qualitätsziele aus Kapitel 1 werden hier als messbare Szenarien (Quelle → Stimulus → Artefakt → Antwort → Mass) konkretisiert.

| ID | Merkmal (ISO 25010) | Szenario | Messgrösse |
| --- | --- | --- | --- |
| QS-1 | Betriebssicherheit | Eine Hand greift während des Kippens in die Öffnung; der PIR meldet Bewegung | Servo stoppt bzw. fährt nicht an; Reaktion ≤ 100 ms (Annahme, zu messen) |
| QS-2 | Betriebssicherheit | Der Jetson stürzt während eines Vorgangs ab | Modul erkennt fehlenden Heartbeat und geht innerhalb 3 s in den sicheren Zustand |
| QS-3 | Funktionale Korrektheit | Ein Gegenstand aus dem Testset wird in eine beliebige Öffnung geworfen | ≥ 90 % korrekt klassifiziert (MUSS-01) |
| QS-4 | Funktionale Korrektheit | Ein erkannter Fehlwurf wird umgelenkt | ≥ 90 % ohne Eingriff im richtigen Behälter (MUSS-02) |
| QS-5 | Effizienz | Eine Person wirft PET in die Alu-Öffnung | Rote LED leuchtet ≤ 2 s nach Einwurf (MUSS-03) |
| QS-6 | Zuverlässigkeit | Die Batterie wird während des Betriebs abgehängt | Alle bereits abgeschlossenen Vorgänge sind nach Neustart vorhanden (KANN-05) |
| QS-7 | Flexibilität | Die Station wird von 3 auf 2 Fraktionen umgebaut | Nur `station.yaml` ändern, kein Code, Inbetriebnahme ≤ 15 min |
| QS-8 | Wartbarkeit | Ein neu trainiertes Modell soll eingesetzt werden | Modelldatei ersetzen und Dienst neu starten, keine Codeänderung |
| QS-9 | Benutzbarkeit | Das Facility Management will Fehlwürfe pro Tag und Fraktion | CSV-Download im Dashboard, direkt in Excel auswertbar (KANN-04) |
| QS-10 | Sicherheit (Security) | Eine fremde Person im WLAN ruft das Dashboard auf | Zugriff nur mit Passwort; keine personenbezogenen Daten vorhanden |

## 11 Risiken und technische Schulden

Das grösste Software-Risiko ist, dass Bildübertragung und Inferenz zusammen die 2-s-Grenze sprengen; es wird zuerst gemessen.

| Risiko | Auswirkung | Massnahme |
| --- | --- | --- |
| Bildübertragung über Serial zu langsam | MUSS-03 verfehlt | Frühe Messung; Auflösung und JPEG-Qualität reduzieren; Baudrate erhöhen; Fallback: Klassifikation auf dem ESP32 (Alternative in ADR-002) |
| Klassifikation bei verformten, nassen, schmutzigen Gegenständen unzuverlässig | MUSS-01 verfehlt | Testset aus realen Fehlwürfen der Station; Datenaugmentierung; Beleuchtung (LED) in der Öffnung |
| Kamera-Optik verschmutzt mit der Zeit | Schleichender Genauigkeitsverlust | Konfidenzwerte im Dashboard beobachten; Reinigungshinweis bei sinkender Konfidenz |
| PIR erkennt Hand in der Öffnung nicht zuverlässig | Safety-Lücke | Mechanischer Schutz hat Vorrang; PIR nur als zusätzliche Massnahme; ggf. IR-Lichtschranke |
| Gegenstand bleibt nach dem Kippen liegen | Folgevorgang falsch | `SORT_DONE` erst nach erneuter Leer-Prüfung per Bild; sonst `STOERUNG` |
| Jetson als Single Point of Failure | Station sortiert nicht mehr | Modul fällt in Modus «durchlassen» (Klappe kippt in den eigenen Behälter) wie ein passiver Eimer |
| Technische Schuld: eigenes Protokoll ohne Standard | Mehr Pflegeaufwand | Contract-Tests, Protokollversion im `HELLO` |
| Mechanik kippt nur in Nachbarbehälter | MUSS-02 verfehlt (Simulation: \~65 %) | Weitergabe von Klappe zu Klappe vorsehen oder Mechanik für nicht benachbarte Behälter entwerfen (Kap. 8.9) |
| Übergabe trifft gleichzeitig mit einem Einwurf auf eine Klappe | PIR hält die Übergabe für einen Einwurf; Gegenstand landet richtig, Statistik zählt ABGEBROCHEN (\~1 %) | Akzeptiert für den Prototyp; sonst eigener Sensor für die Übergabe |
| Simulation weicht von Hardware ab | Falsche Sicherheit bei MUSS-03 | Latenzen (Kamera, Inferenz, Kippen) am Funktionsmuster messen und in die Simulationsparameter übernehmen |

## 12 Glossar

| Begriff | Bedeutung |
| --- | --- |
| Fraktion | Abfallart eines Behälters: PET, ALU (Dosen) oder REST (Allgemeiner Abfall) |
| Einwurf | Ein Gegenstand wird in eine Öffnung geworfen; erzeugt genau einen Vorgang |
| Vorgang | Ablauf von Erkennung bis Abschluss eines Einwurfs, eindeutig durch `seq` identifiziert |
| Gewählte Fraktion | Fraktion der Öffnung, in die die Person geworfen hat |
| Erkannte Fraktion | Ergebnis der Klassifikation |
| Fehlwurf | Gewählte ≠ erkannte Fraktion |
| UNBEKANNT | Klassifikation unter der Konfidenzschwelle; wird als REST behandelt |
| Modul | Ein Aufsatz pro Behälter mit ESP32-CAM, PIR, LED, Servos und Klappe |
| Mother-Modul | Modul mit zusätzlichem Jetson Nano (PET-Behälter) |
| Child-Modul | Modul ohne Jetson, über USB am Mother-Modul |
| Klappe | Bewegliche Ablage im Modul, hält den Gegenstand und kippt ihn |
| Sicherer Zustand | Servos in Grundstellung bzw. stromlos, keine Bewegung, LED zeigt Störung |
| Testset | Definierte, versionierte Menge realer Gegenstände für den Abnahmetest |

## Offene Punkte zur Klärung im Team

Diese Punkte habe ich angenommen oder offen gelassen; bitte bestätigen oder korrigieren.

- [ ] **Nicht benachbarte Behälter:** Wie kommt ein Gegenstand von PET (links) nach REST (rechts), wenn ALU dazwischen liegt? Annahme: Übergabe über das mittlere Modul (zwei Kippvorgänge) oder Klappe kippt über zwei Stufen. Das Protokoll (`SORT` mit Richtung) unterstützt beides.
- [ ] **Zwei Servos pro Modul:** Kippen beide gemeinsam eine Klappe (links/rechts) oder hat jedes Modul zwei Klappen?
- [ ] **Einwurf-Erkennung:** Erkennt der PIR den Einwurf, oder braucht es zusätzlich eine Lichtschranke bzw. Bilddifferenz auf der Klappe?
- [ ] **Diagramm-Inkonsistenz:** Der Spannungsregler nennt «Pi 5: 5 V / 5 A», der Rechner ist ein Jetson Nano – Diagramm anpassen.
- [ ] **Batterie:** Typ und Kapazität offen; bestimmt, ob der Jetson Schlafmodi braucht.
- [ ] **WLAN:** Eigener Access Point des Jetson oder FHNW-Netz für das Dashboard?
- [ ] **Hinweis an Nutzer:in:** Nur LED oder zusätzlich Summer (MUSS-03 erlaubt optisch und/oder akustisch)?

Die Simulation (Kap. 8.9) beantwortet den ersten Punkt teilweise: Ohne Weitergabe zwischen nicht benachbarten Behältern ist MUSS-02 bei einer 3er-Station nicht erreichbar. Beide Varianten sind als Konfiguration (`routing`) umgesetzt, die Mechanik-Entscheidung bleibt beim Team.
