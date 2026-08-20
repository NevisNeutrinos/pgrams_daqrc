"""Heatmap builders adapted from plot_event_adc.ipynb."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import matplotlib.pyplot as plt
from plotly.subplots import make_subplots
from data_monitoring.coord_mapping import coord_mapping

WAVE_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]

# Fixed pixel sizes for the waveform tabs (top plot stays constant regardless of
# how many sub-rows are drawn; each sub-row is 1/3 of the top plot).
WF_TOP_PX = 400
WF_SUB_PX = WF_TOP_PX // 3
WF_MARGIN_T = 56
WF_MARGIN_B = 42
LIGHT_WINDOW_TICKS = 4 * 8192  # 4-frame L-FEM readout window = 32768 ticks
LIGHT_EDGE_PAD = 64            # gap left/right of the L-FEM overlay


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


WF_GAP_SUB_PX = 18     # gap between consecutive waveform rows
WF_GAP_FIRST_PX = 60   # larger gap below the top plot (room for its x-axis label)


def _waveform_dims(n_sub: int) -> tuple[int, list[tuple[float, float]]]:
    """Return (figure height px, per-row y-domains ordered top->bottom).

    Top plot is WF_TOP_PX, each sub-row WF_SUB_PX. A larger gap sits below the
    top plot (so its x-axis label clears the next row); gaps between waveform
    rows stay small."""
    plot_px = WF_TOP_PX + n_sub * WF_SUB_PX
    if n_sub >= 1:
        plot_px += WF_GAP_FIRST_PX + max(0, n_sub - 1) * WF_GAP_SUB_PX
    height = int(plot_px + WF_MARGIN_T + WF_MARGIN_B)

    domains: list[tuple[float, float]] = []
    top = 1.0
    bot = top - WF_TOP_PX / plot_px
    domains.append((bot, top))
    cur = bot - (WF_GAP_FIRST_PX / plot_px if n_sub >= 1 else 0.0)
    for _ in range(n_sub):
        top_i = cur
        bot_i = top_i - WF_SUB_PX / plot_px
        domains.append((max(0.0, bot_i), top_i))
        cur = bot_i - WF_GAP_SUB_PX / plot_px
    return height, domains


def _apply_domains(fig: go.Figure, domains: list[tuple[float, float]]) -> None:
    for idx, (y0, y1) in enumerate(domains):
        key = "yaxis" if idx == 0 else f"yaxis{idx + 1}"
        fig.layout[key].domain = [y0, y1]

N_QFEM_CHANNELS = 64
N_LIGHT_CHANNELS = 36
SAMPLES_PER_FRAME = 256       # Q-FEM: 2 MHz, 256 samples / 128 us frame
LIGHT_TICKS_PER_FRAME = 8192  # L-FEM: 64 MHz, 8192 ticks / 128 us frame

# Shared 4-frame readout window (one frame = 128 us). Heatmaps use a common
# 0..WINDOW_US axis so Q-FEM and L-FEM line up; waveform tabs keep native
# sample/tick units but Reset Axes restores this full window.
N_READOUT_FRAMES = 4
FRAME_US = 128.0
WINDOW_US = N_READOUT_FRAMES * FRAME_US  # 512 us
Q_US_PER_SAMPLE = 0.5                     # 2 MHz
L_US_PER_TICK = 1.0 / 64.0                # 64 MHz
Q_SAMPLES_FULL = N_READOUT_FRAMES * SAMPLES_PER_FRAME  # 1024 (4-frame heatmap axis)
# Nominal Q-FEM readout: 3 frames (256 pre + 512 post); Reset Axes on the
# Q-FEM waveforms tab restores this x-span (64 ch x 768 samples).
Q_SAMPLES_NOMINAL = 3 * SAMPLES_PER_FRAME  # 768
L_TICKS_FULL = LIGHT_WINDOW_TICKS         # 32768
# Q-FEM buffer is trigger-aligned: this many samples before the trigger.
Q_PRETRIGGER_SAMPLES = 256

# Shared plot-area domain so Q/L heatmaps are the same width despite colorbars.
HEATMAP_XAXIS_DOMAIN = [0.0, 0.86]
HEATMAP_COLORBAR = dict(len=1.0, thickness=14, x=0.90, xanchor="left")

COMPACT_MARGIN = dict(l=48, r=12, t=36, b=32)
HEATMAP_HEIGHT = 290
LBW_HEIGHT = 92
ERROR_BIT_CHART_HEIGHT = 195  # 260 * 3/4
N_EVENT_ERROR_BITS = 32
# 1-based EventErrorBit names. Unnamed bins still tick as the number only.
EVENT_ERROR_BIT_NAMES = {
    1: "missing_fem",
    2: "wrong_slot",
    3: "bad_header_tag",
    4: "qfem_event_num_mismatch",
    5: "lfem_event_num_mismatch",
    6: "qfem_frame_num_mismatch",
    7: "lfem_frame_num_mismatch",
    8: "qfem_trigger_num_mismatch",
    9: "lfem_trigger_num_mismatch",
    10: "qfem_sample_num_mismatch",
    11: "lfem_sample_num_mismatch",
    12: "fem_status_warning",
    17: "qfem_adc_count_mismatch",
    18: "lfem_adc_count_mismatch",
    19: "qfem_checksum_mismatch",
    20: "lfem_checksum_mismatch",
    21: "qfem_missing_channels",
    22: "lfem_wrong_channels",
    23: "qfem_untypical_adc_count",
    24: "lfem_untypical_adc_count",
    25: "lfem_roi_inconsistent",
    26: "qfem_stuck_channel",
    32: "event_number_gap",
}
FULL_EVENT_STATUS_NAMES = {
    0: "kOk",
    1: "kFileNotFound",
    2: "kEventNotFound",
    3: "kLlagEventNotFound",
    4: "kLlagUsedClosest",
}
# Minimum half-range for Q ped-sub heatmaps: zlim = max(max(|ADC-med|), Z_FLOOR).
Q_CHARGE_Z_FLOOR = 5.0


def charge_heatmap_zlim(img_sub: np.ndarray) -> float:
    finite = img_sub[np.isfinite(img_sub)]
    if finite.size == 0:
        return Q_CHARGE_Z_FLOOR
    return max(float(np.max(np.abs(finite))), Q_CHARGE_Z_FLOOR)

# Match GramsReadout charge_algs / light_algs (per-event). No max−min reject, no SEM.
Q_PED_T0 = 256
Q_PED_GUARD = 10
Q_HIT_SIGMA_MULT = 5.0
Q_HIT_MIN_ADC = 5.0
Q_HIT_MIN_WIDTH = 3
L_TAIL_SAMPLES = 20
L_TAIL_EXCLUDE_LAST = 2


def _count_hits_above(adc: np.ndarray, threshold: float, min_width: int) -> int:
    above = np.asarray(adc, dtype=np.float64) > threshold
    if above.size == 0 or not bool(above.any()):
        return 0
    padded = np.concatenate(([False], above, [False]))
    edges = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    return int(np.sum((ends - starts) >= min_width))


def _pedestal_mean_rms(window: np.ndarray) -> tuple[float, float]:
    ped = float(np.mean(window))
    rms = float(np.sqrt(np.mean((window - ped) ** 2)))
    return ped, rms


def compute_charge_lbw(
    charge_slots: dict[int, dict[int, np.ndarray]],
    q_slots: list[int],
    channels_per_slot: int = N_QFEM_CHANNELS,
) -> tuple[list[float], list[float], list[float]]:
    """Per-event charge LBW: pedestal [0, t0−10), hits = runs above ped+max(5,5σ) width≥3."""
    baselines: list[float] = []
    rmses: list[float] = []
    hits: list[float] = []
    ped_end = Q_PED_T0 - Q_PED_GUARD
    for slot in q_slots:
        channels = charge_slots.get(slot, {})
        for ch in range(channels_per_slot):
            arr = np.asarray(channels[ch], dtype=np.float64) if ch in channels else np.array([])
            end = min(ped_end, int(arr.size))
            if end < 2:
                baselines.append(0.0)
                rmses.append(0.0)
                hits.append(0.0)
                continue
            ped, rms = _pedestal_mean_rms(arr[:end])
            h = float(_count_hits_above(arr, ped + max(Q_HIT_MIN_ADC, Q_HIT_SIGMA_MULT * rms), Q_HIT_MIN_WIDTH))
            baselines.append(ped)
            rmses.append(rms)
            hits.append(h)
    return baselines, rmses, hits


def compute_light_lbw(
    light_channels: dict[int, list[dict]],
    nchan: int = N_LIGHT_CHANNELS,
) -> tuple[list[float], list[float], list[float]]:
    """Per-event light LBW: hit = ROI count; pedestal = [-22, -3] tail (all ROIs long enough)."""
    baselines: list[float] = []
    rmses: list[float] = []
    hits: list[float] = []
    need = L_TAIL_EXCLUDE_LAST + L_TAIL_SAMPLES
    for ch in range(nchan):
        rois = [r for r in light_channels.get(ch, []) if len(r.get("samples", [])) > 0]
        hits.append(float(len(rois)))
        peds: list[float] = []
        ch_rms: list[float] = []
        for roi in rois:
            samples = np.asarray(roi["samples"], dtype=np.float64)
            if samples.size < need:
                continue
            start = samples.size - need
            end = samples.size - L_TAIL_EXCLUDE_LAST
            ped, rms = _pedestal_mean_rms(samples[start:end])
            peds.append(ped)
            ch_rms.append(rms)
        if peds:
            baselines.append(float(np.mean(peds)))
            rmses.append(float(np.mean(ch_rms)))
        else:
            baselines.append(0.0)
            rmses.append(0.0)
    return baselines, rmses, hits


def build_charge_heatmap(channels: dict[int, np.ndarray]) -> tuple[np.ndarray, int, int]:
    if not channels:
        return np.full((N_QFEM_CHANNELS, 1), np.nan), 0, 0

    nsamp = max(len(v) for v in channels.values())
    img = np.full((N_QFEM_CHANNELS, nsamp), np.nan, dtype=np.float32)
    for ch, samples in channels.items():
        if 0 <= ch < N_QFEM_CHANNELS:
            img[ch, : len(samples)] = samples

    baselines = np.nanmedian(img, axis=1, keepdims=True)
    return img - baselines, N_QFEM_CHANNELS, nsamp


def light_sample_extent(light_channels: dict[int, list[dict]]) -> tuple[int, int]:
    starts, ends = [], []
    for rois in light_channels.values():
        for roi in rois:
            n = len(roi["samples"])
            if n:
                starts.append(roi["start_sample"])
                ends.append(roi["start_sample"] + n - 1)
    if not starts:
        return 0, SAMPLES_PER_FRAME
    return min(starts), max(ends)


def build_light_heatmap(
    light_channels: dict[int, list[dict]],
    nchan: int = N_LIGHT_CHANNELS,
    pad: int = 32,
    xmin: int | None = None,
    xmax: int | None = None,
) -> tuple[np.ndarray, int, int]:
    if xmin is None or xmax is None:
        x0, x1 = light_sample_extent(light_channels)
        if xmin is None:
            xmin = max(0, x0 - pad)
        if xmax is None:
            xmax = x1 + pad
    width = max(1, xmax - xmin + 1)
    img = np.full((nchan, width), np.nan, dtype=np.float32)

    for ch, rois in light_channels.items():
        if ch < 0 or ch >= nchan:
            continue
        for roi in rois:
            samples = roi["samples"]
            n = len(samples)
            if n == 0:
                continue
            col0 = roi["start_sample"] - xmin
            col1 = col0 + n
            if col1 <= 0 or col0 >= width:
                continue
            dst0 = max(col0, 0)
            dst1 = min(col1, width)
            src0 = dst0 - col0
            src1 = src0 + (dst1 - dst0)
            img[ch, dst0:dst1] = samples[src0:src1]

    return img, xmin, xmax


def _add_trigger_line(fig: go.Figure, trigger_x: float | None, *, absolute: bool = False):
    if trigger_x is None:
        return
    fig.add_vline(
        x=trigger_x,
        line_width=2,
        line_dash="dash",
        line_color="limegreen",
        opacity=0.9,
    )
    fig.add_annotation(
        x=trigger_x,
        y=1.02,
        yref="paper",
        text="trigger",
        showarrow=False,
        font=dict(size=9, color="green"),
        xanchor="center",
        yanchor="bottom",
    )


def _fem_header_title_suffix(meta: dict | None) -> str:
    """Format e#/f#/t#/s# from trigger_meta for heatmap titles."""
    if not meta:
        return "e#=?, f#=?, t#=?, s#=?"
    e = meta.get("event_id", "?")
    f = meta.get("frame_id", "?")
    t = meta.get("frame", "?")
    s = meta.get("sample", "?")
    return f"e#={e}, f#={f}, t#={t}, s#={s}"


