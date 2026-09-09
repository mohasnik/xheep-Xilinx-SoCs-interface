# Copyright 2026 Politecnico di Torino.
#
# File: xheepRun.py
# Author: Christian Conti {christian.conti@polito.it}
# Date: 31/03/2026

import argparse
import hashlib
import os
import signal
import socket
import struct
import subprocess
import sys
import time
import telnetlib
import serial
from pathlib import Path
from typing import Optional, Tuple

# Import logging function from xheepDriver
from xheepDriver import log

STATE_FILE = Path("/tmp/.xheep_state")
TTY_DEVICE = "/dev/ttyUL0"

def flush_uart():
    try:
        ser = serial.Serial(TTY_DEVICE, 9600, timeout=0.1)
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        ser.close()
    except:
        pass

def file_hash(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()

def load_state() -> Optional[str]:
    if STATE_FILE.exists():
        try:
            return STATE_FILE.read_text().strip()
        except:
            pass
    return None

def save_state(bit_hash: str):
    try:
        STATE_FILE.write_text(bit_hash)
    except:
        pass

def wait_tcp(host: str, port: int, timeout: float):
    dl = time.monotonic() + timeout
    while time.monotonic() < dl:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except:
            time.sleep(0.1)
    raise TimeoutError(f"{host}:{port}")

def ocd_cmd(cmds, host="127.0.0.1", port=4444, timeout=30.0) -> str:
    tok = f"__D{time.monotonic_ns()}__"
    tn = telnetlib.Telnet(host, port, timeout=timeout)
    tn.read_until(b">", timeout=timeout)
    for c in cmds:
        tn.write(c.encode() + b"\n")
    tn.write(f"echo {tok}\n".encode())
    buf = tn.read_until(tok.encode(), timeout=timeout)
    return buf.decode(errors="replace")

def start_ocd(cfg: Path, log_file: Path, addr: int) -> Tuple[subprocess.Popen, object]:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log_file, "wb", buffering=0)
    proc = subprocess.Popen(
        ["openocd", "-c", f"set XVC_DEV_ADDR 0x{addr:08x}", "-f", str(cfg)],
        stdout=fh, stderr=fh
    )
    return proc, fh

