from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1] / "scripts" / "pitch_patch.py"
SPEC = importlib.util.spec_from_file_location("pitch_patch", MODULE)
pitch_patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pitch_patch)


def curve(values, begin=0, unvoiced=None):
    return pitch_patch.DensePitch({
        "level": "combined",
        "posBegin": begin,
        "count": len(values),
        "points": values,
        "voicing": {"unvoiced": [] if unvoiced is None else unvoiced},
        "fingerprint": "test-token",
    })


class PatchTests(unittest.TestCase):
    def test_bridge_replaces_short_spike_with_healthy_endpoints(self):
        points, _ = pitch_patch.bridge(curve([60, 61, 99, 63, 64]), 1, 3)
        self.assertEqual(points, [[1, 61.0], [3, 63.0]])

    def test_bridge_can_replace_out_of_range_corrupt_interior(self):
        points, _ = pitch_patch.bridge(curve([60, 1000, 61]), 0, 2)
        self.assertEqual(points, [[0, 60.0], [2, 61.0]])

    def test_bridge_span_cap(self):
        with self.assertRaises(pitch_patch.PitchError):
            pitch_patch.bridge(curve([60] * 30), 0, 25)

    def test_bridge_explicit_span_cap(self):
        points, _ = pitch_patch.bridge(curve([60] * 30), 0, 25, max_span=25)
        self.assertEqual(points[-1][0], 25)

    def test_healthy_endpoint_must_be_valid_absolute_pitch(self):
        with self.assertRaises(pitch_patch.PitchError):
            pitch_patch.bridge(curve([-1, 60, 61]), 0, 2)

    def test_reject_null_gap(self):
        with self.assertRaises(pitch_patch.PitchError):
            pitch_patch.bridge(curve([60, None, 61]), 0, 2)

    def test_reject_nonfinite_gap(self):
        with self.assertRaises(pitch_patch.PitchError):
            pitch_patch.bridge(curve([60, float("nan"), 61]), 0, 2)

    def test_reject_unvoiced_even_if_pitch_is_nonzero(self):
        with self.assertRaises(pitch_patch.PitchError):
            pitch_patch.bridge(
                curve([60, 60, 61], unvoiced=[{"begin": 1, "end": 2}]), 0, 2,
            )

    def test_unvoiced_end_is_exclusive(self):
        points, _ = pitch_patch.bridge(
            curve([60, 60, 61], unvoiced=[{"begin": -2, "end": 0}]), 0, 2,
        )
        self.assertEqual(len(points), 2)

    def test_zero_is_legal_pitch_when_voicing_is_known(self):
        points, _ = pitch_patch.bridge(curve([0, 0, 0]), 0, 2)
        self.assertEqual(points[0][1], 0.0)

    def test_absent_voicing_is_not_inferred_from_zero(self):
        with self.assertRaises(pitch_patch.PitchError):
            pitch_patch.DensePitch({
                "level": "combined", "posBegin": 0, "count": 3, "points": [0, 0, 0],
            })

    def test_reject_final_and_raw_delta_inputs(self):
        for payload in (
            {"type": "data", "offset": 0, "values": [-53, -53]},
            {"level": "final", "posBegin": 0, "count": 2, "points": [60, 61]},
            {"level": "user", "points": [[0, 60], [2, 61]]},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(pitch_patch.PitchError):
                    pitch_patch.DensePitch(payload)

    def test_target_ticks_are_local_and_right_endpoint_must_exist(self):
        data = curve([60, 61, 62], begin=100)
        points, _ = pitch_patch.bridge(data, 100, 102)
        self.assertEqual(points[0][0], 100)
        with self.assertRaises(pitch_patch.PitchError):
            pitch_patch.bridge(data, 100, 103)

    def test_donor_preserves_hill_turn_and_target_endpoints(self):
        points, meta = pitch_patch.donor_patch(
            curve([50] * 9 + [52]), curve([60, 62, 64, 62, 60]), 0, 9, 0, 4,
        )
        self.assertEqual(points[0], [0, 50.0])
        self.assertEqual(points[-1], [9, 52.0])
        self.assertIn([4.5, 55.0], points)
        self.assertEqual(meta["donor"]["fingerprint"], "test-token")

    def test_donor_preserves_valley_turn(self):
        points, _ = pitch_patch.donor_patch(
            curve([60] * 5), curve([65, 63, 61, 63, 65]), 0, 4, 0, 4,
        )
        self.assertIn([2.0, 56.0], points)

    def test_donor_affine_rise_keeps_target_rise(self):
        points, _ = pitch_patch.donor_patch(
            curve([50, 50, 54]), curve([60, 61, 62]), 0, 2, 0, 2,
        )
        self.assertEqual(points, [[0, 50.0], [1.0, 52.0], [2, 54.0]])

    def test_donor_default_retains_samples_at_peak_and_shoulders(self):
        values = [60, 61, 62, 63, 64, 63, 62, 61, 60]
        points, meta = pitch_patch.donor_patch(curve([60] * 9), curve(values), 0, 8, 0, 8)
        self.assertEqual(points, [[tick, float(value)] for tick, value in enumerate(values)])
        self.assertEqual(meta["simplification"], "disabled")

    def test_donor_optional_simplification_is_explicitly_polyline_only(self):
        points, meta = pitch_patch.donor_patch(
            curve([60] * 9), curve([60, 61, 62, 63, 64, 63, 62, 61, 60]),
            0, 8, 0, 8, max_error=0.05,
        )
        self.assertEqual(points, [[0, 60.0], [4.0, 64.0], [8, 60.0]])
        self.assertIn("not an engine", meta["simplification"])

    def test_donor_preserves_legal_zero_endpoint_despite_cancellation_roundoff(self):
        points, _ = pitch_patch.donor_patch(
            curve([0, 0, 0]), curve([126.9999, 63.50495, 0.01]), 0, 2, 0, 2,
        )
        self.assertEqual(points[0], [0, 0.0])
        self.assertEqual(points[-1], [2, 0.0])
        self.assertLessEqual(max(abs(value) for _, value in points), 1e-12)

    def test_donor_rejects_gap_and_unvoiced(self):
        for donor in (
            curve([60, None, 61]),
            curve([60, 61, 62], unvoiced=[{"begin": 1, "end": 2}]),
        ):
            with self.subTest(donor=donor):
                with self.assertRaises(pitch_patch.PitchError):
                    pitch_patch.donor_patch(curve([50, 51, 52]), donor, 0, 2, 0, 2)

    def test_donor_does_not_clamp_out_of_range_proposal(self):
        with self.assertRaises(pitch_patch.PitchError):
            pitch_patch.donor_patch(
                curve([125, 125, 125]), curve([60, 70, 60]), 0, 2, 0, 2,
            )

    def test_simplification_bounds_polyline_pitch_error(self):
        original = [[i, 60 + math_value] for i, math_value in enumerate(
            [0, 0.01, 0.02, 2, 0.02, 0.01, 0]
        )]
        simple = pitch_patch.simplify_vertical(original, 0.05)
        self.assertIn([3, 62], simple)
        for x, y in original:
            for (x0, y0), (x1, y1) in zip(simple, simple[1:]):
                if x0 <= x <= x1:
                    expected = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
                    self.assertLessEqual(abs(y - expected), 0.05 + 1e-12)
                    break

    def test_artifact_creation_protects_both_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "pitch.json"
            metadata = {"fingerprint": "token"}
            report = pitch_patch.write_artifacts(output, [[0, 60], [2, 61]], metadata)
            self.assertEqual(json.loads(output.read_text()), [[0, 60], [2, 61]])
            self.assertEqual(json.loads(report.read_text()), metadata)
            with self.assertRaises(pitch_patch.PitchError):
                pitch_patch.write_artifacts(output, [[0, 61], [2, 62]], metadata)
            self.assertEqual(json.loads(output.read_text())[0], [0, 60])

    def test_cli_refuses_replacing_an_input_even_with_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            payload = {
                "level": "combined", "posBegin": 0, "count": 3,
                "points": [60, 99, 61], "voicing": {"unvoiced": []},
            }
            source.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(SystemExit):
                pitch_patch.main([
                    "bridge", "--target", str(source), "--left", "0",
                    "--right", "2", "--output", str(source), "--overwrite",
                ])
            self.assertEqual(json.loads(source.read_text()), payload)


if __name__ == "__main__":
    unittest.main()
