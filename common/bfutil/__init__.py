import sys
import os

# add BF Python to search path
try:
    # Import BFRT GRPC stuff
    import bfrt_grpc.bfruntime_pb2 as bfruntime_pb2
    import bfrt_grpc.client as gc
    import grpc
except:
    python_v = '{}.{}'.format(sys.version_info.major, sys.version_info.minor)
    sde_install = os.environ['SDE_INSTALL']
    tofino_libs = '{}/lib/python{}/site-packages/tofino'.format(sde_install, python_v)
    sys.path.append(tofino_libs)

    import bfrt_grpc.bfruntime_pb2 as bfruntime_pb2
    import bfrt_grpc.client as gc
    import grpc

from bfutil.Pktgen import *
from bfutil.Table import * 
from bfutil.util import * 