def make_charge_heatmap_figure(
    channels: dict[int, np.ndarray],
    title: str = "Q-FEM",
    trigger_x: int | None = None,
    trig_sample: int | None = None,
    header_meta: dict | None = None,
    charge_window_start: int = 0,
) -> go.Figure:
    """Q-FEM heatmap on the shared 4-frame (0..512 us) axis.

    The buffer is trigger-aligned (``Q_PRETRIGGER_SAMPLES`` before the trigger).
    ``trig_sample`` is the 2 MHz sample# from this FEM's FEMHeader6; together with
    the fixed pre-trigger count it places the 3-frame readout inside the same
    [trig_frame-1 .. trig_frame+2] window used for L-FEM.

    ``charge_window_start`` is the readout index of ``channels`` sample 0 when the
    array is a slice of the full trigger-aligned buffer (flight telemetry uses 248).
    """
    img_sub, _nchan_axis, nsamp = build_charge_heatmap(channels)
    if not channels:
        return _empty_figure(title, "(no data)")

    n_data = sum(1 for v in channels.values() if len(v) > 0)
    zlim = charge_heatmap_zlim(img_sub)
    # Trigger sits in local frame 1 of the 4-frame window, at 2 MHz sample#.
    sample = 0 if trig_sample is None else int(trig_sample)
    trig_us = FRAME_US + sample * Q_US_PER_SAMPLE
    t0 = trig_us - Q_PRETRIGGER_SAMPLES * Q_US_PER_SAMPLE
    start = int(charge_window_start)
    x_us = [t0 + (start + i) * Q_US_PER_SAMPLE for i in range(nsamp)]
    cbar = {**HEATMAP_COLORBAR, "title": "ADC-med"}
    fig = go.Figure(
        data=go.Heatmap(
            z=img_sub,
            x=x_us,
            y=list(range(N_QFEM_CHANNELS)),
            colorscale="RdBu_r",
            zmid=0,
            zmin=-zlim,
            zmax=zlim,
            colorbar=cbar,
        )
    )
    for k in range(1, N_READOUT_FRAMES):
        fig.add_vline(
            x=k * FRAME_US, line_width=1, line_dash="dash",
            line_color="black", opacity=0.45,
        )
    # Prefer header-based trig_us; fall back to buffer column if no header sample.
    if trig_sample is not None:
        _add_trigger_line(fig, trig_us)
    elif trigger_x is not None:
        _add_trigger_line(fig, t0 + (start + trigger_x) * Q_US_PER_SAMPLE)

    hdr = _fem_header_title_suffix(header_meta)
    fig.update_layout(
        title=dict(
            text=f"{title} ({n_data} ch \u00d7 {nsamp} smp): {hdr}",
            font=dict(size=11),
        ),
        xaxis=dict(
            title="t (4\u00d7128 \u03bcs; 2 MHz, 500 ns bin width)",
            range=[0, WINDOW_US],
            autorange=False,
            domain=HEATMAP_XAXIS_DOMAIN,
        ),
        yaxis_title="ch",
        margin=COMPACT_MARGIN,
        height=HEATMAP_HEIGHT,
        # So Reset / odd double-click reliably returns to the 0..512 us window
        # (even double-click still autoscales to data, like waveform tabs).
        meta=dict(default_ranges={"xaxis": [0, WINDOW_US]}),
    )
    return fig


