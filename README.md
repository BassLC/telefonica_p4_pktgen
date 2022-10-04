# Packet Generator for Telefónica P4 Switches

Python3 controller code to configure the Packet Generation
mechanism, in Barefoot's SDE 9.1.1.

Targets the [Advanced Programmable Switch BF2556X-1T, from
APS Networks](https://www.opencompute.org/documents/210216-bf2556x-1t-switch-specifications-v2-pdf-1)

It is slightly different than most public versions (in terms of
table names, and config options), so it was mainly adapted from
the examples provided with the SDE, but made simpler to use.

Should work after loading the p4 code with `./run_switchd`, just
run the script on the same machine, or configure gRPC to be exposed
from another external machine.

It might depend on some extra python2/3 libs supplied with the SDE.
