#!/usr/bin/env python3
import argparse, json, socket, struct, time

HDR = struct.Struct('!Id')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=5001)
    ap.add_argument('--idle', type=float, default=3.0)
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    s.bind(('0.0.0.0', a.port))
    s.settimeout(0.5)

    rx = rb = dup = reorder = 0
    seen, max_seq = set(), -1
    first = last = None
    prev_d, jit, dsum = None, 0.0, 0.0
    t0 = time.time()

    while True:
        try:
            data, _ = s.recvfrom(65535)
        except socket.timeout:
            if (last and time.time() - last > a.idle) or (not last and time.time() - t0 > 60):
                break
            continue
        now = time.time()
        if len(data) < HDR.size:
            continue
        seq, ts = HDR.unpack_from(data)
        if seq in seen:
            dup += 1
            continue
        seen.add(seq)
        if seq < max_seq:
            reorder += 1
        max_seq = max(max_seq, seq)
        rx += 1
        rb += len(data)
        first = first or now
        last = now
        d = (now - ts) * 1000
        dsum += d
        if prev_d is not None:
            jit += abs(d - prev_d)
        prev_d = d

    res = dict(rx_pkts=rx, rx_bytes=rb, dup=dup, reorder=reorder, max_seq=max_seq,
               window=(last - first) if rx > 1 else 0,
               avg_delay_ms=dsum / rx if rx else None,
               jitter_ms=jit / (rx - 1) if rx > 1 else None)
    print('SERVER', res)
    if a.out:
        json.dump(res, open(a.out, 'w'))

if __name__ == '__main__':
    main()
