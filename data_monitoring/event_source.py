"""Event loaders: EventRecord + hexdump .txt decoder (charge_light_decoder.h layout)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable, Protocol

import numpy as np

Q_SLOTS_DEFAULT = [13, 14, 15]
LIGHT_SLOT_DEFAULT = 16
SAMPLES_PER_FRAME = 256

_EVENT_OFFSET_CACHE: dict[str, list[int]] = {}
_EVENT_CACHE: dict[tuple, EventRecord] = {}


@dataclass
class EventRecord:
    evt_number: int
    charge_slots: dict[int, dict[int, np.ndarray]] = field(default_factory=dict)
    light_channels: dict[int, list[dict]] = field(default_factory=dict)
    # Per-slot trigger x for heatmap vline (Q: sample in readout, L: 2 MHz tick).
    trigger_ticks: dict[int, int] = field(default_factory=dict)
    # Event-level 2 MHz trigger tick (shared across FEMs in one event).
    trigger_abs: int | None = None


class EventSource(Protocol):
    name: str

    def load(self, path: str, evt_idx: int) -> EventRecord: ...


_REGISTRY: dict[str, EventSource] = {}


def register_source(source: EventSource) -> None:
    _REGISTRY[source.name] = source


def get_source(name: str) -> EventSource:
    if name not in _REGISTRY:
        raise KeyError(f"unknown source '{name}'; available: {list(_REGISTRY)}")
    return _REGISTRY[name]


def load_event(path: str, evt_idx: int, source: str = "hexdump") -> EventRecord:
    return get_source(source).load(path, evt_idx)


# --- hexdump decoder ---


def lo16(w: int) -> int:
    return w & 0xFFFF


def hi16(w: int) -> int:
    return (w >> 16) & 0xFFFF


def parse_hex(line: str) -> int | None:
    s = line.strip()
    if not s:
        return None
    try:
        return int(s, 16)
    except ValueError:
        return None


def iter_16bit(payload32: list[int]) -> Iterable[int]:
    for w in payload32:
        yield lo16(w)
        yield hi16(w)


def parse_trig_from_header6(w: int) -> tuple[int, int, int]:
    """Return (frame, sample, frame*256+sample) from FEMHeader6."""
    lo, hi = lo16(w), hi16(w)
    sample_upper = lo & 0xF
    trig_frame = (lo >> 4) & 0xF
    sample_lower = hi & 0xFF
    sample = ((sample_upper << 8) & 0xF00) | sample_lower
    tick = trig_frame * SAMPLES_PER_FRAME + sample
    return trig_frame, sample, tick


def q_trigger_in_readout(trig_frame: int, trig_sample: int, nsamp: int) -> int:
    """Map FEMHeader6 frame+sample to x within the Q readout window."""
    if nsamp <= 0:
        return trig_sample
    n_frames = max(1, (nsamp + SAMPLES_PER_FRAME - 1) // SAMPLES_PER_FRAME)
    x = (trig_frame % n_frames) * SAMPLES_PER_FRAME + trig_sample
    return min(x, nsamp - 1)


def build_event_offset_index(txt_path: str) -> list[int]:
    if txt_path in _EVENT_OFFSET_CACHE:
        return _EVENT_OFFSET_CACHE[txt_path]
    offsets: list[int] = []
    pos = 0
    with open(txt_path, "rb") as f:
        for line in f:
            if line[:8] == b"ffffffff":
                offsets.append(pos)
            pos += len(line)
    _EVENT_OFFSET_CACHE[txt_path] = offsets
    print(f"indexed {len(offsets)} events in {os.path.basename(txt_path)}")
    return offsets


def get_event_payloads(
    txt_path: str, evt_idx: int, all_slots: list[int]
) -> tuple[dict[int, list[int]], dict[int, dict]]:
    offsets = build_event_offset_index(txt_path)
    if not 0 <= evt_idx < len(offsets):
        raise IndexError(f"evt {evt_idx} out of range (have {len(offsets)} events)")

    out: dict[int, list[int]] = {}
    trigger_meta: dict[int, dict] = {}
    hdr_idx = 6
    current_slot: int | None = None
    target_set = set(all_slots)
    started = False

    with open(txt_path) as f:
        f.seek(offsets[evt_idx])
        for line in f:
            w = parse_hex(line)
            if w is None:
                continue
            if w == 0xFFFFFFFF:
                if started:
                    break
                started = True
                continue
            if w == 0xE0000000:
                break
            lo, hi = lo16(w), hi16(w)
            is_hdr = (lo & 0xF000) == 0xF000 and (hi & 0xF000) == 0xF000
            if is_hdr:
                hdr_idx = 1 if hdr_idx >= 6 else hdr_idx + 1
                if hdr_idx == 1:
                    current_slot = hi & 0x1F
                    if current_slot in target_set and current_slot not in out:
                        out[current_slot] = []
                elif hdr_idx == 6 and current_slot in target_set:
                    fr, smp, tick_abs = parse_trig_from_header6(w)
                    trigger_meta[current_slot] = {
                        "frame": fr,
                        "sample": smp,
                        "abs": tick_abs,
                    }
                continue
            if current_slot in out:
                out[current_slot].append(w)

    return out, trigger_meta


def decode_charge(payload32: list[int]) -> dict[int, np.ndarray]:
    channels: dict[int, list[int]] = {}
    current_ch: int | None = None
    cur_buf: list[int] = []
    for w in iter_16bit(payload32):
        if w == 0:
            continue
        top4 = (w >> 12) & 0xF
        if top4 == 0x4:
            current_ch = w & 0x3F
            cur_buf = []
        elif top4 == 0x5:
            if current_ch is not None:
                channels[current_ch] = np.asarray(cur_buf, dtype=np.int32)
            current_ch = None
            cur_buf = []
        elif current_ch is not None:
            cur_buf.append(w & 0xFFF)
    return {ch: arr for ch, arr in channels.items()}


def decode_light(payload32: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    in_fem_channel_block = False
    state = "idle"
    roi: dict | None = None
    light_hdr_idx = 0

    def fresh_roi(w: int) -> dict:
        return {
            "channel": w & 0x3F,
            "id": (w >> 9) & 0x7,
            "sample_num_upper": 0,
            "frame_num": 0,
            "sample_num_lower": 0,
            "start_sample": 0,
            "samples": [],
        }

    for w in iter_16bit(payload32):
        if w == 0:
            continue
        top2 = (w >> 14) & 0x3
        hdr_tag = (w >> 12) & 0x3

        if top2 == 0b01:
            in_fem_channel_block = True
            state = "between_rois"
            roi = None
            light_hdr_idx = 0
            continue
        if top2 == 0b11:
            in_fem_channel_block = False
            state = "idle"
            roi = None
            continue
        if not in_fem_channel_block or top2 != 0b10:
            continue

        if state == "between_rois":
            if hdr_tag == 0b01:
                roi = fresh_roi(w)
                light_hdr_idx = 1
                state = "in_roi"
            continue

        if light_hdr_idx == 1:
            roi["sample_num_upper"] = w & 0x1F
            roi["frame_num"] = (w >> 5) & 0x7
            light_hdr_idx = 2
            continue
        if light_hdr_idx == 2:
            roi["sample_num_lower"] = w & 0xFFF
            roi["start_sample"] = (roi["sample_num_upper"] << 12) | roi["sample_num_lower"]
            light_hdr_idx = 3
            continue
        if hdr_tag == 0b11:
            roi["samples"] = np.asarray(roi["samples"], dtype=np.int32)
            out.setdefault(roi["channel"], []).append(roi)
            roi = None
            light_hdr_idx = 0
            state = "between_rois"
            continue
        if hdr_tag == 0b01:
            roi["samples"] = np.asarray(roi["samples"], dtype=np.int32)
            out.setdefault(roi["channel"], []).append(roi)
            roi = fresh_roi(w)
            light_hdr_idx = 1
            continue
        roi["samples"].append(w & 0xFFF)
    return out


class HexdumpEventSource:
    name = "hexdump"

    def __init__(
        self,
        q_slots: list[int] | None = None,
        light_slot: int = LIGHT_SLOT_DEFAULT,
    ):
        self.q_slots = q_slots or Q_SLOTS_DEFAULT
        self.light_slot = light_slot

    def load(self, path: str, evt_idx: int) -> EventRecord:
        key = (path, evt_idx, tuple(self.q_slots), self.light_slot)
        if key in _EVENT_CACHE:
            return _EVENT_CACHE[key]

        all_slots = self.q_slots + [self.light_slot]
        payloads, trigger_meta = get_event_payloads(path, evt_idx, all_slots)
        charge_slots = {
            qs: decode_charge(payloads.get(qs, [])) for qs in self.q_slots
        }
        light_channels = decode_light(payloads.get(self.light_slot, []))

        event_trig = next(iter(trigger_meta.values()), None)
        trigger_abs = event_trig["abs"] if event_trig else None
        trig_frame = event_trig["frame"] if event_trig else 0
        trig_sample = event_trig["sample"] if event_trig else 0

        trigger_ticks: dict[int, int] = {}
        for slot in self.q_slots:
            chans = charge_slots.get(slot, {})
            nsamp = max((len(v) for v in chans.values()), default=0)
            if event_trig is not None:
                trigger_ticks[slot] = q_trigger_in_readout(trig_frame, trig_sample, nsamp)
        if self.light_slot in trigger_meta:
            trigger_ticks[self.light_slot] = trigger_meta[self.light_slot]["abs"]

        record = EventRecord(
            evt_number=evt_idx,
            charge_slots=charge_slots,
            light_channels=light_channels,
            trigger_ticks=trigger_ticks,
            trigger_abs=trigger_abs,
        )
        _EVENT_CACHE[key] = record
        return record


register_source(HexdumpEventSource())
