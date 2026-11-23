# Kurs und Sammlung neu erzeugen

[← Übersicht](../README.md)

Die fertigen PDFs und MP3-Hörbeispiele des [Weihnachtskurses](../course/README.md) können direkt benutzt werden. Python wird nur benötigt, wenn die Materialien neu erzeugt werden sollen.

## Voraussetzungen

Python 3.11 oder neuer, die Pakete aus `requirements.txt`, die Systembibliothek Cairo und FFmpeg mit MP3-Unterstützung im Suchpfad. Unter Linux werden für die PDF-Überschriften vorzugsweise DejaVu Sans und DejaVu Sans Bold verwendet; andernfalls greift eine Standard-PDF-Schrift. Verovio erzeugt den eigentlichen Notensatz, CairoSVG wandelt ihn in Vektor-PDF um. NumPy erzeugt einen eigenen synthetischen Übeton; FFmpeg kodiert daraus die MP3-Dateien.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r tools/requirements.txt
python tools/build_beginner_course.py
python tools/verify_beginner_course.py
```

Unter Windows die virtuelle Umgebung mit dem üblichen Windows-Aktivierungsskript aktivieren und Cairo nach den [CairoSVG-Installationshinweisen](https://cairosvg.org/documentation/) bereitstellen.

## Fünf-Lieder-Kurs

`build_beginner_course.py` erzeugt vier Seiten je Lied, den vierseitigen Lehrplan, das gemeinsame 24-seitige Kursheft, fünf MP3-Dateien sowie MusicXML und MIDI für Grund- und Bonusfassungen. Die vollständigen Griffe, Übungen, Balgmetriken und Quellen stehen in `course/data/course.json`.

Die Grundfassungen verwenden ausschließlich die äußere C-Reihe. Die Zusatzroute darf beide Reihen verwenden; ihre klingende Notendauer in einer Richtung ist auf sechs Viertel begrenzt. Eine Pause setzt diese Grenze nicht zurück. Klingende Dauer, verstrichene Richtungsspanne und ununterbrochene Klangdauer werden getrennt berechnet. Diese Werte sind keine physische Luftmessung.

Die Fünf-Lieder-Auswahl und die Unterrichtstexte stehen in `course/data/lesson_content.json`. Die historische Weihnachtsmann-Melodie steht in `course/data/source-morgen-kommt-der-weihnachtsmann.json`; die vier übrigen Melodien stammen aus den dokumentierten Quellen der bestehenden Sammlung. Der Prüfer kontrolliert diese Daten und die konkrete Belegung unabhängig vom Routengenerator. Die Druckseiten werden zusätzlich gerendert und visuell kontrolliert.

Die MP3s verwenden **48 Viertel pro Minute**, Melodie ohne Bass und hörbare Viertelschläge. Vier Viertel zählen die geraden Takte ein; bei „Schneeflöckchen“ gehen zwei Viertel voran, damit der Auftakt auf „3 und“ liegt. MIDI-Dateien beginnen direkt mit der Melodie; die Noten klingen zur deutlichen Trennung 92 % ihres notierten Zeitfensters. Taktlängen und Einsatzzeiten bleiben unverändert.

## Bisherige größere Sammlung

Diese Sammlung bleibt als [Archiv](../docs/ARCHIVE_COLLECTION.md) erhalten. Bei Bedarf separat bauen:

```sh
python tools/build_collection.py
python tools/verify_collection.py
```

### Datenfluss der bisherigen Sammlung

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
