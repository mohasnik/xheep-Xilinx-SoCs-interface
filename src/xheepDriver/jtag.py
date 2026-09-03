# Copyright 2026 Politecnico di Torino.
#
# File: jtag.py
# Author: Christian Conti {christian.conti@polito.it}
# Date: 31/03/2026

from pynq import Overlay
from driver import PLOverlay


class xheepJTAG:
    def __init__(self, overlay: PLOverlay, memAddr: int, memRng: int):
        self._ol = overlay
        self.memAddr = int(memAddr)
        self.memRng = int(memRng)
        
    def getAddr(self) -> int:
        return self.memAddr