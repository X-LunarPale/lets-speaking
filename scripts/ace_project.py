"""Read ACE project data without opening or changing the live application."""

from __future__ import annotations

import argparse
import base64
import collections
import json
import math
import pathlib
import struct
from typing import Any

TICKS_PER_QUARTER = 480


def decompress_zstd(data: bytes) -> bytes:
    try:
        from compression import zstd

        return zstd.decompress(data)
    except ImportError:
        try:
            import zstandard
        except ImportError as exc:
            raise RuntimeError(
                "Use Python 3.14+, or install zstandard: python -m pip install zstandard"
            ) from exc
        return zstandard.ZstdDecompressor().decompress(data)


def load_project(path: str | pathlib.Path) -> tuple[dict[str, Any], dict[str, Any]]:
    source = pathlib.Path(path).resolve()
    data = source.read_bytes()
    info: dict[str, Any] = {"path": str(source), "file_bytes": len(data)}
    if data.startswith(b"ACEP2"):
        if len(data) < 34 or data[5] != 1:
            raise ValueError("Unsupported or truncated ACEP2 header")
        offset, size, raw_size = struct.unpack_from("<QQQ", data, 8)
        if offset < 34 or offset + size > len(data):
            raise ValueError("ACEP2 content slice is outside the supplied file")
        raw = decompress_zstd(data[offset : offset + size])
        if len(raw) != raw_size:
            raise ValueError("ACEP2 decompressed size disagrees with header")
        try:
            import cbor2
        except ImportError as exc:
            raise RuntimeError("Install CBOR support: python -m pip install cbor2") from exc
        project = cbor2.loads(raw)
        info.update(
            format="ACEP2",
            content_offset=offset,
            compressed_bytes=size,
            decoded_bytes=len(raw),
        )
    else:
        wrapper = json.loads(data.decode("utf-8-sig"))
        if "tracks" in wrapper:
            project = wrapper
            info["format"] = "decoded-json"
        elif "content" in wrapper:
            if wrapper.get("version", 0) < 1000:
                raise ValueError("Encrypted legacy ACEP: read it through ACE Studio CLI")
            if wrapper.get("compressMethod", "zstd") != "zstd":
                raise ValueError("Unsupported legacy compression method")
            raw = decompress_zstd(base64.b64decode(wrapper["content"], validate=True))
            project = json.loads(raw.decode("utf-8"))
            info.update(format="ACEP-json-zstd", decoded_bytes=len(raw))
        else:
            raise ValueError("Not an ACE project or decoded project JSON")
    if not isinstance(project, dict) or not isinstance(project.get("tracks"), list):
        raise ValueError("Decoded project has no tracks array")
    info["project_version"] = project.get("version")
    return project, info


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def stats(values: list[float]) -> dict[str, Any]:
    numbers = sorted(float(x) for x in values if finite(x))
    if not numbers:
        return {"count": 0}

    def q(fraction: float) -> float:
        index = fraction * (len(numbers) - 1)
        low = math.floor(index)
        high = math.ceil(index)
        return round(numbers[low] + (numbers[high] - numbers[low]) * (index - low), 6)

    return {
        "count": len(numbers),
        "min": numbers[0],
        "p05": q(0.05),
        "p25": q(0.25),
        "median": q(0.5),
        "p75": q(0.75),
        "p95": q(0.95),
        "p99": q(0.99),
        "max": numbers[-1],
    }


def visible_bounds(pattern: dict[str, Any]) -> tuple[float, float]:
    begin = float(pattern.get("clipPos", 0))
    return begin, begin + float(pattern.get("clipDur", pattern.get("dur", 0)))


def visible_notes(pattern: dict[str, Any]) -> list[dict[str, Any]]:
    begin, end = visible_bounds(pattern)
    return [
        note
        for note in pattern.get("notes", [])
        if finite(note.get("pos"))
        and finite(note.get("dur"))
        and note["pos"] < end
        and note["pos"] + note["dur"] > begin
    ]


def visible_data_slice(curve: dict[str, Any], begin: float, end: float) -> list[Any]:
    offset = curve.get("offset", 0)
    values = curve.get("values", [])
    if not finite(offset) or not isinstance(values, list):
        return []
    first = max(0, math.ceil(begin - offset))
    stop = min(len(values), math.ceil(end - offset))
    return values[first:stop] if first < stop else []


