"""Build a speech-reference index and evaluate short-gap interpolation offline."""

from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from ace_project import finite, load_project, stats, visible_bounds, write_json


class TempoMap:
    def __init__(self, tempos: list[dict[str, Any]]) -> None:
        events = {}
        for event in tempos:
            if event.get("bend", 0) or event.get("isLerp", False):
                raise ValueError("This analysis requires step tempos; use native timing for ramps.")
            tick, bpm = event.get("position", 0), event.get("bpm")
            if not finite(tick) or not finite(bpm) or bpm <= 0:
                raise ValueError("Invalid tempo event")
            events[float(tick)] = float(bpm)
        if not events:
            raise ValueError("No tempo events; do not invent a tempo")
        ordered = sorted(events.items())
        self.ticks = np.asarray([x for x, _ in ordered], dtype=float)
        self.rates = np.asarray([60.0 / (480 * bpm) for _, bpm in ordered], dtype=float)
        self.times = np.zeros(len(ordered), dtype=float)
        self.times[0] = self.ticks[0] * self.rates[0]
        for i in range(1, len(ordered)):
            self.times[i] = self.times[i - 1] + (self.ticks[i] - self.ticks[i - 1]) * self.rates[i - 1]

    def seconds(self, ticks: Any) -> Any:
        values = np.asarray(ticks, dtype=float)
        indices = np.clip(np.searchsorted(self.ticks, values, side="right") - 1, 0, len(self.ticks) - 1)
        result = self.times[indices] + (values - self.ticks[indices]) * self.rates[indices]
        return float(result) if result.ndim == 0 else result


def lane_window(pattern: dict[str, Any], name: str, begin: int, end: int) -> np.ndarray:
    values = np.full(max(0, end - begin), np.nan)
    used = np.zeros(len(values), dtype=bool)
    for curve in pattern.get("parameters", {}).get(name, []):
        if curve.get("type", "data") != "data":
            raise ValueError(f"{name}: deep numeric analysis currently supports legacy data curves only")
        offset = curve.get("offset", 0)
        if not isinstance(offset, int):
            raise ValueError("Dense curve offsets must be integer ticks")
        source = curve.get("values", [])
        left, right = max(begin, offset), min(end, offset + len(source))
        if right <= left:
            continue
        if used[left - begin : right - begin].any():
            raise ValueError(f"{name}: overlapping raw curves need native precedence resolution")
        values[left - begin : right - begin] = np.asarray(source[left - offset : right - offset], dtype=float)
        used[left - begin : right - begin] = True
    return values


def normalized_shape(values: np.ndarray, count: int = 33) -> list[float]:
    return [
        round(float(x), 6)
        for x in np.interp(np.linspace(0, len(values) - 1, count), np.arange(len(values)), values)
    ]


def classify_shape(values: np.ndarray) -> str:
    positions = np.linspace(0, 1, len(values))
    residual = values - (values[0] + positions * (values[-1] - values[0]))
    high, low = float(residual.max()), float(residual.min())
    if high - low < 0.35:
        return "rise" if values[-1] - values[0] > 0.7 else "fall" if values[-1] - values[0] < -0.7 else "level"
    if high > 1.5 * abs(low):
        return "peak"
    if abs(low) > 1.5 * high:
        return "valley"
    return "turns"


def note_context(notes: list[dict[str, Any]], order: list[int], rank: int) -> dict[str, Any]:
    note = notes[order[rank]]

    def describe(index: int) -> dict[str, Any]:
        item = notes[index]
        return {
            "note_index": index,
            "note_uuid": item.get("uuid"),
            "lyric": item.get("lyric"),
            "language": item.get("language"),
            "syllable": item.get("syllable"),
            "frozen_default_syllable": item.get("freezedDefaultSyllable"),
            "local_range": [item["pos"], item["pos"] + item["dur"]],
            "note_pitch_scaffold": item["pitch"],
        }

    head_rank = rank
    while notes[order[head_rank]].get("lyric") == "-" and head_rank:
        current, previous = notes[order[head_rank]], notes[order[head_rank - 1]]
        if previous["pos"] + previous["dur"] != current["pos"]:
            break
        head_rank -= 1
    head = notes[order[head_rank]]
    resolved = head.get("lyric") != "-"
    previous = notes[order[rank - 1]] if rank else None
    following = notes[order[rank + 1]] if rank + 1 < len(order) else None
    return {
        "is_continuation_marker": note.get("lyric") == "-",
        "pronunciation_head": describe(order[head_rank]) if resolved else None,
        "effective_syllable_label": (head.get("syllable") or head.get("freezedDefaultSyllable")) if resolved else None,
        "continuation_head_resolved": resolved,
        "previous_note": describe(order[rank - 1]) if previous else None,
        "next_note": describe(order[rank + 1]) if following else None,
        "previous_gap_ticks": note["pos"] - previous["pos"] - previous["dur"] if previous else None,
        "next_gap_ticks": following["pos"] - note["pos"] - note["dur"] if following else None,
    }


