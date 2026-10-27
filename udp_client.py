#!/usr/bin/env python3
import argparse, json, socket, struct, time

HDR = struct.Struct('!Id')            # seq (4 B) + send timestamp (8 B)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dst', required=True)
    ap.add_argument('--port', type=int, default=5001)
    ap.add_argument('--rate', type=float, required=True, help='offered load, Mbit/s (payload)')
    ap.add_argument('--size', type=int, default=1000, help='UDP payload bytes')
    ap.add_argument('--duration', type=float, default=20)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    pad = b'x' * (a.size - HDR.size)
    interval = a.size * 8 / (a.rate * 1e6)          # seconds between packets
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)

    seq = errs = 0
    start = time.perf_counter()
    nxt, end = start, start + a.duration
    while True:
        now = time.perf_counter()
        if now >= end:
            break
        if now < nxt:                                # pace the sender
            if nxt - now > 0.002:
                time.sleep(nxt - now - 0.001)
            continue
        try:
            s.sendto(HDR.pack(seq, time.time()) + pad, (a.dst, a.port))
            seq += 1
        except OSError:
            errs += 1
        nxt += interval
    elapsed = time.perf_counter() - start

    res = dict(tx_pkts=seq, tx_bytes=seq * a.size, elapsed=elapsed, send_errors=errs,
               rate_mbps=a.rate, size=a.size,
               tx_mbps=seq * a.size * 8 / elapsed / 1e6)
    print('CLIENT', res)
    if a.out:
        json.dump(res, open(a.out, 'w'))

if __name__ == '__main__':
    main()