def make_light_heatmap_figure(
    light_channels: dict[int, list[dict]],
    title: str = "L-FEM",
    trigger_x: int | None = None,
    header_meta: dict | None = None,
) -> go.Figure:
    fired = [
        ch
        for ch, rois in light_channels.items()
        if any(len(r["samples"]) > 0 for r in rois)
    ]
    if not fired:
        return _empty_figure(title, "(no data)")

    # ROI data stay sparse; the shared 0..512 us axis leaves empty space outside
    # fired regions so Q/L heatmaps line up.
    img, xmin, xmax = build_light_heatmap(light_channels)
    finite = img[np.isfinite(img)]
    vmin = float(np.percentile(finite, 2)) if finite.size else 2000.0
    vmax = float(np.percentile(finite, 98)) if finite.size else 2200.0
    if vmax <= vmin:
        vmax = vmin + 1.0

    width = img.shape[1]
    n_rois = sum(len(light_channels[c]) for c in fired)
    x_us = [(xmin + i) * L_US_PER_TICK for i in range(width)]
    cbar = {**HEATMAP_COLORBAR, "title": "ADC"}
    fig = go.Figure(
        data=go.Heatmap(
            z=img,
            x=x_us,
            y=list(range(N_LIGHT_CHANNELS)),
            colorscale="YlOrRd",
            zmin=vmin,
            zmax=vmax,
            colorbar=cbar,
        )
    )
    for k in range(1, N_READOUT_FRAMES):
        fig.add_vline(
            x=k * FRAME_US, line_width=1, line_dash="dash",
            line_color="black", opacity=0.45,
        )
    trig_us = None if trigger_x is None else trigger_x * L_US_PER_TICK
    _add_trigger_line(fig, trig_us)

    hdr = _fem_header_title_suffix(header_meta)
    fig.update_layout(
        title=dict(
            text=f"{title} ({len(fired)} ch, {n_rois} ROIs): {hdr}",
            font=dict(size=11),
        ),
        xaxis=dict(
            title="t (4\u00d7128 \u03bcs; 64 MHz, 15.625 ns bin width)",
            range=[0, WINDOW_US],
            autorange=False,
            domain=HEATMAP_XAXIS_DOMAIN,
        ),
        yaxis_title="ch",
        margin=COMPACT_MARGIN,
        height=HEATMAP_HEIGHT,
        meta=dict(default_ranges={"xaxis": [0, WINDOW_US]}),
    )
    return fig


def light_fired_channels(light_channels: dict[int, list[dict]]) -> list[int]:
    """Channels with at least one non-empty ROI, ordered by earliest ROI."""
    fired = [
        ch for ch, rois in light_channels.items()
        if any(len(r["samples"]) > 0 for r in rois)
    ]

    def earliest(ch: int) -> int:
        return min(r["start_sample"] for r in light_channels[ch] if len(r["samples"]) > 0)

    return sorted(fired, key=earliest)


def make_qfem_waveform_figure(
    channels: dict[int, np.ndarray],
    selected: list[int],
    trigger_x: int | None = None,
    title: str = "Q-FEM",
    band_gray: bool = False,
) -> go.Figure:
    """Top: this Q-FEM's heatmap. Below: each selected channel's raw waveform.

    band_gray=True shades every selected channel on the heatmap with a single
    translucent gray band (used for range input like [0,9]); otherwise each band
    matches its waveform color (used for explicit lists like 0,5,12).
    """
    if not channels:
        return _empty_figure(title, "(no data)")

    nsamp = max(len(v) for v in channels.values())
    sel = [c for c in selected if c in channels]
    n_wave = len(sel)
    rows = 1 + n_wave
    height, domains = _waveform_dims(n_wave)

    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.0)

    img_sub, nchan, _ = build_charge_heatmap(channels)
    zlim = charge_heatmap_zlim(img_sub)
    top_len = domains[0][1] - domains[0][0]
    fig.add_trace(
        go.Heatmap(
            z=img_sub,
            x=list(range(nsamp)),
            y=list(range(nchan)),
            colorscale="RdBu_r",
            zmid=0,
            zmin=-zlim,
            zmax=zlim,
            colorbar=dict(title="ADC-med", len=top_len, y=domains[0][1], yanchor="top", thickness=12),
        ),
        row=1, col=1,
    )
    fig.update_yaxes(title_text="ch", row=1, col=1)

    # Bands marking the channels whose waveforms are drawn below. For a range
    # ([0,9]) draw a single gray band spanning all of them (one outer border);
    # for an explicit list each band uses its own waveform color.
    if band_gray and sel:
        fig.add_hrect(
            y0=min(sel) - 0.5, y1=max(sel) + 0.5, row=1, col=1,
            fillcolor=_rgba("#808080", 0.22), line_width=1, line_color="#808080",
        )
    elif sel:
        for i, ch in enumerate(sel):
            color = WAVE_PALETTE[i % len(WAVE_PALETTE)]
            fig.add_hrect(
                y0=ch - 0.5, y1=ch + 0.5, row=1, col=1,
                fillcolor=_rgba(color, 0.18), line_width=1, line_color=color,
            )

    for i, ch in enumerate(sel):
        raw = np.asarray(channels[ch], dtype=np.float32)
        ped = float(np.median(raw)) if raw.size else 0.0
        color = WAVE_PALETTE[i % len(WAVE_PALETTE)]
        r = i + 2
        fig.add_trace(
            go.Scatter(
                x=np.arange(raw.size), y=raw, mode="lines",
                line=dict(width=1, color=color), showlegend=False,
            ),
            row=r, col=1,
        )
        fig.add_hline(
            y=ped, line=dict(width=1, dash="dot", color="#444"),
            row=r, col=1, annotation_text=f"ped {ped:.0f}",
            annotation_position="top left", annotation_font_size=8,
        )
        fig.update_yaxes(title_text=f"ch{ch}", row=r, col=1)

    # Frame boundaries within the nominal 3-frame (768-sample) readout.
    boundaries = [k * SAMPLES_PER_FRAME for k in range(1, 3)]
    for r in range(1, rows + 1):
        for x in boundaries:
            fig.add_vline(x=x, line_width=1, line_dash="dash", line_color="black", opacity=0.4, row=r, col=1)

    if trigger_x is not None:
        for r in range(1, rows + 1):
            fig.add_vline(
                x=trigger_x, line_width=1.5, line_dash="dash", line_color="limegreen",
                row=r, col=1,
                **(dict(annotation_text="trigger", annotation_position="top",
                        annotation_yshift=12, annotation_font_size=9,
                        annotation_font_color="green") if r == 1 else {}),
            )

    _apply_domains(fig, domains)
    # Default / Autoscale: data extent (so extra-long channels stay visible).
    # Reset Axes / double-click: nominal 64 x 768 window via meta.full_x_range.
    # Keep xtick labels on the top heatmap even when waveform rows are below
    # (shared_xaxes would otherwise hide them).
    fig.update_xaxes(showticklabels=True, row=1, col=1)
    fig.update_xaxes(title_text="sample (0.5 \u03bcs each time tick)", row=1, col=1)
    fig.update_layout(
        title=dict(text=title, font=dict(size=12)),
        margin=dict(l=58, r=14, t=WF_MARGIN_T, b=WF_MARGIN_B),
        height=height,
        showlegend=False,
        meta=dict(full_x_range=[0, Q_SAMPLES_NOMINAL]),
    )
    return fig


