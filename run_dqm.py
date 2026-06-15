#!/usr/bin/env python3
"""Launch DQM. Default: demo (synthetic refresh + UI Load). Use --live for MQTT."""

from __future__ import annotations

import argparse
import math
import threading
import time

import numpy as np

from data_monitoring.dqm_web import CHANNELS_PER_QFEM, DqmWeb, Q_SLOTS

SAMPLES_PER_QFEM_READOUT = 763


def _demo_feeder(dqm: DqmWeb, interval: float = 3.0):
    rng = np.random.default_rng(0)
    evt = 0
    while True:
        if dqm.is_frozen():
            time.sleep(0.5)
            continue
        evt += 1
        dqm.reset_event(evt)

        baselines = [2100 + int(rng.integers(-30, 30)) for _ in range(len(Q_SLOTS) * CHANNELS_PER_QFEM)]
        rms = [8 + int(rng.integers(0, 6)) for _ in range(len(Q_SLOTS) * CHANNELS_PER_QFEM)]
        hits = [int(rng.integers(0, 5)) for _ in range(len(Q_SLOTS) * CHANNELS_PER_QFEM)]
        l_base = [2050 + int(rng.integers(-20, 20)) for _ in range(36)]
        l_rms = [6 + int(rng.integers(0, 4)) for _ in range(36)]
        l_hits = [int(rng.integers(0, 3) * 8) for _ in range(36)]
        dqm.update_lbw(baselines, rms, hits, l_base, l_rms, l_hits, evt_number=evt)

        for slot in Q_SLOTS:
            for ch in range(CHANNELS_PER_QFEM):
                t = np.arange(SAMPLES_PER_QFEM_READOUT, dtype=np.float32)
                pulse = 80.0 * np.exp(-0.5 * ((t - 120 - ch % 9) / 18.0) ** 2)
                noise = rng.normal(0, 8, SAMPLES_PER_QFEM_READOUT)
                dqm.update_charge_channel(
                    ch,
                    2100 + pulse + noise,
                    evt_number=evt,
                    slot=slot,
                )

        for ch in range(12):
            start = 400 + ch * 420
            t = np.arange(256, dtype=np.float32)
            wave = 160.0 * np.sin(2 * math.pi * t / 48.0) + rng.normal(0, 5, 256)
            dqm.update_light_channel(ch, 2050 + wave, start_tick=start, evt_number=evt)

        time.sleep(interval)


def _run_live(dqm: DqmWeb):
    from connections.connection_interface import ConnectionInterface

    conn = ConnectionInterface(interface="TCP", monitor=dqm)
    if conn.get_is_fake_hub():
        conn.open_connections()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        if conn.get_is_fake_hub():
            conn.close_connections()


def main():
    parser = argparse.ArgumentParser(description="pGRAMS DQM webpage")
    parser.add_argument("--live", action="store_true", help="MQTT live stream (needs networking stack)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8051)
    parser.add_argument("--no-browser", action="store_true", help="do not open browser")
    args = parser.parse_args()
    open_browser = not args.no_browser

    dqm = DqmWeb(host=args.host, port=args.port)

    if args.live:
        dqm.run(blocking=False, open_browser=open_browser)
        _run_live(dqm)
    else:
        threading.Thread(target=_demo_feeder, args=(dqm,), daemon=True).start()
        dqm.run(blocking=True, open_browser=open_browser)


if __name__ == "__main__":
    main()
