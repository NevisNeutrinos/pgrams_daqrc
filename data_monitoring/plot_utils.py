"""Heatmap builders adapted from plot_event_adc.ipynb."""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

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
# Minimum half-range for Q ped-sub heatmaps: zlim = max(max(|ADC-med|), Z_FLOOR).
Q_CHARGE_Z_FLOOR = 5.0


def charge_heatmap_zlim(img_sub: np.ndarray) -> float:
    finite = img_sub[np.isfinite(img_sub)]
    if finite.size == 0:
        return Q_CHARGE_Z_FLOOR
    return max(float(np.max(np.abs(finite))), Q_CHARGE_Z_FLOOR)

# Offline Q hit count: samples above median + max(Q_HIT_SIGMA_MULT * RMS, Q_HIT_MIN_ADC).
Q_HIT_SIGMA_MULT = 3.0
Q_HIT_MIN_ADC = 1.0


def compute_charge_lbw(
    charge_slots: dict[int, dict[int, np.ndarray]],
    q_slots: list[int],
    channels_per_slot: int = N_QFEM_CHANNELS,
) -> tuple[list[float], list[float], list[float]]:
    """Per-channel baseline (median), RMS (std), and hit count from Q-FEM waveforms.

    Hits: samples with |ADC − median| > max(3*RMS, 1 ADC).
    """
    baselines: list[float] = []
    rmses: list[float] = []
    hits: list[float] = []
    for slot in q_slots:
        channels = charge_slots.get(slot, {})
        for ch in range(channels_per_slot):
            if ch in channels and len(channels[ch]) > 0:
                arr = np.asarray(channels[ch], dtype=np.float64)
                b = float(np.median(arr))
                r = float(np.std(arr))
                delta = max(Q_HIT_SIGMA_MULT * r, Q_HIT_MIN_ADC)
                h = float(np.sum(np.abs(arr - b) > delta))
            else:
                b, r, h = 0.0, 0.0, 0.0
            baselines.append(b)
            rmses.append(r)
            hits.append(h)
    return baselines, rmses, hits


def compute_light_lbw(
    light_channels: dict[int, list[dict]],
    nchan: int = N_LIGHT_CHANNELS,
) -> tuple[list[float], list[float], list[float]]:
    """Per-channel baseline (median), RMS (std), and ROI count from L-FEM data."""
    baselines: list[float] = []
    rmses: list[float] = []
    hits: list[float] = []
    for ch in range(nchan):
        rois = light_channels.get(ch, [])
        active = [r for r in rois if len(r["samples"]) > 0]
        if active:
            all_samples = np.concatenate(
                [np.asarray(r["samples"], dtype=np.float64) for r in active]
            )
            b = float(np.median(all_samples))
            r = float(np.std(all_samples))
            h = float(len(active))
        else:
            b, r, h = 0.0, 0.0, 0.0
        baselines.append(b)
        rmses.append(r)
        hits.append(h)
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
) -> go.Figure:
    """Q-FEM heatmap on the shared 4-frame (0..512 us) axis.

    The buffer is trigger-aligned (``Q_PRETRIGGER_SAMPLES`` before the trigger).
    ``trig_sample`` is the 2 MHz sample# from this FEM's FEMHeader6; together with
    the fixed pre-trigger count it places the 3-frame readout inside the same
    [trig_frame-1 .. trig_frame+2] window used for L-FEM.
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
    x_us = [t0 + i * Q_US_PER_SAMPLE for i in range(nsamp)]
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
        _add_trigger_line(fig, t0 + trigger_x * Q_US_PER_SAMPLE)

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
    """Two-row panel: median±σ (top) + hits (bottom), one figure per FEM."""
    n = len(baseline)
    x = list(range(n))
    x_range, x_ticks = _lbw_channel_axis(n, is_light)
    major_grid, minor_grid = _lbw_grid_positions(n, is_light)
    hit_title = (
        f"{slot_label} hits (ROIs/ch)"
        if is_light
        else f"{slot_label} hits (|ADC−med|>max({Q_HIT_SIGMA_MULT:g}σ,{Q_HIT_MIN_ADC:g}))"
    )
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.56, 0.44], vertical_spacing=0.10,
        subplot_titles=(f"{slot_label} median±σ", hit_title),
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


def _empty_figure(title: str, message: str) -> go.Figure:
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
        height=HEATMAP_HEIGHT,
    )
    return fig
