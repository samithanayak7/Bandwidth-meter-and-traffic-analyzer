#!/usr/bin/env python3
import csv, glob, json, os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

R, HDR_OVERHEAD = 'results', 14 + 20 + 8        # Ethernet + IP + UDP = 42 B/packet

snap = {}
for label, dpid, pk, by in csv.reader(open(f'{R}/snapshots.csv')):
    snap[(label, int(dpid))] = (int(pk), int(by))

def delta(label, dpid):
    a, b = snap.get((label + '_after', dpid)), snap.get((label + '_before', dpid))
    return (a[0] - b[0], a[1] - b[1]) if a and b else (0, 0)

rows = []
for cf in sorted(glob.glob(f'{R}/*_client.json')):
    label = os.path.basename(cf)[:-12]
    c = json.load(open(cf))
    s = json.load(open(f'{R}/{label}_server.json'))
    el = c['elapsed']
    s1p, s1b = delta(label, 1)
    s2p, s2b = delta(label, 2)
    s3p, _ = delta(label, 3)
    rows.append(dict(
        label=label, mode=label.split('_')[0], offered=c['rate_mbps'],
        app_tx_pkts=c['tx_pkts'], app_rx_pkts=s['rx_pkts'],
        app_tx_mbps=round(c['tx_bytes'] * 8 / el / 1e6, 3),
        app_rx_mbps=round(s['rx_bytes'] * 8 / el / 1e6 - 0, 3),
        app_loss_pct=round((c['tx_pkts'] - s['rx_pkts']) / c['tx_pkts'] * 100, 2),
        flow_s1_pkts=s1p, flow_s2_pkts=s2p, flow_s3_pkts=s3p,
        flow_s1_mbps=round(s1b * 8 / el / 1e6, 3),
        flow_s2_mbps=round(s2b * 8 / el / 1e6, 3),
        flow_loss_pct=round((s1p - s2p) / s1p * 100, 2) if s1p else None,
        diff_tx_vs_s1=s1p - c['tx_pkts'],
        diff_rx_vs_s2=s2p - s['rx_pkts'],
        s1_bytes_vs_expected=s1b - c['tx_pkts'] * (c['size'] + HDR_OVERHEAD),
        avg_delay_ms=round(s['avg_delay_ms'], 2) if s['avg_delay_ms'] else None,
        jitter_ms=round(s['jitter_ms'], 3) if s['jitter_ms'] else None,
    ))

rows.sort(key=lambda r: (r['mode'], r['offered']))
with open(f'{R}/summary.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(rows)
for r in rows:
    print(r)

# ---- plots: static vs dynamic ----
def series(mode, key):
    pts = [(r['offered'], r[key]) for r in rows if r['mode'] == mode and 'failover' not in r['label']]
    return zip(*pts) if pts else ([], [])

for key, ylabel, fname in [('app_rx_mbps', 'Received throughput (Mbps, app)', 'throughput.png'),
                           ('app_loss_pct', 'Packet loss (%, app)', 'loss.png'),
                           ('flow_loss_pct', 'Packet loss (%, SDN flow stats s1−s2)', 'loss_flow.png')]:
    plt.figure()
    for mode in ('static', 'dynamic'):
        x, y = series(mode, key)
        plt.plot(x, y, marker='o', label=mode)
    plt.xlabel('Offered load (Mbps)'); plt.ylabel(ylabel); plt.grid(True); plt.legend()
    plt.savefig(f'{R}/{fname}', dpi=150, bbox_inches='tight')

# app vs network loss on one chart (static mode)
plt.figure()
for key, name in [('app_loss_pct', 'application'), ('flow_loss_pct', 'SDN flow stats')]:
    x, y = series('static', key)
    plt.plot(x, y, marker='o', label=name)
plt.xlabel('Offered load (Mbps)'); plt.ylabel('Loss (%)'); plt.grid(True); plt.legend()
plt.title('Static mode: application vs SDN-measured loss')
plt.savefig(f'{R}/app_vs_flow_loss.png', dpi=150, bbox_inches='tight')
print('Saved summary.csv and PNG plots in results/')
