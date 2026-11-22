#!/usr/bin/env python3
"""Independently validate the generated Corso C/F learning collection.

Does not import the arranger or regenerate artifacts. All timing uses rational
quarter-note units. The instrument's left-hand register is deliberately checked
by pitch class: its reference specifies chord/root names, not sounding octaves.

Usage: python tools/verify_collection.py [--json] [--root PATH]
Exit 0 means the checks passed; warnings require human musical judgement.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from fractions import Fraction as F
import json
from pathlib import Path
import re
import sys
from xml.etree import ElementTree as ET

PUBLIC_IDS = {
    'o-tannenbaum', 'alle-jahre-wieder', 'ihr-kinderlein-kommet',
    'o-du-froehliche', 'stille-nacht-heilige-nacht', 'leise-rieselt-der-schnee',
    'kling-gloeckchen', 'morgen-kinder', 'lasst-uns-froh',
    'froehliche-weihnacht', 'suesser-die-glocken', 'schneefloeckchen',
    'jingle-bells', 'we-wish-you-a-merry-christmas',
}
PROTECTED_IDS = {
    'feliz-navidad', 'rudolph-the-red-nosed-reindeer', 'last-christmas',
    'all-i-want-for-christmas-is-you', 'santa-claus-is-coming-to-town',
    'rockin-around-the-christmas-tree', 'winter-wonderland', 'let-it-snow',
}
NATURAL = dict(zip('CDEFGAB', (0, 2, 4, 5, 7, 9, 11)))
NS = {'m': 'http://www.music-encoding.org/ns/mei'}
XMLID = '{http://www.w3.org/XML/1998/namespace}id'
NO_MUSIC = {'.pdf', '.mid', '.midi', '.mei', '.musicxml', '.mxl', '.abc',
            '.ly', '.mscz', '.mscx', '.wav', '.mp3', '.ogg', '.flac', '.svg'}


def q(value):
    return F(str(value))


def pitch_midi(name):
    match = re.fullmatch(r'([A-G])([#b]?)(-?\d+)', name)
    if not match:
        raise ValueError(f'Invalid scientific pitch: {name!r}')
    letter, accidental, octave = match.groups()
    return (int(octave) + 1) * 12 + NATURAL[letter] + {'': 0, '#': 1, 'b': -1}[accidental]


def mei_timed_elements(parent):
    """Beams group notes visually without changing their sequential timing."""
    for child in parent:
        tag = child.tag.rsplit('}', 1)[-1]
        if tag == 'beam':
            yield from mei_timed_elements(child)
        elif tag in ('note', 'rest', 'chord'):
            yield child
        else:
            raise ValueError(f'Unsupported MEI timing container: {tag}')


def chord_pc(name):
    match = re.fullmatch(r'([A-G])([#b]?)(m?)', name)
    if not match:
        raise ValueError(f'Expected major/minor triad: {name!r}')
    root = (NATURAL[match[1]] + {'': 0, '#': 1, 'b': -1}[match[2]]) % 12
    return root, {root, (root + (3 if match[3] else 4)) % 12, (root + 7) % 12}


class Verification:
    def __init__(self, root):
        self.root = root
        self.errors = []
        self.warnings = []
        self.songs = []
        self.checks = 0
        self.layout = json.loads((root / 'instrument/corso-cf.json').read_text())
        self.right = {str(b['button']): b for b in self.layout['melody']}
        self.left = {str(b['button']): b for b in self.layout['bass']}
        self.chords = {b[d] for b in self.layout['bass'] if b['kind'] == 'chord' for d in ('D', 'Z')}

    def check(self, condition, message):
        self.checks += 1
        if not condition:
            self.errors.append(message)

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def same_timeline(self, actual, expected, duration, where):
        """Compare every pitch at every time interval, including explicit silence."""
        edges = {F(0), duration}
        for records in (actual, expected):
            for start, length, pitch in records:
                self.check(start >= 0 and length > 0 and start + length <= duration,
                           f'{where}: invalid interval {start}+{length}/{duration}')
                edges.update((start, start + length))
        edges = sorted(edges)
        for a, b in zip(edges, edges[1:]):
            mid = (a + b) / 2
            played = Counter(n for t, d, n in actual if n is not None and t <= mid < t + d)
            wanted = Counter(n for t, d, n in expected if n is not None and t <= mid < t + d)
            self.check(played == wanted, f'{where}: pitches differ in [{a}, {b}): {played} != {wanted}')

    def instrument(self):
        self.check(len(self.right) == 21, 'Instrument: expected 21 unique melody buttons')
        self.check(len(self.left) == 8, 'Instrument: expected 8 unique bass buttons')
        for button in self.right.values():
            for direction in ('D', 'Z'):
                self.check(0 <= pitch_midi(button[direction]) <= 127,
                           f'Instrument: invalid MIDI range at {button["button"]}{direction}')
        for button in self.left.values():
            for direction in ('D', 'Z'):
                chord_pc(button[direction])

    def source_and_arrangement(self, source, song):
        sid = source['id']
        self.check(song['id'] == sid, f'{sid}: arrangement ID differs')
        self.check(song['source_key'] == source['key'], f'{sid}: source key metadata differs')
        self.check(song['key'] in ('C', 'F'), f'{sid}: learning key must be C or F')
        self.check(song['meter'] == source['meter'], f'{sid}: meter changed')
        shift = song['transposition_semitones']
        self.check(isinstance(shift, int), f'{sid}: transposition is not an integer')
        self.check((chord_pc(source['key'])[0] + shift) % 12 == chord_pc(song['key'])[0],
                   f'{sid}: key does not match transposition')
        n, d = map(int, source['meter'].split('/'))
        nominal = F(n * 4, d)
        pickup = q(source.get('pickup_quarters', 0))
        self.check(q(song['pickup_quarters']) == pickup, f'{sid}: pickup differs')
        self.check(F(0) <= pickup < nominal, f'{sid}: invalid pickup')
        sm = source['measures']; am = song['measures']
        self.check(len(sm) == len(am), f'{sid}: measure count differs: {len(sm)} != {len(am)}')
        elapsed = F(0); note_count = 0; rest_count = 0; bass_count = 0
        source_note_count = sum(e['pitch'] != 'rest' for m in sm for e in m['events'])
        source_rest_count = sum(e['pitch'] == 'rest' for m in sm for e in m['events'])
        all_events = []
        for mi, (original, arranged) in enumerate(zip(sm, am)):
            where = f'{sid} measure {arranged["number"]}'
            source_duration = sum(q(e['duration']) for e in original['events'])
            expected_bar = pickup if pickup and mi == 0 else nominal - pickup if pickup and mi == len(sm) - 1 else nominal
            self.check(source_duration == expected_bar,
                       f'{where}: source bar totals {source_duration}, expected {expected_bar}')
            self.check(q(arranged['duration']) == source_duration, f'{where}: duration differs')
            self.check(q(arranged['start']) == elapsed, f'{where}: start is not contiguous')
            self.check(arranged['number'] == (mi if pickup else mi + 1), f'{where}: measure numbering differs')
            self.check(len(original['events']) == len(arranged['events']), f'{where}: event count differs')
            offset = F(0)
            for ei, (se, ae) in enumerate(zip(original['events'], arranged['events'])):
                label = f'{where} event {ei + 1}'
                duration = q(se['duration'])
                self.check(duration > 0 and q(ae['duration']) == duration, f'{label}: duration changed/nonpositive')
                self.check(q(ae['offset']) == offset and q(ae['start']) == elapsed + offset,
                           f'{label}: timing/offset differs')
                self.check((se['pitch'] == 'rest') == (ae['pitch'] == 'rest'), f'{label}: note/rest changed')
                if se['pitch'] == 'rest':
                    rest_count += 1
                    self.check(ae['midi'] is None and ae.get('button') is None and ae.get('dir') is None,
                               f'{label}: rest assigned a sounding button')
                else:
                    note_count += 1
                    actual = pitch_midi(ae['pitch'])
                    self.check(actual == pitch_midi(se['pitch']) + shift, f'{label}: nonconstant transposition')
                    self.check(actual == ae['midi'], f'{label}: MIDI disagrees with named pitch')
                    button = self.right.get(str(ae['button']))
                    self.check(button is not None and ae['dir'] in ('D', 'Z'), f'{label}: invalid button/direction')
                    if button and ae['dir'] in ('D', 'Z'):
                        self.check(pitch_midi(button[ae['dir']]) == actual, f'{label}: button sounds a different MIDI pitch')
                        self.check(ae['row'] == button['row'] and ae['position'] == button['position'],
                                   f'{label}: row/position metadata differs from instrument')
                all_events.append(ae)
                offset += duration
            self.check(offset == source_duration, f'{where}: event sum differs')
            harmonies = arranged['harmony']
            self.check(bool(harmonies) and q(harmonies[0]['offset']) == 0, f'{where}: no initial harmony')
            self.check([q(h['offset']) for h in harmonies] == sorted({q(h['offset']) for h in harmonies}),
                       f'{where}: unordered/duplicate harmony offsets')
            for h in harmonies:
                self.check(0 <= q(h['offset']) < source_duration and h['chord'] in self.chords,
                           f'{where}: harmony outside measure or absent from instrument: {h}')
            if original.get('chord') and original.get('chord_events') and original['chord_events'][0]['offset'] != 0:
                self.check(harmonies[0].get('source_chord') == original['chord'],
                           f'{where}: initial source chord was dropped before later chord_events')
            previous_end = F(0)
            for bi, bass in enumerate(arranged['bass']):
                label = f'{where} bass {bi + 1}'
                start = q(bass['offset']); length = q(bass['duration']); end = start + length
                bass_count += 1
                self.check(start >= previous_end and length > 0 and end <= source_duration, f'{label}: overlapping/out-of-bar bass')
                self.check(q(bass['start']) == elapsed + start, f'{label}: absolute start mismatch')
                button = self.left.get(str(bass['button']))
                self.check(button is not None and bass['dir'] in ('D', 'Z'), f'{label}: invalid left button/direction')
                root, tones = chord_pc(bass['chord'])
                if button and bass['dir'] in ('D', 'Z'):
                    self.check(button['kind'] == bass['kind'], f'{label}: bass/chord button kind differs')
                    expected_label = bass['chord'] if bass['kind'] == 'chord' else re.match(r'[A-G][#b]?', bass['chord'])[0]
                    self.check(button[bass['dir']] == expected_label, f'{label}: left button sounds {button[bass["dir"]]}, not {expected_label}')
                actual_midis = [pitch_midi(p) for p in bass['pitches']]
                self.check(actual_midis == bass['midis'], f'{label}: named pitches/MIDI differ')
                wanted_pcs = tones if bass['kind'] == 'chord' else {root}
                self.check({v % 12 for v in actual_midis} == wanted_pcs, f'{label}: written bass/chord pitches are wrong')
                self.check(len(actual_midis) == (3 if bass['kind'] == 'chord' else 1), f'{label}: unexpected chord/root note count')
                active_harmony = [h for h in harmonies if q(h['offset']) <= start][-1]
                self.check(bass['chord'] == active_harmony['chord'], f'{label}: accompaniment differs from harmony')
                for h in harmonies:
                    self.check(not start < q(h['offset']) < end, f'{label}: bass continues beyond a harmony change')
                for event in arranged['events']:
                    a = q(event['offset']); b = a + q(event['duration'])
                    if max(a, start) < min(b, end) and event['pitch'] != 'rest':
                        self.check(event['dir'] == bass['dir'], f'{label}: bass lasts across opposite bellows motion at {a}')
                previous_end = end
            elapsed += source_duration
        self.check(note_count == source_note_count and rest_count == source_rest_count,
                   f'{sid}: incomplete melody/rest sequence')
        self.check(elapsed == q(song['total_quarters']), f'{sid}: total duration differs')
        self.check(bass_count == song['bass_pulses'], f'{sid}: bass count metadata differs')
        mids = [e['midi'] for e in all_events if e['midi'] is not None]
        self.check([pitch_midi(p) for p in song['range']] == [min(mids), max(mids)], f'{sid}: range metadata differs')
        ids = [e['id'] for e in all_events]
        self.check(len(set(ids)) == len(ids), f'{sid}: duplicate event IDs')
        return {'id': sid, 'key': song['key'], 'shift': shift, 'measures': len(am),
                'notes': note_count, 'rests': rest_count, 'bass_pulses': bass_count,
                'quarter_beats': float(elapsed)}

    @staticmethod
    def expected_staff(measure, staff):
        if staff == 1:
            return [(q(e['offset']), q(e['duration']), e['midi']) for e in measure['events']]
        return [(q(b['offset']), q(b['duration']), n) for b in measure['bass'] for n in b['midis']]

    def musicxml(self, folder, song):
        sid = song['id']; root = ET.parse(folder / 'score.musicxml').getroot()
        measures = root.findall('./part/measure')
        self.check(len(measures) == len(song['measures']), f'{sid} MusicXML: measure count differs')
        divisions = None
        all_ids = []
        for mi, (xml, data) in enumerate(zip(measures, song['measures'])):
            where = f'{sid} MusicXML measure {data["number"]}'
            value = xml.findtext('attributes/divisions')
            if value is not None: divisions = int(value)
            self.check(bool(divisions), f'{where}: missing divisions')
            cursor = F(0); last_start = F(0); records = {1: [], 2: []}; grouped = defaultdict(list)
            staff_durations = {1: F(0), 2: F(0)}; bass_labels = []; xml_harmonies = []
            for child in xml:
                if child.tag in ('backup', 'forward'):
                    cursor += F(int(child.findtext('duration')), divisions) * (-1 if child.tag == 'backup' else 1)
                if child.tag == 'harmony':
                    step = child.findtext('root/root-step')
                    alter = int(child.findtext('root/root-alter', '0'))
                    kind = child.findtext('kind', 'major')
                    self.check(kind in ('major', 'minor'), f'{where}: unsupported harmony kind {kind}')
                    name = step + {-1: 'b', 0: '', 1: '#'}[alter] + ('m' if kind == 'minor' else '')
                    when = cursor + F(int(child.findtext('offset', '0')), divisions)
                    xml_harmonies.append((when, name))
                if child.tag != 'note': continue
                length = F(int(child.findtext('duration')), divisions)
                start = last_start if child.find('chord') is not None else cursor
                if child.find('chord') is None:
                    cursor += length; last_start = start
                staff = int(child.findtext('staff', '1'))
                if child.find('chord') is None: staff_durations[staff] += length
                printed_type = child.findtext('type')
                written_length = {'whole': F(4), 'half': F(2), 'quarter': F(1), 'eighth': F(1, 2), '16th': F(1, 4), '32nd': F(1, 8)}[printed_type]
                written_length *= sum((F(1, 2) ** i for i in range(len(child.findall('dot')) + 1)), F(0))
                self.check(written_length == length, f'{where}: visible note type/dots disagree with duration')
                if staff == 2 and child.find("lyric[@number='1']/text") is not None:
                    bass_labels.append((start, child.findtext("lyric[@number='1']/text"), child.findtext("lyric[@number='2']/text")))
                pi = child.find('pitch')
                note = None if pi is None else (int(pi.findtext('octave')) + 1) * 12 + NATURAL[pi.findtext('step')] + int(pi.findtext('alter', '0'))
                records[staff].append((start, length, note))
                nid = child.get('id'); all_ids.append(nid)
                if staff == 1 and nid:
                    grouped[nid.rsplit('-', 2)[0]].append((start, length, note, child))
            self.check(bass_labels == [(q(b['offset']), f"{b['button']}{b['dir']}", b['chord']) for b in data['bass']], f'{where}: printed bass labels differ')
            if xml_harmonies:
                self.check(xml_harmonies == [(q(h['offset']), h['chord']) for h in data['harmony']], f'{where}: MusicXML harmony symbols differ')
            for staff in (1, 2):
                self.check(staff_durations[staff] == q(data['duration']), f'{where}: staff {staff} duration incomplete')
                self.same_timeline(records[staff], self.expected_staff(data, staff), q(data['duration']), f'{where} staff {staff}')
            for event in data['events']:
                pieces = sorted(grouped[event['id']], key=lambda x: x[0])
                self.check(bool(pieces), f'{where}: missing XML event {event["id"]}')
                self.check(sum(p[1] for p in pieces) == q(event['duration']), f'{where}: split duration mismatch {event["id"]}')
                for index, (start, length, note, node) in enumerate(pieces):
                    ties = {t.get('type') for t in node.findall('tie')}
                    expected = set()
                    if note is not None:
                        if index: expected.add('stop')
                        if index < len(pieces) - 1: expected.add('start')
                    self.check(ties == expected, f'{where}: incorrect tie chain {event["id"]}')
                    if index == 0 and note is not None:
                        label = node.findtext("lyric[@number='1']/text")
                        self.check(label == f'{event["button"]}{event["dir"]}', f'{where}: wrong printed melody button label')
                        self.check(node.findtext("lyric[@number='2']/text") == event['pitch'], f'{where}: wrong printed pitch label')
        self.check(None not in all_ids and len(all_ids) == len(set(all_ids)), f'{sid} MusicXML: missing/duplicate note IDs')
        if not root.findall('.//harmony'):
            self.warn(f'{sid}: MusicXML has no harmony elements; the above-staff chord symbols exist in MEI/PDF only.')

    def mei(self, folder, song):
        sid = song['id']; root = ET.parse(folder / 'score.mei').getroot()
        measures = root.findall('.//m:measure', NS)
        self.check(len(measures) == len(song['measures']), f'{sid} MEI: measure count differs')
        all_ids = []
        _, denominator = map(int, song['meter'].split('/'))
        for xml, data in zip(measures, song['measures']):
            where = f'{sid} MEI measure {data["number"]}'
            idnotes = {}; grouped = defaultdict(list); bass_labels = []
            for staff in (1, 2):
                layer = xml.find(f"m:staff[@n='{staff}']/m:layer", NS)
                self.check(layer is not None, f'{where}: missing staff {staff}')
                cursor = F(0); records = []; state = {}
                for element in mei_timed_elements(layer):
                    tag = element.tag.rsplit('}', 1)[-1]
                    if tag not in ('note', 'rest', 'chord'): continue
                    length = F(4, int(element.get('dur'))) * sum((F(1, 2) ** i for i in range(int(element.get('dots', 0)) + 1)), F(0))
                    nodes = element.findall('m:note', NS) if tag == 'chord' else [element]
                    for node in nodes:
                        if tag == 'rest':
                            records.append((cursor, length, None)); continue
                        letter = node.get('pname').upper(); octave = int(node.get('oct'))
                        default = -1 if song['key'] == 'F' and letter == 'B' else 0
                        accidental = state.get((letter, octave), default)
                        if node.get('accid') is not None:
                            accidental = {'n': 0, 's': 1, 'f': -1}[node.get('accid')]
                        state[(letter, octave)] = accidental
                        sounding = {'n': 0, 's': 1, 'f': -1}[node.get('accid.ges', 'n')]
                        self.check(accidental == sounding, f'{where}: visible accidental disagrees with sounding pitch')
                        note = (octave + 1) * 12 + NATURAL[letter] + accidental
                        records.append((cursor, length, note))
                        nid = node.get(XMLID); all_ids.append(nid); idnotes[nid] = (cursor, length, note)
                        if staff == 1: grouped[nid.rsplit('-', 2)[0]].append((nid, cursor, length, note, node))
                        elif node.find("m:verse[@n='1']/m:syl", NS) is not None:
                            bass_labels.append((cursor, node.findtext("m:verse[@n='1']/m:syl", namespaces=NS), node.findtext("m:verse[@n='2']/m:syl", namespaces=NS)))
                    cursor += length
                self.check(cursor == q(data['duration']), f'{where}: staff {staff} duration mismatch')
                self.same_timeline(records, self.expected_staff(data, staff), q(data['duration']), f'{where} staff {staff}')
            self.check(bass_labels == [(q(b['offset']), f"{b['button']}{b['dir']}", b['chord']) for b in data['bass']], f'{where}: printed bass labels differ')
            actual_ties = {(t.get('startid').lstrip('#'), t.get('endid').lstrip('#')) for t in xml.findall('m:tie', NS)}
            for a, b in actual_ties:
                self.check(a in idnotes and b in idnotes, f'{where}: dangling tie')
                if a in idnotes and b in idnotes:
                    ta, da, pa = idnotes[a]; tb, db, pb = idnotes[b]
                    self.check(pa == pb and ta + da == tb, f'{where}: tie has wrong pitch or endpoints')
            for event in data['events']:
                if event['pitch'] == 'rest': continue
                pieces = sorted(grouped[event['id']], key=lambda x: x[1])
                self.check(bool(pieces), f'{where}: melody event not represented')
                for a, b in zip(pieces, pieces[1:]):
                    self.check((a[0], b[0]) in actual_ties, f'{where}: missing tie between split notes')
                if pieces:
                    node = pieces[0][-1]
                    self.check(node.findtext("m:verse[@n='1']/m:syl", namespaces=NS) == f'{event["button"]}{event["dir"]}', f'{where}: wrong melody annotation')
                    self.check(node.findtext("m:verse[@n='2']/m:syl", namespaces=NS) == event['pitch'], f'{where}: wrong pitch annotation')
            harmonies = [(q(h.get('tstamp')) - 1) * F(4, denominator) for h in xml.findall('m:harm', NS)]
            names = [h.text for h in xml.findall('m:harm', NS)]
            self.check(list(zip(harmonies, names)) == [(q(h['offset']), h['chord']) for h in data['harmony']], f'{where}: printed harmony differs')
        self.check(None not in all_ids and len(all_ids) == len(set(all_ids)), f'{sid} MEI: missing/duplicate note IDs')

    def midi_file(self, folder, song):
        import mido
        sid = song['id']; mf = mido.MidiFile(folder / 'practice.mid')
        self.check(len(mf.tracks) == 2, f'{sid} MIDI: expected melody/bass tracks')
        for staff, track in enumerate(mf.tracks, 1):
            tick = 0; opened = {}; actual = []
            for msg in track:
                tick += msg.time
                if msg.type == 'note_on' and msg.velocity:
                    key = (msg.channel, msg.note)
                    self.check(key not in opened, f'{sid} MIDI: overlapping repeated key')
                    opened[key] = tick
                elif msg.type == 'note_off' or msg.type == 'note_on' and not msg.velocity:
                    key = (msg.channel, msg.note)
                    self.check(key in opened, f'{sid} MIDI: note-off without note-on')
                    if key in opened:
                        start = opened.pop(key); actual.append((start, tick - start, msg.note))
            self.check(not opened, f'{sid} MIDI: hanging note')
            expected = []
            for measure in song['measures']:
                for event in measure['events'] if staff == 1 else measure['bass']:
                    pitches = ([event['midi']] if event['midi'] is not None else []) if staff == 1 else event['midis']
                    start = round(q(event['start']) * mf.ticks_per_beat)
                    duration = round((q(event['start']) + q(event['duration']) * F(9, 10)) * mf.ticks_per_beat) - start
                    expected.extend((start, duration, n) for n in pitches)
            self.check(sorted(actual) == sorted(expected), f'{sid} MIDI track {staff}: notes/onsets/articulated durations differ')
            if tick < round(q(song['total_quarters']) * mf.ticks_per_beat):
                self.warn(f'{sid}: MIDI track {staff} ends before the notated final rest/bar boundary; looping in a DAW may shorten the phrase.')

    def protected(self, public_ids):
        catalog = json.loads((self.root / 'songs/licensed/catalog.json').read_text())
        ids = [x['id'] for x in catalog]
        self.check(len(ids) == 8 and set(ids) == PROTECTED_IDS, 'Protected catalog: expected the eight requested titles exactly once')
        self.check(not set(ids) & set(public_ids), 'Protected catalog: protected title appears in full-score collection')
        forbidden_fields = {'measures', 'events', 'pitches', 'midis', 'melody', 'lyrics', 'score', 'svg_pages', 'musicxml'}
        def inspect(value, where):
            if isinstance(value, dict):
                self.check(not forbidden_fields & set(value), f'{where}: contains encoded music/lyrics fields')
                for key, child in value.items(): inspect(child, where + '/' + key)
            elif isinstance(value, list):
                for i, child in enumerate(value): inspect(child, where + '/' + str(i))
        inspect(catalog, 'Protected catalog')
        for item in catalog:
            self.check(str(item.get('licensed_source_url', '')).startswith('https://'), f'{item["id"]}: missing licensed source link')
        for path in (self.root / 'songs/licensed').rglob('*'):
            if path.is_file():
                self.check(path.suffix.lower() not in NO_MUSIC, f'Protected directory includes musical artifact: {path.relative_to(self.root)}')
        return len(catalog)

    def player(self, songs):
        path = self.root / 'practice/index.html'
        if not path.exists():
            self.errors.append('Offline player practice/index.html is missing'); return
        match = re.search(r'<script\b[^>]*\bid="collection-data"[^>]*>(.*?)</script>', path.read_text(), re.S)
        self.check(match is not None, 'Offline player: no embedded collection-data')
        if not match: return
        data = json.loads(match[1])
        actual = {s['id']: s for s in data['songs']}
        self.check(len(data['songs']) == 14 and set(actual) == set(songs), 'Offline player: song set differs')
        for sid, song in songs.items():
            if sid in actual:
                for field in ('measures', 'key', 'transposition_semitones', 'total_quarters'):
                    self.check(actual[sid][field] == song[field], f'Offline player {sid}: stale {field}')
                self.check(len(actual[sid].get('svg_pages', [])) == song['page_count'], f'Offline player {sid}: missing score pages')
        self.check(data['instrument'] == self.layout, 'Offline player: stale instrument mapping')
        self.check({p['id'] for p in data['protected']} == PROTECTED_IDS, 'Offline player: protected catalog differs')
        self.check(all('measures' not in p and 'svg_pages' not in p for p in data['protected']), 'Offline player embeds protected scores')

    def run(self):
        self.instrument()
        folders = sorted((self.root / 'songs/public-domain').glob('*/source.json'))
        self.check({p.parent.name for p in folders} == PUBLIC_IDS and len(folders) == 14,
                   'Expected exactly 14 complete public-domain melody folders')
        songs = {}
        for path in folders:
            sid = path.parent.name
            try:
                source = json.loads(path.read_text())
                song = json.loads((path.parent / 'arrangement.json').read_text())
                songs[sid] = song
                self.songs.append(self.source_and_arrangement(source, song))
                self.musicxml(path.parent, song)
                self.mei(path.parent, song)
                self.midi_file(path.parent, song)
                for filename in ('score.pdf', 'score-01.svg', 'README.md'):
                    self.check((path.parent / filename).is_file(), f'{sid}: missing {filename}')
            except Exception as exc:
                self.errors.append(f'{sid}: verification exception {type(exc).__name__}: {exc}')
        protected_count = self.protected(songs)
        self.player(songs)
        return {'status': 'PASS' if not self.errors else 'FAIL', 'checks': self.checks,
                'public_songs': len(songs), 'protected_titles': protected_count,
                'melody_notes': sum(s['notes'] for s in self.songs),
                'melody_rests': sum(s['rests'] for s in self.songs),
                'bass_pulses': sum(s['bass_pulses'] for s in self.songs),
                'songs': self.songs, 'errors': self.errors, 'warnings': self.warnings,
                'limits': ['Checks transcription fidelity to source.json, not the historical authenticity of the source itself.',
                           'Bass voicing octaves are schematic; only their roots/triad pitch classes are specified by the physical reference.',
                           'Bellows direction compatibility is verified; actual air capacity and hand comfort require playing the instrument.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--json', action='store_true', help='Print machine-readable verification report to stdout')
    args = parser.parse_args()
    result = Verification(args.root).run()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{result['status']}: {result['checks']} checks; {result['public_songs']} full melodies; {result['protected_titles']} protected metadata-only titles")
        print(f"{result['melody_notes']} melody notes, {result['melody_rests']} rests, {result['bass_pulses']} bass/chord pulses")
        for item in result['errors']: print('ERROR:', item)
        for item in result['warnings']: print('WARNING:', item)
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
