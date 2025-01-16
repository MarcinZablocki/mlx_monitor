#!/usr/bin/env python3
import sys
import os
import platform
import socket
import fcntl
import struct
import array
from collections import OrderedDict
from time import sleep

try: 
    import nvidia_smi
    GPUs = True
except Exception as e: 
    print(e)
    GPUs = False
     
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.table import Table
from rich.text import Text
from rich import box
from rich.panel import Panel
from fastcore.xtras import sparkline
from datetime import datetime

SIOCETHTOOL = 0x8946
ETHTOOL_GSTRINGS = 0x0000001b
ETHTOOL_GSSET_INFO = 0x00000037
ETHTOOL_GSTATS = 0x0000001d
ETH_SS_STATS = 0x1
ETH_GSTRING_LEN = 32

if GPUs: 
    try: 
        nvidia_smi.nvmlInit()
    except: 
        GPUs = False
        
if GPUs:
        
    gpu_utilization = {}
    memory_utilization = {}
    deviceCount = nvidia_smi.nvmlDeviceGetCount()

    for d in range(deviceCount):
        gpu_utilization[d] = [0] * 20
        memory_utilization[d] = [0] * 20
        
else: 
    deviceCount = 0
    
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


def make_layout() -> Layout:
    layout = Layout(name="root")
    if deviceCount > 0:
        layout.split(
            Layout(name="header", size=3),
            Layout(name="gpu", size=11),
            Layout(name="main", ratio=1),
        )
    else: 
        layout.split(
            Layout(name="header", size=3),
            Layout(name="main")
        )

    return layout

class Header:
    """Display header with clock."""

    def __rich__(self) -> Panel:
        grid = Table.grid()
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="right", ratio=1)
        text=datetime.now().ctime().replace(":", "[blink]:[/]")
        
        #grid.add_row(
        #    text," |",
            #f" {os.getlogin()}@{platform.node()}"
        
        
        return Panel(grid, box=box.SIMPLE)
    
class Footer:
    

    def __rich__(self) -> Panel:
        grid = Table.grid(expand=True)
        grid.add_column(justify="center", ratio=1)

        grid.add_row(
        platform.node(),
        )
        #return Panel(grid, style="white on blue")
        return Panel(grid)

# find infiniBand devices
def get_ib_devices():
    ib_devices = []
    try: 
        p = sorted(os.listdir('/sys/class/infiniband'))
    except FileNotFoundError:
        #sys.exit("No InfiniBand devices found")
        ib_devices = []
        return ib_devices

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

def generate_table() -> Table:
    # Generate rich table
    
    stats = update_stats()
    
    table = Table(expand=False, box=box.SIMPLE_HEAD, padding=(0,0,0,1))
    table.add_column("Device", justify="left", style="dark_orange", no_wrap=True)
    table.add_column("Net", justify="left", style="dark_orange", no_wrap=True)
    table.add_column("TX", justify="left", min_width=20, max_width=22)
    table.add_column("RX", justify="left", min_width=20, max_width=22)
    table.add_column("Throughput", justify="left", min_width=3)
    
    # TODO: Fix the list comprehension to be more understandable
    
    for device in sorted(ibd, key=lambda x: x["net"]):
        table.add_row(
            device["mlx"], device["net"], 
            sparkline([(stats[device["mlx"]]["rx_bytes_phy"][i] - stats[device["mlx"]]["rx_bytes_phy"][i-1])//1000 for i in range(1, len(stats[device["mlx"]]["rx_bytes_phy"]))]), 
            sparkline([(stats[device["mlx"]]["tx_bytes_phy"][i] - stats[device["mlx"]]["tx_bytes_phy"][i-1])//1000 for i in range(1, len(stats[device["mlx"]]["tx_bytes_phy"]))]), 
            str(f'{(stats[device["mlx"]]["rx_bytes_phy"][-1] - stats[device["mlx"]]["rx_bytes_phy"][-2])/1000000:.2f} / {(stats[device["mlx"]]["tx_bytes_phy"][-1] - stats[device["mlx"]]["tx_bytes_phy"][-2])/1000000:.2f} Mbps'))
            
    return table

def gpu_table() -> Table:
    # Generate rich table
    
    table = Table(expand=False, box=box.SIMPLE_HEAD, padding=(0,0,0,1))
    table.add_column("Device", justify="left", style="dark_orange", no_wrap=True)
    table.add_column("GPU Utilization", justify="left", )
    table.add_column("GPU %", justify="left", )
    table.add_column("MEM %", justify="left", )
    for i in range(deviceCount):
        gpu_utilization[i].append(nvidia_smi.nvmlDeviceGetUtilizationRates(nvidia_smi.nvmlDeviceGetHandleByIndex(i)).gpu)
        mem_info = nvidia_smi.nvmlDeviceGetMemoryInfo(nvidia_smi.nvmlDeviceGetHandleByIndex(i))
        memory_utilization = mem_info.used / mem_info.total * 100
        gpu_utilization[i].pop(0)
        table.add_row(f"GPU {i}", sparkline(gpu_utilization[i]), f"{gpu_utilization[i][-1]}%", f"{memory_utilization:.0f}%" f" ({mem_info.used // 1024**2} / {mem_info.total // 1024**2} MB)" f" (Busy: {nvidia_smi.nvmlDeviceGetUtilizationRates(nvidia_smi.nvmlDeviceGetHandleByIndex(i)).memory}%)")
                
    return table

layout = make_layout()
layout["header"].update(Header())
layout["main"].update(generate_table())
if deviceCount > 0:
    layout["gpu"].update(gpu_table())
    
#layout["footer"].update(Footer())

with Live(layout, refresh_per_second=10, screen=True): 
    while True:
        layout["main"].update(generate_table())
        if deviceCount > 0:
            layout["gpu"].update(gpu_table())
        sleep(0.1)



