"""Load flight telemetry NDJSON from data_files/ for the Flight live tab."""

from __future__ import annotations

import glob
import json
import os
from typing import Any

import numpy as np

from data_monitoring.event_source import (
    LIGHT_FRAME_MOD,
    LIGHT_SLOT_DEFAULT,
    LIGHT_TICKS_PER_FRAME,
    LIGHT_WINDOW_FRAMES,
    LBW_BASELINE_SCALE,
    LBW_HIT_SCALE,
    LBW_RMS_SCALE,
    Q_PRETRIGGER_SAMPLES,
    Q_SAMPLE_TO_LIGHT_TICK,
    Q_SLOTS_DEFAULT,
    EventRecord,
)

DATA_FILES_DIR = "data_files"
FULL_EVENT_CHARGE_START = 248
Q_SLOTS = Q_SLOTS_DEFAULT
LIGHT_SLOT = LIGHT_SLOT_DEFAULT
CHANNELS_PER_QFEM = 64


def _newest_file(pattern: str) -> str | None:
    paths = glob.glob(pattern)
    if not paths:
        return None
    return max(paths, key=os.path.getmtime)


def resolve_default_flight_paths(
    full_path: str | None = None,
    lbw_path: str | None = None,
    data_dir: str = DATA_FILES_DIR,
) -> tuple[str, str]:
    """Fill empty path inputs from newest files (full event and LBW are independent)."""
    fe = (full_path or "").strip()
    lbw = (lbw_path or "").strip()
    if not fe:
        fe = _newest_file(os.path.join(data_dir, "full_event_*.txt")) or ""
    if not lbw:
        lbw = _newest_file(os.path.join(data_dir, "lb_data_metrics_*.txt")) or ""
    return fe, lbw


def _apply_light_window_remap(
    light_channels: dict[int, list[dict]],
    trig_frame: int,
) -> tuple[int, int]:
    """Same remap as offline ``event_source`` / ``decode_light`` after .dat load."""
    earliest_frame = (trig_frame - 1) % LIGHT_FRAME_MOD
    light_roi_oow = 0
    light_roi_total = 0
    for rois in light_channels.values():
        for roi in rois:
            light_roi_total += 1
            frame_num = int(roi["frame_num"])
            local_frame = (frame_num - earliest_frame) % LIGHT_FRAME_MOD
            if local_frame >= LIGHT_WINDOW_FRAMES:
                light_roi_oow += 1
            roi["start_sample"] = int(roi["start_sample"]) + local_frame * LIGHT_TICKS_PER_FRAME
    return light_roi_oow, light_roi_total


def _parse_ndjson_sessions(path: str) -> list[dict[str, Any]]:
    """Return completed full-event sessions with displayable payload.

    status_code 0 = exact OK; 4 = L_lag used closest (still has waveforms).
    """
    sessions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rtype = rec.get("record")
            if rtype == "fem_header":
                if current is None:
                    current = {"fem_headers": {}, "charge": [], "light": None}
                current["fem_headers"][int(rec["slot"])] = rec
            elif rtype == "charge" and current is not None:
                current["charge"].append(rec)
            elif rtype == "light" and current is not None:
                current["light"] = rec
            elif rtype == "complete":
                if current is not None:
                    current["complete"] = rec
                    if int(rec.get("status_code", 0)) in (0, 4):
                        sessions.append(current)
                current = None
    return sessions


def _build_light_channels_from_rois(rois: list[dict[str, Any]]) -> dict[int, list[dict]]:
    light_channels: dict[int, list[dict]] = {}
    for roi in rois:
        ch = int(roi["channel"])
        if "frame_num" not in roi or "start_sample" not in roi:
            continue
        light_channels.setdefault(ch, []).append(
            {
                "frame_num": int(roi["frame_num"]) & 0x7,
                "start_sample": int(roi["start_sample"]),
                "samples": np.asarray(roi["samples"], dtype=np.float32),
            }
        )
    return light_channels


