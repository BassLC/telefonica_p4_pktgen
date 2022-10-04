#!/usr/bin/env python

import os
import sys
import time
import argparse
import logging
from enum import Enum

import bfrt_grpc.bfruntime_pb2 as bfruntime_pb2
import bfrt_grpc.client as gc
import grpc


class PktGenTrigger(Enum):
    """
    Trigger types for packet generation.
    I have only tested the TIMER_ONE_SHOT packet generation.
    The other ones are here for completion sake,
    but I have not tested them.
    Good luck!
    Types of trigger:
        trigger_timer_one_shot
        trigger_timer_periodic
        trigger_port_down        
        trigger_recirc_pattern   
        trigger_dprsr            
        trigger_pfc              
    """

    # These first two are tested in p4_16-examples/pktgen
    TIMER_ONE_SHOT = "$PKTGEN_TRIGGER_TIMER_ONE_SHOT"
    TIMER_PERIODIC = '$PKTGEN_TRIGGER_TIMER_PERIODIC'

    # These only appear briefly in bf-drivers/pdfixed_thrift/gen-py/conn_mgr_pd_rpc/ttypes.py
    # I have no idea if they even work, or what they need to work
    PORT_DOWN = '$PKTGEN_TRIGGER_PORT_DOWN'
    RECIRCULATION = '$PKTGEN_TRIGGER_RECIRC_PATTERN'
    DEPARSER = '$PKTGEN_TRIGGER_DPRSR'
    PFC = '$PKTGEN_TRIGGER_PFC'


