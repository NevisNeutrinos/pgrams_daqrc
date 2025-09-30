import eventlet
eventlet.monkey_patch()  # patch standard library for concurrency

import json
from datetime import datetime
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from threading import Thread
from time import sleep
from config_manager import ConfigManager
from network_module import IOContext, TCPConnection, Command

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

config_mgr = ConfigManager()

"""
  Multiple TCP connections globals
"""

# The connections and their ports
device_dict = {
    "DaemonStat": 50000,
    "DaemonCmd": 50001,
    "TPCReadoutStat": 50002,
    "TPCReadoutCmd": 50003,
}

device_title = [
        {'name': device_name, 'title': device_name + " [" + str(device_dict[device_name]) + "]"}
         for device_name in device_dict
]

# Start the ASIO IO context and start the servers
io_context = IOContext()
devices = {device_name: TCPConnection(io_context, "127.0.0.1", device_dict[device_name],
                                      True, device_name.endswith("Cmd"), device_name.endswith("Stat")) # <server> <heartbeat> <monitor>
                            for device_name in device_dict
           }
devices["DaemonStat"].run_ctx(io_context)

# Map GUI buttons to commands
command_map = {
    "STATUS": 0x0,
    "START_DAQ": 0x1,
    "STOP_DAQ": 0x2,
    "RESET": 0x0,
    "CONFIGURE": 0x1,
    "RUN": 0x2,
    "STOP": 0x3
}

def stream_device(device_name):
    """Continuously read from a TCP connection and emit received commands."""
    tcp_conn = devices[device_name]
    while True:
        cmd_list = tcp_conn.read_recv_buffer(1000)
        if cmd_list:
            for cmd in cmd_list:
                socketio.emit(
                    "command_response",
                    {"device": device_name, "timestamp": datetime.now().strftime("%H:%M:%S"),
                            "command": cmd.command, "args": cmd.arguments},
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
                        'command': recv_cmd, 'args': recv_args},
                room=sid
            )
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
    for device_name in devices:
        print("Stopping connection " + device_name + "..")
        devices[device_name].stop_ctx(io_context)
    print("Closed all server TCP/IP connections...")