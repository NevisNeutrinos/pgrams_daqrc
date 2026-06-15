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

COMPACT_MARGIN = dict(l=48, r=12, t=36, b=32)
HEATMAP_HEIGHT = 290
LBW_HEIGHT = 92
# Matches plot_event_adc.ipynb: VMIN, VMAX = -5, 5
Q_CHARGE_VMIN = -5.0
Q_CHARGE_VMAX = 5.0


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
) -> tuple[np.ndarray, int, int]:
    xmin, xmax = light_sample_extent(light_channels)
    xmin = max(0, xmin - pad)
    xmax = xmax + pad
    width = xmax - xmin + 1
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

    return img, xmin, xmax + pad


def _add_trigger_line(fig: go.Figure, trigger_x: int | None, *, absolute: bool = False):
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


def make_charge_heatmap_figure(
    channels: dict[int, np.ndarray],
    title: str = "Q-FEM",
    trigger_x: int | None = None,
) -> go.Figure:
    img_sub, nchan, nsamp = build_charge_heatmap(channels)
    if not channels:
        return _empty_figure(title, "(no data)")

    fig = go.Figure(
        data=go.Heatmap(
            z=img_sub,
            x=list(range(nsamp)),
            y=list(range(nchan)),
            colorscale="RdBu_r",
            zmid=0,
            zmin=Q_CHARGE_VMIN,
            zmax=Q_CHARGE_VMAX,
            colorbar=dict(title="ADC-med", len=1.0, thickness=14),
        )
    )
    for x in range(SAMPLES_PER_FRAME, nsamp, SAMPLES_PER_FRAME):
        fig.add_vline(x=x, line_width=1, line_dash="dash", line_color="black", opacity=0.45)
    _add_trigger_line(fig, trigger_x)

    fig.update_layout(
        title=dict(text=f"{title} ({nchan}ch x {nsamp} smp, ped-sub)", font=dict(size=11)),
        xaxis_title="sample (0.5 \u03bcs each time tick)",
        yaxis_title="ch",
        margin=COMPACT_MARGIN,
        height=HEATMAP_HEIGHT,
    )
    return fig