def benchmark_starts(sample_count: int, width: int) -> list[int]:
    remaining = sample_count - width - 1
    if remaining < 0:
        return []
    return sorted({round(remaining * fraction) for fraction in (0.2, 0.5, 0.8)})


def build_index(project: dict[str, Any], source: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    tempo = TempoMap(project.get("tempos", []))
    records = []
    bank = []
    seen = {}
    totals = collections.Counter()
    raw_types = collections.Counter()
    syllables = collections.Counter()
    bridge_errors: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
    per_track: dict[int, dict[str, list[float]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for ti, track in enumerate(project["tracks"]):
        for pi, pattern in enumerate(track.get("patterns", [])):
            if pattern.get("timeUnit", "tick") != "tick":
                totals["non_tick_patterns_skipped"] += 1
                continue
            raw_types["patterns_with_notes" if pattern.get("notes") else "patterns_without_notes"] += 1
            vb, ve = visible_bounds(pattern)
            position = float(pattern.get("pos", 0))
            begin, end = math.ceil(vb), math.ceil(ve)
            pitch = lane_window(pattern, "pitchDelta", begin, end)
            energy = lane_window(pattern, "energy", begin, end)
            vuv = lane_window(pattern, "vuv", begin, end)
            notes = pattern.get("notes", [])
            order = sorted(range(len(notes)), key=lambda i: notes[i]["pos"])
            note_rank = {ni: rank for rank, ni in enumerate(order)}
            for ni, note in enumerate(notes):
                raw_begin, raw_end = note["pos"], note["pos"] + note["dur"]
                left, right = max(vb, raw_begin), min(ve, raw_end)
                if left >= right:
                    continue
                lo, hi = math.ceil(left), math.ceil(right)
                ticks = np.arange(lo, hi)
                raw = pitch[lo - begin : hi - begin]
                energies = energy[lo - begin : hi - begin]
                vu = vuv[lo - begin : hi - begin]
                actual_ms = (tempo.seconds(position + right) - tempo.seconds(position + left)) * 1000
                all_ms = (tempo.seconds(position + raw_end) - tempo.seconds(position + raw_begin)) * 1000
                times = tempo.seconds(position + ticks)
                core_mask = (
                    (times >= tempo.seconds(position + raw_begin) + 0.060)
                    & (times <= tempo.seconds(position + raw_end) - 0.060)
                )
                approx = raw + note["pitch"]
                cancellation = np.isfinite(approx) & (np.abs(approx) <= 1e-5)
                numeric_core = raw[core_mask]
                approx_core = approx[core_mask]
                core_ticks = ticks[core_mask]
                core_overlaps = [
                    other_index
                    for other_index, other in enumerate(notes)
                    if len(core_ticks) and other_index != ni
                    and other["pos"] <= core_ticks[-1]
                    and other["pos"] + other["dur"] > core_ticks[0]
                ]
                plausible = np.isfinite(approx_core) & (approx_core >= 20) & (approx_core <= 100)
                rank = note_rank[ni]
                prev_note = notes[order[rank - 1]] if rank else None
                next_note = notes[order[rank + 1]] if rank + 1 < len(order) else None
                record = {
                    "occurrence_id": f"T{ti}/P{pi}/N{ni}",
                    "track_index": ti,
                    "pattern_index": pi,
                    "note_index": ni,
                    "pattern_uuid": pattern.get("uuid"),
                    "note_uuid": note.get("uuid"),
                    "lyric": note.get("lyric"),
                    "language": note.get("language"),
                    "syllable": note.get("syllable"),
                    "frozen_default_syllable": note.get("freezedDefaultSyllable"),
                    **note_context(notes, order, rank),
                    "local_visible_range": [left, right],
                    "global_seconds": [round(tempo.seconds(position + left), 6), round(tempo.seconds(position + right), 6)],
                    "note_pitch_scaffold": note["pitch"],
                    "visible_duration_ms": round(actual_ms, 6),
                    "full_note_duration_ms": round(all_ms, 6),
                    "clipped_head_or_tail": [left != raw_begin, right != raw_end],
                    "head_consonants_seconds": note.get("headConsonants", []) if project.get("version", 0) >= 7 else None,
                    "brLen_raw": note.get("brLen", 0),
                    "previous_scaffold_pitch": prev_note.get("pitch") if prev_note else None,
                    "next_scaffold_pitch": next_note.get("pitch") if next_note else None,
                    "drawn_pitch_fraction": round(float(np.isfinite(raw).mean()), 6) if len(raw) else 0.0,
                    "scaffold_cancellation_samples": int(cancellation.sum()),
                    "vuv_explicit_0_samples": int(np.sum(vu == 0)),
                    "vuv_explicit_1_samples": int(np.sum(vu == 1)),
                    "energy_drawn_fraction": round(float(np.isfinite(energies).mean()), 6) if len(energies) else 0.0,
                    "energy_drawn_values": stats(energies[np.isfinite(energies)].tolist()),
                    "stable_core_sample_count": len(numeric_core),
                    "stable_core_local_inclusive_range": [int(core_ticks[0]), int(core_ticks[-1])] if len(core_ticks) else None,
                    "core_endpoint_span_ms": round(float((tempo.seconds(position + core_ticks[-1]) - tempo.seconds(position + core_ticks[0])) * 1000), 6) if len(core_ticks) else None,
                    "core_overlapping_note_indices": core_overlaps,
                    "plausible_core_sample_count": int(plausible.sum()),
                    "core_approx_midi": stats(approx_core[plausible].tolist()),
                }
                records.append(record)
                per_track[ti]["visible_duration_ms"].append(actual_ms)
                if len(approx_core) >= 10 and float(plausible.mean()) >= 0.9 and not core_overlaps:
                    per_track[ti]["core_note_median_midi"].append(float(np.median(approx_core[plausible])))
                totals["visible_note_occurrences"] += 1
                if not len(numeric_core) >= 10 or not plausible.all():
                    totals["excluded_from_contour_candidate_bank"] += 1
                    continue
                if core_overlaps:
                    totals["excluded_due_to_overlapping_note_in_core"] += 1
                    continue
                if np.max(np.abs(np.diff(numeric_core))) > 2:
                    totals["excluded_due_to_core_step_gt2_semitones"] += 1
                    continue
                core_vuv = vu[core_mask]
                if np.any(core_vuv == 0):
                    totals["excluded_due_to_explicit_raw_vuv0_in_core"] += 1
                    continue
                if not record["continuation_head_resolved"]:
                    totals["excluded_due_to_unresolved_continuation_head"] += 1
                    continue
                # The key compares actual numeric content as well as note identity.
                key = (
                    note["pos"], note["dur"], note["pitch"], note.get("lyric"),
                    (record["pronunciation_head"] or {}).get("language"),
                    int(core_ticks[0]), int(core_ticks[-1]),
                    record["effective_syllable_label"], tuple(float(v) for v in numeric_core),
                    tuple(None if not np.isfinite(v) else float(v) for v in core_vuv),
                )
                if key in seen:
                    record["contour_source_id"] = seen[key]
                    totals["duplicate_core_content_occurrences"] += 1
                    continue
                identifier = f"core-{len(bank):04d}"
                seen[key] = identifier
                record["contour_source_id"] = identifier
                shape = classify_shape(numeric_core)
                syllables[record["effective_syllable_label"] or record["pronunciation_head"].get("lyric") or ""] += 1
                raw_span = float(np.ptp(numeric_core))
                flags = []
                if not np.isfinite(core_vuv).any():
                    flags.append("raw_vuv_absent")
                if raw_span > 12:
                    flags.append("raw_span_above12_semitones")
                bank.append({
                    "id": identifier,
                    "occurrence_id": record["occurrence_id"],
                    "track_index": ti,
                    "pattern_index": pi,
                    "note_index": ni,
                    "pattern_uuid": pattern.get("uuid"),
                    "note_uuid": note.get("uuid"),
                    "lyric": note.get("lyric"),
                    "language": note.get("language"),
                    "syllable": note.get("syllable"),
                    "frozen_default_syllable": note.get("freezedDefaultSyllable"),
                    **note_context(notes, order, rank),
                    "local_inclusive_core_range": [int(core_ticks[0]), int(core_ticks[-1])],
                    "full_note_duration_ms": round(all_ms, 6),
                    "core_endpoint_span_ms": record["core_endpoint_span_ms"],
                    "note_pitch_scaffold": note["pitch"],
                    "shape": shape,
                    "core_raw_delta_span_semitones": round(raw_span, 7),
                    "review_flags": flags,
                    "delta_samples": [round(float(x), 7) for x in numeric_core],
                    "normalized_delta_shape_33": normalized_shape(numeric_core),
                    "vuv_core_values": sorted(set(float(x) for x in core_vuv if np.isfinite(x))),
                    "eligibility": "numeric candidate with no explicit raw VUV0 in core; absent VUV is unknown; verify native voicing and pronunciation before donor use",
                })
                for width in (4, 8, 16, 24, 48, 96):
                    if len(numeric_core) < width + 1:
                        continue
                    for start in benchmark_starts(len(numeric_core), width):
                        segment = numeric_core[start : start + width + 1]
                        expected = np.linspace(segment[0], segment[-1], width + 1)
                        error = np.abs(segment - expected)
                        local_start = int(core_ticks[start])
                        bridge_errors[width].append({
                            "source_id": identifier,
                            "occurrence_id": record["occurrence_id"],
                            "local_range": [local_start, local_start + width],
                            "duration_ms": (tempo.seconds(position + local_start + width) - tempo.seconds(position + local_start)) * 1000,
                            "max_error_semitones": float(error.max()),
                            "rmse_semitones": float(np.sqrt(np.mean(error**2))),
                        })
    summary = {
        "schema": "ace-speech-deep-analysis-v2",
        "source": source,
        "method": {
            "tempo": "step tempos integrated, last event at a duplicated position wins",
            "stable_core": "at least 60ms inside full note start/end, intersect visible trim",
            "candidate_selection": "at least10coreticks, all finite note.pitch+delta in[20,100], maxrawstep<=2semitones/tick; no explicit rawVUV0; continuation head resolved",
            "selection_limits": "Numeric filters are for this diagnostic index, never corruption or valid pitch boundaries.",
            "review_flags": "Raw span above12 semitones is only a sensitivity-analysis stratum, not a corruption threshold; raw_vuv_absent means unknown voicing.",
            "approx_midi": "note.pitch+rawdelta within stable note interiors; not native final/combined",
            "voicing_limits": "Legacy VUV0 exclusion is precautionary from reference timing evidence, not verified native polarity; missing VUV does not imply voiced.",
            "bridge_test": "unique rounded starts at20/50/80percent of each eligible core; endpoint distance=width ticks, width+1 samples; compare endpoint chord to original rawdelta",
            "rmse_scope": "full window including the two unchanged endpoints; not masked-interior-only RMSE",
            "shape_time_axis": "normalized by ticks, not elapsed time; no visible reference note crosses an effective tempo change",
            "overlap_limit": "core windows intersecting other notes are excluded; other numeric candidates are still not verified donors",
            "test_limits": "No listening groundtruth, no model render, no actual ACE write; evaluated chord is not guaranteed identical to native spline boundary tangents.",
        },
        "totals": {**totals, **raw_types, "unique_numeric_core_candidates": len(bank)},
        "tracks": [
            {"index": ti, **{name: stats(values) for name, values in measurements.items()}}
            for ti, measurements in per_track.items()
        ],
        "candidate_shapes": dict(collections.Counter(entry["shape"] for entry in bank)),
        "candidate_review_flags": dict(collections.Counter(flag for entry in bank for flag in entry["review_flags"])),
        "most_common_candidate_syllables": syllables.most_common(20),
        "bridge_holdout": {},
    }
    for width, windows in bridge_errors.items():
        errors = [entry["max_error_semitones"] for entry in windows]
        summary["bridge_holdout"][str(width)] = {
            "window_count": len(windows),
            "duration_ms": stats([entry["duration_ms"] for entry in windows]),
            "max_error_semitones": stats(errors),
            "rmse_semitones": stats([entry["rmse_semitones"] for entry in windows]),
            "fraction_above_0_25_semitones": sum(x > 0.25 for x in errors) / len(errors),
            "fraction_above_0_5_semitones": sum(x > 0.5 for x in errors) / len(errors),
            "worst_examples": sorted(windows, key=lambda entry: entry["max_error_semitones"], reverse=True)[:3],
        }
    index = {
        "schema": "ace-speech-note-index-v2",
        "source": source,
        "units": "clip-local ticks / actual global seconds / raw relative semitones",
        "limits": summary["method"],
        "notes": records,
        "numeric_core_candidates": bank,
    }
    return summary, index


def render_plot(summary: dict[str, Any], index: dict[str, Any], output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if font_path.exists():
        font_manager.fontManager.addfont(str(font_path))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font_path)).get_name()
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    figure, axes = plt.subplots(2, 2, figsize=(13, 8.5), layout="constrained")
    colors = ["#176fa7", "#329871", "#d45f64", "#b29925", "#7564a5"]
    notes = index["notes"]
    ordinary = [entry["visible_duration_ms"] for entry in notes if not entry["is_continuation_marker"]]
    continuation = [entry["visible_duration_ms"] for entry in notes if entry["is_continuation_marker"]]
    axes[0, 0].hist(
        [ordinary, continuation], bins=np.arange(0, 461, 15), stacked=True,
        label=["普通标注音符", "延续标记 -"], color=[colors[0], colors[2]], edgecolor="white",
    )
    axes[0, 0].set(title="实际时长分布：音符不等于字", xlabel="时长 / ms", ylabel="可见音符数")
    axes[0, 0].legend(frameon=False)
    tracks = [entry["index"] for entry in summary["tracks"]]
    values = [
        [n["core_approx_midi"]["median"] for n in notes
         if n["track_index"] == ti and n["stable_core_sample_count"] >= 10
         and n["plausible_core_sample_count"] >= 0.9 * n["stable_core_sample_count"]
         and not n["core_overlapping_note_indices"]]
        for ti in tracks
    ]
    box = axes[0, 1].boxplot(values, tick_labels=[f"T{ti}" for ti in tracks], showfliers=False, patch_artist=True)
    for patch, color in zip(box["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.65)
    axes[0, 1].set(title="稳定内部的近似音高：按轨道比较", xlabel="轨道（0 基）", ylabel="支架 + 偏移 / MIDI")
    wanted = [
        ("T0/P0/N", 17139, "su"),
        ("T0/P0/N", 17821, "zao"),
        ("T1/P2/N", 71216, "de"),
        ("T4/P1/N", 21613, "geng"),
    ]
    chosen = []
    for prefix, start, label in wanted:
        matches = [
            item for item in index["numeric_core_candidates"]
            if item["occurrence_id"].startswith(prefix) and item["lyric"] == label
            and abs((item["local_inclusive_core_range"][0] - 58) - start) < 4
        ]
        if matches:
            chosen.append(matches[0])
    if len(chosen) < 3:
        chosen = []
        for shape in ("peak", "valley", "rise", "fall"):
            matches = [item for item in index["numeric_core_candidates"] if item["shape"] == shape]
            if matches:
                chosen.append(max(matches, key=lambda item: max(item["delta_samples"]) - min(item["delta_samples"])))
    for i, entry in enumerate(chosen[:4]):
        curve = np.asarray(entry["delta_samples"])
        axes[1, 0].plot(np.linspace(0, 1, len(curve)), curve - curve[0], color=colors[i],
                        label=f'{entry["lyric"]} · {entry["occurrence_id"]}', linewidth=1.8)
    axes[1, 0].set(title="真实内部轮廓：相对起点的变化", xlabel="稳定内部的归一化时间", ylabel="相对偏移变化 / 半音")
    axes[1, 0].legend(frameon=False, fontsize=8)
    holds = sorted(summary["bridge_holdout"].items(), key=lambda item: int(item[0]))
    x = [entry["duration_ms"]["median"] for _, entry in holds]
    median = [entry["max_error_semitones"]["median"] for _, entry in holds]
    p95 = [entry["max_error_semitones"]["p95"] for _, entry in holds]
    axes[1, 1].plot(x, median, "o-", label="典型最大误差", color=colors[1])
    axes[1, 1].plot(x, p95, "s-", label="最大误差 P95", color=colors[2])
    axes[1, 1].axhline(0.5, color="#6e6e6e", linestyle=":", linewidth=1)
    axes[1, 1].set(title="遮挡已有曲线后，直线衔接会丢失多少细节", xlabel="被遮挡区间 / ms", ylabel="对原始曲线的误差 / 半音")
    axes[1, 1].legend(frameon=False)
    for axis in axes.flat:
        axis.grid(axis="y", alpha=0.15)
    figure.suptitle("《第二章》说话调教：第二轮数据研究", fontsize=17)
    figure.supxlabel("仅为离线数据分析；近似音高与数值候选不等于原生最终音高或试听结论。", fontsize=10, color="#555555")
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=160, facecolor="white")
    plt.close(figure)


def search_index(index_path: Path, query: str, track: int | None, limit: int) -> list[dict[str, Any]]:
    index = json.loads(index_path.read_text(encoding="utf-8-sig"))
    query = query.lower().strip()
    matches = []
    bank = {entry["id"]: entry for entry in index["numeric_core_candidates"]}
    for record in index["notes"]:
        if "contour_source_id" not in record:
            continue
        entry = bank[record["contour_source_id"]]
        if track is not None and record["track_index"] != track:
            continue
        search_fields = {
            "lyric": str(record.get("lyric") or "").lower(),
            "effective_phonemes": str(record.get("effective_syllable_label") or "").lower(),
            "head_lyric": str((record.get("pronunciation_head") or {}).get("lyric") or "").lower(),
        }
        matched_fields = [name for name, value in search_fields.items() if query == value]
        if query and not matched_fields:
            continue
        candidate = {
            key: value for key, value in entry.items()
            if key not in ("delta_samples", "normalized_delta_shape_33")
        }
        for key in (
            "occurrence_id", "track_index", "pattern_index", "note_index", "pattern_uuid", "note_uuid",
            "lyric", "language", "syllable", "frozen_default_syllable", "is_continuation_marker",
            "pronunciation_head", "effective_syllable_label", "continuation_head_resolved",
            "previous_note", "next_note", "previous_gap_ticks", "next_gap_ticks",
        ):
            candidate[key] = record[key]
        candidate["core_array_source_occurrence"] = entry["occurrence_id"]
        candidate["local_inclusive_core_range"] = record["stable_core_local_inclusive_range"]
        candidate["full_note_duration_ms"] = record["full_note_duration_ms"]
        candidate["core_endpoint_span_ms"] = record["core_endpoint_span_ms"]
        candidate["matched_fields"] = matched_fields if query else ["all"]
        matches.append(candidate)
    return matches[:limit]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    analyze = commands.add_parser("analyze")
    analyze.add_argument("input", type=Path)
    analyze.add_argument("--summary", type=Path, required=True)
    analyze.add_argument("--index", type=Path, required=True)
    analyze.add_argument("--plot", type=Path)
    analyze.add_argument("--overwrite", action="store_true")
    search = commands.add_parser("search")
    search.add_argument("--index", type=Path, required=True)
    search.add_argument("--syllable", default="")
    search.add_argument("--track", type=int)
    search.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    if args.command == "search":
        if args.limit <= 0:
            parser.error("--limit must be positive")
        print(json.dumps(search_index(args.index, args.syllable, args.track, args.limit), ensure_ascii=False, indent=2))
        return
    outputs = [args.summary, args.index] + ([args.plot] if args.plot else [])
    resolved = [path.resolve() for path in outputs]
    if len(set(resolved)) != len(resolved) or args.input.resolve() in resolved:
        parser.error("Outputs must differ and must not replace the input project")
    for output in outputs:
        if output.exists() and not args.overwrite:
            parser.error(f"Output exists: {output}")
    project, source = load_project(args.input)
    summary, index = build_index(project, source)
    write_json(args.summary, summary, args.overwrite)
    write_json(args.index, index, args.overwrite)
    if args.plot:
        render_plot(summary, index, args.plot)
    print(json.dumps({"summary": str(args.summary.resolve()), "index": str(args.index.resolve()), "totals": summary["totals"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
