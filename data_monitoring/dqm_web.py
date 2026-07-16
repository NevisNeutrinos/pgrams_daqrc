"""Real-time DQM: left heatmaps, right per-FEM LBW, top controls."""

from __future__ import annotations

import re
import threading
import webbrowser

import dash
import numpy as np
import plotly.graph_objects as go
from dash import dcc, html, no_update
from dash.dependencies import Input, Output, State

from data_monitoring.event_source import LIGHT_SLOT_DEFAULT, Q_SLOTS_DEFAULT, EventRecord, load_event

DEFAULT_HEX_FILE = "data_files/pGRAMS_bin_441_0_evt100.txt"

TAB_BTN = {
    "padding": "5px 12px",
    "border": "1px solid #bbb",
    "borderRadius": "4px",
    "background": "#fff",
    "cursor": "pointer",
    "fontSize": "13px",
}
TAB_BTN_ACTIVE = {**TAB_BTN, "background": "#2563eb", "color": "#fff", "borderColor": "#2563eb"}
STEP_BTN = {
    "padding": "0",
    "width": "18px",
    "height": "12px",
    "lineHeight": "10px",
    "fontSize": "8px",
    "border": "1px solid #bbb",
    "background": "#f5f5f5",
    "cursor": "pointer",
}
PANEL_HIDE = {"display": "none"}
PANEL_SHOW = {"display": "block"}
HEATMAP_GRAPH_CONFIG = {
    "displayModeBar": False,
    "doubleClick": "reset+autosize",
}
WF_GRAPH_CONFIG = {
    "displaylogo": False,
    "modeBarButtonsToRemove": ["select2d", "lasso2d"],
}
from data_monitoring.plot_utils import (
    HEATMAP_HEIGHT,
    compute_charge_lbw,
    compute_light_lbw,
    make_charge_heatmap_figure,
    make_lbw_panel_figure,
    make_lfem_waveform_figure,
    make_light_heatmap_figure,
    make_qfem_waveform_figure,
    make_qt_figure,
    make_qt_figure_horizontal,
    make_lt_figure
)