def make_lfem_waveform_figure(
    light_channels: dict[int, list[dict]],
    trigger_x: int | None = None,
    title: str = "L-FEM",
) -> go.Figure:
    """Top: all fired channels over the full window. Below: one row per fired
    channel, zoomed to span that channel's ROI(s)."""
    chans = light_fired_channels(light_channels)
    if not chans:
        return _empty_figure(title, "(no data)")

    color_of = {ch: WAVE_PALETTE[i % len(WAVE_PALETTE)] for i, ch in enumerate(chans)}

    n_sub = len(chans)
    rows = 1 + n_sub
    height, domains = _waveform_dims(n_sub)

    fig = make_subplots(rows=rows, cols=1, shared_xaxes=False, vertical_spacing=0.0)

    def _vline(x, y0, y1, r, *, color, width, dash=None, opacity=1.0):
        # Vertical line drawn as a trace (not a layout shape): traces always
        # re-render on interactive zoom/pan, whereas domain-referenced shapes
        # can vanish until the next autoscale.
        fig.add_trace(
            go.Scatter(
                x=[x, x], y=[y0, y1], mode="lines",
                line=dict(color=color, width=width, dash=dash),
                opacity=opacity, showlegend=False, hoverinfo="skip",
            ),
            row=r, col=1,
        )

    def _yspan(lo: float, hi: float) -> tuple[float, float]:
        pad = max(1.0, 0.06 * (hi - lo))
        return lo - pad, hi + pad

    # ---- Row 1: overlay of every ROI across the full window ----
    ov_min = ov_max = ov_ylo = ov_yhi = None
    for ch in chans:
        for roi in light_channels[ch]:
            y = np.asarray(roi["samples"], dtype=np.float32)
            if y.size == 0:
                continue
            x = np.arange(y.size) + roi["start_sample"]
            ov_min = x[0] if ov_min is None else min(ov_min, x[0])
            ov_max = x[-1] if ov_max is None else max(ov_max, x[-1])
            ymn, ymx = float(y.min()), float(y.max())
            ov_ylo = ymn if ov_ylo is None else min(ov_ylo, ymn)
            ov_yhi = ymx if ov_yhi is None else max(ov_yhi, ymx)
            fig.add_trace(
                go.Scatter(x=x, y=y, mode="lines", line=dict(width=1.4, color=color_of[ch]),
                           showlegend=False),
                row=1, col=1,
            )
    # Dedicated thick legend swatches (don't fatten the plotted overlay lines).
    for ch in chans:
        fig.add_trace(
            go.Scatter(x=[None], y=[None], mode="lines",
                       line=dict(width=5, color=color_of[ch]), name=f"ch{ch}"),
            row=1, col=1,
        )
    fig.update_yaxes(title_text="ADC", row=1, col=1)

    # Overlay x-range: 64-tick gap each side, clamped to the [0, 32768] window.
    if ov_min is None:
        ov_lo, ov_hi = 0, LIGHT_WINDOW_TICKS
        oy0, oy1 = 0.0, 1.0
    else:
        ov_lo = max(0, int(ov_min) - LIGHT_EDGE_PAD)
        ov_hi = min(LIGHT_WINDOW_TICKS, int(ov_max) + LIGHT_EDGE_PAD)
        oy0, oy1 = _yspan(ov_ylo, ov_yhi)
    fig.update_xaxes(range=[ov_lo, ov_hi], autorange=False, row=1, col=1)
    fig.update_yaxes(range=[oy0, oy1], autorange=False, row=1, col=1)
    row_ranges: list[tuple[int, int]] = [(ov_lo, ov_hi)]
    row_yranges: list[tuple[float, float] | None] = [(oy0, oy1)]
    # Axis names for Autoscale restore (xaxis / yaxis, xaxis2 / yaxis2, ...).
    default_ranges: dict[str, list[float]] = {
        "xaxis": [ov_lo, ov_hi],
        "yaxis": [oy0, oy1],
    }

    # Window-edge markers (0 / 32768) when they fall inside the overlay range.
    for edge in (0, LIGHT_WINDOW_TICKS):
        if ov_lo <= edge <= ov_hi:
            _vline(edge, oy0, oy1, 1, color="#c0392b", width=1, opacity=0.7)
            fig.add_annotation(
                x=edge, y=oy0, xref="x", yref="y", text=str(edge), showarrow=False,
                font=dict(size=8, color="#c0392b"), yanchor="top",
            )

    # ---- Sub rows: one per channel, spanning all its ROI(s) ----
    for i, ch in enumerate(chans):
        r = i + 2
        color = color_of[ch]
        xmin = xmax = ylo = yhi = None
        roi_edges: list[int] = []
        for roi in light_channels[ch]:
            y = np.asarray(roi["samples"], dtype=np.float32)
            if y.size == 0:
                continue
            x = np.arange(y.size) + roi["start_sample"]
            x0, x1 = int(x[0]), int(x[-1])
            xmin = x0 if xmin is None else min(xmin, x0)
            xmax = x1 if xmax is None else max(xmax, x1)
            ymn, ymx = float(y.min()), float(y.max())
            ylo = ymn if ylo is None else min(ylo, ymn)
            yhi = ymx if yhi is None else max(yhi, ymx)
            roi_edges += [x0, x1]
            fig.add_trace(
                go.Scatter(x=x, y=y, mode="lines", line=dict(width=1.2, color=color),
                           showlegend=False),
                row=r, col=1,
            )
        if xmin is None:
            row_ranges.append((ov_lo, ov_hi))
            row_yranges.append(None)
            continue
        pad = max(8, int(0.04 * (xmax - xmin + 1)))
        lo, hi = xmin - pad, xmax + pad
        ry0, ry1 = _yspan(ylo, yhi)
        fig.update_xaxes(range=[lo, hi], autorange=False, row=r, col=1)
        fig.update_yaxes(range=[ry0, ry1], autorange=False, title_text=f"ch{ch}", row=r, col=1)
        row_ranges.append((lo, hi))
        row_yranges.append((ry0, ry1))
        default_ranges[f"xaxis{r}"] = [lo, hi]
        default_ranges[f"yaxis{r}"] = [ry0, ry1]
        # ROI start / end markers (light gray, original style).
        for xe in roi_edges:
            _vline(xe, ry0, ry1, r, color="#999", width=0.6, opacity=0.6)

    # Frame boundaries: scatter when inside the default view (survives zoom);
    # layout vlines when outside (ignored by Autoscale, visible after Reset).
    for r in range(1, rows + 1):
        yr = row_yranges[r - 1]
        if yr is None:
            continue
        lo, hi = row_ranges[r - 1]
        for x in range(LIGHT_TICKS_PER_FRAME, L_TICKS_FULL, LIGHT_TICKS_PER_FRAME):
            if lo <= x <= hi:
                _vline(x, yr[0], yr[1], r, color="black", width=1, dash="dash", opacity=0.5)
            else:
                fig.add_vline(
                    x=x, line_width=1, line_dash="dash", line_color="black",
                    opacity=0.5, row=r, col=1,
                )

    if trigger_x is not None:
        fig.add_annotation(
            x=trigger_x, y=1.0, xref="x", yref="y domain", yshift=12, text="trigger",
            showarrow=False, font=dict(size=9, color="green"),
            xanchor="center", yanchor="bottom",
        )
        for r in range(1, rows + 1):
            yr = row_yranges[r - 1] if r > 1 else (oy0, oy1)
            lo, hi = row_ranges[r - 1] if r > 1 else (ov_lo, ov_hi)
            if yr is None:
                continue
            # Same pattern as frame boundaries: scatter inside the default view
            # (survives zoom); layout vline outside (visible after Reset).
            # Both use the same y span so Reset never makes trigger look taller.
            if lo <= trigger_x <= hi:
                _vline(trigger_x, yr[0], yr[1], r, color="limegreen", width=1.5, dash="dash")
            else:
                fig.add_vline(
                    x=trigger_x, line_width=1.5, line_dash="dash",
                    line_color="limegreen", opacity=0.9, row=r, col=1,
                )

    _apply_domains(fig, domains)
    # X-axis label under the overlay (the larger first gap keeps it clear of row 2).
    fig.update_xaxes(title_text="sample (15.625 ns each time tick)", row=1, col=1)
    fig.update_layout(
        title=dict(text=title, font=dict(size=12), x=0.01, xanchor="left"),
        margin=dict(l=58, r=104, t=WF_MARGIN_T, b=WF_MARGIN_B),
        height=height,
        legend=dict(
            font=dict(size=13), orientation="v", x=1.005, xanchor="left",
            y=1.0, yanchor="top", itemsizing="constant",
        ),
        # Default ranges restored by Autoscale / even double-click (JS);
        # Reset / odd double-click -> x full 0..32768, y stays at default.
        meta=dict(
            full_x_range=[0, L_TICKS_FULL],
            default_ranges=default_ranges,
            autoscale_to_default=True,
        ),
    )
    return fig