def make_light_heatmap_figure(
    light_channels: dict[int, list[dict]],
    title: str = "L-FEM",
    trigger_x: int | None = None,
) -> go.Figure:
    fired = [
        ch
        for ch, rois in light_channels.items()
        if any(len(r["samples"]) > 0 for r in rois)
    ]
    if not fired:
        return _empty_figure(title, "(no data)")

    img, xmin, xmax = build_light_heatmap(light_channels)
    finite = img[np.isfinite(img)]
    vmin = float(np.percentile(finite, 2)) if finite.size else 2000.0
    vmax = float(np.percentile(finite, 98)) if finite.size else 2200.0
    if vmax <= vmin:
        vmax = vmin + 1.0

    width = img.shape[1]
    n_rois = sum(len(light_channels[c]) for c in fired)
    fig = go.Figure(
        data=go.Heatmap(
            z=img,
            x=[xmin + i for i in range(width)],
            y=list(range(N_LIGHT_CHANNELS)),
            colorscale="YlOrRd",
            zmin=vmin,
            zmax=vmax,
            colorbar=dict(title="ADC", len=1.0, thickness=14),
        )
    )
    first_line = ((xmin + LIGHT_TICKS_PER_FRAME - 1) // LIGHT_TICKS_PER_FRAME) * LIGHT_TICKS_PER_FRAME
    for x in range(first_line, xmin + width, LIGHT_TICKS_PER_FRAME):
        fig.add_vline(x=x, line_width=1, line_dash="dash", line_color="black", opacity=0.45)
    _add_trigger_line(fig, trigger_x)

    fig.update_layout(
        title=dict(text=f"{title} ({len(fired)} ch, {n_rois} ROIs)", font=dict(size=11)),
        xaxis_title="sample (15.6 ns each time tick)",
        yaxis_title="ch",
        margin=COMPACT_MARGIN,
        height=HEATMAP_HEIGHT,
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
    top_len = domains[0][1] - domains[0][0]
    fig.add_trace(
        go.Heatmap(
            z=img_sub,
            x=list(range(nsamp)),
            y=list(range(nchan)),
            colorscale="RdBu_r",
            zmid=0,
            zmin=Q_CHARGE_VMIN,
            zmax=Q_CHARGE_VMAX,
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

    # Gray frame boundaries (every 256 samples) on every row.
    boundaries = list(range(SAMPLES_PER_FRAME, nsamp, SAMPLES_PER_FRAME))
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
    # X-axis label under the heatmap (the larger first gap keeps it clear of row 2).
    fig.update_xaxes(title_text="sample (0.5 \u03bcs each time tick)", row=1, col=1)
    fig.update_layout(
        title=dict(text=title, font=dict(size=12)),
        margin=dict(l=58, r=14, t=WF_MARGIN_T, b=WF_MARGIN_B),
        height=height,
        showlegend=False,
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

    def _frame_boundaries(lo: int, hi: int) -> list[int]:
        k0 = (lo + 8192 - 1) // 8192
        return [k * 8192 for k in range(k0, hi // 8192 + 1) if lo <= k * 8192 <= hi]

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
    ov_lo = max(0, int(ov_min) - LIGHT_EDGE_PAD)
    ov_hi = min(LIGHT_WINDOW_TICKS, int(ov_max) + LIGHT_EDGE_PAD)
    oy0, oy1 = _yspan(ov_ylo, ov_yhi)
    fig.update_xaxes(range=[ov_lo, ov_hi], row=1, col=1)
    fig.update_yaxes(range=[oy0, oy1], row=1, col=1)
    row_ranges: list[tuple[int, int]] = [(ov_lo, ov_hi)]
    row_yranges: list[tuple[float, float] | None] = [(oy0, oy1)]

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
        fig.update_xaxes(range=[lo, hi], row=r, col=1)
        fig.update_yaxes(range=[ry0, ry1], title_text=f"ch{ch}", row=r, col=1)
        row_ranges.append((lo, hi))
        row_yranges.append((ry0, ry1))
        # ROI start / end markers (light gray, original style).
        for xe in roi_edges:
            _vline(xe, ry0, ry1, r, color="#999", width=0.6, opacity=0.6)

    # Frame boundaries (every 8192 ticks) on every row, where in range.
    for r in range(1, rows + 1):
        yr = row_yranges[r - 1]
        if yr is None:
            continue
        lo, hi = row_ranges[r - 1]
        for x in _frame_boundaries(lo, hi):
            _vline(x, yr[0], yr[1], r, color="black", width=1, dash="dash", opacity=0.5)

    if trigger_x is not None:
        _vline(trigger_x, oy0, oy1, 1, color="limegreen", width=1.5, dash="dash")
        fig.add_annotation(
            x=trigger_x, y=1.0, xref="x", yref="y domain", yshift=12, text="trigger",
            showarrow=False, font=dict(size=9, color="green"),
            xanchor="center", yanchor="bottom",
        )
        for r in range(2, rows + 1):
            yr = row_yranges[r - 1]
            lo, hi = row_ranges[r - 1]
            if yr is not None and lo <= trigger_x <= hi:
                _vline(trigger_x, yr[0], yr[1], r, color="limegreen", width=1.5, dash="dash")

    _apply_domains(fig, domains)
    # X-axis label under the overlay (the larger first gap keeps it clear of row 2).
    fig.update_xaxes(title_text="sample (15.6 ns each time tick)", row=1, col=1)
    fig.update_layout(
        title=dict(text=title, font=dict(size=12), x=0.01, xanchor="left"),
        margin=dict(l=58, r=104, t=WF_MARGIN_T, b=WF_MARGIN_B),
        height=height,
        legend=dict(
            font=dict(size=13), orientation="v", x=1.005, xanchor="left",
            y=1.0, yanchor="top", itemsizing="constant",
        ),
    )
    return fig


def make_compact_bar(y, title: str, y_title: str = "", height: int | None = None) -> go.Figure:
    h = height or LBW_HEIGHT
    fig = go.Figure()
    fig.add_bar(x=list(range(len(y))), y=y, marker_line_width=0)
    fig.update_layout(
        title=dict(text=title, font=dict(size=10)),
        xaxis_title="ch",
        yaxis_title=y_title,
        margin=dict(l=40, r=4, t=26, b=20),
        height=h,
        showlegend=False,
    )
    fig.update_xaxes(tickmode="linear", dtick=16, tickfont=dict(size=8))
    return fig


def make_compact_bar_with_error(
    baseline, std_dev, title: str, y_title: str = "", height: int | None = None
) -> go.Figure:
    h = height or LBW_HEIGHT
    fig = go.Figure()
    fig.add_bar(
        x=list(range(len(baseline))),
        y=baseline,
        error_y=dict(type="data", array=std_dev, visible=True, thickness=0.8, width=2),
        marker_line_width=0,
    )
    fig.update_layout(
        title=dict(text=title, font=dict(size=10)),
        xaxis_title="ch",
        yaxis_title=y_title,
        margin=dict(l=40, r=4, t=26, b=20),
        height=h,
        showlegend=False,
    )
    fig.update_xaxes(tickmode="linear", dtick=16, tickfont=dict(size=8))
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