class PktGenPriv:

    def __init__(self, gc, bfrt_info, logger):
        self.gc = gc
        self.bfrt_info = bfrt_info
        self.app_cfg_table = self.bfrt_info.table_get(
            "$PKTGEN_APPLICATION_CFG")
        self.pkt_buffer_table = self.bfrt_info.table_get("$PKTGEN_PKT_BUFFER")
        self.port_cfg_table = self.bfrt_info.table_get("$PKTGEN_PORT_CFG")
        self.logger = logger
        self.has_started = False

    def _enable_port_pktgen(self):
        """
        Enable the packet generation in a specific port.
        For Tofino1 the port must be in [68..71];
        for Tofino2 the port must be in [0..7].
        """

        self.logger.info("Enabling pktgen port config")
        assert self.port_cfg_table

        port = self.config["port"]
        assert port in range(68, 72)

        key = self.port_cfg_table.make_key([gc.KeyTuple("dev_port", port)])
        value = self.port_cfg_table.make_data(
            [gc.DataTuple("pktgen_enable", bool_val=True)])
        self.port_cfg_table.entry_add(self.target, [key], [value])

        # Check that the values were written correctly
        get_value = self.port_cfg_table.make_data(
            [gc.DataTuple("pktgen_enable")], get=True)
        resp = self.port_cfg_table.entry_get(self.target, [key],
                                             {"from_hw": False}, get_value)

        data_dict = next(resp)[0].to_dict()
        assert data_dict["pktgen_enable"]

        self.logger.info("Pktgen port config was successful")

    def _config_app(self):
        """
        Setup the app configuration, depending on the config dict passed before.
        """
        self.logger.info("Setting up pktgen app config")

        # Config keys' meaning:
        # "app_id" = app id
        # "port" = sender port
        # "timer_nanosec" = timer delay to start, in ns
        # "pkt_len" = len of the packet to generate
        # "pkt_buffer_offset" = buffer offset to start reading packet data
        # "pkt_buffer" = bytearray to be copied into each generated packet
        # "pkt_buffer_size" = bytearray to be copied into each generated packet
        # "increment_source_port" = boolean, should pktgen increment the port in the packet
        # "batch_count_cfg" = how many batches should we generate (starting at 1)
        # "packets_per_batch_cfg" = how many packets per batch should pktgen generate (starting at 1)
        # "ibg" = idk
        # "ibg_jitter" = idk
        # "ipg" = idk
        # "ipg_jitter" = idk
        cfg = [
            gc.DataTuple('timer_nanosec', self.config['timer_nanosec']),
            gc.DataTuple('pkt_len', self.config['pkt_len']),
            gc.DataTuple('pkt_buffer_offset',
                         self.config['pkt_buffer_offset']),
            gc.DataTuple('pipe_local_source_port', self.config["port"]),
            gc.DataTuple('increment_source_port',
                         bool_val=self.config['increment_source_port']),
            gc.DataTuple('batch_count_cfg',
                         self.config['batch_count_cfg'] - 1),
            gc.DataTuple('packets_per_batch_cfg',
                         self.config['packets_per_batch_cfg'] - 1),
            gc.DataTuple('ibg', self.config['ibg']),
            gc.DataTuple('ibg_jitter', self.config['ibg_jitter']),
            gc.DataTuple('ipg', self.config['ipg']),
            gc.DataTuple('ipg_jitter', self.config['ipg_jitter']),

            # We want the pktgen functionality disabled by default
            gc.DataTuple('app_enable', bool_val=False),

            # These counters will be incremented when pktgen is executing,
            # they probably should not be modified beforehand.
            gc.DataTuple('batch_counter', 0),
            gc.DataTuple('pkt_counter', 0),
            gc.DataTuple('trigger_counter', 0)
        ]

        key = self.app_cfg_table.make_key(
            [gc.KeyTuple("app_id", self.config["app_id"])])

        data = self.app_cfg_table.make_data(cfg,
                                            self.config["pktgen_type"].value)
        self.app_cfg_table.entry_add(self.target, [key], [data])

        # Check if the entries we care about are correct
        resp = self.app_cfg_table.entry_get(
            self.target, [key], {"from_hw": False},
            self.app_cfg_table.make_data([
                gc.DataTuple('timer_nanosec'),
                gc.DataTuple('app_enable'),
                gc.DataTuple('pkt_len'),
                gc.DataTuple('pkt_buffer_offset'),
                gc.DataTuple('pipe_local_source_port'),
                gc.DataTuple('increment_source_port'),
                gc.DataTuple('batch_count_cfg'),
                gc.DataTuple('packets_per_batch_cfg'),
                gc.DataTuple('ibg'),
                gc.DataTuple('ibg_jitter'),
                gc.DataTuple('ipg'),
                gc.DataTuple('ipg_jitter')
            ],
                                         self.config["pktgen_type"].value,
                                         get=True))
        resp_dict = next(resp)[0].to_dict()

        keys_to_check = [
            "timer_nanosec", "pkt_len", "pkt_buffer_offset",
            "increment_source_port", "ibg", "ibg_jitter", "ipg", "ipg_jitter"
        ]

        for key in keys_to_check:
            assert resp_dict[key] == self.config[
                key], f"Key is different: {key}\tExpected: {self.config[key]}\tGot: {resp_dict[key]}"

        self.logger.info("Pktgen app config finished correctly")

    def _set_packet_buffer(self):
        """
        Set the packet buffer correctly
        NOTE: we reuse the pkt_len attribute for the pkt_buffer_size;
        these don't necessarily have to be the same, but idc
        """
        self.logger.info("Setting up the packet buffer in pktgen")
        key = self.pkt_buffer_table.make_key([
            gc.KeyTuple("pkt_buffer_offset", self.config["pkt_buffer_offset"]),
            gc.KeyTuple("pkt_buffer_size", self.config["pkt_len"])
        ])
        self.pkt_buffer_table.entry_add(self.target, [key], [
            self.pkt_buffer_table.make_data(
                [gc.DataTuple("buffer", self.config["pkt_buffer"])])
        ])
        # NOTE: I wanted to check if the buffer and everything was set correctly, but the operation
        # is invalid for some fucking reason that I don't want to know
        self.logger.info("Pktgen buffer setup correctly")

    def _enable_pktgen(self):
        """
        Enable the packet generation mechanism
        This should only be called after calling all the previous setup methods
        """
        self.logger.info("Enabling pktgen")
        self.app_cfg_table.entry_mod(self.target, [
            self.app_cfg_table.make_key(
                [gc.KeyTuple('app_id', self.config["app_id"])])
        ], [
            self.app_cfg_table.make_data(
                [gc.DataTuple('app_enable', bool_val=True)],
                self.config["pktgen_type"].value)
        ])
        self.logger.info("Pktgen was enabled successfully")

    def _disable_pktgen(self):
        """
        Disable the packet generation mechanism
        This should only be called after enabling it
        """
        self.logger.info("Disabling pktgen")
        self.app_cfg_table.entry_mod(self.target, [
            self.app_cfg_table.make_key(
                [gc.KeyTuple('app_id', self.config["app_id"])])
        ], [
            self.app_cfg_table.make_data(
                [gc.DataTuple('app_enable', bool_val=False)],
                self.config["pktgen_type"].value)
        ])
        self.logger.info("Pktgen was disabled successfully")

    def set_app(self, config):
        """
        Setup the application, accordingly to the config values passed
        """

        self.logger.info("Setting up pktgen")
        self.config = config
        self.target = self.gc.Target(device_id=0, pipe_id=0xffff)

        self._enable_port_pktgen()
        self._config_app()
        self._set_packet_buffer()
        self.logger.info("Pktgen finished setup correctly")

    def start(self):
        """
        Start the packet generation mechanism
        """
        self.logger.info("Starting pktgen")
        if self.has_started:
            self.logger.fatal("pktgen is already running")
        self.has_started = True
        self._enable_pktgen()
        self.logger.info("Started pktgen")

    def stop(self):
        """
        Stop the packet generation mechanism
        """
        self.logger.info("Stopping pktgen")
        if not self.has_started:
            self.logger.fatal("pktgen is not running")
        self.has_started = False
        self._disable_pktgen()
        self.logger.info("Stopped pktgen")

    def get_counters(self):
        key = self.app_cfg_table.make_key(
            [self.gc.KeyTuple('app_id', self.config["app_id"])])
        data = self.app_cfg_table.make_data([
            self.gc.DataTuple('batch_counter'),
            self.gc.DataTuple('pkt_counter'),
            self.gc.DataTuple('trigger_counter')
        ],
                                            self.config["pktgen_type"].value,
                                            get=True)
        resp = self.app_cfg_table.entry_get(self.target, [key],
                                            {"from_hw": True}, data)
        resp_dict = next(resp)[0].to_dict()
        return resp_dict