def shutdown_ocd(proc, fh):
    try:
        ocd_cmd(["shutdown"], timeout=3.0)
    except:
        pass
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except:
            proc.kill()
            proc.wait()
    if fh:
        try:
            fh.close()
        except:
            pass

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--overlay", required=True, help="Path to .bit or .pdi")
    ap.add_argument("-f", "--firmware", required=True, help="Path to .elf or .bin")
    ap.add_argument("-l", "--linker", choices=["on_chip", "flash_load", "flash_exec"],
                    default="on_chip", help="Execution mode")
    ap.add_argument("--verify", action="store_true", help="Verify after load")
    ap.add_argument("--force", action="store_true", help="Force PL reload")

    args = ap.parse_args()

    bit = Path(args.overlay).resolve()
    fw = Path(args.firmware).resolve()
    cfg = Path("cfg/xheep_xilinx_xvc.cfg").resolve()


    for p, l in ((bit, "overlay"), (fw, "firmware"), (cfg, "cfg")):
        if not p.is_file():
            log("error", f"Missing {l}: {p}")
            return 2

    log_dir = Path("xheep_logs")
    ocd_log = log_dir / "openocd.log"

    # Check if bitstream changed
    cur_hash = file_hash(bit)
    prev_hash = load_state()
    need_reload = args.force or (cur_hash != prev_hash)

    # Import drivers
    from xheepDriver import xheepDriver, xheepGPIO, xheepJTAG, xheepSPI, xheepFlashProgrammer
    from pynq import Overlay

    # xheepDriver()

    if need_reload:
        log("info", "Loading bitstream...")
        xheep = xheepDriver(str(bit))
        save_state(cur_hash)
    else:
        log("info", "Reusing existing bitstream...")
        ol = Overlay(str(bit), download=False)
        gpio_ip = ol.ip_dict["axi_gpio"]
        jtag_ip = ol.ip_dict["axi_jtag"]
        spi_ip = ol.ip_dict.get("axi_quad_spi")

        class _Stub:
            def __init__(self):
                self.gpio = xheepGPIO(ol, int(gpio_ip["phys_addr"]), int(gpio_ip["addr_range"]))
                self.jtag = xheepJTAG(ol, int(jtag_ip["phys_addr"]), int(jtag_ip["addr_range"]))
                if spi_ip:
                    # SPI IRQ is at concat position 2 (In2), UART is at position 0 (In0)
                    # So SPI_IRQ = UART_IRQ + 2
                    board = os.getenv("BOARD", "pynq-z2").lower()
                    if board == "aup-zu3":
                        spi_irq = 92  # UltraScale+: UART=90 (In0), In1=91, SPI=92 (In2)
                    else:
                        spi_irq = 32  # Zynq-7000: UART=30 (In0), In1=31, SPI=32 (In2)
                    self.spi = xheepSPI(int(spi_ip["phys_addr"]), spi_irq)
                    # Note: don't bind here - we'll use direct MMIO for flash programming
                    self.flash_programmer = xheepFlashProgrammer(int(spi_ip["phys_addr"]), self.gpio)
                else:
                    self.spi = None
                    self.flash_programmer = None

        xheep = _Stub()

    xvc_addr = xheep.jtag.getAddr()

    # Handle flash operations using direct MMIO (does not require kernel drivers)
    if args.linker in ["flash_load", "flash_exec"]:
        # Need .bin file for flash
        if fw.suffix == ".elf":
            bin_file = fw.with_suffix(".bin")
            if not bin_file.exists():
                log("error", "Flash mode requires .bin file")
                return 2
        else:
            bin_file = fw

        # Check if flash programmer is available
        if not getattr(xheep, "flash_programmer", None):
            log("error", "Flash programmer not available (no SPI IP found)")
            return 2

        # Program flash using direct MMIO
        ok = False
        try:
            ok = xheep.flash_programmer.program_file(bin_file, verify=args.verify)
        except KeyboardInterrupt:
            log("warning", "Interrupted during flash programming")
            return 130
        except Exception as e:
            log("error", f"Flash programming failed: {e}")
            import traceback
            traceback.print_exc()
            ok = False

        if not ok:
            return 1

    try:
        input("Press enter to start the program on x-heep...")
    except KeyboardInterrupt:
        log("warning", "Interrupted before start")
        return 130

    # Ensure X-HEEP has flash control before starting execution
    # (GPIO starts with PS control to prevent flash corruption during setup)
    xheep.gpio.setSpiFlashControl(False)
    xheep.gpio.resetJTAG()

    # Hold X-HEEP in reset while configuring boot mode to prevent spurious
    # execution caused by BOOTSEL/EXECFLASH changing while RST_NI=1
    xheep.gpio.assertReset()

    # Configure boot mode (safe: RST_NI=0)
    if args.linker == "on_chip":
        xheep.gpio.bootFromJTAG()
    elif args.linker == "flash_load":
        xheep.gpio.loadFromFlash()
    elif args.linker == "flash_exec":
        xheep.gpio.execFromFlash()

    # Release reset - X-HEEP starts exactly once with the correct boot mode
    xheep.gpio.deassertReset()
    time.sleep(0.1)

    # For flash_exec and flash_load, X-HEEP handles flash access autonomously
    # (flash_exec: XIP execution; flash_load: bootrom copies flash->RAM)
    # No JTAG loading needed in either case - only on_chip uses JTAG
    if args.linker in ["flash_exec", "flash_load"]:
        log("info", "X-HEEP is executing from flash...")
        log("info", "Waiting for completion...")

        v, e = xheep.gpio.getExitCode()
        timeout = time.time() + 30
        try:
            while not v and time.time() < timeout:
                time.sleep(0.01)
                v, e = xheep.gpio.getExitCode()
        except KeyboardInterrupt:
            v, e = xheep.gpio.getExitCode()
            print(f"\nexit_valid={v} | exit_value={e}")
            return 130

        if not v:
            log("warning", "Timeout waiting for completion")

        print(f"exit_valid={v} | exit_value={e}")
        return 0 if e == 0 else 1

    # JTAG loading path
    entry = struct.unpack_from("<I", fw.read_bytes()[:0x34], 0x18)[0]
    proc, fh = start_ocd(cfg, ocd_log, xvc_addr)

    try:
        time.sleep(0.2)
        if proc.poll() is not None:
            log("error", f"OpenOCD failed, see {ocd_log}")
            return 1

        wait_tcp("127.0.0.1", 4444, 10.0)

        fwq = "{" + str(fw).replace("}", r"\}") + "}"
        cmds = ["targets riscv0", "halt", f"load_image {fwq}"]
        if args.verify:
            cmds.append(f"verify_image {fwq}")

        out = ocd_cmd(cmds, timeout=60.0)
        if args.verify:
            if "verified" in out.lower() and "error" not in out.lower():
                log("info", "Verification passed")
            else:
                log("warning", "Verification failed")

        flush_uart()
        ocd_cmd(["targets riscv0", f"resume 0x{entry:08x}"], timeout=15.0)

        log("info", "Waiting for execution to complete...")
        v, e = xheep.gpio.getExitCode()
        timeout = time.time() + 30
        while not v and time.time() < timeout:
            time.sleep(0.01)
            v, e = xheep.gpio.getExitCode()

        if not v:
            log("warning", "Timeout waiting for completion")

        print(f"exit_valid={v} | exit_value={e}")
        return 0 if e == 0 else 1

    except KeyboardInterrupt:
        v, e = xheep.gpio.getExitCode()
        print(f"\nexit_valid={v} | exit_value={e}")
        return 130

    finally:
        shutdown_ocd(proc, fh)


if __name__ == "__main__":
    raise SystemExit(main())
