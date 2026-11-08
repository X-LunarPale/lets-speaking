from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1] / "scripts" / "ace_project.py"
SPEC = importlib.util.spec_from_file_location("ace_project", MODULE)
ace_project = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ace_project)


def reference_fixture(version=14):
    return {
        "version": version,
        "tracks": [
            {
                "type": "sing",
                "patterns": [
                    {
                        "uuid": "clip-a",
                        "timeUnit": "tick",
                        "pos": 1000,
                        "clipPos": 100,
                        "clipDur": 100,
                        "notes": [
                            {"uuid": "hidden", "pos": 0, "dur": 50, "pitch": 72},
                            {
                                "uuid": "visible",
                                "pos": 90,
                                "dur": 30,
                                "pitch": 60,
                                "headConsonants": [0.07 if version >= 7 else 120],
                            },
                            {"uuid": "after", "pos": 200, "dur": 50, "pitch": 80},
                        ],
                        "parameters": {
                            "pitchDelta": [
                                {"type": "data", "offset": 98, "values": [-99, -99, -60, -60, 0.5, 1.0]},
                                {"type": "anchor", "points": [150, 60.5, 160, 61.5]},
                            ],
                            "tension": [],
                        },
                        "vocalControls": {
                            "soft": {"global": 0, "lines": [{"ticks": [50, 110, 180, 220], "values": [9, -0.1, 0.2, 9]}]}
                        },
                    }
                ],
            }
        ],
    }


def contour_fixture():
    return {
        "version": 14,
        "tracks": [{"patterns": [{
            "uuid": "clip-a", "timeUnit": "tick", "pos": 0, "clipPos": 0, "clipDur": 200,
            "notes": [{"uuid": "note-a", "pos": 0, "dur": 200, "pitch": 60, "lyric": "de",
                       "syllable": "", "freezedDefaultSyllable": "d e"}],
            "parameters": {"pitchDelta": [{"offset": 0, "values": [0.01 * i for i in range(200)]}]},
        }]}],
    }


