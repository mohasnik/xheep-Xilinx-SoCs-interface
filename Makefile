# Copyright 2026 Politecnico di Torino.
#
# File: Makefile
# Author: Christian Conti {christian.conti@polito.it}
# Date: 31/03/2026
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
# REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
# INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
# LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
# OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
# PERFORMANCE OF THIS SOFTWARE.
#
# Makefile for x-heep Xilinx SoCs interface utilities
# See the README for usage instructions

SHELL := /bin/bash
ROOT := $(shell pwd)
USER := xilinx
NOTEBOOK_DIR := /home/$(USER)/jupyter_notebooks/xheep

# Run parameters — all overridable on the command line
LINKER    ?= on_chip
OVERLAY   ?= xilinx_core_v_mini_mcu_wrapper.bit
APP       ?= 
JTAG_ADDR ?= 0xA4000000
GPIO_ADDR ?= 0xA4020000
SPI_ADDR  ?= 0xA4030000
SPI_RANGE ?= 0x00010000
UART_DEV  ?= /dev/ttyUL0
UART_BAUD ?= 115200

.PHONY: help install install-notebook uninstall uninstall-notebook run run-vpk180 clean

# help target prints this file with comments as descriptions for each target
help:
	@FILE_FOR_HELP=Makefile util/MakefileHelp

## @section Setup & Installation

## Install dependencies and configure board environment
## Requires sudo privileges to manage system packages and ConfigFS
install:
	@sudo -v || (echo "sudo is required. Run 'sudo -v' to cache credentials and retry." && exit 1)
	@sudo bash util/install_apt.sh
	@sudo bash util/install_openocd.sh
	@sudo bash util/config_bashrc.sh

## Uninstall and remove PATH entries from shell profiles
uninstall:
	@sudo rm -rf /usr/local/src/openocd
	@sudo rm -f /usr/local/bin/openocd
	@sudo sed -i '/source \/etc\/profile.d\/pynq_venv.sh/d' /root/.bashrc
	@sudo sed -i '/cd \/home\/xilinx/d' /root/.bashrc

## Uninstall notebook files from jupyter_notebooks directory
## @param USER=xilinx(default) Username for jupyter installation path
uninstall-notebook:
	@rm -rf $(NOTEBOOK_DIR)
	@echo "Notebook uninstalled from $(NOTEBOOK_DIR)"

## Install notebook and dependencies to jupyter_notebooks directory
## @param USER=xilinx(default) Username for jupyter installation path
install-notebook:
	@bash util/install_python.sh
	@mkdir -p $(NOTEBOOK_DIR)/src
	@mkdir -p $(NOTEBOOK_DIR)/cfg
	@mkdir -p $(NOTEBOOK_DIR)/dts
	@cp notebook/xheepNotebook.ipynb $(NOTEBOOK_DIR)/
	@cp notebook/notebookUtils.py $(NOTEBOOK_DIR)/
	@cp -r src/xheepDriver $(NOTEBOOK_DIR)/src/
	@cp src/xheepRun.py $(NOTEBOOK_DIR)/src/
	@cp cfg/xheep_xilinx_xvc.cfg $(NOTEBOOK_DIR)/cfg/
	@cp dts/*.tpl $(NOTEBOOK_DIR)/dts/
	@echo "Notebook installed to $(NOTEBOOK_DIR)"

## @section Execution

## Run application on x-heep via JTAG (or flash for flash_load/flash_exec)
## @param LINKER=on_chip      	  			Execution mode: on_chip, flash_load, flash_exec
## @param OVERLAY=/path/to/bitstream.bit    Path to FPGA bitstream
## @param APP=/path/to/application.elf      Path to application image (.elf or .bin)
run:
	@if [ -z "$(APP)" ]; then \
		echo "Error: set APP=/path/to/application.elf (or .bin)"; \
		exit 1; \
	fi
	@python3 src/xheepRun.py -o $(OVERLAY) -f $(APP) -l $(LINKER)

## Run application on VPK180 through static AXI helper addresses
## @param LINKER=on_chip                    Execution mode: on_chip, flash_load, flash_exec
## @param APP=/path/to/application.elf      Path to X-HEEP ELF or BIN
## @param JTAG_ADDR=0xA4000000              AXI JTAG base address
## @param GPIO_ADDR=0xA4020000              AXI GPIO base address
## @param SPI_ADDR=0xA4030000               AXI Quad SPI base address
## @param SPI_RANGE=0x00010000              AXI Quad SPI address range
## @param UART_DEV=/dev/ttyUL0              AXI UARTLite device
## @param UART_BAUD=115200                  AXI UARTLite baud rate
run-vpk180:
	@if [ -z "$(APP)" ]; then \
		echo "Error: set APP=/path/to/application.elf (or .bin)"; \
		exit 1; \
	fi
	@python3 src/xheepRun_vpk180.py -f $(APP) -l $(LINKER) --jtag-addr $(JTAG_ADDR) --gpio-addr $(GPIO_ADDR) --spi-addr $(SPI_ADDR) --spi-range $(SPI_RANGE) --uart $(UART_DEV) --baud $(UART_BAUD)

## @section Cleanup

## Clean all build artifacts
clean:
	@echo "Clean complete"
