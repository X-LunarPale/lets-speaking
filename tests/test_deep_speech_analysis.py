from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import deep_speech_analysis as deep


def fixture(duration=200):
    return {
        "version": 14,
        "tempos": [{"position": 0, "bpm": 120}],
        "tracks": [{
            "patterns": [{
                "uuid": "clip-a",
                "timeUnit": "tick",
                "pos": 1000,
                "clipPos": 0,
                "clipDur": duration,
                "notes": [{
                    "uuid": "note-a", "pos": 0, "dur": duration, "pitch": 60,
                    "lyric": "de", "syllable": "", "freezedDefaultSyllable": "d e",
                }],
                "parameters": {"pitchDelta": [{
                    "type": "data", "offset": 0,
                    "values": [0.01 * i for i in range(duration)],
                }]},
            }],
        }],
    }


class DeepAnalysisTests(unittest.TestCase):
    def test_tempo_integrates_duplicate_position_using_last_value(self):
        tempo = deep.TempoMap([
            {"position": 0, "bpm": 120},
            {"position": 480, "bpm": 60},
            {"position": 480, "bpm": 240},
            {"position": 960, "bpm": 120},
        ])
        np.testing.assert_allclose(
            tempo.seconds([-480, 0, 480, 960, 1440]), [-0.5, 0, 0.5, 0.75, 1.25],
        )

    def test_tempo_refuses_ramps_and_missing_timing(self):
        for events in ([], [{"position": 0, "bpm": 0}], [{"bpm": 120, "bend": 1}], [{"bpm": 120, "isLerp": True}]):
            with self.subTest(events=events), self.assertRaises(ValueError):
                deep.TempoMap(events)

    def test_trim_uses_full_note_core_and_excludes_right_boundary(self):
        project = fixture()
        pattern = project["tracks"][0]["patterns"][0]
        pattern.update(clipPos=80, clipDur=30)
        _, index = deep.build_index(project, {})
        record = index["notes"][0]
        core = index["numeric_core_candidates"][0]
        self.assertEqual(record["local_visible_range"], [80, 110])
        self.assertEqual(record["clipped_head_or_tail"], [True, True])
        self.assertEqual(core["local_inclusive_core_range"], [80, 109])
        self.assertEqual(len(core["delta_samples"]), 30)
        self.assertAlmostEqual(record["visible_duration_ms"], 31.25)

    def test_missing_curve_sample_is_not_interpolated_or_selected(self):
        project = fixture()
        pattern = project["tracks"][0]["patterns"][0]
        values = pattern["parameters"]["pitchDelta"][0]["values"]
        pattern["parameters"]["pitchDelta"] = [
            {"offset": 0, "values": values[:90]},
            {"offset": 91, "values": values[91:]},
        ]
        _, index = deep.build_index(project, {})
        self.assertEqual(len(index["notes"]), 1)
        self.assertEqual(index["numeric_core_candidates"], [])
        self.assertTrue(np.isnan(deep.lane_window(pattern, "pitchDelta", 89, 92)[1]))

    def test_overlapping_raw_curves_require_native_precedence(self):
        pattern = fixture()["tracks"][0]["patterns"][0]
        pattern["parameters"]["pitchDelta"].append({"offset": 80, "values": [0]})
        with self.assertRaisesRegex(ValueError, "overlapping"):
            deep.lane_window(pattern, "pitchDelta", 0, 200)

    def test_candidates_use_content_not_reused_uuid(self):
        project = fixture()
        original = project["tracks"][0]["patterns"][0]
        changed = copy.deepcopy(original)
        changed["uuid"] = "clip-b"
        changed["parameters"]["pitchDelta"][0]["values"][80] += 0.2
        duplicate = copy.deepcopy(original)
        duplicate["uuid"] = "clip-c"
        project["tracks"][0]["patterns"].extend([changed, duplicate])
        report, index = deep.build_index(project, {})
        self.assertEqual(len(index["numeric_core_candidates"]), 2)
        self.assertEqual(report["totals"]["duplicate_core_content_occurrences"], 1)
        self.assertNotEqual(index["notes"][0]["contour_source_id"], index["notes"][1]["contour_source_id"])
        self.assertEqual(index["notes"][0]["contour_source_id"], index["notes"][2]["contour_source_id"])

    def test_benchmark_counts_distinct_windows_once(self):
        self.assertEqual(deep.benchmark_starts(25, 24), [0])
        self.assertEqual(deep.benchmark_starts(26, 24), [0, 1])
        self.assertEqual(deep.benchmark_starts(24, 24), [])
        report, _ = deep.build_index(fixture(duration=140), {})
        self.assertEqual(report["bridge_holdout"]["24"]["window_count"], 1)
        self.assertAlmostEqual(report["bridge_holdout"]["24"]["max_error_semitones"]["max"], 0)

    def test_raw_vuv0_in_core_is_excluded_but_missing_vuv_is_unknown(self):
        project = fixture()
        pattern = project["tracks"][0]["patterns"][0]
        pattern["parameters"]["vuv"] = [{"offset": 80, "values": [0]}]
        report, index = deep.build_index(project, {})
        self.assertEqual(index["numeric_core_candidates"], [])
        self.assertEqual(report["totals"]["excluded_due_to_explicit_raw_vuv0_in_core"], 1)
        pattern["parameters"]["vuv"][0]["offset"] = 0
        _, index = deep.build_index(project, {})
        candidate = index["numeric_core_candidates"][0]
        self.assertEqual(candidate["vuv_core_values"], [])
        self.assertIn("absent VUV is unknown", candidate["eligibility"])

    def test_continuation_resolves_head_and_searches_by_head_pronunciation(self):
        project = fixture(duration=400)
        pattern = project["tracks"][0]["patterns"][0]
        pattern["notes"][0]["dur"] = 200
        pattern["notes"].append({
            "uuid": "tail", "pos": 200, "dur": 200, "pitch": 62,
            "lyric": "-", "syllable": "-", "freezedDefaultSyllable": "-",
        })
        report, index = deep.build_index(project, {})
        tail = index["notes"][1]
        self.assertEqual(tail["pronunciation_head"]["note_index"], 0)
        self.assertEqual(tail["effective_syllable_label"], "d e")
        self.assertEqual(tail["previous_gap_ticks"], 0)
        self.assertEqual(report["most_common_candidate_syllables"], [("d e", 2)])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.json"
            path.write_text(json.dumps(index), encoding="utf-8")
            results = deep.search_index(path, "de", None, 8)
            self.assertEqual([entry["lyric"] for entry in results], ["de", "-"])

    def test_orphan_continuation_is_retained_in_records_but_not_donor_bank(self):
        project = fixture()
        project["tracks"][0]["patterns"][0]["notes"][0]["lyric"] = "-"
        report, index = deep.build_index(project, {})
        self.assertEqual(len(index["notes"]), 1)
        self.assertIsNone(index["notes"][0]["pronunciation_head"])
        self.assertEqual(index["numeric_core_candidates"], [])
        self.assertEqual(report["totals"]["excluded_due_to_unresolved_continuation_head"], 1)

    def test_search_finds_duplicate_content_occurrence_on_requested_track(self):
        project = fixture()
        project["tracks"].append(copy.deepcopy(project["tracks"][0]))
        project["tracks"][1]["patterns"][0]["uuid"] = "other-track"
        _, index = deep.build_index(project, {})
        self.assertEqual(len(index["numeric_core_candidates"]), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.json"
            path.write_text(json.dumps(index), encoding="utf-8")
            results = deep.search_index(path, "de", 1, 8)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["occurrence_id"], "T1/P0/N0")
            self.assertEqual(results[0]["core_array_source_occurrence"], "T0/P0/N0")

    def test_explicit_pronunciation_replaces_frozen_default_in_search(self):
        project = fixture()
        project["tracks"][0]["patterns"][0]["notes"][0]["syllable"] = "t e"
        _, index = deep.build_index(project, {})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.json"
            path.write_text(json.dumps(index), encoding="utf-8")
            self.assertEqual(deep.search_index(path, "d e", None, 8), [])
            phonetic_match = deep.search_index(path, "t e", None, 8)
            self.assertEqual(len(phonetic_match), 1)
            self.assertEqual(phonetic_match[0]["matched_fields"], ["effective_phonemes"])
            lyric_match = deep.search_index(path, "de", None, 8)
            self.assertEqual(len(lyric_match), 1)
            self.assertNotIn("effective_phonemes", lyric_match[0]["matched_fields"])

    def test_large_span_is_an_inspectable_flag_not_a_damage_verdict(self):
        project = fixture()
        pattern = project["tracks"][0]["patterns"][0]
        pattern["parameters"]["pitchDelta"][0]["values"] = [0.2 * i for i in range(200)]
        report, index = deep.build_index(project, {})
        candidate = index["numeric_core_candidates"][0]
        self.assertIn("raw_vuv_absent", candidate["review_flags"])
        self.assertIn("raw_span_above12_semitones", candidate["review_flags"])
        self.assertGreater(candidate["core_raw_delta_span_semitones"], 12)
        self.assertEqual(report["candidate_review_flags"]["raw_span_above12_semitones"], 1)

    def test_fractional_note_without_integer_sample_remains_a_serializable_record(self):
        project = fixture(duration=10)
        project["tracks"][0]["patterns"][0]["notes"][0].update(pos=0.1, dur=0.1)
        report, index = deep.build_index(project, {})
        self.assertEqual(report["totals"]["visible_note_occurrences"], 1)
        self.assertEqual(index["notes"][0]["drawn_pitch_fraction"], 0.0)
        self.assertEqual(index["notes"][0]["stable_core_sample_count"], 0)
        json.dumps(index, allow_nan=False)

    def test_core_overlapping_another_note_cannot_be_a_numeric_donor_candidate(self):
        project = fixture(duration=300)
        pattern = project["tracks"][0]["patterns"][0]
        pattern["notes"][0]["dur"] = 200
        following = copy.deepcopy(pattern["notes"][0])
        following.update(uuid="overlap", pos=100)
        pattern["notes"].append(following)
        report, index = deep.build_index(project, {})
        self.assertEqual(index["numeric_core_candidates"], [])
        self.assertEqual(report["totals"]["excluded_due_to_overlapping_note_in_core"], 2)
        self.assertEqual(index["notes"][0]["core_overlapping_note_indices"], [1])
        self.assertEqual(index["notes"][1]["core_overlapping_note_indices"], [0])

    def test_identical_values_in_different_trim_ranges_keep_their_geometry(self):
        project = fixture(duration=500)
        first = project["tracks"][0]["patterns"][0]
        first.update(clipPos=170, clipDur=50)
        first["parameters"]["pitchDelta"][0]["values"] = [0] * 500
        second = copy.deepcopy(first)
        second.update(uuid="different-trim", clipPos=230)
        project["tracks"].append({"patterns": [second]})
        _, index = deep.build_index(project, {})
        self.assertEqual(len(index["numeric_core_candidates"]), 2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.json"
            path.write_text(json.dumps(index), encoding="utf-8")
            result = deep.search_index(path, "de", 1, 8)[0]
            self.assertEqual(result["local_inclusive_core_range"], [230, 279])

    def test_deduplicated_search_retains_occurrence_tempo_duration(self):
        project = fixture(duration=1000)
        project["tempos"].append({"position": 2000, "bpm": 240})
        first = project["tracks"][0]["patterns"][0]
        first.update(pos=0, clipPos=170, clipDur=50)
        second = copy.deepcopy(first)
        second.update(uuid="faster-copy", pos=2000)
        project["tracks"].append({"patterns": [second]})
        _, index = deep.build_index(project, {})
        self.assertEqual(len(index["numeric_core_candidates"]), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.json"
            path.write_text(json.dumps(index), encoding="utf-8")
            result = deep.search_index(path, "de", 1, 8)[0]
            self.assertAlmostEqual(result["full_note_duration_ms"], 520.833333)
            self.assertAlmostEqual(result["core_endpoint_span_ms"], 25.520833)


if __name__ == "__main__":
    unittest.main()
