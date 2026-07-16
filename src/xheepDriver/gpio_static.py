# Copyright 2026 Politecnico di Torino.
#
# File: gpio_static.py
# AXI GPIO control for static PetaLinux systems such as Versal VPK180.

import time
from typing import Tuple

from .mmio import DevMemMMIO


class xheepStaticGPIO:
    CH1_DATA = 0x00
    CH1_TRI = 0x04
    CH2_DATA = 0x08
    CH2_TRI = 0x0C

    BIT_RST_NI = 0
    BIT_BOOTSEL = 1
    BIT_EXECFLASH = 2
    BIT_TRST_NI = 3
    BIT_SPI_SEL = 4

    EXIT_VALID = 0
    EXIT_VALUE = 1

    def __init__(self, mem_addr: int = 0xA4020000, mem_range: int = 0x10000):
        self._mmio = DevMemMMIO(mem_addr, mem_range)

        self._mmio.write(self.CH1_TRI, 0x0)
        self._mmio.write(self.CH2_TRI, 0x3)

        initial_val = (1 << self.BIT_RST_NI) | (1 << self.BIT_TRST_NI)
        self._mmio.write(self.CH1_DATA, initial_val)
        time.sleep(10e-3)

    def close(self) -> None:
        self._mmio.close()

    def setBit(self, channel: int, bit: int, value: bool) -> None:
        reg_offset = channel << 3
        reg = self._mmio.read(reg_offset)
        reg = (reg | (1 << bit)) if value else (reg & ~(1 << bit))
        self._mmio.write(reg_offset, reg)

    def getBit(self, channel: int, bit: int) -> int:
        return (self._mmio.read(channel << 3) >> bit) & 0x1

    def setChannel(self, value: int) -> None:
        self._mmio.write(self.CH1_DATA, value & 0x1F)

    def getChannel(self, channel: int) -> int:
        return self._mmio.read(channel << 3)

    def setSpiFlashControl(self, use_ps: bool) -> None:
        self.setBit(0, self.BIT_SPI_SEL, bool(use_ps))
        time.sleep(20e-3)

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

    def loadFromFlash(self) -> None:
        self.setBit(0, self.BIT_BOOTSEL, 1)
        self.setBit(0, self.BIT_EXECFLASH, 0)

    def execFromFlash(self) -> None:
        self.setBit(0, self.BIT_BOOTSEL, 1)
        self.setBit(0, self.BIT_EXECFLASH, 1)

    def getExitCode(self) -> Tuple[int, int]:
        exit_val = self.getChannel(1)
        exit_valid = (exit_val >> self.EXIT_VALID) & 0x1
        exit_value = (exit_val >> self.EXIT_VALUE) & 0x1
        return exit_valid, exit_value
