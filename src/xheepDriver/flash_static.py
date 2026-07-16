# Copyright 2026 Politecnico di Torino.
#
# File: flash_static.py
# SPI flash programmer for static /dev/mem based deployments.

import time
from pathlib import Path
from typing import Tuple

from .gpio_static import xheepStaticGPIO
from .logger import log
from .mmio import DevMemMMIO


class xheepStaticFlashProgrammer:
    # AXI Quad SPI register offsets (PG153)
    SRR = 0x40
    SPICR = 0x60
    SPISR = 0x64
    SPIDTR = 0x68
    SPIDRR = 0x6C
    SPISSR = 0x70

    # SPI flash commands (compatible with W25Q128JV and similar)
    CMD_WRITE_ENABLE = 0x06
    CMD_READ_STATUS1 = 0x05
    CMD_PAGE_PROGRAM = 0x02
    CMD_SECTOR_ERASE = 0x20
    CMD_READ_DATA = 0x03
    CMD_JEDEC_ID = 0x9F

    PAGE_SIZE = 256
    SECTOR_SIZE = 4096
    STATUS_WIP = 0x01
    FIFO_DEPTH = 16

    def __init__(self, spi_addr: int, gpio: xheepStaticGPIO, spi_range: int = 0x10000):
        self.spi_addr = spi_addr
        self.spi_range = spi_range
        self.spi = DevMemMMIO(spi_addr, spi_range)
        self.gpio = gpio
        self._initialized = False

    def close(self) -> None:
        if self.spi:
            try:
                self.spi.close()
            except Exception:
                pass
            self.spi = None

    def _spi_reset(self) -> None:
        self.spi.write(self.SRR, 0x0000000A)
        time.sleep(0.05)

    def _spi_init(self) -> None:
        self._spi_reset()

        spicr = (1 << 6) | (1 << 5) | (1 << 2) | (1 << 1) | (1 << 7)
        self.spi.write(self.SPICR, spicr)
        time.sleep(0.01)

        spicr = (1 << 2) | (1 << 1) | (1 << 7) | (1 << 8)
        self.spi.write(self.SPICR, spicr)
        time.sleep(0.01)

        self._initialized = True

    def _cs_assert(self) -> None:
        self.spi.write(self.SPISSR, 0xFFFFFFFE)

    def _cs_deassert(self) -> None:
        self.spi.write(self.SPISSR, 0xFFFFFFFF)

    def _wait_tx_empty(self, timeout_ms: int = 100) -> bool:
        for _ in range(timeout_ms):
            spisr = self.spi.read(self.SPISR)
            if spisr & (1 << 2):
                return True
            time.sleep(0.001)
        return False

    def _flush_rx(self) -> None:
        for _ in range(256):
            spisr = self.spi.read(self.SPISR)
            if spisr & (1 << 0):
                break
            self.spi.read(self.SPIDRR)

    def _start_transfer(self) -> None:
        spicr = self.spi.read(self.SPICR) & ~(1 << 8)
        self.spi.write(self.SPICR, spicr)

    def _stop_transfer(self) -> None:
        spicr = self.spi.read(self.SPICR) | (1 << 8)
        self.spi.write(self.SPICR, spicr)

    def _transfer(self, tx_data: bytes, rx_len: int = 0) -> bytes:
        tx_buf = bytes(tx_data) + bytes(rx_len)
        total = len(tx_buf)

        self._flush_rx()
        self._cs_assert()
        time.sleep(0.0001)

        rx_data = []
        offset = 0
        started = False

        while offset < total:
            chunk = min(self.FIFO_DEPTH, total - offset)
            for i in range(chunk):
                self.spi.write(self.SPIDTR, tx_buf[offset + i])

            if not started:
                self._start_transfer()
                started = True

            if not self._wait_tx_empty(timeout_ms=1000):
                spisr = self.spi.read(self.SPISR)
                log("error", f"SPI timeout at offset {offset}/{total}, SPISR=0x{spisr:08X}")
                self._spi_init()
                return b""

            time.sleep(0.001)

            for _ in range(chunk):
                if self.spi.read(self.SPISR) & 1:
                    break
                rx_data.append(self.spi.read(self.SPIDRR) & 0xFF)

            offset += chunk

        self._stop_transfer()
        self._cs_deassert()

        return bytes(rx_data)

    def read_jedec_id(self) -> Tuple[int, int, int]:
        rx = self._transfer(bytes([self.CMD_JEDEC_ID]), rx_len=3)
        if len(rx) >= 4:
            return (rx[1], rx[2], rx[3])
        return (0, 0, 0)

    def read_status1(self) -> int:
        rx = self._transfer(bytes([self.CMD_READ_STATUS1]), rx_len=1)
        return rx[1] if len(rx) >= 2 else 0xFF

    def write_enable(self) -> None:
        self._transfer(bytes([self.CMD_WRITE_ENABLE]))
        time.sleep(0.001)

    def wait_busy(self, timeout_s: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status = self.read_status1()
            if not (status & self.STATUS_WIP):
                return True
            time.sleep(0.01)
        return False

    def sector_erase(self, addr: int) -> bool:
        self.write_enable()
        cmd = bytes([
            self.CMD_SECTOR_ERASE,
            (addr >> 16) & 0xFF,
            (addr >> 8) & 0xFF,
            addr & 0xFF,
        ])
        self._transfer(cmd)
        return self.wait_busy(timeout_s=3.0)

    def page_program(self, addr: int, data: bytes) -> bool:
        if len(data) > self.PAGE_SIZE:
            data = data[:self.PAGE_SIZE]
        if not data:
            return True

        self.write_enable()
        cmd = bytes([
            self.CMD_PAGE_PROGRAM,
            (addr >> 16) & 0xFF,
            (addr >> 8) & 0xFF,
            addr & 0xFF,
        ]) + data
        self._transfer(cmd)
        return self.wait_busy(timeout_s=5.0)

    def read_data(self, addr: int, length: int) -> bytes:
        cmd = bytes([
            self.CMD_READ_DATA,
            (addr >> 16) & 0xFF,
            (addr >> 8) & 0xFF,
            addr & 0xFF,
        ])
        rx = self._transfer(cmd, rx_len=length)
        return rx[4:] if len(rx) > 4 else b""

    def program_binary(self, data: bytes, start_addr: int = 0, verify: bool = True, erase: bool = True) -> bool:
        self.gpio.setChannel(0x1F)
        time.sleep(0.1)

        self.close()
        self.spi = DevMemMMIO(self.spi_addr, self.spi_range)
        time.sleep(0.05)

        self._spi_init()

        jedec = self.read_jedec_id()
        if jedec[0] == 0xFF or jedec[0] == 0x00:
            log("error", "No flash detected or communication error")
            return False

        data_len = len(data)
        log("info", f"Programming {data_len} bytes ({data_len/1024:.1f} KB) starting at 0x{start_addr:06X}")

        if erase:
            start_sector = start_addr // self.SECTOR_SIZE
            end_sector = (start_addr + data_len - 1) // self.SECTOR_SIZE
            num_sectors = end_sector - start_sector + 1

            for i in range(num_sectors):
                sector_addr = (start_sector + i) * self.SECTOR_SIZE
                if not self.sector_erase(sector_addr):
                    log("error", f"Failed to erase sector at 0x{sector_addr:06X}")
                    return False
                if (i + 1) % 16 == 0:
                    log("info", f"  Erased {i + 1}/{num_sectors} sectors")

        log("info", "Programming flash...")
        offset = 0
        page_count = 0
        total_pages = (data_len + self.PAGE_SIZE - 1) // self.PAGE_SIZE

        while offset < data_len:
            addr = start_addr + offset
            page_data = data[offset:offset + self.PAGE_SIZE]

            if not self.page_program(addr, page_data):
                log("error", f"Failed to program page at 0x{addr:06X}")
                return False

            offset += len(page_data)
            page_count += 1

            if page_count % 64 == 0:
                progress = (page_count / total_pages) * 100
                log("info", f"  Programmed {page_count}/{total_pages} pages ({progress:.1f}%)")

        log("info", f"Programming complete: {page_count} pages written")

        if verify:
            log("info", "Verifying flash contents...")
            offset = 0
            errors = 0

            while offset < data_len:
                chunk_size = min(256, data_len - offset)
                addr = start_addr + offset
                read_data = self.read_data(addr, chunk_size)
                expected = data[offset:offset + chunk_size]

                if read_data != expected:
                    errors += 1
                    if errors <= 5:
                        log("error", f"Verification failed at 0x{addr:06X}")

                offset += chunk_size

                if offset % (64 * 256) == 0:
                    progress = (offset / data_len) * 100
                    log("info", f"  Verified {offset}/{data_len} bytes ({progress:.1f}%)")

            if errors > 0:
                log("error", f"Verification failed with {errors} errors")
                return False

        return True

    def program_file(self, filepath: Path, start_addr: int = 0, verify: bool = True, erase: bool = True) -> bool:
        if not filepath.exists():
            log("error", f"File not found: {filepath}")
            return False

        data = filepath.read_bytes()
        return self.program_binary(data, start_addr, verify, erase)
