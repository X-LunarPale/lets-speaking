"""Prepare a local pitch replacement from ACE Studio's dense combined export."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


class PitchError(ValueError):
    pass


def finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def pitch_value(value: Any) -> float:
    if not finite_number(value) or not 0 <= value <= 127:
        raise PitchError("A healthy pitch must be finite absolute MIDI in [0, 127].")
    return float(value)


class DensePitch:
    def __init__(self, payload: dict[str, Any], source: str = "") -> None:
        if payload.get("level") != "combined":
            raise PitchError(
                "Use a dense --level combined --encoding json export. "
                "Raw ACEP pitchDelta, user anchors, ai, and final are not accepted."
            )
        self.source = source
        self.begin = payload.get("posBegin")
        self.points = payload.get("points")
        count = payload.get("count")
        if (
            not isinstance(self.begin, int)
            or isinstance(self.begin, bool)
            or not isinstance(count, int)
            or isinstance(count, bool)
            or not isinstance(self.points, list)
            or count != len(self.points)
            or count < 2
        ):
            raise PitchError("Dense export requires integer posBegin and matching count.")
        voicing = payload.get("voicing")
        if not isinstance(voicing, dict) or not isinstance(
            voicing.get("unvoiced"), list
        ):
            raise PitchError(
                "voicing.unvoiced is required; neither 0 nor an absent mask proves voicing."
            )
        self.unvoiced: list[tuple[float, float]] = []
        for span in voicing["unvoiced"]:
            if not isinstance(span, dict):
                raise PitchError("Each unvoiced span must contain begin and end.")
            left, right = span.get("begin"), span.get("end")
            if not finite_number(left) or not finite_number(right) or left >= right:
                raise PitchError("Malformed half-open unvoiced span.")
            self.unvoiced.append((float(left), float(right)))
        self.fingerprint = payload.get("fingerprint")
        if self.fingerprint is not None and not isinstance(self.fingerprint, str):
            raise PitchError("fingerprint must be a CLI token string when present.")

    @classmethod
    def load(cls, path: Path) -> "DensePitch":
        with path.open(encoding="utf-8-sig") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict):
            raise PitchError("Input must be the complete vocalpitch read JSON object.")
        return cls(payload, str(path.resolve()))

    def window(
        self, left: int, right: int, *, healthy_interior: bool
    ) -> list[tuple[int, float]]:
        if (
            not isinstance(left, int)
            or not isinstance(right, int)
            or left >= right
            or left < self.begin
            or right >= self.begin + len(self.points)
        ):
            raise PitchError(
                "Healthy endpoint ticks must be ascending and both exist in the export. "
                "The CLI export's range-end is exclusive."
            )
        if any(start <= right and end > left for start, end in self.unvoiced):
            raise PitchError("The selected interval intersects unvoiced samples.")
        samples = []
        for tick in range(left, right + 1):
            value = self.points[tick - self.begin]
            if not finite_number(value):
                raise PitchError(
                    "The selected interval contains null, NaN, or nonnumeric gaps; "
                    "do not bridge an unknown or unrendered gap."
                )
            if healthy_interior or tick in (left, right):
                pitch_value(value)
            samples.append((tick, float(value)))
        return samples


def bridge(
    target: DensePitch, left: int, right: int, max_span: int = 24
) -> tuple[list[list[float]], dict[str, Any]]:
    if max_span <= 0:
        raise PitchError("max-span must be positive.")
    if right - left > max_span:
        raise PitchError(
            "Bridge span exceeds max-span. Choose a speech-contour donor for longer damage."
        )
    samples = target.window(left, right, healthy_interior=False)
    points = [[left, samples[0][1]], [right, samples[-1][1]]]
    return points, {
        "method": "bridge",
        "maxSpanTicks": max_span,
        "note": (
            "Only appropriate for a confirmed short glitch between healthy endpoints "
            "within one phonation segment. Two anchors remove all interior turns."
        ),
    }


def simplify_vertical(
    points: list[list[float]], max_error: float
) -> list[list[float]]:
    """RDP using pitch error in semitones rather than mixed tick/pitch distance."""
    if len(points) <= 2 or max_error == 0:
        return points
    keep = {0, len(points) - 1}
    pending = [(0, len(points) - 1)]
    while pending:
        first, last = pending.pop()
        if last <= first + 1:
            continue
        x0, y0 = points[first]
        x1, y1 = points[last]
        index, error = first, -1.0
        for i in range(first + 1, last):
            x, y = points[i]
            expected = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
            if abs(y - expected) > error:
                index, error = i, abs(y - expected)
        if error > max_error:
            keep.add(index)
            pending.extend(((first, index), (index, last)))
    return [points[i] for i in sorted(keep)]


def donor_patch(
    target: DensePitch,
    donor: DensePitch,
    left: int,
    right: int,
    donor_left: int,
    donor_right: int,
    amplitude: float = 1.0,
    max_error: float = 0.0,
) -> tuple[list[list[float]], dict[str, Any]]:
    if not finite_number(amplitude) or not 0 <= amplitude <= 2:
        raise PitchError("amplitude must be between 0 and 2.")
    if not finite_number(max_error) or not 0 <= max_error <= 1:
        raise PitchError("max-error must be between 0 and 1 semitone.")
    target_samples = target.window(left, right, healthy_interior=False)
    donor_samples = donor.window(donor_left, donor_right, healthy_interior=True)
    y0, y1 = target_samples[0][1], target_samples[-1][1]
    d0, d1 = donor_samples[0][1], donor_samples[-1][1]
    points = []
    for tick, value in donor_samples:
        if tick == donor_left:
            points.append([left, y0])
            continue
        if tick == donor_right:
            points.append([right, y1])
            continue
        fraction = (tick - donor_left) / (donor_right - donor_left)
        target_line = y0 + fraction * (y1 - y0)
        donor_line = d0 + fraction * (d1 - d0)
        value = target_line + amplitude * (value - donor_line)
        pitch_value(value)
        points.append([left + fraction * (right - left), value])
    sparse = simplify_vertical(points, max_error)
    return sparse, {
        "method": "donor-residual",
        "donor": {
            "source": donor.source,
            "fingerprint": donor.fingerprint,
            "leftTick": donor_left,
            "rightTick": donor_right,
        },
        "amplitude": amplitude,
        "timeStretch": (right - left) / (donor_right - donor_left),
        "maxPolylineErrorSemitones": max_error,
        "simplification": "disabled" if max_error == 0 else "polyline-only; not an engine interpolation bound",
        "denseDonorSampleCount": len(points),
        "note": (
            "Select a healthy donor of the same syllable, tone, phonation and prosodic role. "
            "Default output retains each donor sample; time-mapped fractional ticks are allowed. "
            "Optional simplification bounds only its polyline, not ACE's MonotonicHermite "
            "interpolant. Compare dense combined pitch inside and outside the repaired span "
            "and effective voicing after a guarded write."
        ),
    }


def write_artifacts(
    output: Path,
    points: list[list[float]],
    metadata: dict[str, Any],
    *,
    overwrite: bool = False,
) -> Path:
    report = output.with_name(output.stem + ".meta.json")
    if not overwrite and any(path.exists() for path in (output, report)):
        raise PitchError("Output or metadata already exists; choose a new path or --overwrite.")
    output.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "x"
    with output.open(mode, encoding="utf-8", newline="\n") as stream:
        json.dump(points, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")
    with report.open(mode, encoding="utf-8", newline="\n") as stream:
        json.dump(metadata, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("bridge", "donor"):
        command = commands.add_parser(name)
        command.add_argument("--target", required=True, type=Path)
        command.add_argument("--left", required=True, type=int, help="Healthy left endpoint tick, inclusive.")
        command.add_argument("--right", required=True, type=int, help="Healthy right endpoint tick, inclusive.")
        command.add_argument("--output", required=True, type=Path)
        command.add_argument("--overwrite", action="store_true")
        if name == "bridge":
            command.add_argument("--max-span", type=int, default=24)
        else:
            command.add_argument("--donor", required=True, type=Path)
            command.add_argument("--donor-left", required=True, type=int)
            command.add_argument("--donor-right", required=True, type=int)
            command.add_argument("--amplitude", type=float, default=1.0)
            command.add_argument(
                "--max-error", type=float, default=0.0,
                help="Optional polyline simplification tolerance; default 0 retains every donor sample.",
            )
    args = parser.parse_args(argv)
    try:
        report_path = args.output.with_name(args.output.stem + ".meta.json")
        inputs = [args.target]
        if args.command == "donor":
            inputs.append(args.donor)
        if any(
            output.resolve() == source.resolve()
            for output in (args.output, report_path)
            for source in inputs
        ):
            raise PitchError("Output and metadata must not replace an input export.")
        target = DensePitch.load(args.target)
        if args.command == "bridge":
            points, details = bridge(target, args.left, args.right, args.max_span)
        else:
            donor = DensePitch.load(args.donor)
            points, details = donor_patch(
                target, donor, args.left, args.right,
                args.donor_left, args.donor_right, args.amplitude, args.max_error,
            )
        metadata = {
            "format": "ace-speech-pitch-patch-v1",
            "target": {
                "source": target.source,
                "fingerprint": target.fingerprint,
                "level": "combined",
                "leftTick": args.left,
                "rightTick": args.right,
            },
            "pointCount": len(points),
            "units": "clip-local ticks / absolute MIDI semitones",
            "writeVoicing": "omit",
            "status": "proposal-only; no project or application changed",
            **details,
        }
        report = write_artifacts(args.output, points, metadata, overwrite=args.overwrite)
        print(json.dumps({
            "pointsFile": str(args.output.resolve()),
            "metadataFile": str(report.resolve()),
            "pointCount": len(points),
            "status": metadata["status"],
        }, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"pitch_patch: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
