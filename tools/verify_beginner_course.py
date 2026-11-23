#!/usr/bin/env python3
"""Independently check the five-song Corso C/F beginner course.

This checker does not import either builder. It compares the course to its
stored melody sources and the instrument map, recomputes timing and bellows
metrics, and checks every sounding accompaniment interval. It cannot certify
air capacity, fingering comfort, source transcription, or the printed/audio
rendering; those are separate reviews.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
import sys
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree as ET

import mido

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SONGS = [
    "morgen-kommt-der-weihnachtsmann", "lasst-uns-froh", "schneefloeckchen",
    "alle-jahre-wieder", "o-du-froehliche",
]
NOTE_CLASS = dict(C=0, D=2, E=4, F=5, G=7, A=9, B=11)


def q(value):
    """Exact score time, without float accumulation or half-beat rounding."""
    if isinstance(value, bool):
        raise ValueError("A boolean is not a duration")
    return Fraction(str(value))


def midi(pitch):
    match = re.fullmatch(r"([A-G])([#b]?)([0-9])", pitch or "")
    if not match:
        raise ValueError(f"Invalid scientific pitch: {pitch!r}")
    letter, accidental, octave = match.groups()
    return 12 * (int(octave) + 1) + NOTE_CLASS[letter] + {"": 0, "#": 1, "b": -1}[accidental]


def chord_info(symbol):
    match = re.fullmatch(r"([A-G])([#b]?)(m?)", symbol or "")
    if not match:
        raise ValueError(f"Unsupported chord: {symbol!r}")
    letter, accidental, quality = match.groups()
    root = (NOTE_CLASS[letter] + {"": 0, "#": 1, "b": -1}[accidental]) % 12
    return letter + accidental, {root, (root + (3 if quality else 4)) % 12, (root + 7) % 12}


def overlap(a_start, a_duration, b_start, b_duration):
    return max(q(a_start), q(b_start)) < min(q(a_start) + q(a_duration), q(b_start) + q(b_duration))


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def independent_metrics(track):
    """Direction groups retain rests; only uninterrupted sound excludes gaps."""
    notes = [e for m in track["measures"] for e in m["events"] if e["pitch"] != "rest"]
    groups = []
    for direction, members in itertools.groupby(notes, key=lambda e: e["dir"]):
        members = list(members)
        groups.append({
            "direction": direction,
            "start": q(members[0]["start"]),
            "end": q(members[-1]["start"]) + q(members[-1]["duration"]),
            "sound": sum((q(e["duration"]) for e in members), Fraction()),
        })
    continuous = []
    for note in notes:
        start, end = q(note["start"]), q(note["start"]) + q(note["duration"])
        if continuous and continuous[-1]["direction"] == note["dir"] and continuous[-1]["end"] == start:
            continuous[-1]["end"] = end
        else:
            continuous.append({"direction": note["dir"], "start": start, "end": end})
    result = {
        "reversals": max(0, len(groups) - 1),
        "row_changes": sum(a["row"] != b["row"] for a, b in zip(notes, notes[1:])),
        "buttons": sorted({e["button"] for e in notes}),
        "notes": len(notes),
        "bass_pulses": sum(len(m.get("bass", [])) for m in track["measures"]),
        "by_direction": {},
    }
    for direction in ("D", "Z"):
        selected = [g for g in groups if g["direction"] == direction]
        if not selected:
            result["by_direction"][direction] = {
                "sounding_quarters": 0, "elapsed_quarters": 0, "continuous_quarters": 0,
                "longest_sounding_seconds_at_48": 0,
                "longest_span_start": None, "longest_span_end": None,
            }
            continue
        # The schema's start/end identify the first longest SOUNDING span.
        longest = max(selected, key=lambda g: g["sound"])
        result["by_direction"][direction] = {
            "sounding_quarters": float(longest["sound"]),
            "elapsed_quarters": float(max(g["end"] - g["start"] for g in selected)),
            "continuous_quarters": float(max(
                g["end"] - g["start"] for g in continuous if g["direction"] == direction)),
            "longest_sounding_seconds_at_48": float(longest["sound"] * Fraction(60, 48)),
            "longest_span_start": float(longest["start"]),
            "longest_span_end": float(longest["end"]),
        }
    return result


class Review:
    def __init__(self, layout):
        self.melody = {b["button"]: b for b in layout["melody"]}
        self.bass = {b["button"]: b for b in layout["bass"]}
        self.errors = []
        self.observations = []
        self.tracks = []
        self.exports = []

    def check(self, condition, where, message):
        if not condition:
            self.errors.append({"location": where, "message": message})

    def numbers_equal(self, actual, expected, where, label):
        self.check(actual is not None and q(actual) == q(expected), where,
                   f"{label}: expected {expected}, got {actual}")

    def compare_metrics(self, actual, expected, where):
        for key, wanted in expected.items():
            loc = f"{where}.{key}"
            if isinstance(wanted, dict):
                self.check(isinstance(actual.get(key), dict), loc, "Missing metric object")
                if isinstance(actual.get(key), dict):
                    self.compare_metrics(actual[key], wanted, loc)
            elif isinstance(wanted, (int, float)):
                self.numbers_equal(actual.get(key), wanted, loc, "Metric")
            else:
                self.check(actual.get(key) == wanted, loc, f"Expected {wanted!r}, got {actual.get(key)!r}")

    def compare_source(self, track, source, where):
        for field in ("id", "key", "meter"):
            self.check(track.get(field) == source.get(field), where, f"Source {field} changed")
        self.numbers_equal(track.get("pickup_quarters"), source.get("pickup_quarters", 0), where, "Source pickup")
        self.check(len(track["measures"]) == len(source["measures"]), where, "Source measure count changed")
        for index, (measure, original) in enumerate(zip(track["measures"], source["measures"])):
            loc = f"{where}/source-measure-{index}"
            self.check(len(measure["events"]) == len(original["events"]), loc, "Source event count changed")
            for event_index, (event, original_event) in enumerate(zip(measure["events"], original["events"])):
                event_loc = f"{loc}/event-{event_index}"
                self.check(event["pitch"] == original_event["pitch"], event_loc, "Source pitch or rest changed")
                self.numbers_equal(event["duration"], original_event["duration"], event_loc, "Source duration")
                self.check(event.get("tie") == original_event.get("tie"), event_loc, "Source tie metadata changed")

    def verify_track(self, track, where, *, source=None, melody_only=False, cap=None):
        before = len(self.errors)
        measures = track["measures"]
        self.check(bool(measures), where, "Track has no measures")
        numerator, denominator = (int(x) for x in track["meter"].split("/"))
        bar_length = Fraction(numerator * 4, denominator)
        pickup = q(track.get("pickup_quarters", 0))
        self.check(track.get("key") == "C", where, "Course track is not concert-pitch C")
        self.check(0 <= pickup < bar_length, where, "Invalid pickup duration")
        self.check(q(track["practice_tempo_bpm"]) > 0 and q(track["tempo_bpm"]) > 0, where, "Nonpositive tempo")
        absolute = Fraction()
        all_events, all_bass, seen_ids = [], [], set()
        open_tie = None
        for measure_index, measure in enumerate(measures):
            loc = f"{where}/measure-{measure.get('number')}"
            expected_number = measure_index if pickup else measure_index + 1
            self.check(measure["number"] == expected_number, loc, "Unexpected measure numbering")
            self.numbers_equal(measure["start"], absolute, loc, "Measure start")
            expected_length = bar_length
            if measure_index == 0 and pickup:
                expected_length = pickup
            elif measure_index == len(measures) - 1 and pickup:
                expected_length = bar_length - pickup
            self.numbers_equal(measure["duration"], expected_length, loc, "Complete bar/pickup duration")
            position = Fraction()
            for event_index, event in enumerate(measure["events"]):
                event_loc = f"{loc}/event-{event_index}"
                self.check(event["id"] not in seen_ids, event_loc, "Duplicate event ID within track")
                seen_ids.add(event["id"])
                self.check(q(event["duration"]) > 0, event_loc, "Nonpositive note/rest duration")
                self.numbers_equal(event["offset"], position, event_loc, "Event offset")
                self.numbers_equal(event["start"], absolute + position, event_loc, "Event start")
                if event["pitch"] == "rest":
                    self.check(all(event.get(k) is None for k in ("midi", "button", "dir", "row", "position")),
                               event_loc, "A rest has a sounding pitch/route")
                else:
                    self.numbers_equal(event["midi"], midi(event["pitch"]), event_loc, "Melody MIDI")
                    button = self.melody.get(event.get("button"))
                    self.check(button is not None, event_loc, "Unknown melody button")
                    self.check(event.get("dir") in ("D", "Z"), event_loc, "Unknown bellows direction")
                    if button and event.get("dir") in ("D", "Z"):
                        self.check(button[event["dir"]] == event["pitch"], event_loc, "Button/direction does not sound the written pitch/octave")
                        for field in ("row", "position"):
                            self.check(event.get(field) == button[field], event_loc, f"Wrong button {field}")
                    if melody_only:
                        self.check(event.get("row") == "C", event_loc, "Basic route/exercise leaves the C row")
                tie = event.get("tie")
                self.check(tie in (None, "start", "continue", "stop"), event_loc, "Unsupported tie metadata; review required")
                if open_tie:
                    self.check(tie in ("continue", "stop"), event_loc, "An open tie was interrupted")
                    self.check(all(event.get(k) == open_tie.get(k) for k in ("pitch", "button", "dir")),
                               event_loc, "Held tone changes pitch, button, or direction")
                elif tie in ("continue", "stop"):
                    self.check(False, event_loc, "Tie continuation has no start")
                open_tie = event if tie in ("start", "continue") else None
                position += q(event["duration"])
                all_events.append(event)
            self.numbers_equal(position, measure["duration"], loc, "Sum of note/rest durations")
            harmony = measure.get("harmony", [])
            self.check(bool(harmony) and q(harmony[0]["offset"]) == 0, loc, "Harmony must start at measure offset 0")
            for i, change in enumerate(harmony):
                self.check(0 <= q(change["offset"]) < q(measure["duration"]), loc, "Harmony change outside measure")
                if i:
                    self.check(q(harmony[i - 1]["offset"]) < q(change["offset"]), loc, "Unordered/duplicate harmony changes")
                chord_info(change["chord"])
            pulses = measure.get("bass", [])
            if melody_only:
                self.check(not pulses, loc, "Basic route/exercise contains accompaniment")
            self.check(len(pulses) <= 2, loc, "Beginner accompaniment has more than two pulses in a measure")
            for pulse_index, bass in enumerate(pulses):
                bass_loc = f"{loc}/bass-{pulse_index}"
                self.numbers_equal(bass["start"], absolute + q(bass["offset"]), bass_loc, "Bass start")
                self.check(0 <= q(bass["offset"]) and 0 < q(bass["duration"]) <= Fraction(1, 2),
                           bass_loc, "Bass pulse is not a positive short (at most eighth-note) impulse")
                self.check(q(bass["offset"]) + q(bass["duration"]) <= q(measure["duration"]),
                           bass_loc, "Bass pulse extends beyond measure")
                reference = self.bass.get(bass.get("button"))
                self.check(reference is not None, bass_loc, "Unknown L-label")
                self.check(bass.get("dir") in ("D", "Z"), bass_loc, "Invalid bass direction")
                root_name, triad = chord_info(bass["chord"])
                target = bass["chord"] if bass["kind"] == "chord" else root_name
                if reference and bass.get("dir") in ("D", "Z"):
                    self.check(reference["kind"] == bass["kind"], bass_loc, "Bass/chord button kind is wrong")
                    self.check(reference[bass["dir"]] == target, bass_loc, "L-label/direction does not produce written root/chord quality")
                calculated_midi = [midi(p) for p in bass["pitches"]]
                self.check(calculated_midi == bass["midis"], bass_loc, "Bass MIDI and written pitches differ")
                classes = {n % 12 for n in calculated_midi}
                if bass["kind"] == "chord":
                    self.check(len(calculated_midi) == 3 and classes == triad, bass_loc, "Wrong chord tones/quality")
                else:
                    self.check(bass["kind"] == "bass" and len(calculated_midi) == 1 and classes == {midi(root_name + "3") % 12},
                               bass_loc, "Wrong single bass root")
                for i, change in enumerate(harmony):
                    end = q(harmony[i + 1]["offset"]) if i + 1 < len(harmony) else q(measure["duration"])
                    if overlap(bass["offset"], bass["duration"], change["offset"], end - q(change["offset"])):
                        self.check(bass["chord"] == change["chord"], bass_loc, "Bass spans a different written harmony")
                all_bass.append((bass, bass_loc))
            absolute += q(measure["duration"])
        self.check(open_tie is None, where, "Unclosed tie at track end")
        self.numbers_equal(track["total_quarters"], absolute, where, "Track duration")
        for bass, loc in all_bass:
            for event in all_events:
                if event["pitch"] != "rest" and overlap(bass["start"], bass["duration"], event["start"], event["duration"]):
                    self.check(bass["dir"] == event["dir"], loc, f"Opposite melody direction overlaps at {event['start']}")
            for other, other_loc in all_bass:
                if other_loc > loc and overlap(bass["start"], bass["duration"], other["start"], other["duration"]):
                    self.check(bass["dir"] == other["dir"], loc, "Overlapping bass pulses require opposite directions")
        for window in track.get("reset_windows", []):
            loc = f"{where}/reset-at-{window['start']}"
            self.check(window.get("automatic_air_reset") is False, loc, "A pause must not claim an automatic air reset")
            self.check(window.get("practice_stop_allowed") is True, loc, "Reset window is not identified as an optional practice stop")
            measure = next((m for m in measures if m["number"] == window["measure"]), None)
            self.check(measure is not None, loc, "Reset names an unknown measure")
            self.check(q(window["duration"]) >= Fraction(1, 2), loc, "Reset window shorter than marked eighth-note minimum")
            if measure:
                self.numbers_equal(q(window["start"]) + q(window["duration"]),
                                   q(measure["start"]) + q(measure["duration"]), loc, "Reset must end at the measure end")
                self.check(q(window["start"]) >= q(measure["start"]), loc, "Reset starts before its measure")
            for event in all_events:
                if event["pitch"] != "rest":
                    self.check(not overlap(window["start"], window["duration"], event["start"], event["duration"]),
                               loc, "Reset window overlaps a sounding melody note")
            for bass, _ in all_bass:
                self.check(not overlap(window["start"], window["duration"], bass["start"], bass["duration"]),
                           loc, "Reset window overlaps accompaniment")
        if source:
            self.compare_source(track, source, where)
        measured = independent_metrics(track)
        self.compare_metrics(track.get("stats", {}), measured, f"{where}/stats")
        if cap is not None:
            for direction in ("D", "Z"):
                self.check(q(measured["by_direction"][direction]["sounding_quarters"]) <= q(cap),
                           where, f"Bonus exceeds {cap} sounded quarters in direction {direction}, even when rests are retained")
        if "answers" in track:
            expected = [e["pitch"] for e in all_events if e["pitch"] != "rest"]
            self.check(track["answers"] == expected, where, "Reading quiz answers differ from notes")
        self.tracks.append({"track": where, "passed": len(self.errors) == before, "measured_stats": measured})
        return measured

    def verify_exports(self, track, xml_path, midi_path, where):
        """Check written durations and the MIDI's explicitly articulated gates."""
        before = len(self.errors)
        expected = [e for m in track["measures"] for e in m["events"]]
        tree = ET.parse(xml_path).getroot()
        parts = tree.findall("part")
        self.check(len(parts) == 1, where, "MusicXML must contain one Corso part")
        xml_measures = parts[0].findall("measure")
        self.check(len(xml_measures) == len(track["measures"]), where, "MusicXML measure count differs")
        divisions = None
        xml_events = []
        absolute = Fraction()
        pending = None
        type_quarters = {"whole": Fraction(4), "half": Fraction(2), "quarter": Fraction(1),
                         "eighth": Fraction(1, 2), "16th": Fraction(1, 4), "32nd": Fraction(1, 8)}
        for measure, reference in zip(xml_measures, track["measures"]):
            loc = f"{where}/MusicXML-measure-{reference['number']}"
            new_divisions = measure.findtext("attributes/divisions")
            if new_divisions:
                divisions = int(new_divisions)
            self.check(divisions is not None and divisions > 0, loc, "Missing MusicXML duration divisions")
            self.check(measure.get("number") == str(reference["number"]), loc, "Exported measure number differs")
            position = Fraction()
            for note in measure.findall("note"):
                if note.findtext("staff", "1") != "1":
                    continue
                self.check(note.find("chord") is None, loc, "Melody export unexpectedly contains simultaneous chord notes")
                duration = Fraction(int(note.findtext("duration")), divisions)
                typ = note.findtext("type")
                self.check(typ in type_quarters, loc, f"Unsupported written note value {typ}")
                if typ in type_quarters:
                    dot_factor = sum((Fraction(1, 2**i) for i in range(len(note.findall("dot")) + 1)), Fraction())
                    self.numbers_equal(duration, type_quarters[typ] * dot_factor, loc,
                                       "MusicXML written note value versus encoded duration")
                if note.find("rest") is not None:
                    pitch = "rest"
                else:
                    alter = int(note.findtext("pitch/alter", "0"))
                    if alter not in (-1, 0, 1):
                        raise ValueError(f"Unexpected accidental in {xml_path}")
                    pitch = note.findtext("pitch/step") + {-1: "b", 0: "", 1: "#"}[alter] + note.findtext("pitch/octave")
                labels = {lyric.get("number"): lyric.findtext("text") for lyric in note.findall("lyric")}
                event = {"pitch": pitch, "start": absolute + position, "duration": duration, "labels": labels}
                ties = {tie.get("type") for tie in note.findall("tie")}
                if "stop" in ties:
                    self.check(pending is not None, loc, "Exported tie has no starting note")
                    if pending:
                        self.check(pending["pitch"] == pitch and pending["start"] + pending["duration"] == event["start"],
                                   loc, "Exported tie changes pitch or timing")
                        pending["duration"] += duration
                        if "start" not in ties:
                            pending = None
                else:
                    self.check(pending is None, loc, "Exported tie was interrupted")
                    xml_events.append(event)
                    pending = event if "start" in ties else None
                position += duration
            self.numbers_equal(position, reference["duration"], loc, "Exported melody measure duration")
            absolute += position
        self.check(pending is None, where, "MusicXML ends with an open tie")
        self.check(len(xml_events) == len(expected), where, "Exported melody note/rest count differs after joining ties")
        for index, (actual, wanted) in enumerate(zip(xml_events, expected)):
            loc = f"{where}/MusicXML-event-{index}"
            self.check(actual["pitch"] == wanted["pitch"], loc, "MusicXML melody pitch/rest differs")
            self.numbers_equal(actual["start"], wanted["start"], loc, "MusicXML melody start")
            self.numbers_equal(actual["duration"], wanted["duration"], loc, "MusicXML full melody duration")
            if wanted["pitch"] != "rest":
                self.check(actual["labels"].get("1") == f"{wanted['button']}{wanted['dir']}", loc, "Printed route label differs")
                self.check(actual["labels"].get("2") == wanted["pitch"], loc, "Printed pitch label differs")
        self.numbers_equal(absolute, track["total_quarters"], where, "MusicXML total duration")

        mf = mido.MidiFile(midi_path)
        ticks = mf.ticks_per_beat
        midi_tracks = {}
        tempos = []
        for messages in mf.tracks:
            time = 0
            name = None
            active = {}
            completed = []
            for message in messages:
                time += message.time
                if message.type == "track_name":
                    name = message.name
                elif message.type == "set_tempo":
                    tempos.append((time, message.tempo))
                elif message.type in ("note_on", "note_off"):
                    key = (message.channel, message.note)
                    starts = message.type == "note_on" and message.velocity > 0
                    if starts:
                        self.check(key not in active, where, "MIDI repeats an already held note without a note-off")
                        active[key] = time
                    else:
                        self.check(key in active, where, "MIDI note-off has no matching note-on")
                        if key in active:
                            completed.append((message.note, active.pop(key), time))
            self.check(not active, where, "MIDI leaves notes sounding at track end")
            self.numbers_equal(time, q(track["total_quarters"]) * ticks, where, "MIDI track end")
            self.check(name not in midi_tracks, where, "Duplicate MIDI track name")
            midi_tracks[name] = sorted(completed, key=lambda item: (item[1], item[0], item[2]))
        self.check(set(midi_tracks) == {"Melody", "Optional bass"}, where, "Missing or unexpected MIDI tracks")
        wanted_tempo = round(Fraction(60_000_000, 1) / q(track["tempo_bpm"]))
        self.check(tempos == [(0, wanted_tempo)], where, "MIDI tempo is missing, changed, or starts late")
        for track_name, source_events in (
            ("Melody", [e for e in expected if e["pitch"] != "rest"]),
            ("Optional bass", [e for m in track["measures"] for e in m["bass"]]),
        ):
            wanted_notes = []
            for event in source_events:
                note_numbers = [event["midi"]] if track_name == "Melody" else event["midis"]
                start = round(q(event["start"]) * ticks)
                end = round((q(event["start"]) + q(event["duration"]) * Fraction(23, 25)) * ticks)
                wanted_notes.extend((n, start, end) for n in note_numbers)
            wanted_notes.sort(key=lambda item: (item[1], item[0], item[2]))
            actual = midi_tracks.get(track_name, [])
            self.check(len(actual) == len(wanted_notes), where, f"MIDI {track_name} note count differs")
            for index, (got, wanted) in enumerate(zip(actual, wanted_notes)):
                self.check(got == wanted, f"{where}/MIDI-{track_name}-{index}",
                           f"Expected pitch/start/end {wanted}, got {got}; end includes declared 92% articulation")
        self.exports.append({"track": where, "passed": len(self.errors) == before,
                             "musicxml": str(xml_path.relative_to(ROOT)), "midi": str(midi_path.relative_to(ROOT)),
                             "musicxml_sha256": checksum(xml_path), "midi_sha256": checksum(midi_path),
                             "midi_gate_fraction": "23/25 (92%); start grid and total duration remain complete"})