LBW_PANEL_TICK_FONT = 10
LBW_PANEL_TITLE_FONT = 11


def _lbw_channel_axis(n: int, is_light: bool) -> tuple[list[int], list[int]]:
    """X range [-1, n+1]: 1-bin pad left, tick at n, 1 blank bin right of n."""
    tickvals = [0, 9, 18, 27, 36] if is_light else [0, 16, 32, 48, 64]
    if tickvals[-1] != n:
        tickvals = [t for t in tickvals if t <= n]
        if tickvals[-1] != n:
            tickvals.append(n)
    return [-1, n + 1], tickvals


def _lbw_grid_positions(n: int, is_light: bool) -> tuple[list[int], list[int]]:
    """Major/minor vertical grid positions (minor excludes major to avoid double lines)."""
    major_step = 9 if is_light else 16
    minor_step = 3 if is_light else 4
    major = list(range(0, n + 1, major_step))
    if major[-1] != n:
        major.append(n)
    major_set = set(major)
    minor = [x for x in range(0, n + 1, minor_step) if x not in major_set]
    return major, minor


def _add_lbw_grid_vlines(fig: go.Figure, major: list[int], minor: list[int], row: int) -> None:
    """Draw grid behind traces (layer='below')."""
    yref = "y domain" if row == 1 else "y2 domain"
    xref = "x"
    for x in minor:
        fig.add_shape(
            type="line", x0=x, x1=x, y0=0, y1=1,
            xref=xref, yref=yref,
            line=dict(color="white", width=0.4),
            layer="below",
        )
    for x in major:
        fig.add_shape(
            type="line", x0=x, x1=x, y0=0, y1=1,
            xref=xref, yref=yref,
            line=dict(color="white", width=1.2),
            layer="below",
        )


def make_lbw_panel_figure(
    baseline,
    std_dev,
    hits,
    *,
    slot_label: str,
    is_light: bool = False,
    height: int = HEATMAP_HEIGHT,
) -> go.Figure:
    """Two-row panel: baseline±RMS (top) + hit histogram (bottom). No SEM."""
    n = len(baseline)
    x = list(range(n))
    x_range, x_ticks = _lbw_channel_axis(n, is_light)
    hit_title = (
        f"{slot_label} hits (ROIs/ch)"
        if is_light
        else f"{slot_label} hits (run ≥{Q_HIT_MIN_WIDTH} above ped+max({Q_HIT_MIN_ADC:g},{Q_HIT_SIGMA_MULT:g}σ))"
    )
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.56, 0.44], vertical_spacing=0.10,
        subplot_titles=(f"{slot_label} baseline ± RMS", hit_title),
    )
    fig.add_trace(
        go.Scatter(
            x=x, y=baseline, mode="markers",
            marker=dict(size=4, color="#2563eb"),
            error_y=dict(type="data", array=std_dev, visible=True, thickness=1.0, width=3),
            showlegend=False,
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Bar(x=x, y=hits, marker_line_width=0, showlegend=False),
        row=2, col=1,
    )

    spans = [(b - r, b + r) for b, r in zip(baseline, std_dev) if b > 0]
    if spans:
        y0 = min(lo for lo, _ in spans)
        y1 = max(hi for _, hi in spans)
        pad = max(0.5, 0.1 * (y1 - y0))
        fig.update_yaxes(
            range=[y0 - pad, y1 + pad], title_text="ADC", row=1, col=1,
            tickfont=dict(size=LBW_PANEL_TICK_FONT),
        )

    hit_max = max((v for v in hits if v > 0), default=1.0)
    fig.update_yaxes(
        range=[0, hit_max * 1.15 + 0.5], title_text="Hit counts", row=2, col=1,
        tickfont=dict(size=LBW_PANEL_TICK_FONT),
    )

    x_kw = dict(
        range=x_range,
        tickmode="array",
        tickvals=x_ticks,
        tickfont=dict(size=LBW_PANEL_TICK_FONT),
        showgrid=False,
    )
    fig.update_xaxes(**x_kw, showticklabels=False, row=1, col=1)
    fig.update_xaxes(**x_kw, title_text="ch", row=2, col=1)
    for r in (1, 2):
        _add_lbw_grid_vlines(fig, major_grid, minor_grid, r)

    fig.update_layout(
        height=height,
        margin=dict(l=42, r=14, t=38, b=24),
        showlegend=False,
    )
    fig.update_annotations(font_size=LBW_PANEL_TITLE_FONT)
    return fig


def error_bit_tick_label(bit_1based: int) -> str:
    """X-axis label: `[n]-[name]` if named, otherwise just `n`."""
    bit = int(bit_1based)
    name = EVENT_ERROR_BIT_NAMES.get(bit)
    return f"{bit}-{name}" if name else str(bit)


def decode_event_error_bit_numbers(word: int) -> list[int]:
    """1-based bit indices that are set in an EventErrorBit word."""
    word = int(word)
    return [bit for bit in range(1, N_EVENT_ERROR_BITS + 1) if word & (1 << (bit - 1))]


def decode_event_error_bits(word: int) -> list[str]:
    """Return x-axis-style labels for each set 1-based EventErrorBit."""
    return [error_bit_tick_label(bit) for bit in decode_event_error_bit_numbers(word)]


def full_event_status_name(status_code) -> str:
    if status_code is None:
        return "--"
    code = int(status_code)
    return FULL_EVENT_STATUS_NAMES.get(code, f"status_{code}")


def make_error_bit_counts_figure(
    counts,
    n_error_events=None,
    *,
    event_error_bit_word=None,
    height: int = ERROR_BIT_CHART_HEIGHT,
) -> go.Figure:
    """Full-width 32-bin EventErrorBit counts from LBW `error_bit_words`."""
    _ = event_error_bit_word
    if counts is None:
        return _empty_figure(
            "LBW readout error bit summary",
            "no error_bit_words in this LBW packet",
            height=height,
        )

    arr = np.asarray(counts, dtype=float).ravel()
    if arr.size == 0:
        return _empty_figure(
            "LBW readout error bit summary",
            "no error_bit_words in this LBW packet",
            height=height,
        )
    if arr.size < N_EVENT_ERROR_BITS:
        arr = np.pad(arr, (0, N_EVENT_ERROR_BITS - int(arr.size)))
    arr = arr[:N_EVENT_ERROR_BITS]

    xs = list(range(1, N_EVENT_ERROR_BITS + 1))
    ticktext = [error_bit_tick_label(b) for b in xs]
    y_max = float(np.nanmax(arr)) if arr.size else 0.0

    fig = go.Figure(
        data=[
            go.Bar(
                x=xs,
                y=arr.tolist(),
                marker=dict(color="#3b82f6"),
                width=0.72,
                showlegend=False,
                hovertemplate="bit %{x}: %{y}<extra></extra>",
            )
        ]
    )

    title = "LBW readout error bit summary"
    if n_error_events is not None:
        title = f"LBW readout error bit summary    n_sample = {int(n_error_events)}"

    fig.update_layout(
        title=dict(text=title, font=dict(size=12)),
        height=height,
        margin=dict(l=48, r=12, t=36, b=78),
        bargap=0.22,
        showlegend=False,
    )
    fig.update_xaxes(
        tickmode="array",
        tickvals=xs,
        ticktext=ticktext,
        tickangle=-50,
        range=[0.4, N_EVENT_ERROR_BITS + 0.6],
        tickfont=dict(size=8),
        showgrid=False,
        zeroline=False,
        automargin=True,
    )
    ymax = int(np.ceil(max(1.0, y_max * 1.15))) if y_max > 0 else 1
    if ymax <= 10:
        dtick = 1
    elif ymax <= 20:
        dtick = 2
    else:
        dtick = max(1, int(np.ceil(ymax / 6)))
    fig.update_yaxes(
        title_text="counts",
        rangemode="tozero",
        range=[0, ymax],
        dtick=dtick,
        tick0=0,
        tickformat="d",
        gridcolor="#eee",
        zeroline=True,
    )
    return fig


def _empty_figure(title: str, message: str, height: int = HEATMAP_HEIGHT) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        xref="paper",
        yref="paper",
        x=0.5,
        y=0.5,
        showarrow=False,
        font=dict(size=12),
    )
    fig.update_layout(
        title=dict(text=title, font=dict(size=11)),
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        margin=COMPACT_MARGIN,
        height=height,
    )
    return fig