def anchor_pairs(curve: dict[str, Any]) -> list[list[Any]]:
    points = curve.get("points", [])
    if not isinstance(points, list) or len(points) % 2:
        raise ValueError("Flattened anchor points must contain tick/value pairs")
    return [points[i : i + 2] for i in range(0, len(points), 2)]


def curve_intersects(curve: dict[str, Any], begin: float, end: float) -> bool:
    if curve.get("type", "data") == "data":
        offset = curve.get("offset", 0)
        values = curve.get("values", [])
        return finite(offset) and isinstance(values, list) and offset < end and offset + len(values) > begin
    points = curve.get("points", [])
    return (
        isinstance(points, list)
        and len(points) >= 2
        and finite(points[0])
        and finite(points[-2])
        and points[0] < end
        and points[-2] >= begin
    )


def write_json(path: pathlib.Path, payload: Any, overwrite: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def analyze(project: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": "ace-speech-reference-profile-v1",
        "source": source,
        "units": {
            "ticks_per_quarter": TICKS_PER_QUARTER,
            "note_pitch": "MIDI semitones; scaffold, not measured F0",
            "data_pitchDelta": "relative semitones against engine base pitch",
            "anchor_pitchDelta": "absolute MIDI semitones",
        },
        "tempos": project.get("tempos", []),
        "tracks": [],
    }
    totals = collections.Counter()
    note_ids: set[str] = set()
    all_note_ids: set[str] = set()
    durations: list[float] = []
    pitches: list[float] = []
    consonants: list[float] = []
    breaths: list[float] = []
    languages: collections.Counter[str] = collections.Counter()
    lane_values: dict[str, list[float]] = collections.defaultdict(list)
    lane_counts: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    lane_nulls: collections.Counter[str] = collections.Counter()
    controls: list[dict[str, Any]] = []
    structural: list[dict[str, Any]] = []
    for ti, track in enumerate(project["tracks"]):
        patterns = track.get("patterns", [])
        if not patterns:
            continue
        track_pitches: list[float] = []
        track_counts = collections.Counter()
        for pi, pattern in enumerate(patterns):
            totals["patterns"] += 1
            track_counts["patterns"] += 1
            if pattern.get("timeUnit", "tick") != "tick":
                totals["non_tick_patterns"] += 1
                continue
            begin, end = visible_bounds(pattern)
            notes = visible_notes(pattern)
            totals["stored_notes"] += len(pattern.get("notes", []))
            all_note_ids.update(n["uuid"] for n in pattern.get("notes", []) if n.get("uuid"))
            totals["visible_note_occurrences"] += len(notes)
            track_counts["visible_note_occurrences"] += len(notes)
            for note in notes:
                if note.get("uuid"):
                    note_ids.add(note["uuid"])
                duration = min(note["pos"] + note["dur"], end) - max(note["pos"], begin)
                durations.append(duration)
                if finite(note.get("pitch")):
                    pitches.append(note["pitch"])
                    track_pitches.append(note["pitch"])
                languages[str(note.get("language", "unknown"))] += 1
                consonants.extend(x for x in note.get("headConsonants", []) if finite(x))
                if finite(note.get("brLen")) and note["brLen"] > 0:
                    breaths.append(note["brLen"])
                if note.get("syllable") != note.get("freezedDefaultSyllable"):
                    totals["nondefault_syllable_occurrences"] += 1
                if "vibrato" in note:
                    totals["notes_with_serialized_vibrato"] += 1
                if note["pos"] % 30 == 0:
                    totals["on_30_tick_grid"] += 1
            for name, curves in pattern.get("parameters", {}).items():
                lane_counts[name]["present_patterns"] += 1
                lane_counts[name].setdefault("stored_curves", 0)
                lane_counts[name].setdefault("visible_curves", 0)
                if not isinstance(curves, list):
                    structural.append({"track": ti, "pattern": pi, "lane": name, "issue": "not-list"})
                    continue
                for ci, curve in enumerate(curves):
                    kind = curve.get("type", "data")
                    lane_counts[name]["stored_curves"] += 1
                    lane_counts[name][f"type_{kind}"] += 1
                    if not curve_intersects(curve, begin, end):
                        continue
                    lane_counts[name]["visible_curves"] += 1
                    if kind == "data":
                        values = visible_data_slice(curve, begin, end)
                    elif kind == "anchor":
                        try:
                            pairs = anchor_pairs(curve)
                            values = [v for tick, v in pairs if finite(tick) and begin <= tick < end]
                            if any(a[0] >= b[0] for a, b in zip(pairs, pairs[1:])):
                                structural.append(
                                    {"track": ti, "pattern": pi, "lane": name, "curve": ci, "issue": "anchor-order"}
                                )
                        except (ValueError, TypeError):
                            values = []
                            structural.append(
                                {"track": ti, "pattern": pi, "lane": name, "curve": ci, "issue": "anchor-shape"}
                            )
                    else:
                        values = []
                        structural.append(
                            {"track": ti, "pattern": pi, "lane": name, "curve": ci, "issue": f"unknown-{kind}"}
                        )
                    for value in values:
                        if finite(value):
                            # Absolute anchors and relative samples must never share a distribution.
                            lane_values[f"{name}:{kind}"].append(value)
                        elif value is None:
                            lane_nulls[name] += 1
                        else:
                            lane_counts[name]["nonfinite_or_invalid_values"] += 1
            for name, control in pattern.get("vocalControls", {}).items():
                if not isinstance(control, dict):
                    continue
                values: list[float] = []
                for line in control.get("lines", []):
                    if not isinstance(line, dict):
                        continue
                    if isinstance(line.get("ticks"), list) and isinstance(line.get("values"), list):
                        pairs = zip(line["ticks"], line["values"])
                    else:
                        flat = line.get("points", [])
                        pairs = zip(flat[::2], flat[1::2]) if isinstance(flat, list) else []
                    if pairs:
                        values.extend(
                            value
                            for tick, value in pairs
                            if finite(tick) and finite(value) and begin <= tick < end
                        )
                controls.append(
                    {"track": ti, "pattern": pi, "lane": name, "global": control.get("global"), "visible_anchor_values": stats(values)}
                )
        result["tracks"].append(
            {"index": ti, "type": track.get("type"), **track_counts, "visible_note_pitch": stats(track_pitches)}
        )
    totals["stored_track_slots"] = len(project["tracks"])
    totals["occupied_tracks"] = len(result["tracks"])
    totals["visible_unique_note_uuids"] = len(note_ids)
    totals["stored_unique_note_uuids"] = len(all_note_ids)
    totals.setdefault("notes_with_serialized_vibrato", 0)
    result.update(
        totals=dict(totals),
        languages=dict(languages),
        visible_note_duration_ticks=stats(durations),
        visible_note_pitch=stats(pitches),
        head_consonants={
            "unit": "seconds" if project.get("version", 0) >= 7 else "legacy ticks",
            **stats(consonants),
        },
        positive_brLen_raw=stats(breaths),
        parameters={
            name: {
                **counts,
                "visible_nulls": lane_nulls[name],
                "values_by_storage_type": {
                    kind: stats(lane_values[f"{name}:{kind}"])
                    for kind in ("data", "anchor")
                    if f"{name}:{kind}" in lane_values
                },
            }
            for name, counts in lane_counts.items()
        },
        vocal_controls=controls,
        structural_observations=structural,
        limits=[
            "One user-designated speech reference; descriptive evidence, not a trained general voice model.",
            "Visible note occurrences can share UUIDs. Preserve occurrence identity when repairing.",
            "Raw legacy pitch deltas include low sentinel-like runs. No universal corruption threshold is inferred.",
            "VUV polarity and exact live engine F0 are not established by offline values.",
            "No synthesis, listening, or live target repair was performed by this analysis.",
        ],
    )
    return result


def build_contour_bank(project: dict[str, Any], max_examples: int = 40) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    seen: set[tuple[Any, ...]] = set()
    for ti, track in enumerate(project["tracks"]):
        for pi, pattern in enumerate(track.get("patterns", [])):
            if pattern.get("timeUnit", "tick") != "tick":
                continue
            begin, end = visible_bounds(pattern)
            pitch_curves = pattern.get("parameters", {}).get("pitchDelta", [])
            curves = [
                c for c in pitch_curves
                if c.get("type", "data") == "data" and isinstance(c.get("values"), list)
            ]
            non_data_curves = [c for c in pitch_curves if c.get("type", "data") != "data"]
            for note in visible_notes(pattern):
                start = math.ceil(max(begin, note["pos"]))
                stop = math.ceil(min(end, note["pos"] + note["dur"]))
                if not 35 <= stop - start <= 600:
                    continue
                if any(curve_intersects(c, start, stop) for c in non_data_curves):
                    continue
                matches = [
                    c for c in curves
                    if c["values"] and curve_intersects(c, start, stop)
                ]
                if len(matches) != 1:
                    continue
                curve = matches[0]
                if curve.get("offset", 0) > start or curve.get("offset", 0) + len(curve["values"]) < stop:
                    continue
                offset = curve.get("offset", 0)
                if not finite(offset) or offset != int(offset):
                    continue
                offset = int(offset)
                values = curve["values"][start - offset : stop - offset]
                if not values or any(not finite(v) or abs(v) > 18 for v in values):
                    continue
                identity = (
                    note.get("uuid"), note.get("pos"), note.get("dur"), note.get("pitch"),
                    note.get("lyric"), note.get("language"),
                    note.get("syllable") or note.get("freezedDefaultSyllable"),
                    start, stop, tuple(float(v) for v in values),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                first, last = float(values[0]), float(values[-1])
                residual = [
                    float(value) - (first + (last - first) * index / (len(values) - 1))
                    for index, value in enumerate(values)
                ]
                high, low = max(residual), min(residual)
                if high - low < 0.35:
                    shape = "rise" if last - first > 0.7 else "fall" if last - first < -0.7 else "level"
                elif high > 1.5 * abs(low):
                    shape = "peak"
                elif abs(low) > 1.5 * high:
                    shape = "valley"
                else:
                    shape = "turns"
                groups[shape].append(
                    {
                        "shape": shape,
                        "track_index": ti,
                        "pattern_index": pi,
                        "pattern_uuid": pattern.get("uuid"),
                        "note_uuid": note.get("uuid"),
                        "lyric": note.get("lyric"),
                        "syllable": note.get("syllable"),
                        "note_pitch_scaffold": note.get("pitch"),
                        "local_begin": start,
                        "local_end_exclusive": stop,
                        "global_begin": pattern.get("pos", 0) + start,
                        "delta_samples": [round(float(v), 7) for v in values],
                        "endpoint_relative_residual_range": [round(low, 5), round(high, 5)],
                    }
                )
    # Allocate a fair quota, then spread selections across each ordered group.
    for entries in groups.values():
        entries.sort(key=lambda entry: (entry["track_index"], entry["global_begin"]))
    quotas = dict.fromkeys(groups, 0)
    remaining = min(max_examples, sum(len(entries) for entries in groups.values()))
    while remaining > 0:
        for kind, entries in groups.items():
            if quotas[kind] < len(entries):
                quotas[kind] += 1
                remaining -= 1
                if remaining == 0:
                    break
    selected = {
        kind: [
            entries[round(i * (len(entries) - 1) / max(1, quotas[kind] - 1))]
            for i in range(quotas[kind])
        ]
        for kind, entries in groups.items()
    }
    chosen = [
        entries[i]
        for i in range(max(quotas.values(), default=0))
        for entries in selected.values()
        if i < len(entries)
    ]
    return {
        "schema": "ace-speech-raw-delta-examples-v2",
        "representation": "Legacy pitchDelta data, relative semitones; NOT a CLI absolute pitch payload",
        "selection": "Visible note window, identity plus actual delta/range/effective spelling dedup, abs(delta)<=18; diagnostic selection only",
        "limits": [
            "Shape names describe raw deltas, not lexical tones or exact audible F0.",
            "Low sentinel-like values are excluded only to make examples inspectable, never as a repair rule.",
            "For donor repair, re-read combined pitch in ACE Studio at the recorded location.",
        ],
        "candidate_counts": {kind: len(entries) for kind, entries in groups.items()},
        "examples": chosen,
    }


def extract_region(
    project: dict[str, Any], track_index: int, pattern_index: int, begin: float, end: float
) -> dict[str, Any]:
    pattern = project["tracks"][track_index]["patterns"][pattern_index]
    if pattern.get("timeUnit", "tick") != "tick" or end <= begin:
        raise ValueError("Require a tick-based pattern and begin < end")
    vb, ve = visible_bounds(pattern)
    if begin < vb or end > ve:
        raise ValueError(f"Requested interval must be in visible clip-local bounds [{vb}, {ve})")
    lanes: dict[str, list[dict[str, Any]]] = {}
    for name, curves in pattern.get("parameters", {}).items():
        selected = []
        for ci, curve in enumerate(curves):
            if not curve_intersects(curve, begin, end):
                continue
            if curve.get("type", "data") == "data":
                offset = curve.get("offset", 0)
                first = max(0, math.ceil(begin - offset))
                values = visible_data_slice(curve, begin, end)
                selected.append({"source_curve_index": ci, "type": "data", "offset": offset + first, "values": values})
            else:
                # Keep original anchors, including neighbours needed for interpolation.
                selected.append({"source_curve_index": ci, **curve})
        if selected:
            lanes[name] = selected
    return {
        "schema": "ace-speech-offline-region-v1",
        "track_index": track_index,
        "pattern_index": pattern_index,
        "pattern_uuid": pattern.get("uuid"),
        "coordinate_system": "clip-local ticks",
        "range": [begin, end],
        "pattern_pos_global": pattern.get("pos"),
        "notes": [
            n for n in visible_notes(pattern)
            if n["pos"] < end and n["pos"] + n["dur"] > begin
        ],
        "parameters": lanes,
        "vocalControls": pattern.get("vocalControls", {}),
        "vocalControls_scope": "full pattern, not clipped; retained as context only",
        "anchor_curve_scope": "intersecting original curves retain all anchors as interpolation context",
        "limits": ["Raw legacy deltas are not absolute CLI pitch.", "This export is read-only analysis data, not an .acep rewrite."],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    profile = sub.add_parser("profile", help="Measure visible content and save a reference profile")
    profile.add_argument("input")
    profile.add_argument("--out", required=True)
    profile.add_argument("--contours-out")
    profile.add_argument("--max-examples", type=int, default=40)
    profile.add_argument("--overwrite", action="store_true")
    region = sub.add_parser("extract", help="Export a selected raw clip-local region for inspection")
    region.add_argument("input")
    region.add_argument("--track", type=int, required=True)
    region.add_argument("--pattern", type=int, required=True)
    region.add_argument("--begin", type=float, required=True)
    region.add_argument("--end", type=float, required=True)
    region.add_argument("--out", required=True)
    region.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    project, source = load_project(args.input)
    if args.command == "profile":
        if args.max_examples < 1:
            parser.error("--max-examples must be positive")
        outputs = [pathlib.Path(args.out)]
        if args.contours_out:
            outputs.append(pathlib.Path(args.contours_out))
        if len({p.resolve() for p in outputs}) != len(outputs):
            parser.error("Profile and contour outputs must differ")
        for output in outputs:
            if output.resolve() == pathlib.Path(args.input).resolve():
                parser.error("Output must not replace the source")
            if output.exists() and not args.overwrite:
                parser.error(f"Output exists: {output}")
        report = analyze(project, source)
        write_json(outputs[0], report, args.overwrite)
        if args.contours_out:
            write_json(outputs[1], build_contour_bank(project, args.max_examples), args.overwrite)
        print(json.dumps({"profile": str(outputs[0].resolve()), "totals": report["totals"]}, ensure_ascii=False))
    else:
        if pathlib.Path(args.out).resolve() == pathlib.Path(args.input).resolve():
            parser.error("Output must not replace the source")
        payload = extract_region(project, args.track, args.pattern, args.begin, args.end)
        write_json(pathlib.Path(args.out), payload, args.overwrite)
        print(json.dumps({"region": str(pathlib.Path(args.out).resolve()), "notes": len(payload["notes"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
