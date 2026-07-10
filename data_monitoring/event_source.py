"""Event loaders: EventRecord + hexdump .txt / binary .dat decoder."""

from __future__ import annotations

import mmap
import os
import struct
from dataclasses import dataclass, field
from typing import Iterable, Protocol

import numpy as np

Q_SLOTS_DEFAULT = [13, 14, 15]
LIGHT_SLOT_DEFAULT = 16
SAMPLES_PER_FRAME = 256
# Q-FEM readout is trigger-aligned: firmware records a fixed number of
# pre-trigger samples (256 pre + ~507 post; observed window = 763 samples), so
# the trigger lands at this column regardless of absolute frame/sample.
# NOTE: 256 is the documented pre-trigger count, still to be confirmed against
# firmware (the readout could instead be frame-aligned -> 256 + trig_sample).
Q_PRETRIGGER_SAMPLES = 256

# L-FEM (light) timing. SiPM digitizes at 64 MHz, 1 frame = 128 us = 8192 ticks.
# frame_num is a 3-bit (mod-8) master frame counter; the light readout window is
# the 4 frames [trig_frame-1 .. trig_frame+2]. The FEMHeader6 trigger is stored
# at 2 MHz (256 samples/frame), so 1 charge sample = 32 light ticks.
LIGHT_FRAME_MOD = 8
LIGHT_TICKS_PER_FRAME = 8192
Q_SAMPLE_TO_LIGHT_TICK = LIGHT_TICKS_PER_FRAME // SAMPLES_PER_FRAME  # 2 MHz -> 64 MHz
# Light readout window = 4 frames [trig_frame-1 .. trig_frame+2]. A ROI whose
# frame_num lands outside this window cannot belong to this trigger (stale /
# duplicated light data), so it is flagged for the UI.
LIGHT_WINDOW_FRAMES = 4

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
    # Per-slot decoded FEMHeader6 trigger: {slot: {"frame","sample","abs"}}.
    # Kept so the UI can flag Q/L (or Q/Q) header desync.
    trigger_meta: dict[int, dict] = field(default_factory=dict)
    # L-FEM ROIs whose frame_num falls outside the 4-frame readout window of the
    # (lag-adjusted) L header vs total ROIs -> flags stale/duplicate light data.
    light_roi_oow: int = 0
    light_roi_total: int = 0


class EventSource(Protocol):
    name: str

    def load(
        self,
        path: str,
        evt_idx: int,
        q_lag: tuple[int, int] = (0, 0),
        l_lag: tuple[int, int] = (0, 0),
    ) -> EventRecord: ...


_REGISTRY: dict[str, EventSource] = {}


def register_source(source: EventSource) -> None:
    _REGISTRY[source.name] = source


def get_source(name: str) -> EventSource:
    if name not in _REGISTRY:
        raise KeyError(f"unknown source '{name}'; available: {list(_REGISTRY)}")
    return _REGISTRY[name]


def load_event(
    path: str,
    evt_idx: int,
    source: str = "auto",
    q_lag: tuple[int, int] = (0, 0),
    l_lag: tuple[int, int] = (0, 0),
) -> EventRecord:
    if source == "auto":
        source = infer_source(path)
    return get_source(source).load(path, evt_idx, q_lag=q_lag, l_lag=l_lag)


def infer_source(path: str) -> str:
    """Pick reader from file extension (.dat/.bin -> binary, else hexdump txt)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".dat", ".bin"):
        return "binary"
    return "hexdump"


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


EVENT_MARKER = 0xFFFFFFFF
EVENT_END = 0xE0000000
_HEX_LINE_MARKER = b"ffffffff\n"
_BIN_MARKER = struct.pack("<I", EVENT_MARKER)  # native LE 32-bit words on disk

# path -> ("hextxt"|"binary", list of byte offsets to each event's 0xFFFFFFFF)
_EVENT_INDEX_CACHE: dict[str, tuple[str, list[int]]] = {}


def _file_kind(path: str) -> str:
    return "binary" if os.path.splitext(path)[1].lower() in (".dat", ".bin") else "hextxt"


def build_event_offset_index(path: str) -> list[int]:
    """Return byte offsets of each event's 0xFFFFFFFF marker (cached)."""
    kind, offsets = _get_event_index(path)
    return offsets