def make_qt_figure(charge_slots_dict, window_size=200, restrict_window=True):
    """Generates the X Position vs Time event display."""
    coords = coord_mapping()

    if not charge_slots_dict:
        # Re-using your existing helper function for empty plots
        from plot_utils import _empty_figure 
        return _empty_figure("X Position vs Time", "(no data)")
    
    charge_slots = pd.DataFrame(charge_slots_dict)

    slot_thirteen = np.vstack(np.array(charge_slots[13]))
    slot_fourteen = np.vstack(np.array(charge_slots[14]))
    slot_fifteen = np.vstack(np.array(charge_slots[15]))

    all_channels = np.vstack((slot_thirteen,slot_fourteen,slot_fifteen))

    all_channels = all_channels - np.median(all_channels, axis=1)[:,None]

    #Get max ADC index:
    flat_max_ADC_idx = np.argmax(all_channels)
    max_row_idx, max_col_idx = np.unravel_index(flat_max_ADC_idx, all_channels.shape)

    #RESTRICT TIME WINDOW:
    if restrict_window:
        #Snip ADC timestamps to only be 200 samples + / - the maximum ADC value's timestamp:
        all_channels = all_channels[:,np.max((max_col_idx-(window_size//4),0)): max_col_idx + window_size]

    #all_channels is ordered according to channels, 0:191, as is coords.
    #We will simply snip the missing channels from both arrays, preserving the ordering:
    missing_channel_mask = ~((coords[:,1] == -159) & (coords[:,2] == -159))

    all_channels = all_channels[missing_channel_mask,:]
    coords = coords[missing_channel_mask,:]

    x_mask = (coords[:,1] != -159)
    x_coords = coords[:,1][x_mask]
    x_ADC = all_channels[x_mask,:]
    x_ADC_max = x_ADC.max()

    y_mask = (coords[:,2] != -159)
    y_coords = coords[:,2][y_mask]
    y_ADC = all_channels[y_mask,:]
    y_ADC_max = y_ADC.max()

    #Finally, sort the x and y coords in ascending order:
    x_sorter = np.argsort(x_coords)
    y_sorter = np.argsort(y_coords)
    
    x_coords = x_coords[x_sorter]
    x_ADC = x_ADC[x_sorter,:]
    
    y_coords = y_coords[y_sorter]
    y_ADC = y_ADC[y_sorter,:]

    #Let's add in a list of 0 ADC values at the missing coordinates:

    x_diffs = np.diff(x_coords)
    missing_idx = np.where(x_diffs != 1)
    for idx in missing_idx:
        x_coords = np.insert(x_coords, idx+1, x_coords[idx]+1)
        x_ADC = np.insert(x_ADC, idx+1, np.full(x_ADC.shape[1], 0), axis=0)

    y_diffs = np.diff(y_coords)
    missing_idx = np.where(y_diffs != 1)
    for idx in missing_idx:
        y_coords = np.insert(y_coords, idx+1, y_coords[idx]+1)
        y_ADC = np.insert(y_ADC, idx+1, np.full(y_ADC.shape[1], 0), axis=0)

    ADC_max = np.max((x_ADC_max,y_ADC_max))

    t = np.arange(0,all_channels.shape[1],1)

    fig = make_subplots(
        rows=1, cols=2, 
        shared_yaxes=True,
        #shared_xaxes=True,           # Locks the x-axis panning/zooming together
        horizontal_spacing=0.1,       # Gap between the two plots
        subplot_titles=("x vs t", "y vs t") # Optional
    )

    fig.add_trace(
        go.Heatmap(
            z=x_ADC.transpose(),
            x=x_coords,
            y=t,
            zmax=ADC_max,
            zmid=0,
            zmin=-ADC_max,
            colorscale=[
                        [0.0, '#35DFE5'],
                        [0.35, '#4261FF'],
                        [0.5, '#000000'],
                        [0.65, '#FF0000'],
                        [0.85, '#FFAE00'],
                        [1.0, '#FFFFFF']
                        ],
            hovertemplate="x: %{x}<br>t: %{y}<br>ADC: %{z}<extra></extra>",
            showscale=False
        ),
        row=1, col=1
    )

    fig.add_trace(
        go.Heatmap(
            z=y_ADC.transpose(),
            x=y_coords,
            y=t,
            zmax=ADC_max,
            zmid=0,
            zmin=-ADC_max,
            colorscale=[
                        [0.0, '#35DFE5'],
                        [0.35, '#4261FF'],
                        [0.5, '#000000'],
                        [0.65, '#FF0000'],
                        [0.85, '#FFAE00'],
                        [1.0, '#FFFFFF']
                        ],
            colorbar=dict(title="ADC", len=0.8, thickness=12, y=-0.35, orientation='h', title_side='top'),
            hovertemplate="y: %{x}<br>t: %{y}<br>ADC: %{z}<extra></extra>"
        ),
        row=1, col=2
    )

    fig.update_layout(
        title="Position (wire spacings) vs Time (2MHz Sample Number)",
        margin=dict(l=48, r=12, t=80, b=32) 
    )
    fig.update_yaxes(title_text="2MHz Sample Number", row=1, col=1)
    fig.update_xaxes(title_text="x", row=1, col=1)
    fig.update_xaxes(title_text="y", row=1, col=2)

    return fig

def make_qt_figure_testing(charge_slots_dict, window_size=200, restrict_window=True):
    """Generates the X Position vs Time event display."""
    coords = coord_mapping()

    if not charge_slots_dict:
        # Re-using your existing helper function for empty plots
        from plot_utils import _empty_figure 
        return _empty_figure("X Position vs Time", "(no data)")
    
    charge_slots = pd.DataFrame(charge_slots_dict)

    slot_thirteen = np.vstack(np.array(charge_slots[13]))
    slot_fourteen = np.vstack(np.array(charge_slots[14]))
    slot_fifteen = np.vstack(np.array(charge_slots[15]))

    all_channels = np.vstack((slot_fourteen,slot_fifteen,slot_thirteen))

    all_channels = all_channels - np.median(all_channels, axis=1)[:,None]

    #Get max ADC index:
    flat_max_ADC_idx = np.argmax(all_channels)
    max_row_idx, max_col_idx = np.unravel_index(flat_max_ADC_idx, all_channels.shape)

    #RESTRICT TIME WINDOW:
    if restrict_window:
        #Snip ADC timestamps to only be 200 samples + / - the maximum ADC value's timestamp:
        all_channels = all_channels[:,np.max((max_col_idx-(window_size//4),0)): max_col_idx + window_size]

    #all_channels is ordered according to channels, 0:191, as is coords.
    #We will simply snip the missing channels from both arrays, preserving the ordering:
    missing_channel_mask = ~((coords[:,1] == -159) & (coords[:,2] == -159))

    all_channels = all_channels[missing_channel_mask,:]
    coords = coords[missing_channel_mask,:]

    x_mask = (coords[:,1] != -159)
    x_coords = coords[:,1][x_mask]
    x_ADC = all_channels[x_mask,:]
    x_ADC_max = x_ADC.max()

    y_mask = (coords[:,2] != -159)
    y_coords = coords[:,2][y_mask]
    y_ADC = all_channels[y_mask,:]
    y_ADC_max = y_ADC.max()

    #Finally, sort the x and y coords in ascending order:
    x_sorter = np.argsort(x_coords)
    y_sorter = np.argsort(y_coords)
    
    x_coords = x_coords[x_sorter]
    x_ADC = x_ADC[x_sorter,:]
    
    y_coords = y_coords[y_sorter]
    y_ADC = y_ADC[y_sorter,:]

    #Let's add in a list of 0 ADC values at the missing coordinates:

    x_diffs = np.diff(x_coords)
    missing_idx = np.where(x_diffs != 1)
    for idx in missing_idx:
        x_coords = np.insert(x_coords, idx+1, x_coords[idx]+1)
        x_ADC = np.insert(x_ADC, idx+1, np.full(x_ADC.shape[1], 0), axis=0)

    y_diffs = np.diff(y_coords)
    missing_idx = np.where(y_diffs != 1)
    for idx in missing_idx:
        y_coords = np.insert(y_coords, idx+1, y_coords[idx]+1)
        y_ADC = np.insert(y_ADC, idx+1, np.full(y_ADC.shape[1], 0), axis=0)

    ADC_max = np.max((x_ADC_max,y_ADC_max))

    t = np.arange(0,all_channels.shape[1],1)

    fig = make_subplots(
        rows=1, cols=2, 
        shared_yaxes=True,
        #shared_xaxes=True,           # Locks the x-axis panning/zooming together
        horizontal_spacing=0.1,       # Gap between the two plots
        subplot_titles=("x vs t", "y vs t") # Optional
    )

    fig.add_trace(
        go.Heatmap(
            z=x_ADC.transpose(),
            x=x_coords,
            y=t,
            zmax=ADC_max,
            zmid=0,
            zmin=-ADC_max,
            colorscale=[
                        [0.0, '#35DFE5'],
                        [0.35, '#4261FF'],
                        [0.5, '#000000'],
                        [0.65, '#FF0000'],
                        [0.85, '#FFAE00'],
                        [1.0, '#FFFFFF']
                        ],
            hovertemplate="x: %{x}<br>t: %{y}<br>ADC: %{z}<extra></extra>",
            showscale=False
        ),
        row=1, col=1
    )

    fig.add_trace(
        go.Heatmap(
            z=y_ADC.transpose(),
            x=y_coords,
            y=t,
            zmax=ADC_max,
            zmid=0,
            zmin=-ADC_max,
            colorscale=[
                        [0.0, '#35DFE5'],
                        [0.35, '#4261FF'],
                        [0.5, '#000000'],
                        [0.65, '#FF0000'],
                        [0.85, '#FFAE00'],
                        [1.0, '#FFFFFF']
                        ],
            colorbar=dict(title="ADC", len=0.8, thickness=12, y=-0.35, orientation='h', title_side='top'),
            hovertemplate="y: %{x}<br>t: %{y}<br>ADC: %{z}<extra></extra>"
        ),
        row=1, col=2
    )

    fig.update_layout(
        title="Position (wire spacings) vs Time (2MHz Sample Number)",
        margin=dict(l=48, r=12, t=80, b=32) 
    )
    fig.update_yaxes(title_text="2MHz Sample Number", row=1, col=1)
    fig.update_xaxes(title_text="x", row=1, col=1)
    fig.update_xaxes(title_text="y", row=1, col=2)

    return fig

def make_qt_figure_horizontal(charge_slots_dict, window_size=200):
    """Generates the X Position vs Time event display."""
    coords = coord_mapping()

    if not charge_slots_dict:
        # Re-using your existing helper function for empty plots
        from plot_utils import _empty_figure 
        return _empty_figure("X Position vs Time", "(no data)")
    
    charge_slots = pd.DataFrame(charge_slots_dict)

    slot_thirteen = np.vstack(np.array(charge_slots[13]))
    slot_fourteen = np.vstack(np.array(charge_slots[14]))
    slot_fifteen = np.vstack(np.array(charge_slots[15]))

    all_channels = np.vstack((slot_thirteen,slot_fourteen,slot_fifteen))

    all_channels = all_channels - np.median(all_channels, axis=1)[:,None]

    #Get max ADC index:
    flat_max_ADC_idx = np.argmax(all_channels)
    max_row_idx, max_col_idx = np.unravel_index(flat_max_ADC_idx, all_channels.shape)

    #RESTRICT TIME WINDOW:
    #Snip ADC timestamps to only be 200 samples + / - the maximum ADC value's timestamp:
    all_channels = all_channels[:,np.max((max_col_idx-(window_size//4),0)): max_col_idx + window_size]

    #all_channels is ordered according to channels, 0:191, as is coords.
    #We will simply snip the missing channels from both arrays, preserving the ordering:
    missing_channel_mask = ~((coords[:,1] == -159) & (coords[:,2] == -159))

    all_channels = all_channels[missing_channel_mask,:]
    coords = coords[missing_channel_mask,:]

    x_mask = (coords[:,1] != -159)
    x_coords = coords[:,1][x_mask]
    x_ADC = all_channels[x_mask,:]
    x_ADC_max = x_ADC.max()

    y_mask = (coords[:,2] != -159)
    y_coords = coords[:,2][y_mask]
    y_ADC = all_channels[y_mask,:]
    y_ADC_max = y_ADC.max()

    #Finally, sort the x and y coords in ascending order:
    x_sorter = np.argsort(x_coords)
    y_sorter = np.argsort(y_coords)
    
    x_coords = x_coords[x_sorter]
    x_ADC = x_ADC[x_sorter,:]
    
    y_coords = y_coords[y_sorter]
    y_ADC = y_ADC[y_sorter,:]

    ADC_max = np.max((x_ADC_max, y_ADC_max))

    t = np.arange(0,all_channels.shape[1],1)

    fig = make_subplots(
        rows=2, cols=1, 
        #shared_yaxes=True,
        #shared_xaxes=True,           # Locks the x-axis panning/zooming together
        vertical_spacing=0.15,       # Gap between the two plots
        subplot_titles=("x vs t", "y vs t") # Optional
    )

    fig.add_trace(
        go.Heatmap(
            z=x_ADC,
            x=t,
            y=x_coords,
            zmax=ADC_max,
            zmid=0,
            zmin=-ADC_max,
            colorscale=[
                        [0.0, '#35DFE5'],
                        [0.35, '#4261FF'],
                        [0.5, '#000000'],
                        [0.65, '#FF0000'],
                        [0.85, '#FFAE00'],
                        [1.0, '#FFFFFF']
                        ],
            colorbar=dict(title="ADC", len=0.8, thickness=12, y=0.8),
            hovertemplate="t: %{x}<br>x: %{y}<br>ADC: %{z}<extra></extra>"
        ),
        row=1, col=1
    )

    fig.add_trace(
        go.Heatmap(
            z=y_ADC,
            x=t,
            y=y_coords,
            zmax=ADC_max,
            zmid=0,
            zmin=-ADC_max,
            colorscale=[
                        [0.0, '#35DFE5'],
                        [0.35, '#4261FF'],
                        [0.5, '#000000'],
                        [0.65, '#FF0000'],
                        [0.85, '#FFAE00'],
                        [1.0, '#FFFFFF']
                        ],
            colorbar=dict(title="ADC", len=0.8, thickness=12, y=0.15),
            hovertemplate="t: %{x}<br>y: %{y}<br>ADC: %{z}<extra></extra>"
        ),
        row=2, col=1
    )

    fig.update_layout(
        title="Position (wire spacings) vs Time (2MHz Sample Number)",
        margin=dict(l=48, r=12, t=80, b=32) 
    )
    fig.update_yaxes(title_text="2MHz Sample Number", row=1, col=1)
    fig.update_xaxes(title_text="x", row=1, col=1)
    fig.update_xaxes(title_text="y", row=1, col=2)

    return fig

def L_channel_mapping(input_channels: np.ndarray) -> np.ndarray:
    #channels = np.array([0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35])

    #Channel lookup table. Col. 0 is channel #, col. 1 is x coord, col. 2 is y coord:
    channel_mapping = np.array([[0,0,7],
                                [1,0,6],
                                [2,1,7],
                                [3,1,6],
                                [4,3,7],
                                [5,3,6],
                                [6,4,7],
                                [7,6,7],
                                [8,6,6],
                                [9,7,7],
                                [10,7,6],
                                [11,6,4],
                                [12,6,3],
                                [13,7,4],
                                [14,3,4],
                                [15,3,3],
                                [16,4,4],
                                [17,4,3],
                                [18,0,4],
                                [19,0,3],
                                [20,1,4],
                                [21,0,1],
                                [22,0,0],
                                [23,1,1],
                                [24,1,0],
                                [25,3,1],
                                [26,3,0],
                                [27,4,1],
                                [28,6,1],
                                [29,6,0],
                                [30,7,1],
                                [31,7,0],
                                [32,4,6],
                                [33,7,3],
                                [34,1,3],
                                [35,4,0]])

    mask = np.isin(channel_mapping[:,0], input_channels)
    coords = channel_mapping[mask,:]
    return coords


def make_lt_figure(light, t=0):

    #Taken from Yinrui's code:
    fired = [
        ch
        for ch, rois in light.items()
        if any(len(r["samples"]) > 0 for r in rois)
    ]
    if not fired:
        return _empty_figure('PMT Position and Charge at Sample N/A"', "(no data)")

    #channels = np.array(list(light.keys()))[:,None]
    #positions = L_channel_mapping(channels)
    positions = L_channel_mapping(np.array(list(light.keys()))[:,None])

    ADC_time = []
    n_samps = []

    for ch, rois in light.items():
        
        for roi in rois:
            ADC_time.append([ch, roi['start_sample']] + [ADC for ADC in roi['samples']])
            n_samps.append(roi['samples'].size)

     #Ensure same # of samples for each ROI (snip extra samples):
    min_n_samps = min(n_samps)

    for i, roi in enumerate(ADC_time):
        ADC_time[i] = roi[:min_n_samps]

    ADC_time = np.array(ADC_time).astype('f8')

    start_times = ADC_time[:,1][:,None]

    #ADC_time[:,1] = ADC_time[:,1] - time_center

    #Yields an array of times, each row corresponds to the corresponding channel_roi-row in ADC_time. This gives us a correspondance b/w time ticks and charge in ADC_time:
    times = np.tile(np.arange(0, ADC_time[:,2:].shape[1]), (ADC_time.shape[0],1)) + start_times

    bin_edges = np.linspace(times.min(), times.max(), 1000)

    #Create an ADC histogram in each ROW. Each row is a ROI / channel. Direct row-correspondance with ADC_time:
    histogram = []
    for row in range(times.shape[0]):
        histogram.append(np.histogram(times[row], bins = bin_edges, weights = ADC_time[row,2:])[0])
    
    histogram = np.array(histogram)

    times = bin_edges[:-1]

    #Make the positions array have the correct number of rows:
    pos_arr = []

    for channel in ADC_time[:,0]:
        mask = (positions[:,0] == channel)
        pos_arr.append(positions[mask][0][1:])

    pos_arr = np.array(pos_arr)

    fig = go.Figure()

    min_ADC = histogram.min()
    max_ADC = histogram.max()

    t_idx = max(0, min(t, histogram.shape[1] - 1))

    all_coords = L_channel_mapping(np.arange(0,36))

    # VUV_mask = np.isin(pos_arr[:,1], np.array((1,4,7)))
    # VIS_mask = ~VUV_mask

    fig.add_trace(go.Scatter(
        x=all_coords[:,1],
        y=all_coords[:,2],
        mode='markers',
        marker=dict(
            symbol='square',
            size=30,
            showscale=False,
            color='#440154'
        ),
        text=[f"Channel: {int(ch)}" for ch in positions[:, 0]],
        hovertemplate="X: %{x}<br>Y: %{y}<br>Charge: 0.00<extra></extra>"
    ))

    fig.add_trace(go.Scatter(
        x=pos_arr[:, 0],
        y=pos_arr[:, 1],
        mode='markers',
        marker=dict(
            symbol='square',
            size=30,
            color=histogram[:,t_idx],
            colorscale='Viridis', # You can change this to 'Plasma', 'Inferno', etc.
            showscale=True,
            cmin=min_ADC,
            cmax=max_ADC,
            colorbar=dict(title="ADC")
        ),
        text=[f"Channel: {int(ch)}" for ch in positions[:, 0]],
        hovertemplate="X: %{x}<br>Y: %{y}<br>Charge: %{marker.color:.2f}<br>%{text}<extra></extra>"
    ))

    fig.update_layout(
    xaxis=dict(range=[-1, 8]),
    yaxis=dict(range=[-1, 8]),
    title=f"PMT Position and Charge\n({(bin_edges[:-1][t]/64):.2f} < t < {(bin_edges[1:][t]/64):.2f})",
    width=600,
    height=600,
    showlegend=False,
    )

    # fig = go.Figure()

    # fig.add_trace(
    #         go.Scatter(
    #             x=np.arange(raw.size), y=raw, mode="lines",
    #             line=dict(width=1, color=color), showlegend=False,
    #         ),
    #         row=r, col=1,
    #     )


    return fig