def _parse_channels(spec: str | None) -> tuple[list[int], bool]:
    """Parse a channel spec into (channels, is_range).

    - "[a,b]" -> inclusive range a..b, is_range=True (shade gray on heatmap).
    - "0,5,12" -> explicit list, is_range=False (per-channel colored bands).
    """
    if not spec:
        return [], False
    s = spec.strip()
    m = re.fullmatch(r"\[\s*(\d+)\s*,\s*(\d+)\s*\]", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        lo, hi = min(a, b), max(a, b)
        return list(range(lo, hi + 1)), True
    out: list[int] = []
    for tok in re.split(r"[,\s]+", s):
        if not tok:
            continue
        try:
            out.append(int(tok))
        except ValueError:
            pass
    return out, False

Q_SLOTS = Q_SLOTS_DEFAULT
LIGHT_SLOT = LIGHT_SLOT_DEFAULT
FEM_SLOTS = Q_SLOTS + [LIGHT_SLOT]
CHANNELS_PER_QFEM = 64
LIGHT_FRAME_MOD = 8

ERR_STYLE = {"color": "#d00", "fontWeight": "bold"}


def _err(msg: str) -> html.Span:
    """Render an error/warning status message in red."""
    return html.Span(msg, style=ERR_STYLE)


def _parse_lag(raw) -> tuple[int, int]:
    """Parse a lag input into (header_lag, adc_lag).

    A single number ("1") shifts both header and ADC. A pair ("1,0") shifts the
    header and ADC independently. Blank/invalid -> (0, 0).
    """
    if raw is None:
        return (0, 0)
    parts = [p.strip() for p in str(raw).split(",") if p.strip() != ""]
    try:
        if not parts:
            return (0, 0)
        if len(parts) == 1:
            v = int(parts[0])
            return (v, v)
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return (0, 0)


def _fmt_lag(lag: tuple[int, int]) -> str:
    h, a = lag
    return f"{h:+d}" if h == a else f"({h:+d},{a:+d})"


def _header_match_warnings(record: EventRecord) -> list[str]:
    """Flag FEMHeader6 trigger desync. L-FEM frame is mod-8, so Q/L frames are
    compared modulo 8. Cheap (a few int compares per Load), no perf impact."""
    tm = getattr(record, "trigger_meta", None) or {}
    warns: list[str] = []
    q_present = [s for s in Q_SLOTS if s in tm]
    if len(q_present) >= 2:
        q_keys = {(tm[s]["frame"], tm[s]["sample"]) for s in q_present}
        if len(q_keys) > 1:
            warns.append("Q-FEM headers not match")
    if q_present and LIGHT_SLOT in tm:
        q, l = tm[q_present[0]], tm[LIGHT_SLOT]
        if (q["frame"] % LIGHT_FRAME_MOD) != (l["frame"] % LIGHT_FRAME_MOD) or q["sample"] != l["sample"]:
            warns.append(
                f"Q-FEM and L-FEM header not match: "
                f"({q['frame']}, {q['sample']}) and ({l['frame']}, {l['sample']})"
            )
    # ROIs whose frame_num falls outside the L header's 4-frame readout window
    # cannot belong to this trigger -> stale/duplicate light data. This fires even
    # when a lag makes the headers match, flagging the event as still corrupt.
    oow = getattr(record, "light_roi_oow", 0)
    if oow > 0:
        total = getattr(record, "light_roi_total", 0)
        warns.append(f"{oow}/{total} L-FEM ROIs outside trigger window (stale/duplicate light?)")
    return warns


def _slice_q_lbw(lbw_q: tuple | None, slot_idx: int) -> tuple[list, list, list] | None:
    if lbw_q is None:
        return None
    bq, rq, hq = lbw_q
    i0 = slot_idx * CHANNELS_PER_QFEM
    i1 = i0 + CHANNELS_PER_QFEM
    b, r, h = bq[i0:i1], rq[i0:i1], hq[i0:i1]
    if len(b) < CHANNELS_PER_QFEM:
        pad = CHANNELS_PER_QFEM - len(b)
        b = list(b) + [0.0] * pad
        r = list(r) + [0.0] * pad
        h = list(h) + [0.0] * pad
    return b, r, h


def _slice_l_lbw(lbw_l: tuple | None) -> tuple[list, list, list] | None:
    if lbw_l is None:
        return None
    return lbw_l


class DqmWeb:
    def __init__(self, host: str = "127.0.0.1", port: int = 8051):
        self.host = host
        self.port = port
        self._lock = threading.Lock()

        self.lbw_charge = None
        self.lbw_light = None
        self.evt_number = None
        self.charge_slots: dict[int, dict[int, np.ndarray]] = {s: {} for s in Q_SLOTS}
        self.light_channels: dict[int, list[dict]] = {}
        self.trigger_ticks: dict[int, int] = {}
        self.trigger_meta: dict[int, dict] = {}
        self.freeze_live = False
        # Bumped after each explicit load/step so figure callbacks rebuild from the
        # freshly loaded snapshot (serializes load -> build, avoids races).
        self._load_seq = 0

        self.app = dash.Dash(__name__, suppress_callback_exceptions=True)
        # Hide the browser's native number spinner on Evt# so our custom in-box
        # up/down arrows are the only stepper. Also patch Plotly so:
        # - Reset / odd double-click: x -> meta.full_x_range (waveforms) or
        #   meta.default_ranges x (heatmaps); y stays at default_ranges y
        # - Autoscale / even double-click: when meta.autoscale_to_default,
        #   restore meta.default_ranges (L-FEM) instead of a raw data fit.
        # Modebar + double-click use Registry _guiRelayout (not Plotly.relayout),
        # so we re-register that API method.
        self.app.index_string = self.app.index_string.replace(
            "{%css%}",
            "{%css%}\n<style>"
            "#evt-input::-webkit-outer-spin-button,"
            "#evt-input::-webkit-inner-spin-button"
            "{-webkit-appearance:none;margin:0;}"
            "</style>",
        ).replace(
            "</body>",
            """
<script>
(function () {
  function metaOf(gd) {
    return (gd && gd.layout && gd.layout.meta) || null;
  }
  function defaultRanges(gd) {
    var m = metaOf(gd);
    return (m && m.default_ranges) || null;
  }
  // Lock Plotly's Reset / odd-double-click targets from layout.meta.
  function applyResetTarget(gd) {
    var m = metaOf(gd);
    if (!m) return;
    var fl = gd._fullLayout;
    if (!fl) return;
    var full = m.full_x_range;
    var dr = m.default_ranges || {};
    Object.keys(fl).forEach(function (k) {
      var ax = fl[k];
      if (!ax) return;
      if (k === "xaxis" || /^xaxis\\d+$/.test(k)) {
        if (full && full.length >= 2) {
          ax._rangeInitial0 = full[0];
          ax._rangeInitial1 = full[1];
        } else if (dr[k] && dr[k].length >= 2) {
          ax._rangeInitial0 = dr[k][0];
          ax._rangeInitial1 = dr[k][1];
        }
      } else if (k === "yaxis" || /^yaxis\\d+$/.test(k)) {
        // Keep y at the curated default on Reset (don't follow guide-line traces).
        if (dr[k] && dr[k].length >= 2) {
          ax._rangeInitial0 = dr[k][0];
          ax._rangeInitial1 = dr[k][1];
        }
      }
    });
  }
  function buildDefaultUpdate(gd) {
    var dr = defaultRanges(gd);
    if (!dr) return null;
    var update = {};
    Object.keys(dr).forEach(function (ax) {
      update[ax + ".range"] = dr[ax].slice();
      update[ax + ".autorange"] = false;
    });
    return update;
  }
  function isPureAutoscale(update) {
    if (!update || typeof update !== "object" || Array.isArray(update)) return false;
    var keys = Object.keys(update);
    if (!keys.length) return false;
    return keys.every(function (k) {
      return /\\.autorange$/.test(k) && update[k] === true;
    });
  }
  function maybeRemapAutoscale(gd, update) {
    var m = metaOf(gd);
    if (!(m && m.autoscale_to_default && isPureAutoscale(update))) return update;
    return buildDefaultUpdate(gd) || update;
  }
  function afterPlot(gd) {
    applyResetTarget(gd);
  }
  function wrapPlot(name) {
    if (!Plotly[name] || Plotly[name]._pgramsWrapped) return;
    var orig = Plotly[name];
    function wrapped(gd) {
      var ret = orig.apply(this, arguments);
      Promise.resolve(ret).then(function () { afterPlot(gd); });
      return ret;
    }
    wrapped._pgramsWrapped = true;
    Plotly[name] = wrapped;
  }
  function wrapGuiRelayout() {
    if (Plotly._pgramsGuiRelayoutWrapped) return;
    if (typeof Plotly.register !== "function" || typeof Plotly.relayout !== "function") return;
    Plotly._pgramsGuiRelayoutWrapped = true;
    // Modebar Autoscale + double-click go through Registry "_guiRelayout",
    // which keeps its own fn reference — re-register to intercept.
    Plotly.register({
      moduleType: "apiMethod",
      name: "_guiRelayout",
      fn: function (gd, update) {
        if (gd && gd._fullLayout) gd._fullLayout._guiEditing = true;
        try {
          if (arguments.length > 2) {
            return Plotly.relayout(gd, arguments[1], arguments[2]);
          }
          if (typeof update === "object" && update !== null && !Array.isArray(update)) {
            update = maybeRemapAutoscale(gd, update);
          }
          return Plotly.relayout(gd, update);
        } finally {
          if (gd && gd._fullLayout) gd._fullLayout._guiEditing = false;
          // Re-assert Reset targets; some Plotly paths refresh axis internals.
          applyResetTarget(gd);
        }
      },
    });
  }
  function ready() {
    if (typeof Plotly === "undefined") { setTimeout(ready, 50); return; }
    wrapPlot("react");
    wrapPlot("newPlot");
    wrapGuiRelayout();
  }
  ready();
})();
</script>
</body>""",
        )
        self.app.layout = self._build_layout()
        self._register_callbacks()

    def _fem_row(self, slot: int, is_light: bool) -> html.Div:
        heat_id = "lfem-heatmap" if is_light else f"qfem-slot-{slot}"
        return html.Div(
            style={
                "display": "grid",
                "gridTemplateColumns": "minmax(0, 1.4fr) minmax(0, 0.86fr)",
                "gap": "8px",
                "alignItems": "stretch",
                "marginBottom": "6px",
            },
            children=[
                dcc.Graph(
                    id=heat_id,
                    config=HEATMAP_GRAPH_CONFIG,
                    style={"height": f"{HEATMAP_HEIGHT}px"},
                ),
                dcc.Graph(
                    id=f"lbw-{slot}",
                    config={"displayModeBar": False},
                    style={"height": f"{HEATMAP_HEIGHT}px"},
                ),
            ],
        )

    def _placeholder_tab(self, title: str) -> html.Div:
        return html.Div(
            style={
                "padding": "48px 24px",
                "color": "#888",
                "textAlign": "center",
                "border": "1px dashed #ccc",
                "borderRadius": "8px",
                "margin": "12px 0",
            },
            children=[html.H3(title, style={"color": "#555"}), html.P("Coming soon.")],
        )

    def _qfem_waveform_panel(self) -> html.Div:
        return html.Div(
            children=[
                html.Div(
                    style={
                        "display": "flex", "gap": "10px", "alignItems": "center",
                        "margin": "8px 0", "flexWrap": "wrap",
                    },
                    children=[
                        html.Label("Q-FEM:"),
                        dcc.Dropdown(
                            id="qdetail-slot",
                            options=[{"label": f"slot {s}", "value": s} for s in Q_SLOTS],
                            value=Q_SLOTS[0],
                            clearable=False,
                            style={"width": "130px"},
                        ),
                        html.Label("channels:"),
                        dcc.Input(
                            id="qdetail-channels", type="text",
                            placeholder="E.g. [0,9] for a range; 0,5,12 for specific channels",
                            value="", debounce=True, style={"width": "340px"},
                        ),
                        html.Span("(0\u201363, any number)", style={"color": "#888", "fontSize": "12px"}),
                    ],
                ),
                dcc.Graph(id="qdetail-graph", style={"width": "96%"}, config=WF_GRAPH_CONFIG),
            ],
        )

    def _lfem_waveform_panel(self) -> html.Div:
        return html.Div(
            children=[
                html.Div(
                    "Full-window overlay (top) + each responding channel zoomed to its ROI(s).",
                    style={"color": "#888", "fontSize": "12px", "margin": "8px 0"},
                ),
                dcc.Graph(id="ldetail-graph", style={"width": "96%"}, config=WF_GRAPH_CONFIG),
            ],
        )

    def _build_layout(self) -> html.Div:
        return html.Div(
            style={"fontFamily": "Arial, sans-serif", "margin": "6px 10px"},
            children=[
                html.Div(
                    style={
                        "display": "flex",
                        "flexWrap": "wrap",
                        "gap": "10px",
                        "alignItems": "center",
                        "marginBottom": "6px",
                        "padding": "8px 10px",
                        "background": "#f0f0f0",
                        "borderRadius": "6px",
                        "width": "100%",
                        "boxSizing": "border-box",
                    },
                    children=[
                        html.H2("pGRAMS DQM", style={"margin": "0 8px 0 0"}),
                        html.Div(id="evt-label", children="event: --"),
                        html.Label("Refresh (ms):"),
                        dcc.Input(
                            id="refresh-ms", type="number", value=2000,
                            min=200, max=60000, step=100, style={"width": "80px"},
                        ),
                        dcc.Checklist(
                            id="pause-check",
                            options=[{"label": " Pause", "value": "pause"}],
                            value=[],
                        ),
                        html.Label("File:"),
                        dcc.Input(
                            id="file-path", type="text", placeholder="hexdump .txt or .dat path",
                            value=DEFAULT_HEX_FILE, style={"width": "260px"},
                        ),
                        html.Label("Evt#:"),
                        html.Div(
                            style={"position": "relative", "width": "72px",
                                   "display": "inline-block"},
                            children=[
                                dcc.Input(
                                    id="evt-input", type="number", value=0, min=0, step=1,
                                    style={"width": "100%", "paddingRight": "18px",
                                           "boxSizing": "border-box",
                                           "MozAppearance": "textfield"},
                                ),
                                html.Div(
                                    style={"position": "absolute", "right": "1px",
                                           "top": "1px", "bottom": "1px",
                                           "display": "flex", "flexDirection": "column",
                                           "justifyContent": "center"},
                                    children=[
                                        html.Button("\u25b2", id="evt-up", n_clicks=0, style=STEP_BTN),
                                        html.Button("\u25bc", id="evt-down", n_clicks=0, style=STEP_BTN),
                                    ],
                                ),
                            ],
                        ),
                        html.Label("Q lag:", title="header_lag,adc_lag (e.g. '1' shifts both, '1,0' shifts header only). header_lag picks the FEMHeader6 event; adc_lag picks the charge-ADC event."),
                        dcc.Input(id="qlag-input", type="text", value="",
                                  placeholder="0,0", style={"width": "56px"}),
                        html.Label("L lag:", title="header_lag,adc_lag (e.g. '1' shifts both, '1,0' shifts header only). header_lag picks the L-FEM FEMHeader6 (trigger/ROI remap) event; adc_lag picks the light-ROI event."),
                        dcc.Input(id="llag-input", type="text", value="",
                                  placeholder="0,0", style={"width": "56px"}),
                        html.Button("Load", id="load-btn", n_clicks=0),
                        html.Div(id="status-msg", style={"color": "#666", "fontSize": "12px"}),
                        html.Div(
                            style={"marginLeft": "auto", "display": "flex", "gap": "4px"},
                            children=[
                                html.Button("Heatmaps", id="tab-btn-heat", n_clicks=0, style=TAB_BTN_ACTIVE),
                                html.Button("Q-FEM waveforms", id="tab-btn-q", n_clicks=0, style=TAB_BTN),
                                html.Button("L-FEM waveforms", id="tab-btn-l", n_clicks=0, style=TAB_BTN),
                                html.Button("Event display", id="tab-btn-evt", n_clicks=0, style=TAB_BTN),
                            ],
                        ),
                    ],
                ),
                html.Div(
                    id="panel-heat",
                    style=PANEL_SHOW,
                    children=[
                        *[self._fem_row(slot, is_light=False) for slot in Q_SLOTS],
                        self._fem_row(LIGHT_SLOT, is_light=True),
                    ],
                ),
                html.Div(id="panel-q", style=PANEL_HIDE, children=[self._qfem_waveform_panel()]),
                html.Div(id="panel-l", style=PANEL_HIDE, children=[self._lfem_waveform_panel()]),
                html.Div(
                    id="panel-evt",
                    style=PANEL_HIDE,
                    children=[dcc.Graph(id="evt-display-graph", style={"width": "96%"})],
                ),
                dcc.Store(id="active-tab", data="heat"),
                dcc.Store(id="event-version", data=0),
                dcc.Interval(id="update-interval", interval=2000, n_intervals=0),
            ],
        )

    def is_frozen(self) -> bool:
        with self._lock:
            return self.freeze_live

    def reset_event(self, evt_number: int | None = None):
        with self._lock:
            if self.freeze_live:
                return
            self.evt_number = evt_number
            self.charge_slots = {s: {} for s in Q_SLOTS}
            self.light_channels = {}
            self.trigger_ticks = {}
            self.trigger_meta = {}

    def _maybe_roll_event(self, evt_number: int | None):
        if self.freeze_live:
            return
        if evt_number is not None and evt_number != self.evt_number:
            self.evt_number = evt_number
            self.charge_slots = {s: {} for s in Q_SLOTS}
            self.light_channels = {}
            self.trigger_ticks = {}
            self.trigger_meta = {}

    def load_event_record(self, record: EventRecord):
        with self._lock:
            self.freeze_live = True
            self.evt_number = record.evt_number
            self.charge_slots = {
                slot: {ch: np.asarray(arr, dtype=np.float32) for ch, arr in channels.items()}
                for slot, channels in record.charge_slots.items()
            }
            self.light_channels = {
                ch: [
                    {
                        "start_sample": int(r["start_sample"]),
                        "samples": np.asarray(r["samples"], dtype=np.float32),
                    }
                    for r in rois
                ]
                for ch, rois in record.light_channels.items()
            }
            self.trigger_ticks = dict(record.trigger_ticks)
            self.trigger_meta = dict(getattr(record, "trigger_meta", None) or {})
            self.lbw_charge = compute_charge_lbw(self.charge_slots, Q_SLOTS)
            self.lbw_light = compute_light_lbw(self.light_channels)

    def load_offline_event(
        self,
        q_decoded: dict[int, dict[int, np.ndarray]],
        light_channels: dict[int, list[dict]],
        evt_number: int | None = None,
        trigger_ticks: dict[int, int] | None = None,
    ):
        self.load_event_record(
            EventRecord(
                evt_number=evt_number or 0,
                charge_slots=q_decoded,
                light_channels=light_channels,
                trigger_ticks=trigger_ticks or {},
            )
        )

    def update_lbw(
        self,
        charge_baseline,
        charge_rms,
        charge_hits,
        light_baseline,
        light_rms,
        light_hits,
        evt_number: int | None = None,
    ):
        light_hits = [h / 8.0 for h in light_hits]
        with self._lock:
            if self.freeze_live:
                return
            self.lbw_charge = (
                list(charge_baseline[:192]),
                list(charge_rms[:192]),
                list(charge_hits[:192]),
            )
            self.lbw_light = (
                list(light_baseline),
                list(light_rms),
                list(light_hits),
            )
            if evt_number is not None:
                self.evt_number = evt_number

    def update_charge_channel(
        self,
        channel: int,
        samples,
        evt_number: int | None = None,
        slot: int | None = None,
    ):
        arr = np.asarray(samples, dtype=np.float32)
        with self._lock:
            if self.freeze_live:
                return
            self._maybe_roll_event(evt_number)
            if slot is None:
                slot_idx = channel // CHANNELS_PER_QFEM
                local_ch = channel % CHANNELS_PER_QFEM
                slot = Q_SLOTS[slot_idx] if slot_idx < len(Q_SLOTS) else Q_SLOTS[0]
                channel = local_ch if slot_idx < len(Q_SLOTS) else channel
            if slot not in self.charge_slots:
                self.charge_slots[slot] = {}
            self.charge_slots[slot][channel] = arr

    def update_light_channel(
        self,
        channel: int,
        samples,
        start_tick: int = 0,
        evt_number: int | None = None,
    ):
        arr = np.asarray(samples, dtype=np.float32)
        with self._lock:
            if self.freeze_live:
                return
            self._maybe_roll_event(evt_number)
            self.light_channels[channel] = [
                {"start_sample": int(start_tick), "samples": arr}
            ]

    def _snapshot(self):
        with self._lock:
            charge = {
                slot: {ch: arr.copy() for ch, arr in chans.items()}
                for slot, chans in self.charge_slots.items()
            }
            light = {
                ch: [
                    {"start_sample": r["start_sample"], "samples": r["samples"].copy()}
                    for r in rois
                ]
                for ch, rois in self.light_channels.items()
            }
            return (
                self.evt_number,
                charge,
                light,
                self.lbw_charge,
                self.lbw_light,
                dict(self.trigger_ticks),
                dict(self.trigger_meta),
            )

    @staticmethod
    def _empty():
        return go.Figure()

    def _build_figures(self):
        evt, charge_slots, light, lbw_q, lbw_l, triggers, tmeta = self._snapshot()
        evt_label = f"event: {evt if evt is not None else '--'}"

        heatmaps = [
            make_charge_heatmap_figure(
                charge_slots.get(slot, {}),
                title=f"Q-FEM slot {slot}",
                trigger_x=triggers.get(slot),
                trig_sample=(tmeta.get(slot) or {}).get("sample"),
                header_meta=tmeta.get(slot),
            )
            for slot in Q_SLOTS
        ]
        heatmaps.append(
            make_light_heatmap_figure(
                light,
                title=f"L-FEM slot {LIGHT_SLOT}",
                trigger_x=triggers.get(LIGHT_SLOT),
                header_meta=tmeta.get(LIGHT_SLOT),
            )
        )

        lbw_figs = []
        for i, slot in enumerate(Q_SLOTS):
            chunk = _slice_q_lbw(lbw_q, i)
            if chunk is None:
                lbw_figs.append(self._empty())
            else:
                b, r, h = chunk
                lbw_figs.append(make_lbw_panel_figure(b, r, h, slot_label=f"Q{slot}"))

        l_chunk = _slice_l_lbw(lbw_l)
        if l_chunk is None:
            lbw_figs.append(self._empty())
        else:
            b, r, h = l_chunk
            lbw_figs.append(
                make_lbw_panel_figure(
                    b, r, h, slot_label=f"L{LIGHT_SLOT}", is_light=True,
                )
            )

        return evt_label, heatmaps, lbw_figs

    def _register_callbacks(self):
        figure_outputs = [Output("evt-label", "children")]
        figure_outputs += [Output(f"qfem-slot-{s}", "figure") for s in Q_SLOTS]
        figure_outputs += [Output("lfem-heatmap", "figure")]
        for slot in FEM_SLOTS:
            figure_outputs.append(Output(f"lbw-{slot}", "figure"))

        # The up/down arrows step Evt# AND load immediately; typing into the box
        # still requires the Load button. refresh owns Evt#'s value so it can
        # write back the stepped index.
        load_triggers = ("load-btn", "evt-up", "evt-down")

        @self.app.callback(
            figure_outputs
            + [
                Output("pause-check", "value"),
                Output("status-msg", "children"),
                Output("evt-input", "value"),
                Output("event-version", "data"),
            ],
            [
                Input("update-interval", "n_intervals"),
                Input("load-btn", "n_clicks"),
                Input("evt-up", "n_clicks"),
                Input("evt-down", "n_clicks"),
            ],
            [
                State("pause-check", "value"),
                State("file-path", "value"),
                State("evt-input", "value"),
                State("qlag-input", "value"),
                State("llag-input", "value"),
            ],
            running=[
                (
                    Output("status-msg", "children"),
                    html.Span("Loading data file...", style={"color": "limegreen"}),
                    True,
                ),
            ],
        )
        def refresh(_, _load_clicks, _up, _down, pause_val, file_path, evt_idx, q_lag, l_lag):
            triggered = dash.callback_context.triggered_id
            status = no_update
            pause_out = no_update
            evt_out = no_update
            ver_out = no_update

            if triggered in load_triggers:
                if not file_path:
                    return (*((no_update,) * len(figure_outputs)), no_update, _err("no file path"), no_update, no_update)
                try:
                    evt_idx = int(evt_idx or 0)
                    if triggered == "evt-up":
                        evt_idx += 1
                    elif triggered == "evt-down":
                        evt_idx = max(0, evt_idx - 1)
                    evt_out = evt_idx
                    q_lag = _parse_lag(q_lag)
                    l_lag = _parse_lag(l_lag)
                    record = load_event(
                        file_path.strip(), evt_idx, source="auto",
                        q_lag=q_lag, l_lag=l_lag,
                    )
                    self.load_event_record(record)
                    qx = record.trigger_ticks.get(Q_SLOTS[0], "?")
                    lag_note = ""
                    if any(q_lag) or any(l_lag):
                        lag_note = f" | lag Q{_fmt_lag(q_lag)} L{_fmt_lag(l_lag)}"
                    status = (
                        f"loaded evt {evt_idx} | trigger abs={record.trigger_abs} "
                        f"Q x={qx}{lag_note} (auto-paused)"
                    )
                    warns = _header_match_warnings(record)
                    if warns:
                        status = [status] + [_err("  ⚠ " + w) for w in warns]
                    pause_out = ["pause"]
                    self._load_seq += 1
                    ver_out = self._load_seq
                except Exception as exc:
                    return (*((no_update,) * len(figure_outputs)), no_update, _err(f"load failed: {exc}"), no_update, no_update)
            elif self.is_frozen():
                # Frozen on a loaded event: live-interval ticks must not overwrite
                # it. Check the live backend flag (not the stale pause_val State),
                # so an interval racing a just-finished Load can't clobber it.
                return (
                    *((no_update,) * len(figure_outputs)),
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                )

            evt_label, heatmaps, lbw_figs = self._build_figures()
            return (evt_label, *heatmaps, *lbw_figs, pause_out, status, evt_out, ver_out)

        @self.app.callback(
            Output("status-msg", "children", allow_duplicate=True),
            Input("pause-check", "value"),
            prevent_initial_call=True,
        )
        def on_pause_toggle(pause_val):
            with self._lock:
                self.freeze_live = bool(pause_val and "pause" in pause_val)
            return no_update

        @self.app.callback(Output("update-interval", "interval"), Input("refresh-ms", "value"))
        def set_refresh_rate(ms):
            if ms is None or ms < 200:
                return 2000
            return int(ms)

        @self.app.callback(
            Output("update-interval", "disabled"),
            Input("pause-check", "value"),
        )
        def toggle_interval(pause_val):
            # Stop live ticks entirely while frozen so they can't race/overwrite
            # offline browsing (Load / step arrows / channel selection).
            return bool(pause_val and "pause" in pause_val)

        @self.app.callback(
            [
                Output("active-tab", "data"),
                Output("panel-heat", "style"),
                Output("panel-q", "style"),
                Output("panel-l", "style"),
                Output("panel-evt", "style"),
                Output("tab-btn-heat", "style"),
                Output("tab-btn-q", "style"),
                Output("tab-btn-l", "style"),
                Output("tab-btn-evt", "style"),
            ],
            [
                Input("tab-btn-heat", "n_clicks"),
                Input("tab-btn-q", "n_clicks"),
                Input("tab-btn-l", "n_clicks"),
                Input("tab-btn-evt", "n_clicks"),
            ],
            State("active-tab", "data"),
        )
        def switch_tab(n_heat, n_q, n_l, n_evt, current):
            triggered = dash.callback_context.triggered_id
            order = ["heat", "q", "l", "evt"]
            by_btn = {
                "tab-btn-heat": "heat", "tab-btn-q": "q",
                "tab-btn-l": "l", "tab-btn-evt": "evt",
            }
            tab = by_btn.get(triggered, current or "heat")

            styles = [PANEL_HIDE] * 4
            btn_styles = [TAB_BTN] * 4
            idx = order.index(tab)
            styles[idx] = PANEL_SHOW
            btn_styles[idx] = TAB_BTN_ACTIVE
            return tab, *styles, *btn_styles

        @self.app.callback(
            Output("qdetail-graph", "figure"),
            [
                Input("update-interval", "n_intervals"),
                Input("active-tab", "data"),
                Input("qdetail-slot", "value"),
                Input("qdetail-channels", "value"),
                Input("event-version", "data"),
            ],
        )
        def q_waveforms(_n, tab, slot, chan_str, _ver):
            if tab != "q":
                return no_update
            if dash.callback_context.triggered_id == "update-interval" and self.is_frozen():
                return no_update
            evt, charge, _light, _bq, _bl, triggers, _tmeta = self._snapshot()
            slot = int(slot) if slot is not None else Q_SLOTS[0]
            selected, is_range = _parse_channels(chan_str)
            return make_qfem_waveform_figure(
                charge.get(slot, {}), selected,
                trigger_x=triggers.get(slot),
                title=f"Q-FEM slot {slot} (evt {evt if evt is not None else '--'})",
                band_gray=is_range,
            )

        @self.app.callback(
            Output("ldetail-graph", "figure"),
            [
                Input("update-interval", "n_intervals"),
                Input("active-tab", "data"),
                Input("event-version", "data"),
            ],
        )
        def l_waveforms(_n, tab, _ver):
            if tab != "l":
                return no_update
            if dash.callback_context.triggered_id == "update-interval" and self.is_frozen():
                return no_update
            evt, _charge, light, _bq, _bl, triggers, _tmeta = self._snapshot()
            return make_lfem_waveform_figure(
                light, trigger_x=triggers.get(LIGHT_SLOT),
                title=f"L-FEM slot {LIGHT_SLOT} (evt {evt if evt is not None else '--'})",
            )
        
        @self.app.callback(
            Output("evt-display-graph", "figure"),
            [
                Input("update-interval", "n_intervals"),
                Input("active-tab", "data"),
                Input("event-version", "data"),
            ],
        )
        def event_display_q_graphs(_n, tab, _ver):
            # 1. Do nothing if the user is on a different tab
            if tab != "evt":
                return no_update
            
            # 2. Do nothing if the app is paused/frozen (avoids overwriting manual event selection)
            if dash.callback_context.triggered_id == "update-interval" and self.is_frozen():
                return no_update
            
            # 3. Safely pull the data snapshot from the backend
            evt, charge_slots, _light, _bq, _bl, _trigger_ticks, _trigger_meta = self._snapshot()
            
            # 4. Pass the charge dictionary to your plotting function
            # return make_qt_figure_horizontal(charge_slots)
            return make_qt_figure(charge_slots, restrict_window=True)
        
        # @self.app.callback(
        #     Output("evt-display-graph", "figure"),
        #     [
        #         Input("update-interval", "n_intervals"),
        #         Input("active-tab", "data"),
        #         Input("event-version", "data"),
        #     ],
        # )

        # def event_display_l_graphs(_n, tab, _ver):
        #     # 1. Do nothing if the user is on a different tab
        #     # if tab != "evt":
        #     #     return no_update
            
        #     # 2. Do nothing if the app is paused/frozen (avoids overwriting manual event selection)
        #     if dash.callback_context.triggered_id == "update-interval" and self.is_frozen():
        #         return no_update
            
        #     # 3. Safely pull the data snapshot from the backend
        #     evt, charge_slots, light, bq, bl, trigger_ticks, trigger_meta = self._snapshot()

        #     figure = make_lt_figure(light)
            
        #     # 4. Pass the charge dictionary to your plotting function
        #     return figure

    def run(self, blocking: bool = False, open_browser: bool = False):
        url = f"http://{self.host}:{self.port}"
        if open_browser:
            threading.Timer(1.2, webbrowser.open, args=(url,)).start()

        if blocking:
            print(f"DQM at {url}")
            self.app.run(host=self.host, port=self.port, debug=False)
            return

        thread = threading.Thread(
            target=self.app.run,
            kwargs=dict(host=self.host, port=self.port, debug=False),
            daemon=True,
        )
        thread.start()
        print(f"DQM at {url}")