def main():

    # set up options
    argparser = argparse.ArgumentParser(
        description="Tofino pktgen app tester.")
    argparser.add_argument('--program_name',
                           type=str,
                           default='b_2pt_sender_tofino',
                           help='P4 program name')
    argparser.add_argument('--grpc_server',
                           type=str,
                           default='localhost',
                           help='GRPC server name/address')
    argparser.add_argument('--grpc_port',
                           type=int,
                           default=50052,
                           help='GRPC server port')
    argparser.add_argument('--topology', type=str, help='Topology file')
    args = argparser.parse_args()

    PROGRAM_NAME = args.program_name

    # Configure logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(PROGRAM_NAME)

    # Connect to GRPC server
    logger.info(
        'Connecting to GRPC server {}:{} and binding to program {}...'.format(
            args.grpc_server, args.grpc_port, PROGRAM_NAME))
    c = gc.ClientInterface('{}:{}'.format(args.grpc_server, args.grpc_port), 0,
                           0)
    c.bind_pipeline_config(PROGRAM_NAME)

    # Get all protobuf tables for program
    bfrt_info = c.bfrt_info_get(PROGRAM_NAME)

    pktgen = PktGenPriv(gc, bfrt_info, logger)

    config = {
        # I have no idea how to get the app_id, I just set it to 1 and roll with it
        # From my tests, we can go from [0..7] with no issue, even with 0 loaded programs
        # for some insane reason
        "app_id": 1,
        "port": 68,
        "timer_nanosec": 1000000000,
        "pkt_len": 100,
        "pkt_buffer_offset": 0,
        "pkt_buffer": bytearray([65] * 100),
        "increment_source_port": False,
        "batch_count_cfg": 1,
        "packets_per_batch_cfg": 2**16 - 1,
        "ibg": 0,
        "ibg_jitter": 0,
        "ipg": 0,
        "ipg_jitter": 0,
        "pktgen_type": PktGenTrigger.TIMER_ONE_SHOT
    }

    pktgen.set_app(config)

    d = pktgen.get_counters()
    print(f"{d}")
    pktgen.start()

    s = input("> ")
    while s != "quit":
        d = pktgen.get_counters()
        print(f"{d}")
        s = input("> ")
    pktgen.stop()

    # flush logs, stdout, stderr
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()


if __name__ == '__main__':
    main()
