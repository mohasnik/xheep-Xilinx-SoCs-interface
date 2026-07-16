# Copyright 2026 Politecnico di Torino.
#
# File: __init__.py
# Author: Christian Conti {christian.conti@polito.it}
# Date: 31/03/2026

from .logger import log
from .flash_static import xheepStaticFlashProgrammer
from .gpio_static import xheepStaticGPIO
from .jtag_static import xheepStaticJTAG

try:
    from .gpio import xheepGPIO
    from .uart import xheepUART
    from .spi import xheepSPI
    from .jtag import xheepJTAG
    from .flash import xheepFlashProgrammer
    from .driver import xheepDriver
except ImportError:
    xheepGPIO = None
    xheepUART = None
    xheepSPI = None
    xheepJTAG = None
    xheepFlashProgrammer = None
    xheepDriver = None

__all__ = [
    "log",
    "xheepGPIO",
    "xheepUART",
    "xheepSPI",
    "xheepJTAG",
    "xheepFlashProgrammer",
    "xheepDriver",
    "xheepStaticFlashProgrammer",
    "xheepStaticGPIO",
    "xheepStaticJTAG",
]
