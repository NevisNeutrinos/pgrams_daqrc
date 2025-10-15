import eventlet
import numpy as np

eventlet.monkey_patch()  # patch standard library for concurrency

import json
from datetime import datetime
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from threading import Thread
from time import sleep
from config_manager import ConfigManager
from fake_hub import FakeHub
# from network_module import IOContext, TCPConnection, Command
from network_module import Command
from datamon import DaqCompMonitor, TpcReadoutMonitor, CommCodes


app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

config_mgr = ConfigManager()
daq_metrics = DaqCompMonitor()
tpc_readout_metrics = TpcReadoutMonitor()

"""
  Make the TCP connections. 
"""
fake_hub = FakeHub()
fake_hub.start_connection()
devices = fake_hub.get_devices()
device_title = fake_hub.get_device_names()


# Map GUI buttons to communication codes
command_map = {
    "START_STATUS": int(CommCodes.OrcStartComputerStatus),
    "STOP_STATUS": int(CommCodes.OrcStopComputerStatus),
    "START_ALL_DAQ": int(CommCodes.OrcBootAllDaq),
    "SHUTDOWN_ALL_DAQ": int(CommCodes.OrcShutdownAllDaq),
    "REBOOT_COMPUTER": int(CommCodes.OrcExecCpuRestart),
    "SHUTDOWN_COMPUTER": int(CommCodes.OrcExecCpuShutdown),
    "PCIE_DRIVER_INIT": int(CommCodes.OrcPcieInit),
    "RESET": int(CommCodes.ColResetRun),
    "CONFIGURE": int(CommCodes.ColConfigure),
    "START_RUN": int(CommCodes.ColStartRun),
    "STOP_RUN": int(CommCodes.ColStopRun)
}

def prepare_metric_dict(metric_dict):
    for k, v in metric_dict.items():
        if type(v) is np.ndarray:
            metric_dict[k] = v.tolist()
    return metric_dict

def stream_device(device_name):
    """Continuously read from a TCP connection and emit received commands."""
    tcp_conn = devices[device_name]
    while True:
        cmd_list = tcp_conn.read_recv_buffer(1000)
        if cmd_list:
            for cmd in cmd_list:
                if device_name == "DaemonStat":
                    daq_metrics.deserialize(cmd.arguments)
                    payload = prepare_metric_dict(daq_metrics.get_metric_dict())
                elif device_name == "TPCReadoutStat":
                    tpc_readout_metrics.deserialize(cmd.arguments)
                    payload = prepare_metric_dict(tpc_readout_metrics.get_metric_dict())
                else:
                    payload = cmd.arguments

                socketio.emit("command_response",
                    {"device": device_name, "timestamp": datetime.now().strftime("%H:%M:%S"),
                            "command": cmd.command, "args": payload}
                )
        eventlet.sleep(0.5)  # non-blocking sleep for eventlet

for device_name in devices:
    t = Thread(target=stream_device, args=(device_name,), daemon=True)
    t.start()

def handle_command(device_name, command_name, sid, value=None):
    tcp_conn = devices[device_name]

    if value is not None:
        if type(value) is dict:
            args = [1]
            args += config_mgr.serialize()
            cmd = Command(command_map[command_name], len(args))
            cmd.arguments = args
            tcp_conn.write_send_buffer(cmd)
        else:
            cmd = Command(command_map[command_name], 1)
            cmd.arguments = [int(value)]
            tcp_conn.write_send_buffer(cmd)
    else:
        cmd = Command(command_map[command_name], 0)
        tcp_conn.write_send_buffer(cmd)

    while True:
        cmd_list = tcp_conn.read_recv_buffer(1000)
        if not cmd_list:
            break
        for cmd in cmd_list:
            recv_cmd = cmd.command
            recv_args = cmd.arguments
            # Emit to client: include device name
            socketio.emit(
                'command_response',
                {'device': device_name, "timestamp": datetime.now().strftime("%H:%M:%S"),
                        'command': recv_cmd, 'args': recv_args}, room=sid)
        sleep(0.5)
@app.route('/')
def index():
    return render_template('index_twocol_wconfig.html', devices=device_title)
    # return render_template('index_twocol.html', devices=device_title)
    # return render_template('index.html', devices=device_title)

@socketio.on('load_config_file')
def on_load_config_file(data):
    path = data.get('path')
    sid = request.sid
    try:
        print("Loading config file: ", path)
        with open(path, 'r') as f:
            json_data = json.load(f)
        emit('config_loaded', json_data, room=sid)
    except Exception as e:
        emit('command_response', {'device': 'SERVER', 'command': 'ERROR', 'args': f'Failed to load file: {e}'}, room=sid)

@socketio.on('update_config')
def on_update_config(new_config):
    sid = request.sid
    config_mgr.update_from_dict(new_config)
    updated_dict = config_mgr.get_config()
    print("Updated config: ", updated_dict)
    emit("command_response", {'device': 'SERVER', 'command': 'INFO', 'args': f'Updated config {updated_dict}'}, room=sid)

@socketio.on('send_command')
def on_send_command(data):
    device_name = data.get('device')
    cmd_name = data.get('cmd')
    value = data.get('value')
    sid = request.sid
    if device_name in devices and cmd_name:
        thread = Thread(target=handle_command, args=(device_name, cmd_name, sid, value))
        thread.start()
    else:
        emit('command_response', {'device': device_name, 'command': 'ERROR', 'args': 'Invalid device or command'}, room=sid)

if __name__ == '__main__':
    # socketio.run(app, debug=True)
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, use_reloader=False)

    # Stop the connections
    fake_hub.shutdown_connections()
    # for device_name in devices:
    #     print("Stopping connection " + device_name + "..")
    #     devices[device_name].stop_ctx(io_context)
    # print("Closed all server TCP/IP connections...")