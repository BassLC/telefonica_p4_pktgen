import logging

from bfutil.util import simple_eth_pkt
from bfutil.Table import Table

from pprint import pprint, pformat
from enum import Enum

import bfrt_grpc.bfruntime_pb2 as bfruntime_pb2
import bfrt_grpc.client as gc
import grpc

class PktgenConfig():
    def __init__(self):
        # default values
        self.cfg = {
            'timer_nanosec': 1000000000,
            'pkt_len': 100,
            'pkt_buffer_offset': 144,
            'increment_source_port': True,
            'batch_count_cfg': 1,
            'packets_per_batch_cfg': 1,
            'ibg': 0,
            'ibg_jitter': 0,
            'ipg': 0,
            'ipg_jitter': 0,
        }
    
    def get_config(self):
        return self.cfg
    
    def set_packets_per_batch(self, packets_per_batch):
        self.cfg['packets_per_batch_cfg'] = packets_per_batch
    
    def get_packets_per_batch(self):
        return self.cfg['packets_per_batch_cfg']

    def set_batch_count_cfg(self, batch_count_cfg):
        self.cfg['batch_count_cfg'] = batch_count_cfg
    
    def get_batch_count_cfg(self):
        return self.cfg['batch_count_cfg']
    
    def get_packet_length(self):
        return self.cfg['pkt_len']
        
    def set_packet_length(self, pkt_len):
        self.cfg['pkt_len'] = pkt_len
    
    def get_pkt_buffer_offset(self):
        return self.cfg['pkt_buffer_offset']
    
    def set_pkt_buffer_offset(self, pkt_buffer_offset):
        self.cfg['pkt_buffer_offset'] = pkt_buffer_offset
    
    def set_timer_given_pps(self, pps):
        packets_per_batch = self.get_packets_per_batch()
        batch_count_cfg = self.get_batch_count_cfg()

        batch_frequency = int(1e9 * packets_per_batch * batch_count_cfg / pps)
        self.cfg['timer_nanosec'] = batch_frequency
    
    def set_max_throughput(self):
        self.cfg['timer_nanosec'] = 0
        self.cfg['ibg'] = 0
        self.cfg['ibg_jitter'] = 0
        self.cfg['ipg'] = 0
        self.cfg['ipg_jitter'] = 0
    
    def _build_table_data(self, local_port):
        return [
            gc.DataTuple('timer_nanosec', self.cfg['timer_nanosec']),
            gc.DataTuple('app_enable', bool_val=False),
            gc.DataTuple('pkt_len', self.cfg['pkt_len'] - 6),
            gc.DataTuple('pkt_buffer_offset', self.cfg['pkt_buffer_offset']),
            gc.DataTuple('pipe_local_source_port', local_port),
            gc.DataTuple('increment_source_port', bool_val=self.cfg['increment_source_port']),
            gc.DataTuple('batch_count_cfg', self.cfg['batch_count_cfg'] - 1),
            gc.DataTuple('packets_per_batch_cfg', self.cfg['packets_per_batch_cfg'] - 1),
            gc.DataTuple('ibg', self.cfg['ibg']),
            gc.DataTuple('ibg_jitter', self.cfg['ibg_jitter']),
            gc.DataTuple('ipg', self.cfg['ipg']),
            gc.DataTuple('ipg_jitter', self.cfg['ipg_jitter']),

            # These counters will be incremented when pktgen is executing,
            # they probably should not be modified beforehand.
            gc.DataTuple('batch_counter', 0),
            gc.DataTuple('pkt_counter', 0),
            gc.DataTuple('trigger_counter', 0)
        ]

class PktgenTrigger(Enum):
    """
    Types of trigger:
        trigger_timer_one_shot
        trigger_timer_periodic
        trigger_port_down        TODO: confirm
        trigger_recirc_pattern   TODO: confirm
        trigger_dprsr            TODO: confirm
        trigger_pfc              TODO: confirm
    """

    ONE_SHOT = 'trigger_timer_one_shot'
    PERIODIC = 'trigger_timer_periodic'
    PORT_DOWN = 'trigger_port_down'
    RECIRCULATION = 'trigger_recirc_pattern'
    DEPARSER = 'trigger_dprsr'
    PFC = 'trigger_pfc'

