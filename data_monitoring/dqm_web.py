"""Real-time DQM: left heatmaps, right per-FEM LBW, top controls."""

from __future__ import annotations

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
PANEL_HIDE = {"display": "none"}
PANEL_SHOW = {"display": "block"}
from data_monitoring.plot_utils import (
    HEATMAP_HEIGHT,
    LBW_HEIGHT,
    make_charge_heatmap_figure,
    make_compact_bar,
    make_compact_bar_with_error,
    make_light_heatmap_figure,
)

Q_SLOTS = Q_SLOTS_DEFAULT
LIGHT_SLOT = LIGHT_SLOT_DEFAULT
FEM_SLOTS = Q_SLOTS + [LIGHT_SLOT]
CHANNELS_PER_QFEM = 64
LBW_STACK_HEIGHT = max(80, (HEATMAP_HEIGHT - 8) // 3)


def _slice_q_lbw(lbw_q: tuple | None, slot_idx: int) -> tuple[list, list, list] | None:
    if lbw_q is None:
        return None
    bq, rq, hq = lbw_q
    i0 = slot_idx * CHANNELS_PER_QFEM
    i1 = i0 + CHANNELS_PER_QFEM
    return bq[i0:i1], rq[i0:i1], hq[i0:i1]


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
        self.freeze_live = False

        self.app = dash.Dash(__name__, suppress_callback_exceptions=True)
        self.app.layout = self._build_layout()
        self._register_callbacks()

    def _fem_row(self, slot: int, is_light: bool) -> html.Div:
        heat_id = "lfem-heatmap" if is_light else f"qfem-slot-{slot}"
        return html.Div(
            style={
                "display": "grid",
                "gridTemplateColumns": "minmax(0, 1.55fr) minmax(0, 0.85fr)",
                "gap": "8px",
                "alignItems": "stretch",
                "marginBottom": "6px",
            },
            children=[
                dcc.Graph(
                    id=heat_id,
                    config={"displayModeBar": False},
                    style={"height": f"{HEATMAP_HEIGHT}px"},
                ),
                html.Div(
                    style={
                        "display": "grid",
                        "gridTemplateRows": "1fr 1fr 1fr",
                        "gap": "4px",
                        "height": f"{HEATMAP_HEIGHT}px",
                    },
                    children=[
                        dcc.Graph(id=f"lbw-{slot}-baseline", config={"displayModeBar": False}),
                        dcc.Graph(id=f"lbw-{slot}-rms", config={"displayModeBar": False}),
                        dcc.Graph(id=f"lbw-{slot}-hits", config={"displayModeBar": False}),
                    ],
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
                            id="file-path", type="text", placeholder="hexdump .txt path",
                            value=DEFAULT_HEX_FILE, style={"width": "260px"},
                        ),
                        html.Label("Evt#:"),
                        dcc.Input(id="evt-input", type="number", value=0, min=0, step=1,
                                  style={"width": "72px"}),
                        html.Button("Load", id="load-btn", n_clicks=0),
                        html.Div(id="status-msg", style={"color": "#666", "fontSize": "12px"}),
                        html.Div(
                            style={"marginLeft": "auto", "display": "flex", "gap": "4px"},
                            children=[
                                html.Button("Main", id="tab-btn-main", n_clicks=0, style=TAB_BTN_ACTIVE),
                                html.Button("Q-FEM Detail", id="tab-btn-q", n_clicks=0, style=TAB_BTN),
                                html.Button("L-FEM Detail", id="tab-btn-l", n_clicks=0, style=TAB_BTN),
                            ],
                        ),
                    ],
                ),
                html.Div(
                    id="panel-main",
                    style=PANEL_SHOW,
                    children=[
                        *[self._fem_row(slot, is_light=False) for slot in Q_SLOTS],
                        self._fem_row(LIGHT_SLOT, is_light=True),
                    ],
                ),
                html.Div(id="panel-q", style=PANEL_HIDE, children=[self._placeholder_tab("Q-FEM Detail")]),
                html.Div(id="panel-l", style=PANEL_HIDE, children=[self._placeholder_tab("L-FEM Detail")]),
                dcc.Store(id="active-tab", data="main"),
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

    def _maybe_roll_event(self, evt_number: int | None):
        if self.freeze_live:
            return
        if evt_number is not None and evt_number != self.evt_number:
            self.evt_number = evt_number
            self.charge_slots = {s: {} for s in Q_SLOTS}
            self.light_channels = {}
            self.trigger_ticks = {}

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
            # Placeholder LBW until live 0x4001 arrives.
            self.lbw_charge = ([2100] * 192, [8] * 192, [1] * 192)
            self.lbw_light = ([2050] * 36, [6] * 36, [8] * 36)

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
            return self.evt_number, charge, light, self.lbw_charge, self.lbw_light, dict(self.trigger_ticks)

    @staticmethod
    def _empty():
        return go.Figure()

    def _build_figures(self):
        evt, charge_slots, light, lbw_q, lbw_l, triggers = self._snapshot()
        evt_label = f"event: {evt if evt is not None else '--'}"

        heatmaps = [
            make_charge_heatmap_figure(
                charge_slots.get(slot, {}),
                title=f"Q-FEM slot {slot}",
                trigger_x=triggers.get(slot),
            )
            for slot in Q_SLOTS
        ]
        heatmaps.append(
            make_light_heatmap_figure(
                light,
                title=f"L-FEM slot {LIGHT_SLOT}",
                trigger_x=triggers.get(LIGHT_SLOT),
            )
        )

        lbw_figs = []
        for i, slot in enumerate(Q_SLOTS):
            chunk = _slice_q_lbw(lbw_q, i)
            if chunk is None:
                lbw_figs.extend([self._empty(), self._empty(), self._empty()])
            else:
                b, r, h = chunk
                lbw_figs.extend([
                    make_compact_bar_with_error(b, r, f"Q{slot} base", "ADC", LBW_STACK_HEIGHT),
                    make_compact_bar(r, f"Q{slot} RMS", "ADC", LBW_STACK_HEIGHT),
                    make_compact_bar(h, f"Q{slot} hits", "", LBW_STACK_HEIGHT),
                ])

        l_chunk = _slice_l_lbw(lbw_l)
        if l_chunk is None:
            lbw_figs.extend([self._empty(), self._empty(), self._empty()])
        else:
            b, r, h = l_chunk
            lbw_figs.extend([
                make_compact_bar_with_error(b, r, f"L{LIGHT_SLOT} base", "ADC", LBW_STACK_HEIGHT),
                make_compact_bar(r, f"L{LIGHT_SLOT} RMS", "ADC", LBW_STACK_HEIGHT),
                make_compact_bar(h, f"L{LIGHT_SLOT} hits", "", LBW_STACK_HEIGHT),
            ])

        return evt_label, heatmaps, lbw_figs

    def _register_callbacks(self):
        figure_outputs = [Output("evt-label", "children")]
        figure_outputs += [Output(f"qfem-slot-{s}", "figure") for s in Q_SLOTS]
        figure_outputs += [Output("lfem-heatmap", "figure")]
        for slot in FEM_SLOTS:
            figure_outputs += [
                Output(f"lbw-{slot}-baseline", "figure"),
                Output(f"lbw-{slot}-rms", "figure"),
                Output(f"lbw-{slot}-hits", "figure"),
            ]

        @self.app.callback(
            figure_outputs + [Output("pause-check", "value"), Output("status-msg", "children")],
            [Input("update-interval", "n_intervals"), Input("load-btn", "n_clicks")],
            [
                State("pause-check", "value"),
                State("file-path", "value"),
                State("evt-input", "value"),
            ],
        )
        def refresh(_, _load_clicks, pause_val, file_path, evt_idx):
            triggered = dash.callback_context.triggered_id
            status = no_update
            pause_out = no_update

            if triggered == "load-btn":
                if not file_path:
                    return (*((no_update,) * len(figure_outputs)), no_update, "no file path")
                try:
                    evt_idx = int(evt_idx or 0)
                    record = load_event(file_path.strip(), evt_idx, source="hexdump")
                    self.load_event_record(record)
                    qx = record.trigger_ticks.get(Q_SLOTS[0], "?")
                    status = (
                        f"loaded evt {evt_idx} | trigger abs={record.trigger_abs} "
                        f"Q x={qx} (auto-paused)"
                    )
                    pause_out = ["pause"]
                except Exception as exc:
                    return (*((no_update,) * len(figure_outputs)), no_update, f"load failed: {exc}")
            elif pause_val and "pause" in pause_val:
                return (
                    *((no_update,) * len(figure_outputs)),
                    no_update,
                    no_update,
                )

            evt_label, heatmaps, lbw_figs = self._build_figures()
            return (evt_label, *heatmaps, *lbw_figs, pause_out, status)

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
            [
                Output("active-tab", "data"),
                Output("panel-main", "style"),
                Output("panel-q", "style"),
                Output("panel-l", "style"),
                Output("tab-btn-main", "style"),
                Output("tab-btn-q", "style"),
                Output("tab-btn-l", "style"),
            ],
            [
                Input("tab-btn-main", "n_clicks"),
                Input("tab-btn-q", "n_clicks"),
                Input("tab-btn-l", "n_clicks"),
            ],
            State("active-tab", "data"),
        )
        def switch_tab(n_main, n_q, n_l, current):
            triggered = dash.callback_context.triggered_id
            tab = current or "main"
            if triggered == "tab-btn-q":
                tab = "q"
            elif triggered == "tab-btn-l":
                tab = "l"
            elif triggered == "tab-btn-main":
                tab = "main"

            styles = [PANEL_HIDE, PANEL_HIDE, PANEL_HIDE]
            btn_styles = [TAB_BTN, TAB_BTN, TAB_BTN]
            idx = {"main": 0, "q": 1, "l": 2}[tab]
            styles[idx] = PANEL_SHOW
            btn_styles[idx] = TAB_BTN_ACTIVE
            return tab, *styles, *btn_styles

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