def run(course_path, report_path):
    layout_path = ROOT / "instrument/corso-cf.json"
    course = json.loads(course_path.read_text(encoding="utf-8"))
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    review = Review(layout)
    review.check(course.get("schema_version") == 1, "course", "Unsupported course schema")
    review.check(course.get("tempo_unit") == "quarter", "course", "Tempo unit must be explicit quarters")
    review.check(course.get("cap_resets_at_rests") is False, "course", "Rests must not automatically reset the direction cap")
    review.check([s["id"] for s in course["songs"]] == EXPECTED_SONGS, "course", "Five-song selection/order changed")
    review.check(q(course["practice_tempo_bpm"]) == 48, "course", "Expected agreed 48-quarter practice tempo")
    cap = course["bonus_max_sounding_quarters_per_direction"]
    review.check(q(cap) == 6, "course", "Expected agreed six-quarter editorial bonus cap")
    hashes = {str(layout_path.relative_to(ROOT)): checksum(layout_path), str(course_path.relative_to(ROOT)): checksum(course_path)}
    overview = []
    for index, song in enumerate(course["songs"], 1):
        sid = song["id"]
        review.check(song["index"] == index, sid, "Lesson index does not match order")
        source_path = (ROOT / song["source_path"]).resolve()
        review.check(source_path.is_relative_to(ROOT), sid, "Source path escapes repository")
        source = json.loads(source_path.read_text(encoding="utf-8"))
        hashes[song["source_path"]] = checksum(source_path)
        review.check(set(song["variants"]) == {"basic", "bonus"}, sid, "Basic/bonus variants missing or unexpected")
        measured = {}
        for variant in ("basic", "bonus"):
            track = song["variants"][variant]
            review.check(track.get("variant") == variant, sid, "Variant label mismatch")
            review.check(q(track["practice_tempo_bpm"]) == 48, sid, "Song practice tempo differs from course")
            measured[variant] = review.verify_track(track, f"{sid}/{variant}", source=source,
                                                   melody_only=variant == "basic", cap=cap if variant == "bonus" else None)
            review.verify_exports(track, ROOT / f"course/materials/{sid}_{variant}.musicxml",
                                  ROOT / f"course/materials/{sid}_{variant}.mid", f"{sid}/{variant}")
        review.check(len(song["exercises"]) == 3, sid, "Expected three progressive preparatory exercises")
        for exercise in song["exercises"]:
            review.verify_track(exercise, exercise["id"], melody_only=True)
        review.verify_track(song["reading_quiz"], song["reading_quiz"]["id"], melody_only=True)
        basic, bonus = measured["basic"], measured["bonus"]
        for direction in ("D", "Z"):
            stat = bonus["by_direction"][direction]
            if stat["elapsed_quarters"] > stat["sounding_quarters"]:
                review.observations.append({"song": sid, "direction": direction,
                    "message": "Pauses extend elapsed direction span; they do not refill the bellows.",
                    "sounding_quarters": stat["sounding_quarters"], "elapsed_quarters": stat["elapsed_quarters"],
                    "elapsed_seconds_at_48": stat["elapsed_quarters"] * 60 / 48})
        overview.append({"id": sid, "basic_reversals": basic["reversals"], "basic_row_changes": basic["row_changes"],
                         "bonus_reversals": bonus["reversals"], "bonus_row_changes": bonus["row_changes"],
                         "bonus_max_sounding_quarters": max(v["sounding_quarters"] for v in bonus["by_direction"].values()),
                         "bonus_max_elapsed_quarters": max(v["elapsed_quarters"] for v in bonus["by_direction"].values())})
    report = {
        "status": "PASS" if not review.errors else "FAIL",
        "scope": "Five stored melody sources, ten song routes, fifteen exercises and five reading quizzes. One written pass; no simulated air capacity.",
        "independent_of_builder": True,
        "reference_hashes": hashes,
        "song_summary": overview,
        "errors": review.errors,
        "practical_observations": review.observations,
        "not_certified": ["Source transcription against historical printed music", "Final PDF and MP3 rendering", "Physical air reserve or finger comfort on Patrick's instrument"],
        "tracks": review.tracks,
        "exports": review.exports,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{report['status']}: five songs checked against source melodies and Corso C/F mapping.")
    print("Basic routes: C row only, no bass. Bonus: direction, accompaniment, six-quarter cap and metrics checked.")
    print("MusicXML and MIDI: complete melody, timing and labels checked; MIDI note gates use the declared 92% articulation.")
    if review.errors:
        for error in review.errors[:20]:
            print(f"ERROR {error['location']}: {error['message']}")
        if len(review.errors) > 20:
            print(f"Further errors are in {report_path}.")
    if review.observations:
        print("Note: Lasst uns froh has six sounded quarters across seven elapsed quarters; silence is not an air reset.")
    print(f"Report: {report_path}")
    return 0 if not review.errors else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course", type=Path, default=ROOT / "course/data/course.json")
    parser.add_argument("--report", type=Path, default=ROOT / "course/data/verification-report.json")
    args = parser.parse_args()
    try:
        return run(args.course.resolve(), args.report.resolve())
    except (KeyError, ValueError, TypeError, OSError, ZeroDivisionError) as exc:
        print(f"FAIL: course data cannot be completely verified: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
