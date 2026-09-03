# Copyright 2026 Politecnico di Torino.
#
# File: gpio.py
# Author: Christian Conti {christian.conti@polito.it}
# Date: 31/03/2026

import time
from pynq import Overlay, MMIO
from abc import abstractmethod
from mmio import PSMMIO, DevMemMMIO, PynqMemMMIO
from driver import PLOverlay




class xheepGPIO():
    # AXI GPIO register offsets
    CH1_DATA = 0x00
    CH1_TRI  = 0x04
    CH2_DATA = 0x08
    CH2_TRI  = 0x0C

    BIT_RST_NI    = 0     # rst_ni
    BIT_BOOTSEL   = 1     # boot_select_i
    BIT_EXECFLASH = 2     # execute_from_flash_i
    BIT_TRST_NI   = 3     # jtag_trst_ni
    BIT_SPI_SEL   = 4     # SPI flash mux select (0=X-HEEP, 1=PS)

    EXIT_VALID = 0
    EXIT_VALUE = 1


    def __init__(self, overlay : PLOverlay, mmio : PSMMIO):
        self._ol = overlay
        self._mmio = mmio

        # Set direction: CH1 = output, CH2 = input
        self._mmio.write(self.CH1_TRI, 0x0)
        self._mmio.write(self.CH2_TRI, 0x3)
        
        # Initialize GPIO values:
        # IMPORTANT: Start with PS controlling flash (SPI_SEL=1) to prevent
        # X-HEEP from sending garbage to flash before we configure things.
        # This matches what debug_spi.py does and avoids JEDEC read issues.
        # bit 0: rst_ni = 1 (not in reset)
        # bit 1: boot_select = 0 (JTAG boot)
        # bit 2: execute_from_flash = 0
        # bit 3: jtag_trst_ni = 1 (not in reset)
        # bit 4: spi_sel = 1 (PS control - prevents X-HEEP from touching flash)
        initial_val = (1 << self.BIT_RST_NI) | (1 << self.BIT_TRST_NI) | (1 << self.BIT_SPI_SEL)
        self._mmio.write(self.CH1_DATA, initial_val)
        time.sleep(10e-3)

    def setBit(self, channel: int, bit: int, value: bool):
        reg = int(self._mmio.read(channel << 3))
        reg = (reg | (1 << bit)) if value else (reg & ~(1 << bit))
        self._mmio.write(channel << 3, reg)
        
    def getBit(self, channel: int, bit: int) -> int:
        return (int(self._mmio.read(channel << 3)) >> bit) & 0x1

    def setChannel(self, value: int) -> None:
        self._mmio.write(self.CH1_DATA, (value & 0x1F))

    def getChannel(self, channel: int) -> int:
        return int(self._mmio.read(channel << 3))

    def assertReset(self) -> None:
        self.setBit(0, self.BIT_RST_NI, 0)
        time.sleep(1e-3)
    
    def deassertReset(self) -> None:
        self.setBit(0, self.BIT_RST_NI, 1)
        time.sleep(1e-3)
    
    def resetXheep(self) -> None:
        self.assertReset()
        self.deassertReset()
    
    def resetJTAG(self) -> None:
        self.setBit(0, self.BIT_TRST_NI, 0)
        time.sleep(1e-3)
        self.setBit(0, self.BIT_TRST_NI, 1)
        time.sleep(1e-3)

    def bootFromJTAG(self) -> None:
        self.setBit(0, self.BIT_BOOTSEL, 0)
        self.setBit(0, self.BIT_EXECFLASH, 0)
    
    def getExitCode(self) -> tuple[int, int]:
        exitVal = self.getChannel(1)
        exit_valid = (exitVal >> self.EXIT_VALID) & 0x1
        exit_value = (exitVal >> self.EXIT_VALUE) & 0x1
        return (exit_valid, exit_value)


class xheepStaticGPIO(xheepGPIO):
    def __init__(self, overlay : PLOverlay, mem_addr: int = 0xA4020000, mem_range: int = 0x10000):
        self._mmio : DevMemMMIO = None

        mmio = DevMemMMIO(mem_addr, mem_range)
        super().__init__(overlay, mmio)

    def close(self) -> None:
        self._mmio.close()


class xheepPynqGPIO(xheepGPIO):

    def __init__(self, overlay: Overlay, memAddr: int, memRng: int):
        mmio = PynqMemMMIO(memAddr, memRng)
        super().__init__(overlay, mmio)

    def setSpiFlashControl(self, use_ps: bool) -> None:
        """
        Set SPI flash control: True=PS, False=X-HEEP.
        When enabling PS control, set ALL GPIO bits high (0x1F) like debug_spi.py does.
        """
        # Read current value
        current = int(self._mmio.read(self.CH1_DATA))
        
        if use_ps:
            # Set ALL bits to 1 (0x1F) - this is what debug_spi.py does and it works
            # bit 0: rst_ni = 1
            # bit 1: boot_select = 1  
            # bit 2: execute_from_flash = 1
            # bit 3: jtag_trst_ni = 1
            # bit 4: spi_sel = 1 (PS control)
            new_val = 0x1F
        else:
            # Keep rst/jtag high, clear spi_sel
            new_val = (1 << self.BIT_RST_NI) | (1 << self.BIT_TRST_NI)
        
        self._mmio.write(self.CH1_DATA, new_val)
        time.sleep(20e-3)  # Wait for mux to settle

    def resetXheep(self) -> None:
        self.assertReset()
        self.deassertReset()

    def execFromFlash(self) -> None:
        self.setBit(0, self.BIT_BOOTSEL, 1)
        self.setBit(0, self.BIT_EXECFLASH, 1)    