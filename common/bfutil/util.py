from scapy.all import *
from scapy.utils import PcapWriter

from random import randint

def port_to_pipe(port):
    local_port = port & 0x7F
    pipe = port >> 7
    return pipe

def simple_eth_pkt(pktlen, dmac=None):
    if dmac:
        pkt = Ether(dst=dmac)
    else:
        pkt = Ether(src='AA:AA:AA:AA:AA:AA',dst='FF:FF:FF:FF:FF:FF') / IP() / UDP()
    pkt = pkt / Raw('\x00' * (pktlen - len(pkt)))
    return pkt

def pgen_timer_hdr_to_dmac(pipe_id, app_id, batch_id, packet_id):
    """
    Given the fields of a 6-byte packet-gen header return an Ethernet MAC address
    which encodes the same values.
    """
    pipe_shift = 3

    return '%02x:00:%02x:%02x:%02x:%02x' % ((pipe_id << pipe_shift) | app_id,
                                            batch_id >> 8,
                                            batch_id & 0xFF,
                                            packet_id >> 8,
                                            packet_id & 0xFF)

def build_expected_pkts(app_cfg, pktgen_pipe_id, g_timer_app_id):
    cfg = app_cfg.get_cfg()

    pktlen = cfg['pkt_len']
    p_count = cfg['packets_per_batch_cfg']
    b_count = cfg['batch_count_cfg']

    pkt_lst = []
    pkt_len = [pktlen] * p_count * b_count

    for batch in range(b_count):
        for pkt_num in range(p_count):
            dmac = pgen_timer_hdr_to_dmac(pktgen_pipe_id, g_timer_app_id, batch, pkt_num)
            p_exp = simple_eth_pkt(pktlen=pktlen, dmac=dmac)
            pkt_lst.append(p_exp)

    return pkt_lst

def random_mac():
    return '02:00:00:{:02x}:{:02x}:{:02x}'.format(
        randint(0, 0xff),
        randint(0, 0xff),
        randint(0, 0xff)
    )

def random_ip():
    addr = random.randint(0,0xFFFFFFFF)
    return socket.inet_ntoa(struct.pack('!L', addr))

def random_port():
    return random.randint(1,10000)

def create_random_flows(n):
    flows = []
    for i in range(n):
        flows.append({
            'src_addr': random_ip(),
            'dst_addr': random_ip(),
            'src_port': random_port(),
            'dst_port': random_port(),
        })
    return flows

def get_timestamped_pkt_from_iface(iface):
    pkts = sniff(iface=iface, count=1)
    pkt = bytes(pkts[0])

    print(pkt)

    # Parse the payload and extract the timestamps
    # import pdb; pdb.set_trace()
    ts_ingress_mac, ts_ingress_global, \
        ts_enqueue, ts_dequeue_delta, \
        ts_egress_global, ts_egress_tx = \
        struct.unpack("!QQIIQQ", pkt[-40:])

    ns = 1000000000.0
    print("Timestamps")
    print("  raw values in ns:")
    print("    ingress mac                   : {:>15}".format(ts_ingress_mac))
    print("    ingress global                : {:>15}".format(ts_ingress_global))
    print("    traffic manager enqueue       : {:>15}".format(ts_enqueue))
    print("    traffic manager dequeue delta : {:>15}".format(ts_dequeue_delta))
    print("    egress global                 : {:>15}".format(ts_egress_global))
    print("    egress tx (no value in model) : {:>15}".format(ts_egress_tx))
    print("  values in s:")
    print("    ingress mac                   : {:>15.9f}".format(ts_ingress_mac / ns))
    print("    ingress global                : {:>15.9f}".format(ts_ingress_global / ns))
    print("    traffic manager enqueue       : {:>15.9f}".format(ts_enqueue / ns))
    print("    traffic manager dequeue delta : {:>15.9f}".format(ts_dequeue_delta / ns))
    print("    egress global                 : {:>15.9f}".format(ts_egress_global / ns))
    print("    egress tx (no value in model) : {:>15.9f}".format(ts_egress_tx))
    print("Please note that the timestamps are using the internal time " +
                "of the model/chip. They are not synchronized with the global time. "
                "Furthermore, the traffic manager timestamps in the model do not " +
                "accurately reflect the packet processing. Correct values are shown " +
                "by the hardware implementation.")

