# Sammlung neu erzeugen

[← Übersicht](../README.md)

Die fertigen PDFs, MIDI-Dateien und der Offline-Player können direkt benutzt werden. Python wird nur benötigt, wenn die Sammlung neu erzeugt werden soll.

## Voraussetzungen

Python 3.10 oder neuer, die Pakete aus `requirements.txt` und die Systembibliothek Cairo. Unter Linux werden für die PDF-Überschriften vorzugsweise DejaVu Sans und DejaVu Sans Bold verwendet; andernfalls greift eine Standard-PDF-Schrift. Verovio erzeugt den eigentlichen Notensatz, CairoSVG wandelt ihn in Vektor-PDF um.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r tools/requirements.txt
python tools/build_collection.py
python tools/verify_collection.py
```

Unter Windows die virtuelle Umgebung mit dem üblichen Windows-Aktivierungsskript aktivieren und Cairo nach den [CairoSVG-Installationshinweisen](https://cairosvg.org/documentation/) bereitstellen.

## Datenfluss

| Eingabe | Verwendung |
|---|---|
| `instrument/corso-cf.json` | Exakte Ton- und Bassbelegung. |
| `songs/public-domain/*/source.json` | Melodie, Rhythmus, Quellen, einfache Harmoniefunktionen und Lernhinweise. |
| `songs/licensed/catalog.json` | Nur Metadaten, Lernhinweise und Bezugslinks für acht geschützte Titel. |
| `tools/practice_template.html` | Oberfläche des Offline-Players; wird mit den geprüften Daten und SVG-Seiten gefüllt. |
| `instrument/notes-pull-push.pdf` | Separat gesetzte Tonübersicht; wird unverändert ins Sammelheft eingefügt. |

Der Generator sucht spielbare Fassungen in C oder F. Er bewertet Balgrichtungswechsel, Reihenwechsel, Knopfbewegung, Register und verfügbare Bässe. Das ist eine nachvollziehbare Lernroute, keine Behauptung eines für jede Hand optimalen Fingersatzes. Unverfügbare Harmonien werden für die neue Lernbegleitung vereinfacht, unvereinbare Bassimpulse ausgelassen oder vor dem Richtungswechsel beendet.

Der unabhängige Prüfer vergleicht Quellen, Arrangement, MusicXML, MEI, MIDI und Playerdaten. Er kontrolliert Tonhöhe, Dauer, Taktfüllung, Haltebögen, Akkordsymbole und die konkrete Instrumentbelegung. Er ersetzt keine Prüfung von Klang, Luftbedarf und Ergonomie am realen Instrument. PDF-Layout und Playerbedienung müssen nach Änderungen ebenfalls kontrolliert werden.

Die Quellen enthalten vollständige ausgeschriebene Melodiedurchgänge ohne Liedtexte. Neue Stücke benötigen zuerst dokumentierte Rechte und eine verlässliche Melodiequelle; geschützte Kaufnoten gehören nicht in diesen öffentlichen Datenfluss.
