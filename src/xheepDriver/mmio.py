# Copyright 2026 Politecnico di Torino.
#
# File: mmio.py
# Minimal /dev/mem MMIO helper for static PetaLinux deployments.

import mmap
import os
import struct


class DevMemMMIO:
    def __init__(self, base_addr: int, addr_range: int = 0x10000):
        page_size = mmap.PAGESIZE
        page_base = base_addr & ~(page_size - 1)
        page_offset = base_addr - page_base
        map_size = page_offset + addr_range

        self._fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self._mem = mmap.mmap(
            self._fd,
            map_size,
            mmap.MAP_SHARED,
            mmap.PROT_READ | mmap.PROT_WRITE,
            offset=page_base,
        )
        self._offset = page_offset

    def read(self, offset: int) -> int:
        self._mem.seek(self._offset + offset)
        return struct.unpack("<I", self._mem.read(4))[0]

    def write(self, offset: int, value: int) -> None:
        self._mem.seek(self._offset + offset)
        self._mem.write(struct.pack("<I", value & 0xFFFFFFFF))

    def close(self) -> None:
        self._mem.close()
        os.close(self._fd)

