import os, socket, time
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub

H1, H2 = '10.0.0.1', '10.0.0.2'
UDP_PORT = 5001
PRIO_GENERIC, PRIO_MEASURE = 10, 100
CTRL_ADDR = ('127.0.0.1', 9999)
POLL_SEC = 1.0
THRESH_MBPS = 9.0            # reroute when offered L2 rate on s1 exceeds this
MIN_DT = 0.5                 # ignore rate samples closer together than this (s)
REQUIRED_HITS = 2            # consecutive samples above threshold before rerouting

# s1: 1=h1 2=s2(bottleneck) 3=s3 | s2: 1=h2 2=s1 3=s3 | s3: 1=s1 2=s2
PATHS = {
    'direct': {1: {H1: 1, H2: 2}, 2: {H1: 2, H2: 1}, 3: {H1: 1, H2: 2}},
    'alt':    {1: {H1: 1, H2: 3}, 2: {H1: 3, H2: 1}, 3: {H1: 1, H2: 2}},
}


class UdpMeasureController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dps, self.snap = {}, {}
        self.path, self.mode, self.prev, self.hits = 'direct', 'dynamic', None, 0
        os.makedirs('results', exist_ok=True)
        self.flow_csv = open('results/flow_stats.csv', 'a', buffering=1)
        self.snap_csv = open('results/snapshots.csv', 'a', buffering=1)
        self.evt_csv = open('results/events.csv', 'a', buffering=1)
        hub.spawn(self._poll_loop)
        hub.spawn(self._control_loop)

    # ---------- helpers ----------
    def log_event(self, msg):                                                                                        #logs an event to the console
        self.logger.info(msg)                                                                                        
        self.evt_csv.write(f'{time.time():.3f},{msg}\n')

    def _flow(self, dp, prio, match, port, cmd):                                                                      #sends one flow rule to a switch
        p, o = dp.ofproto_parser, dp.ofproto
        inst = [p.OFPInstructionActions(o.OFPIT_APPLY_ACTIONS, [p.OFPActionOutput(port)])]
        dp.send_msg(p.OFPFlowMod(datapath=dp, command=cmd, priority=prio,
                                 match=match, instructions=inst))

    def install(self, dp, path, cmd):                                                                                 #installs all rules for one switch
        p = dp.ofproto_parser
        table = PATHS[path][dp.id]
        for ip, port in table.items():
            self._flow(dp, PRIO_GENERIC, p.OFPMatch(eth_type=0x0800, ipv4_dst=ip), port, cmd)
        self._flow(dp, PRIO_MEASURE,
                   p.OFPMatch(eth_type=0x0800, ip_proto=17, ipv4_dst=H2, udp_dst=UDP_PORT),
                   table[H2], cmd)

    def reroute(self, path, reason):                                                                                  #moves all switches to a new path
        self.path = path
        for dp in self.dps.values():
            # MODIFY_STRICT keeps the flow counters (important for the comparison)
            self.install(dp, path, dp.ofproto.OFPFC_MODIFY_STRICT)
        self.log_event(f'reroute_to_{path};{reason}')

    # ---------- OpenFlow events ----------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)                                                  #runs when a switch first connects               
    def on_features(self, ev):
        dp = ev.msg.datapath
        self.dps[dp.id] = dp
        self.install(dp, self.path, dp.ofproto.OFPFC_ADD)
        self.log_event(f'switch_connected;s{dp.id}')

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)                                                    #runs when a switch replies with flow stats
    def on_flow_stats(self, ev):
        dpid, now = ev.msg.datapath.id, time.time()
        for st in ev.msg.body:
            if st.priority != PRIO_MEASURE:
                continue
            self.flow_csv.write(f'{now:.3f},{dpid},{st.packet_count},{st.byte_count}\n')
            if dpid in self.snap:
                self.snap_csv.write(f'{self.snap.pop(dpid)},{dpid},{st.packet_count},{st.byte_count}\n')
            if dpid == 1:
                self._check_congestion(st)

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)                                                         #runs when a port chanes state                         
    def on_port_status(self, ev):
        msg, dp = ev.msg, ev.msg.datapath
        down = bool(msg.desc.state & dp.ofproto.OFPPS_LINK_DOWN)
        self.log_event(f'port_status;s{dp.id};port={msg.desc.port_no};down={down}')
        if (self.mode == 'dynamic' and dp.id in (1, 2) and msg.desc.port_no == 2
                and down and self.path == 'direct'):
            self.reroute('alt', 'link_s1_s2_down')

    # ---------- dynamic logic ----------
    def _check_congestion(self, st):
        # Switch-side flow-duration clock, so a delayed Ryu thread can't skew dt.
        t = st.duration_sec + st.duration_nsec / 1e9
        if self.prev is None or t < self.prev[0]:      # first sample, or flow was reset
            self.prev = (t, st.byte_count)
            return
        dt = t - self.prev[0]
        if dt < MIN_DT:                                # e.g. extra reply from SNAPSHOT
            return
        rate = (st.byte_count - self.prev[1]) * 8 / dt / 1e6
        self.prev = (t, st.byte_count)
        if self.mode == 'dynamic' and self.path == 'direct':
            if rate > THRESH_MBPS:
                self.hits += 1
                if self.hits >= REQUIRED_HITS:
                    self.hits = 0
                    self.reroute('alt', f'offered_rate={rate:.2f}Mbps')
            else:
                self.hits = 0

    # ---------- background loops ----------
    def _req(self, dp):                                                                                                     #asks a switch for its flow statistics
        dp.send_msg(dp.ofproto_parser.OFPFlowStatsRequest(dp))

    def _poll_loop(self):                                                                                                   #endless polling loop
        while True:
            for dp in list(self.dps.values()):
                self._req(dp)
            hub.sleep(POLL_SEC)

    def _control_loop(self):                                                                                                #creates a UDP socket and sends the replies back
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(CTRL_ADDR)
        while True:
            data, addr = sock.recvfrom(1024)
            parts = data.decode().split()
            sock.sendto(self._handle(parts).encode(), addr)

    def _handle(self, parts):                                                                                               #command interpreter 
        if not parts:
            return 'ERR'
        cmd = parts[0].upper()
        if cmd == 'MODE' and len(parts) == 2 and parts[1] in ('static', 'dynamic'):
            self.mode = parts[1]
            self.log_event(f'mode;{self.mode}')
            return f'OK mode={self.mode}'
        if cmd == 'RESET':
            self.prev, self.hits = None, 0
            self.reroute('direct', 'reset')
            return 'OK reset'
        if cmd == 'SNAPSHOT' and len(parts) == 2:
            for dp in self.dps.values():
                self.snap[dp.id] = parts[1]
                self._req(dp)
            return f'OK snapshot {parts[1]}'
        return 'ERR unknown'
