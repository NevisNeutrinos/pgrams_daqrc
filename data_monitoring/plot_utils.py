"""Heatmap builders adapted from plot_event_adc.ipynb."""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

N_QFEM_CHANNELS = 64
N_LIGHT_CHANNELS = 36
SAMPLES_PER_FRAME = 256

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
    label = "trigger"
    fig.add_vline(
        x=trigger_x,
        line_width=2,
        line_color="limegreen",
        opacity=0.9,
    )
    fig.add_annotation(
        x=trigger_x,
        y=1.02,
        yref="paper",
        text=label,
        showarrow=False,
        font=dict(size=9, color="green"),
        xanchor="center",
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
            z=img_sub[::-1],
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
        xaxis_title="sample",
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
            z=img[::-1],
            x=[xmin + i for i in range(width)],
            y=list(range(N_LIGHT_CHANNELS)),
            colorscale="YlOrRd",
            zmin=vmin,
            zmax=vmax,
            colorbar=dict(title="ADC", len=1.0, thickness=14),
        )
    )
    first_line = ((xmin + SAMPLES_PER_FRAME - 1) // SAMPLES_PER_FRAME) * SAMPLES_PER_FRAME
    for x in range(first_line, xmin + width, SAMPLES_PER_FRAME):
        fig.add_vline(x=x, line_width=1, line_dash="dash", line_color="black", opacity=0.45)
    _add_trigger_line(fig, trigger_x)

    fig.update_layout(
        title=dict(text=f"{title} ({len(fired)} ch, {n_rois} ROIs)", font=dict(size=11)),
        xaxis_title="2 MHz tick",
        yaxis_title="ch",
        margin=COMPACT_MARGIN,
        height=HEATMAP_HEIGHT,
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
