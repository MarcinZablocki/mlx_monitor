#!/usr/bin/env python3
import os
import shutil
from rich.console import Console
from rich.live import Live
from rich.table import Table
from fastcore.xtras import sparkline
import time
from collections import OrderedDict
import socket
import fcntl
import struct
import array

SIOCETHTOOL = 0x8946
ETHTOOL_GSTRINGS = 0x0000001b
ETHTOOL_GSSET_INFO = 0x00000037
ETHTOOL_GSTATS = 0x0000001d
ETH_SS_STATS = 0x1
ETH_GSTRING_LEN = 32

class Ethtool(object):
    """
    A class for interacting with the ethtool API to retrieve network interface card (NIC) statistics.
    """

    def __init__(self, ifname):
        """
        Initializes an Ethtool object.

        Args:
            ifname (str): The name of the network interface.

        """
        self.ifname = ifname
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)

    def _send_ioctl(self, data):
        """
        Sends an ioctl request to the network interface.

        Args:
            data (bytes): The data to be sent.

        Returns:
            bytes: The response from the ioctl request.

        """
        ifr = struct.pack('16sP', self.ifname.encode("utf-8"), data.buffer_info()[0])
        return fcntl.ioctl(self._sock.fileno(), SIOCETHTOOL, ifr)

    def get_gstringset(self, set_id):
        """
        Retrieves the set of strings associated with a given set ID.

        Args:
            set_id (int): The ID of the set.

        Yields:
            str: The strings associated with the set.

        """
        sset_info = array.array('B', struct.pack("IIQI", ETHTOOL_GSSET_INFO, 0, 1 << set_id, 0))
        self._send_ioctl(sset_info)
        sset_mask, sset_len = struct.unpack("8xQI", sset_info)
        if sset_mask == 0:
            sset_len = 0

        strings = array.array("B", struct.pack("III", ETHTOOL_GSTRINGS, ETH_SS_STATS, sset_len))
        strings.extend(b'\x00' * sset_len * ETH_GSTRING_LEN)
        self._send_ioctl(strings)
        for i in range(sset_len):
            offset = 12 + ETH_GSTRING_LEN * i
            s = strings[offset:offset+ETH_GSTRING_LEN].tobytes().partition(b'\x00')[0].decode("utf-8")
            yield s

    def get_nic_stats(self):
        """
        Retrieves the NIC statistics.

        Yields:
            tuple: A tuple containing the statistic name and its corresponding value.

        """
        strings = list(self.get_gstringset(ETH_SS_STATS))
        n_stats = len(strings)

        stats = array.array("B", struct.pack("II", ETHTOOL_GSTATS, n_stats))
        stats.extend(struct.pack('Q', 0) * n_stats)
        self._send_ioctl(stats)
        for i in range(n_stats):
            offset = 8 + 8 * i
            value = struct.unpack('Q', stats[offset:offset+8])[0]
            yield (strings[i], value)

  
# find infiniBand devices
def get_ib_devices():
    ib_devices = []
    p = sorted(os.listdir('/sys/class/infiniband'))

    for device in p:
        if 'mlx' in device:
            ib_devices.append({"mlx" :device, "net": os.listdir('/sys/class/infiniband/{}/device/net'.format(device))[0]})
    return ib_devices

# find ib devices that are up
def get_up_ib_devices():
    ib_devices = get_ib_devices()
    up_ib_devices = []
    for device in ib_devices:
        netdevice = os.listdir('/sys/class/infiniband/{}/device/net'.format(device["mlx"]))[0]
        if os.path.exists('/sys/class/infiniband/{}/device/net/{}'.format(device["mlx"], netdevice)):
            with open('/sys/class/infiniband/{}/device/net/{}/operstate'.format(device["mlx"], netdevice), 'r') as f:
                if f.read().strip() == 'up':
                    up_ib_devices.append(device)
    return up_ib_devices

ibd = get_up_ib_devices()
stats = {}

for device in ibd:
    # initialize stats
    d = Ethtool(device["net"])
    ethtool_data = {k: v for k, v in d.get_nic_stats()}  
    stats[device["mlx"]] = {}
    stats[device["mlx"]]["rx_bytes_phy"] = [ethtool_data["rx_bytes_phy"]] * 20
    stats[device["mlx"]]["tx_bytes_phy"] = [ethtool_data["tx_bytes_phy"]] * 20
    
def update_stats():
    for device in ibd: 
        d = Ethtool(device["net"])
        ethtool_data = {k: v for k, v in d.get_nic_stats()}
        
        stats[device["mlx"]]["rx_bytes_phy"].append(ethtool_data["rx_bytes_phy"])
        stats[device["mlx"]]["tx_bytes_phy"].append(ethtool_data["tx_bytes_phy"])
        stats[device["mlx"]]["rx_bytes_phy"].pop(0)
        stats[device["mlx"]]["tx_bytes_phy"].pop(0)
    return stats

table = Table()
table.add_column("Device", style="cyan", no_wrap=True)
table.add_column("Graph", justify="right")

def generate_table() -> Table:
    # Generate rich table
    
    stats = update_stats()
    
    table = Table()
    table.add_column("Device", style="cyan", no_wrap=True)
    table.add_column("Net", style="cyan", no_wrap=True)
    table.add_column("TX", justify="right")
    table.add_column("RX", justify="right")
    table.add_column("Throughput", justify="right", width=32)
    
    # TODO: Fix the list comprehension to be more understandable
    
    for device in ibd:
        table.add_row(
            device["mlx"], device["net"], 
            sparkline([(stats[device["mlx"]]["rx_bytes_phy"][i] - stats[device["mlx"]]["rx_bytes_phy"][i-1])//1000000 for i in range(1, len(stats[device["mlx"]]["rx_bytes_phy"]))]), 
            sparkline([(stats[device["mlx"]]["tx_bytes_phy"][i] - stats[device["mlx"]]["tx_bytes_phy"][i-1])//1000000 for i in range(1, len(stats[device["mlx"]]["tx_bytes_phy"]))]), 
            str(f'{(stats[device["mlx"]]["rx_bytes_phy"][-1] - stats[device["mlx"]]["rx_bytes_phy"][-2])/1000000:.2f} / {(stats[device["mlx"]]["tx_bytes_phy"][-1] - stats[device["mlx"]]["tx_bytes_phy"][-2])/1000000:.2f} Mbps'))
            
            
    
    return table

#print(ibd)
with Live(generate_table(), refresh_per_second=1) as live:
    while True:
        live.update(generate_table())
        time.sleep(0.1)