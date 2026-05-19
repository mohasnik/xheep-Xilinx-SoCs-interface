# Copyright 2026 Politecnico di Torino.
#
# File: jtag_static.py
# Static AXI JTAG address helper for PetaLinux systems.


class xheepStaticJTAG:
    def __init__(self, mem_addr: int = 0xA4000000):
        self.memAddr = int(mem_addr)

    def getAddr(self) -> int:
        return self.memAddr