def _get_event_index(path: str) -> tuple[str, list[int]]:
    if path in _EVENT_INDEX_CACHE:
        return _EVENT_INDEX_CACHE[path]
    kind = _file_kind(path)
    if kind == "binary":
        offsets = _index_binary(path)
    else:
        offsets = _index_hextxt(path)
    _EVENT_INDEX_CACHE[path] = (kind, offsets)
    print(f"indexed {len(offsets)} events in {os.path.basename(path)} ({kind})")
    return kind, offsets


def _index_hextxt(path: str) -> list[int]:
    """mmap scan for 'ffffffff\\n' line starts — much faster than a Python line loop."""
    offsets: list[int] = []
    with open(path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        pos = 0
        while pos < mm.size():
            i = mm.find(_HEX_LINE_MARKER, pos)
            if i < 0:
                break
            offsets.append(i)
            pos = i + len(_HEX_LINE_MARKER)
        mm.close()
    return offsets


def _index_binary(path: str) -> list[int]:
    """mmap scan for big-endian 0xFFFFFFFF words."""
    offsets: list[int] = []
    with open(path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        pos = 0
        while pos <= mm.size() - 4:
            i = mm.find(_BIN_MARKER, pos)
            if i < 0:
                break
            offsets.append(i)
            pos = i + 4
        mm.close()
    return offsets


def _read_event_words(path: str, evt_idx: int) -> list[int]:
    kind, offsets = _get_event_index(path)
    if not 0 <= evt_idx < len(offsets):
        raise IndexError(f"evt {evt_idx} out of range (have {len(offsets)} events)")
    start = offsets[evt_idx]
    end = offsets[evt_idx + 1] if evt_idx + 1 < len(offsets) else None
    if kind == "binary":
        return _read_words_binary(path, start, end)
    return _read_words_hextxt(path, start, end)


def _read_words_hextxt(path: str, start: int, end: int | None) -> list[int]:
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read((end - start) if end is not None else -1)
    words: list[int] = []
    for line in data.splitlines():
        s = line.strip()
        if s:
            words.append(int(s, 16))
    return words


def _read_words_binary(path: str, start: int, end: int | None) -> list[int]:
    """Read native little-endian 32-bit words (Linux/XMIT PCIe readout byte order)."""
    with open(path, "rb") as f:
        f.seek(start)
        nbytes = (end - start) if end is not None else -1
        data = f.read(nbytes)
    nwords = len(data) // 4
    if nwords == 0:
        return []
    return list(struct.unpack(f"<{nwords}I", data[: nwords * 4]))


def parse_event_payloads(
    words: Iterable[int], all_slots: list[int]
) -> tuple[dict[int, list[int]], dict[int, dict]]:
    """Walk one event's 32-bit word stream and extract per-slot payloads + headers."""
    out: dict[int, list[int]] = {}
    trigger_meta: dict[int, dict] = {}
    hdr_idx = 6
    current_slot: int | None = None
    target_set = set(all_slots)
    started = False

    for w in words:
        if w == EVENT_MARKER:
            if started:
                break
            started = True
            continue
        if w == EVENT_END:
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


def get_event_payloads(
    path: str, evt_idx: int, all_slots: list[int]
) -> tuple[dict[int, list[int]], dict[int, dict]]:
    words = _read_event_words(path, evt_idx)
    return parse_event_payloads(words, all_slots)


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


class _PgramsFileEventSource:
    """Load events from hexdump .txt or raw big-endian .dat (auto-detected per path)."""

    def __init__(
        self,
        q_slots: list[int] | None = None,
        light_slot: int = LIGHT_SLOT_DEFAULT,
    ):
        self.q_slots = q_slots or Q_SLOTS_DEFAULT
        self.light_slot = light_slot

    def load(
        self,
        path: str,
        evt_idx: int,
        q_lag: tuple[int, int] = (0, 0),
        l_lag: tuple[int, int] = (0, 0),
    ) -> EventRecord:
        # Each lag is (header_lag, adc_lag): a FEM's FEMHeader6 (trigger) can be
        # shipped from a different event than the ADC payload it travels with (the
        # header trails the ADC by one event when the light readout stalls). The
        # per-ROI timestamp and its samples are one inseparable packet, so a single
        # adc_lag covers both. lag=0 -> this event. lag=+1 -> next event, etc.
        q_hlag, q_alag = q_lag
        l_hlag, l_alag = l_lag
        key = (
            path, evt_idx, q_hlag, q_alag, l_hlag, l_alag,
            tuple(self.q_slots), self.light_slot,
        )
        if key in _EVENT_CACHE:
            return _EVENT_CACHE[key]

        all_slots = self.q_slots + [self.light_slot]
        base_payloads, base_meta = get_event_payloads(path, evt_idx, all_slots)

        def _at(idx: int) -> tuple[dict[int, list[int]], dict[int, dict]]:
            if idx == evt_idx:
                return base_payloads, base_meta
            try:
                return get_event_payloads(path, idx, all_slots)
            except IndexError:
                return base_payloads, base_meta

        # ADC payloads (charge / light ROIs) and trigger headers, each from its own
        # (possibly lag-shifted) event.
        q_adc_payloads, _ = _at(evt_idx + q_alag)
        l_adc_payloads, _ = _at(evt_idx + l_alag)
        _, q_meta = _at(evt_idx + q_hlag)
        _, l_meta = _at(evt_idx + l_hlag)

        charge_slots = {
            qs: decode_charge(q_adc_payloads.get(qs, [])) for qs in self.q_slots
        }
        light_channels = decode_light(l_adc_payloads.get(self.light_slot, []))

        q_trig = next((q_meta[s] for s in self.q_slots if s in q_meta), None)
        if q_trig is None:
            q_trig = next((base_meta[s] for s in self.q_slots if s in base_meta), None)
        trigger_abs = q_trig["abs"] if q_trig else None

        trigger_ticks: dict[int, int] = {}
        # Q-FEM: the trigger sits at the fixed pre-trigger column within the readout
        # window (the readout is trigger-aligned), independent of the header sample,
        # so the Q header lag does not move this line.
        for slot in self.q_slots:
            chans = charge_slots.get(slot, {})
            nsamp = max((len(v) for v in chans.values()), default=0)
            if nsamp > 0:
                trigger_ticks[slot] = min(Q_PRETRIGGER_SAMPLES, nsamp - 1)

        # L-FEM: remap each ROI onto a continuous 64 MHz axis and place the trigger
        # line, both anchored on the L-FEM's OWN FEMHeader6 (header-lag-adjusted).
        # frame_num is mod-8: map the 4-frame window [trig_frame-1 .. trig_frame+2]
        # with the -1 frame at tick 0; start_sample (0..8191) is the 64 MHz position
        # within its frame.
        light_roi_oow = 0
        light_roi_total = 0
        l_trig = l_meta.get(self.light_slot) or base_meta.get(self.light_slot)
        if l_trig is not None:
            trig_frame = l_trig["frame"]
            trig_sample = l_trig["sample"]
            earliest_frame = (trig_frame - 1) % LIGHT_FRAME_MOD
            for rois in light_channels.values():
                for roi in rois:
                    local_frame = (roi["frame_num"] - earliest_frame) % LIGHT_FRAME_MOD
                    roi["start_sample"] += local_frame * LIGHT_TICKS_PER_FRAME
                    light_roi_total += 1
                    # local_frame in [0..3] is inside the window; >=4 means the ROI
                    # cannot belong to this trigger (stale/duplicate light).
                    if local_frame >= LIGHT_WINDOW_FRAMES:
                        light_roi_oow += 1
            # trig_frame maps to local frame 1; convert 2 MHz sample to ticks.
            trigger_ticks[self.light_slot] = (
                LIGHT_TICKS_PER_FRAME + trig_sample * Q_SAMPLE_TO_LIGHT_TICK
            )

        # Headers actually used (after lag) so the UI's header-match warning reflects
        # the user's choice: if a header lag makes Q and L agree, no warning shows.
        used_meta: dict[int, dict] = {s: q_meta[s] for s in self.q_slots if s in q_meta}
        if self.light_slot in l_meta:
            used_meta[self.light_slot] = l_meta[self.light_slot]

        record = EventRecord(
            evt_number=evt_idx,
            charge_slots=charge_slots,
            light_channels=light_channels,
            trigger_ticks=trigger_ticks,
            trigger_abs=trigger_abs,
            trigger_meta=used_meta,
            light_roi_oow=light_roi_oow,
            light_roi_total=light_roi_total,
        )
        _EVENT_CACHE[key] = record
        return record


class HexdumpEventSource(_PgramsFileEventSource):
    name = "hexdump"


class BinaryEventSource(_PgramsFileEventSource):
    name = "binary"


register_source(HexdumpEventSource())
register_source(BinaryEventSource())
