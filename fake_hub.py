from network_module import IOContext, TCPConnection, Command
from threading import Thread
from queue import Queue
import time

"""
  Multiple TCP connections in place of the Hub Computer for now
"""
class FakeHub:

    def __init__(self):
        # The connections and their ports
        self.device_dict = {
            "DaemonStat": 50000,
            "DaemonCmd": 50001,
            "TPCReadoutStat": 50002,
            "TPCReadoutCmd": 50003,
        }

        self.device_title = [
                {'name': device_name, 'title': device_name + " [" + str(self.device_dict[device_name]) + "]"}
                 for device_name in self.device_dict
        ]

        self.queues = {name: Queue() for name in self.device_dict}

        self.io_context = None
        self.devices = {}
        self.connections_open = False


    def get_device_names(self):
        if not self.connections_open:
            raise ConnectionError("No connections open..")
        return self.device_title

    def get_devices(self):
        if not self.connections_open:
            raise ConnectionError("No connections open..")
        return self.devices

    def start_connection(self):
        # Start the ASIO IO context and start the servers
        self.io_context = IOContext()
        self.devices = {device_name: TCPConnection(self.io_context, "127.0.0.1", self.device_dict[device_name],
                                              True, device_name.endswith("Cmd"), device_name.endswith("Stat")) # <server> <heartbeat> <monitor>
                                    for device_name in self.device_dict
                   }
        self.devices["DaemonStat"].run_ctx(self.io_context)

        for device_name in self.devices:
            t = Thread(target=self.stream_device, args=(device_name,), daemon=True)
            t.start()

        self.connections_open = True

    def shutdown_connections(self):
        # Stop the connections
        for device_name in self.devices:
            print("Stopping connection " + device_name + "..")
            self.devices[device_name].stop_ctx(self.io_context)
        print("Closed all server TCP/IP connections...")
        self.connections_open = False

    def get_telemetry_data(self, device_name):
        return self.queues[device_name].get()

    def clear_queues(self):
        for queue in self.queues:
            num_elements = 0
            while not self.queues[queue].empty():
                self.queues[queue].get()
                num_elements += 1
            print("Cleared " + str(num_elements) + " elements from queue " + queue)

    def stream_device(self, device_name):
        """Continuously read from a TCP connection and emit received commands."""
        tcp_conn = self.devices[device_name]
        while self.connections_open:
            cmd_list = tcp_conn.read_recv_buffer(1000)
            for cmd in cmd_list:
                self.queues[device_name].put({'name': device_name, 'timestamp_sec': time.time(),
                                              "cmd": cmd.command, 'args': cmd.arguments})
            time.sleep(0.5)