class ProjectTests(unittest.TestCase):
    def test_profile_uses_trimmed_notes_and_excludes_end_boundary(self):
        report = ace_project.analyze(reference_fixture(), {})
        self.assertEqual(report["totals"]["stored_notes"], 3)
        self.assertEqual(report["totals"]["visible_note_occurrences"], 1)
        self.assertEqual(report["visible_note_duration_ticks"]["median"], 20)
        self.assertEqual(report["visible_note_pitch"]["median"], 60)

    def test_anchor_absolute_and_legacy_relative_values_are_separate(self):
        report = ace_project.analyze(reference_fixture(), {})
        lane = report["parameters"]["pitchDelta"]["values_by_storage_type"]
        self.assertEqual(lane["data"]["count"], 4)
        self.assertEqual(lane["data"]["min"], -60)
        self.assertEqual(lane["anchor"]["median"], 61)

    def test_empty_lane_is_present_without_invented_values(self):
        report = ace_project.analyze(reference_fixture(), {})
        lane = report["parameters"]["tension"]
        self.assertEqual(lane["stored_curves"], 0)
        self.assertEqual(lane["visible_curves"], 0)
        self.assertEqual(lane["values_by_storage_type"], {})

    def test_control_parallel_arrays_keep_negative_values_and_trim(self):
        report = ace_project.analyze(reference_fixture(), {})
        values = report["vocal_controls"][0]["visible_anchor_values"]
        self.assertEqual(values["count"], 2)
        self.assertEqual(values["min"], -0.1)
        self.assertEqual(values["max"], 0.2)

    def test_consonant_units_follow_project_version(self):
        current = ace_project.analyze(reference_fixture(), {})
        legacy = ace_project.analyze(reference_fixture(version=6), {})
        self.assertEqual(current["head_consonants"]["unit"], "seconds")
        self.assertEqual(legacy["head_consonants"]["unit"], "legacy ticks")
        self.assertEqual(legacy["head_consonants"]["median"], 120)

    def test_same_note_uuid_in_two_clips_does_not_remove_playback_occurrence(self):
        project = reference_fixture()
        pattern = copy.deepcopy(project["tracks"][0]["patterns"][0])
        pattern["uuid"] = "clip-b"
        pattern["pos"] = 2000
        project["tracks"][0]["patterns"].append(pattern)
        report = ace_project.analyze(project, {})
        self.assertEqual(report["totals"]["visible_note_occurrences"], 2)
        self.assertEqual(report["totals"]["visible_unique_note_uuids"], 1)

    def test_extract_preserves_raw_delta_and_does_not_modify_source_dict(self):
        project = reference_fixture()
        original = copy.deepcopy(project)
        region = ace_project.extract_region(project, 0, 0, 100, 103)
        self.assertEqual(region["parameters"]["pitchDelta"][0]["values"], [-60, -60, 0.5])
        self.assertEqual(region["parameters"]["pitchDelta"][0]["offset"], 100)
        self.assertIn("full pattern", region["vocalControls_scope"])
        self.assertEqual(project, original)

    def test_extract_refuses_outside_visible_bounds(self):
        with self.assertRaises(ValueError):
            ace_project.extract_region(reference_fixture(), 0, 0, 50, 103)

    def test_json_reader_never_changes_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.json"
            payload = json.dumps(reference_fixture()).encode("utf-8")
            path.write_bytes(payload)
            project, source = ace_project.load_project(path)
            self.assertEqual(source["format"], "decoded-json")
            self.assertEqual(project["version"], 14)
            self.assertEqual(path.read_bytes(), payload)

    def test_acep2_real_decompression_and_size_validation(self):
        import cbor2
        from compression import zstd

        raw = cbor2.dumps(reference_fixture())
        compressed = zstd.compress(raw)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.acep"
            payload = b"ACEP2\x01\x00\x00" + struct.pack("<QQQ", 40, len(compressed), len(raw)) + b"\x00" * 8 + compressed
            path.write_bytes(payload)
            project, source = ace_project.load_project(path)
            self.assertEqual(project, reference_fixture())
            self.assertEqual(source["decoded_bytes"], len(raw))
            self.assertEqual(path.read_bytes(), payload)
            path.write_bytes(payload[:24] + struct.pack("<Q", len(raw) + 1) + payload[32:])
            with self.assertRaisesRegex(ValueError, "size"):
                ace_project.load_project(path)

    def test_encrypted_legacy_route_is_explicitly_unsupported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "encrypted.acep"
            path.write_text(json.dumps({"version": 2, "content": "AA=="}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "CLI"):
                ace_project.load_project(path)

    def test_output_json_refuses_existing_file_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            ace_project.write_json(path, {"test": 1})
            with self.assertRaises(FileExistsError):
                ace_project.write_json(path, {"test": 2})
            self.assertEqual(json.loads(path.read_text()), {"test": 1})

    def test_contour_examples_keep_actual_variants_of_reused_note_uuid(self):
        project = contour_fixture()
        changed = copy.deepcopy(project["tracks"][0]["patterns"][0])
        changed["uuid"] = "clip-b"
        changed["parameters"]["pitchDelta"][0]["values"] = [3 - 0.01 * i for i in range(200)]
        project["tracks"][0]["patterns"].append(changed)
        result = ace_project.build_contour_bank(project)
        self.assertEqual(sum(result["candidate_counts"].values()), 2)
        self.assertEqual(len(result["examples"]), 2)
        self.assertEqual({entry["shape"] for entry in result["examples"]}, {"rise", "fall"})

    def test_contour_examples_skip_partially_overlapping_raw_curves(self):
        project = contour_fixture()
        pattern = project["tracks"][0]["patterns"][0]
        pattern["parameters"]["pitchDelta"].append({"offset": 80, "values": [9] * 20})
        result = ace_project.build_contour_bank(project)
        self.assertEqual(result["examples"], [])
        self.assertEqual(result["candidate_counts"], {})

    def test_contour_overlap_check_respects_visible_window_and_adjacent_boundaries(self):
        project = contour_fixture()
        pattern = project["tracks"][0]["patterns"][0]
        pattern.update(clipPos=40, clipDur=120)
        pattern["parameters"]["pitchDelta"].extend([
            {"offset": 0, "values": [9] * 40},
            {"offset": 160, "values": [9] * 40},
            {"offset": 80, "values": []},
        ])
        result = ace_project.build_contour_bank(project)
        self.assertEqual(len(result["examples"]), 1)
        example = result["examples"][0]
        self.assertEqual([example["local_begin"], example["local_end_exclusive"]], [40, 160])
        self.assertEqual(example["delta_samples"], [round(0.01 * i, 7) for i in range(40, 160)])

    def test_contour_examples_skip_intersecting_absolute_anchor_curve(self):
        project = contour_fixture()
        pattern = project["tracks"][0]["patterns"][0]
        pattern["parameters"]["pitchDelta"].append({
            "type": "anchor", "points": [80, 69, 100, 69],
        })
        result = ace_project.build_contour_bank(project)
        self.assertEqual(result["examples"], [])
        self.assertEqual(result["candidate_counts"], {})

    def test_contour_examples_allow_anchor_curves_outside_visible_window(self):
        project = contour_fixture()
        pattern = project["tracks"][0]["patterns"][0]
        pattern.update(clipPos=40, clipDur=120)
        pattern["parameters"]["pitchDelta"].extend([
            {"type": "anchor", "points": [0, 69, 39, 69]},
            {"type": "anchor", "points": [160, 69, 180, 69]},
        ])
        result = ace_project.build_contour_bank(project)
        self.assertEqual(len(result["examples"]), 1)
        self.assertEqual(result["examples"][0]["delta_samples"], [round(0.01 * i, 7) for i in range(40, 160)])

    def test_contour_examples_skip_fractional_dense_offset_without_truncating(self):
        project = contour_fixture()
        pattern = project["tracks"][0]["patterns"][0]
        pattern.update(clipPos=1, clipDur=199)
        pattern["parameters"]["pitchDelta"][0]["offset"] = 0.5
        result = ace_project.build_contour_bank(project)
        self.assertEqual(result["examples"], [])
        self.assertEqual(result["candidate_counts"], {})

    def test_small_same_shape_contour_bank_does_not_stop_on_rounded_duplicate(self):
        project = contour_fixture()
        original = project["tracks"][0]["patterns"][0]
        for i in range(1, 4):
            pattern = copy.deepcopy(original)
            pattern.update(uuid=f"clip-{i}", pos=i * 300)
            pattern["notes"][0]["uuid"] = f"note-{i}"
            project["tracks"][0]["patterns"].append(pattern)
        result = ace_project.build_contour_bank(project, max_examples=40)
        self.assertEqual(result["candidate_counts"], {"rise": 4})
        self.assertEqual(len(result["examples"]), 4)
        limited = ace_project.build_contour_bank(project, max_examples=2)
        self.assertEqual(len(limited["examples"]), 2)
        self.assertEqual(len({entry["note_uuid"] for entry in limited["examples"]}), 2)


if __name__ == "__main__":
    unittest.main()