def _session_to_event_record(session: dict[str, Any]) -> EventRecord:
    complete = session["complete"]
    evt_number = int(complete["evt_number"])
    charge_slots: dict[int, dict[int, np.ndarray]] = {s: {} for s in Q_SLOTS}

    for block in session.get("charge", []):
        slot = int(block["slot"])
        channels = block.get("channels") or {}
        for ch_str, samples in channels.items():
            charge_slots[slot][int(ch_str)] = np.asarray(samples, dtype=np.float32)

    trigger_meta: dict[int, dict] = {}
    for slot, hdr in session.get("fem_headers", {}).items():
        slot = int(slot)
        t_frame = int(hdr["trigger_frame"])
        t_sample = int(hdr["trigger_sample"])
        trigger_meta[slot] = {
            "event_id": int(hdr["event_id"]),
            "frame_id": int(hdr["frame_id"]),
            "frame": t_frame,
            "sample": t_sample,
            "abs": t_frame * 256 + t_sample,
        }

    light_channels: dict[int, list[dict]] = {}
    light_roi_oow = 0
    light_roi_total = 0
    light_block = session.get("light")
    l_trig = trigger_meta.get(LIGHT_SLOT)
    if light_block:
        light_channels = _build_light_channels_from_rois(light_block.get("rois") or [])
        if l_trig is not None and light_channels:
            light_roi_oow, light_roi_total = _apply_light_window_remap(
                light_channels, l_trig["frame"]
            )

    trigger_ticks: dict[int, int] = {}
    for slot in Q_SLOTS:
        chans = charge_slots.get(slot, {})
        nsamp = max((len(v) for v in chans.values()), default=0)
        if nsamp > 0:
            rel = Q_PRETRIGGER_SAMPLES - FULL_EVENT_CHARGE_START
            trigger_ticks[slot] = max(0, min(rel, nsamp - 1))

    if l_trig is not None:
        trigger_ticks[LIGHT_SLOT] = (
            LIGHT_TICKS_PER_FRAME + l_trig["sample"] * Q_SAMPLE_TO_LIGHT_TICK
        )

    q_trig = next((trigger_meta[s] for s in Q_SLOTS if s in trigger_meta), None)
    trigger_abs = q_trig["abs"] if q_trig else None

    return EventRecord(
        evt_number=evt_number,
        charge_slots=charge_slots,
        light_channels=light_channels,
        trigger_ticks=trigger_ticks,
        trigger_abs=trigger_abs,
        trigger_meta=trigger_meta,
        light_roi_oow=light_roi_oow,
        light_roi_total=light_roi_total,
    )


def load_full_event_from_path(path: str) -> tuple[EventRecord | None, str, dict | None]:
    if not os.path.isfile(path):
        return None, f"file not found: {path}", None
    sessions = _parse_ndjson_sessions(path)
    if not sessions:
        return None, f"no complete event in {os.path.basename(path)}", None
    # Multiple completed sessions in one file: use the last successful one.
    record = _session_to_event_record(sessions[-1])
    complete = sessions[-1]["complete"]
    status_code = int(complete.get("status_code", 0))
    status_note = "ok" if status_code == 0 else f"status={status_code}"
    if status_code == 4:
        status_note = "closest L_lag"
    msg = (
        f"full event run={complete['run_number']} file={complete['file_number']} "
        f"evt={complete['evt_number']} l_lag={complete.get('l_lag')} "
        f"({status_note}) ({os.path.basename(path)})"
    )
    meta = {
        "run_number": int(complete["run_number"]),
        "file_number": int(complete["file_number"]),
        "path": path,
        "status_code": status_code,
        "l_lag": complete.get("l_lag"),
    }
    return record, msg, meta


def unscale_lbw_packet(data: dict) -> tuple[tuple, tuple]:
    """Divide packed 0x4001 uint16 fields. Disk JSON stays packed; only display unscales."""

    def _unscale(vals, scale):
        return [v / scale for v in vals]

    lbw_q = (
        _unscale(data.get("charge_baseline", [])[:192], LBW_BASELINE_SCALE),
        _unscale(data.get("charge_rms", [])[:192], LBW_RMS_SCALE),
        _unscale(data.get("charge_avg_num_hits", [])[:192], LBW_HIT_SCALE),
    )
    lbw_l = (
        _unscale(data.get("light_baseline", []), LBW_BASELINE_SCALE),
        _unscale(data.get("light_rms", []), LBW_RMS_SCALE),
        _unscale(data.get("light_avg_num_hits", []), LBW_HIT_SCALE),
    )
    return lbw_q, lbw_l


def load_lbw_from_path(path: str) -> tuple[tuple | None, tuple | None, str]:
    if not os.path.isfile(path):
        return None, None, f"file not found: {path}"

    last_line = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                last_line = line
    if not last_line:
        return None, None, f"empty {os.path.basename(path)}"

    try:
        data = json.loads(last_line)
    except json.JSONDecodeError as exc:
        return None, None, f"LBW parse error: {exc}"

    lbw_q, lbw_l = unscale_lbw_packet(data)
    evt = data.get("evt_number", "?")
    msg = f"LBW evt={evt} ({os.path.basename(path)})"
    return lbw_q, lbw_l, msg


def load_latest_full_event(data_dir: str = DATA_FILES_DIR) -> tuple[EventRecord | None, str, dict | None]:
    path = _newest_file(os.path.join(data_dir, "full_event_*.txt"))
    if path is None:
        return None, "no full_event_*.txt in data_files/", None
    return load_full_event_from_path(path)


def load_latest_lbw(data_dir: str = DATA_FILES_DIR) -> tuple[tuple | None, tuple | None, str]:
    path = _newest_file(os.path.join(data_dir, "lb_data_metrics_*.txt"))
    if path is None:
        return None, None, "no lb_data_metrics_*.txt in data_files/"
    return load_lbw_from_path(path)