class Pktgen():

    def __init__(self, client, bfrt_info):
        self.gc = client
        self.bfrt_info = bfrt_info
        self.logger = logging.getLogger('Pktgen')
        self.apps = {}

        self.logger.info("Setting up port_cfg table...")
        self.port_cfg = self.bfrt_info.table_get("port_cfg")
        
        self.logger.info("Setting up app_cfg table...")
        self.app_cfg = self.bfrt_info.table_get("app_cfg")

        self.logger.info("Setting up pkt_buffer table...")
        self.pkt_buffer = self.bfrt_info.table_get("pkt_buffer")
    
    def get_app_port(self, app_id):
        assert app_id in self.apps.keys()
        return self.apps[app_id]['source_port']

    def _get_pktgen_port_status(self, local_port):
        target = gc.Target(device_id=0)

        resp = self.port_cfg.entry_get(
            target,
            [
                self.port_cfg.make_key([ gc.KeyTuple('dev_port', local_port) ])
            ],
            { "from_hw": False },
            self.port_cfg.make_data([ gc.DataTuple("pktgen_enable")], get=True)
        )

        data_dict = next(resp)[0].to_dict()
        return data_dict["pktgen_enable"]
    
    def _enable_pktgen_port(self, local_port):
        """
        Given a pipe return a port in that pipe which is usable for packet
        generation.  Note that Tofino allows ports 68-71 in each pipe to be used for
        packet generation while Tofino2 allows ports 0-7.  This example will use
        either port 68 or port 6 in a pipe depending on chip type.
        """

        min_port = 68
        max_port = 71
        
        assert local_port in range(min_port, max_port + 1)

        # Check if port is already enabled for pktgen
        if self._get_pktgen_port_status(local_port):
            return

        target = gc.Target(device_id=0)
        
        self.port_cfg.entry_add(
            target,
            [
                self.port_cfg.make_key([ gc.KeyTuple('dev_port', local_port) ])
            ],
            [
                self.port_cfg.make_data([ gc.DataTuple('pktgen_enable', bool_val=True)])
            ]
        )

        assert self._get_pktgen_port_status(local_port)

        return
    
    def _add_app(self, app_id, local_port, config, trigger):
        assert isinstance(config, PktgenConfig)
        assert isinstance(trigger, PktgenTrigger)

        target = gc.Target(device_id=0)

        table_data = config._build_table_data(local_port)
        pktlen = config.get_packet_length()
        pkt_buffer_offset = config.get_pkt_buffer_offset()

        self.app_cfg.entry_add(
            target,
            [
                self.app_cfg.make_key([ gc.KeyTuple('app_id', app_id) ])
            ],
            [
                self.app_cfg.make_data(table_data, trigger.value)
            ]
        )

        # Configure pkt_buffer table
        pkt = simple_eth_pkt(pktlen=pktlen)

        self.pkt_buffer.entry_add(
            target,
            [
                self.pkt_buffer.make_key([
                    gc.KeyTuple('pkt_buffer_offset', pkt_buffer_offset),
                    gc.KeyTuple('pkt_buffer_size', (pktlen - 6))
                ])
            ],
            [
                self.pkt_buffer.make_data([
                    gc.DataTuple('buffer', bytearray(bytes(pkt)[6:]))
                ])
            ]
        )
    
    def _set_app(self, app_id, local_port, config, trigger):
        assert isinstance(config, PktgenConfig)
        assert isinstance(trigger, PktgenTrigger)

        target = gc.Target(device_id=0)

        table_data = config._build_table_data(local_port)
        pktlen = config.get_packet_length()
        pkt_buffer_offset = config.get_pkt_buffer_offset()

        self.app_cfg.entry_mod(
            target,
            [
                self.app_cfg.make_key([ gc.KeyTuple('app_id', app_id) ])
            ],
            [
                self.app_cfg.make_data(table_data, trigger.value)
            ]
        )

        # Configure pkt_buffer table
        pkt = simple_eth_pkt(pktlen=pktlen)

        self.pkt_buffer.entry_add(
            target,
            [
                self.pkt_buffer.make_key([
                    gc.KeyTuple('pkt_buffer_offset', pkt_buffer_offset),
                    gc.KeyTuple('pkt_buffer_size', (pktlen - 6))
                ])
            ],
            [
                self.pkt_buffer.make_data([
                    gc.DataTuple('buffer', bytearray(bytes(pkt)[6:]))
                ])
            ]
        )

    def set_app(self, app_id, local_port, config, trigger):
        assert isinstance(config, PktgenConfig)
        assert isinstance(trigger, PktgenTrigger)

        target = gc.Target(device_id=0)
        self._enable_pktgen_port(local_port)
        
        if app_id not in self.apps:
            self._add_app(app_id, local_port, config, trigger)           
        else:
            self._set_app(app_id, local_port, config, trigger)
        
        self.apps[app_id] = {
            'source_port': local_port,
            'trigger': trigger,
        }
       
    def start(self, app_id):
        assert app_id in self.apps.keys()

        self.logger.info('Enabling pktgen app {}'.format(app_id))

        target = gc.Target(device_id=0)

        self.app_cfg.entry_mod(
            target,
            [
                self.app_cfg.make_key([ gc.KeyTuple('app_id', app_id) ])
            ],
            [
                self.app_cfg.make_data(
                    [ gc.DataTuple('app_enable', bool_val=True) ],
                    self.apps[app_id]['trigger'].value
                )
            ]
        )
    
    def stop(self, app_id):
        assert app_id in self.apps.keys()

        self.logger.info('Disabling pktgen app {}'.format(app_id))

        target = gc.Target(device_id=0)

        self.app_cfg.entry_mod(
            target,
            [
                self.app_cfg.make_key([ gc.KeyTuple('app_id', app_id) ])
            ],
            [
                self.app_cfg.make_data(
                    [ gc.DataTuple('app_enable', bool_val=False) ],
                    self.apps[app_id]['trigger'].value
                )
            ]
        )
    
    def get_report(self, app_id):
        assert app_id in self.apps.keys()

        target = gc.Target(device_id=0)

        resp = self.app_cfg.entry_get(
            target,
            [
                self.app_cfg.make_key([ gc.KeyTuple('app_id', app_id) ])
            ],
            { "from_hw": True }
        )

        data_dict = next(resp)[0].to_dict()

        return {
            'batch_counter': data_dict['batch_counter'],
            'pkt_counter': data_dict['pkt_counter'],
            'trigger_counter': data_dict['trigger_counter'],
        }
