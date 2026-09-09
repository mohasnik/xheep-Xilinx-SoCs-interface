# Copyright 2026 Politecnico di Torino.
#
# File: driver.py
# Author: Christian Conti {christian.conti@polito.it}
# Date: 31/03/2026

import os
import time
from pathlib import Path

from pynq import Overlay, PL

from .logger import log
from .gpio import xheepGPIO
from .uart import xheepUART
from .spi import xheepSPI
from .jtag import xheepJTAG
from .flash import xheepFlashProgrammer
from abc import abstractmethod






class xheepDriver():

    IP_GPIO = "axi_gpio"
    IP_UART = "axi_uartlite"
    IP_JTAG = "axi_jtag"
    IP_SPI  = "axi_quad_spi"


    def __init__(self, overlay_path, **kwargs):
        overlay_path = Path(overlay_path)
        self.AXI_GPIO_ADDR = 0
        self.AXI_GPIO_RNG  = 0
        self.AXI_UART_ADDR = 0
        self.AXI_UART_RNG  = 0
        self.AXI_JTAG_ADDR = 0
        self.AXI_JTAG_RNG  = 0

    def _createUART(self, axi_uart_address : int):
        self.uart = xheepUART(axi_uart_address)
        self.uart.unbind()
        return True

    @abstractmethod
    def _getBoardInfo(self) -> str:
        return ""


    

        
        
        
         



class xheepDriver(Overlay):
    IP_GPIO = "axi_gpio"
    IP_UART = "axi_uartlite"
    IP_JTAG = "axi_jtag"
    IP_SPI  = "axi_quad_spi"

    def __init__(self, overlay_path, **kwargs):
        overlay_path = Path(overlay_path)
        super().__init__(str(overlay_path), download=False, **kwargs)

        gpio_ip = self.ip_dict[self.IP_GPIO]
        uart_ip = self.ip_dict[self.IP_UART]
        jtag_ip = self.ip_dict[self.IP_JTAG]
        spi_ip  = self.ip_dict.get(self.IP_SPI)

        self.AXI_GPIO_ADDR = int(gpio_ip["phys_addr"])
        self.AXI_GPIO_RNG  = int(gpio_ip["addr_range"])
        self.AXI_UART_ADDR = int(uart_ip["phys_addr"])
        self.AXI_UART_RNG  = int(uart_ip["addr_range"])
        self.AXI_JTAG_ADDR = int(jtag_ip["phys_addr"])
        self.AXI_JTAG_RNG  = int(jtag_ip["addr_range"])

        self.uart = xheepUART(self.AXI_UART_ADDR)
        self.uart.unbind()

        # SPI IRQ is at concat position 2 (In2), UART is at position 0 (In0)
        # So SPI_IRQ = UART_IRQ + 2
        board = os.getenv("BOARD", "pynq-z2").lower()
        if board == "aup-zu3":
            spi_irq = 92  # UltraScale+: UART=90 (In0), In1=91, SPI=92 (In2)
        else:
            spi_irq = 32  # Zynq-7000: UART=30 (In0), In1=31, SPI=32 (In2)

        if spi_ip:
            self.AXI_SPI_ADDR = int(spi_ip["phys_addr"])
            self.AXI_SPI_RNG  = int(spi_ip["addr_range"])
            self.spi = xheepSPI(self.AXI_SPI_ADDR, spi_irq)
            self.spi.unbind()
        else:
            log("warning", "SPI IP not found - PL SPI overlay unavailable (flash may still be accessible via PS mux)")
            self.spi = None

        PL.reset()
        time.sleep(0.2)  # Wait for PL reset to complete (like debug_spi.py)
        self.download()
        time.sleep(0.1)  # Wait for fabric to stabilize after bitstream load

        self.gpio = xheepGPIO(self, self.AXI_GPIO_ADDR, self.AXI_GPIO_RNG)
        self.jtag = xheepJTAG(self, self.AXI_JTAG_ADDR, self.AXI_JTAG_RNG)

        self.uart.bind()
        if spi_ip:
            self.flash_programmer = xheepFlashProgrammer(self.AXI_SPI_ADDR, self.gpio)
        else:
            self.flash_programmer = None