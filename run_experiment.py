#!/usr/bin/env python3
import argparse, os, socket, threading, time
from functools import partial
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel

BOTTLENECK_MBPS = 10
HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, 'results')


def build():
    net = Mininet(controller=None, link=TCLink, autoSetMacs=True, autoStaticArp=True,
                  switch=partial(OVSSwitch, protocols='OpenFlow13'))
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
    h1 = net.addHost('h1', ip='10.0.0.1/24')
    h2 = net.addHost('h2', ip='10.0.0.2/24')
    s1, s2, s3 = (net.addSwitch(n, dpid=f'{i:016x}') for i, n in ((1, 's1'), (2, 's2'), (3, 's3')))
    net.addLink(h1, s1, port2=1, bw=100)
    net.addLink(h2, s2, port2=1, bw=100)
    net.addLink(s1, s2, port1=2, port2=2, bw=BOTTLENECK_MBPS, max_queue_size=100)  # bottleneck
    net.addLink(s1, s3, port1=3, port2=1, bw=100)
    net.addLink(s2, s3, port1=3, port2=2, bw=100)
    return net


def ctl(cmd):
    """Send a command to the controller's UDP control port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    s.sendto(cmd.encode(), ('127.0.0.1', 9999))
    try:
        return s.recvfrom(1024)[0].decode()
    except socket.timeout:
        return 'NO REPLY (is ryu-manager running?)'


def run_test(net, mode, rate, size, duration, tag=None, fail_at=None):
    h1, h2 = net.get('h1', 'h2')
    label = tag or f'{mode}_{rate:g}'
    print(f'\n=== {label}: mode={mode} rate={rate} Mbps size={size} dur={duration}s ===')
    print(ctl(f'MODE {mode}'), '|', ctl('RESET'))
    time.sleep(1)
    ctl(f'SNAPSHOT {label}_before'); time.sleep(1.5)

    srv = h2.popen(['python3', f'{HERE}/udp_server.py', '--port', '5001',
                    '--idle', '3', '--out', f'{RES}/{label}_server.json'])
    time.sleep(1)
    timer = None
    if fail_at:
        timer = threading.Timer(fail_at, net.configLinkStatus, ['s1', 's2', 'down'])
        timer.start()
    out = h1.cmd(f'python3 {HERE}/udp_client.py --dst 10.0.0.2 --port 5001 --rate {rate} '
                 f'--size {size} --duration {duration} --out {RES}/{label}_client.json')
    print(out.strip())
    sout, serr = srv.communicate()
    print(sout.decode().strip())
    if serr:
        print('SERVER STDERR:', serr.decode().strip())
    if fail_at:
        timer.cancel()
        net.configLinkStatus('s1', 's2', 'up')
    time.sleep(1)
    ctl(f'SNAPSHOT {label}_after'); time.sleep(1.5)
    return label


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--modes', nargs='+', default=['static', 'dynamic'])
    ap.add_argument('--rates', nargs='+', type=float, default=[1, 2, 5, 8, 10, 12, 15, 20])
    ap.add_argument('--size', type=int, default=1000)
    ap.add_argument('--duration', type=int, default=20)
    ap.add_argument('--failover', action='store_true', help='link-failure scenario')
    ap.add_argument('--cli', action='store_true', help='open Mininet CLI instead of testing')
    a = ap.parse_args()

    os.makedirs(RES, exist_ok=True)
    setLogLevel('info')
    net = build()
    net.start()
    net.waitConnected()
    time.sleep(2)
    print('Warm-up ping:', net.ping([net.get('h1'), net.get('h2')]), '% loss')

    if a.cli:
        CLI(net)
    elif a.failover:
        for mode in a.modes:
            run_test(net, mode, 5, a.size, 20, tag=f'failover_{mode}', fail_at=8)
    else:
        for rate in a.rates:
            for mode in a.modes:
                run_test(net, mode, rate, a.size, a.duration)
    net.stop()


if __name__ == '__main__':
    main()
