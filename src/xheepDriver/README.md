
## xheepDriver - Modules and API

### driver.py
- **xheepDriver(Overlay)**: Manages and initializes all AXI peripherals (GPIO, UART, JTAG, SPI) on FPGA using PYNQ.
  - Attributes: gpio, uart, jtag, spi, flash_programmer.

### gpio.py
- **xheepGPIO**: Handles GPIO pins and boot/reset signals.
  - __init__(overlay, memAddr, memRng): Initialize GPIO peripheral.
  - setBit(channel, bit, value): Set a bit on a channel.
  - getBit(channel, bit): Read a bit from a channel.
  - setChannel(value): Set all output bits.
  - getChannel(channel): Read all bits from a channel.
  - setSpiFlashControl(use_ps): Select flash control between PS and X-HEEP.
  - assertReset()/deassertReset(): Control core reset.
  - resetXheep()/resetJTAG(): Hardware reset for X-HEEP/JTAG.
  - bootFromJTAG()/loadFromFlash()/execFromFlash(): Select boot mode.
  - getExitCode(): Read exit code from the core.

### uart.py
- **xheepUART**: Manages the UART peripheral via device tree overlay.
  - __init__(memAddr): Initialize UART peripheral.
  - _patchDts(), _dtsCompile(): Device tree handling and compilation.
  - _wait(), _pids_using_path(): Utilities for waiting and process checks.
  - unbind(), bind(): (De)activate UART in the system.

### spi.py
- **xheepSPI**: Manages the SPI peripheral via device tree overlay.
  - __init__(memAddr, irqId): Initialize SPI peripheral.
  - _patchDts(), _dtsCompile(): Device tree handling and compilation.
  - _get_spi_device(): Find the SPI device.
  - _wait(): Utility for waiting.
  - unbind(), bind(): (De)activate SPI in the system.

### flash.py
- **xheepFlashProgrammer**: Programs and manages the SPI flash memory.
  - __init__(spi_addr, gpio): Initialize flash programmer.
  - _spi_reset(), _spi_init(): Reset and setup SPI controller.
  - _cs_assert(), _cs_deassert(): Chip select control.
  - _transfer(), _wait_tx_empty(), _flush_rx(): SPI data transfer utilities.
  - read_jedec_id(), read_status1(): Read flash info.
  - write_enable(), wait_busy(): Enable write and wait for operation end.
  - sector_erase(), page_program(), read_data(): Basic flash operations.
  - program_binary(), program_file(): Program flash from data or file.

### jtag.py
- **xheepJTAG**: Manages the JTAG interface.
  - __init__(overlay, memAddr, memRng): Initialize JTAG peripheral.
  - getAddr(): Return JTAG base address.

### mmio.py
- **DevMemMMIO**: Minimal `/dev/mem` helper for static PetaLinux systems.
  - read(offset): Read a 32-bit little-endian register.
  - write(offset, value): Write a 32-bit little-endian register.

### gpio_static.py
- **xheepStaticGPIO**: AXI GPIO controller for systems without PYNQ.
  - Uses a fixed physical address, defaulting to VPK180 `0xA4020000`.
  - Provides the same reset, boot mode, JTAG reset, and exit status helpers as `xheepGPIO`.

### jtag_static.py
- **xheepStaticJTAG**: Static AXI JTAG address helper.
  - Uses a fixed physical address, defaulting to VPK180 `0xA4000000`.

### logger.py
- **log(level, msg, stderr=None)**: Print colored messages to stdout/stderr depending on the level.
