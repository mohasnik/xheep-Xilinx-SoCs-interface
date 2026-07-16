# Copyright 2026 Politecnico di Torino.
#
# File: xheepRun_vpk180.py
# Static-address runner for X-HEEP on Versal VPK180/PetaLinux.

import argparse
import os
import socket
import struct
import subprocess
import time
import telnetlib
from pathlib import Path
from typing import Tuple

from xheepDriver import log, xheepStaticFlashProgrammer, xheepStaticGPIO, xheepStaticJTAG


DEFAULT_JTAG_ADDR = 0xA4000000
DEFAULT_GPIO_ADDR = 0xA4020000
DEFAULT_SPI_ADDR = 0xA4030000
DEFAULT_SPI_RANGE = 0x00010000
DEFAULT_UART = "/dev/ttyUL0"
DEFAULT_BAUD = 115200


def parse_int(value: str) -> int:
    return int(value, 0)


def elf_entry(path: Path) -> int:
    data = path.read_bytes()
    if len(data) < 0x20 or data[:4] != b"\x7fELF":
        raise ValueError(f"{path} is not an ELF file")
    if data[5] != 1:
        raise ValueError("Only little-endian ELF files are supported")
    if data[4] == 1:
        return struct.unpack_from("<I", data, 0x18)[0]
    if data[4] == 2:
        return struct.unpack_from("<Q", data, 0x18)[0]
    raise ValueError("Unknown ELF class")


def wait_tcp(host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"{host}:{port}")


def ocd_cmd(cmds, host="127.0.0.1", port=4444, timeout=30.0) -> str:
    token = f"__XHEEP_DONE_{time.monotonic_ns()}__"
    with telnetlib.Telnet(host, port, timeout=timeout) as tn:
        tn.read_until(b">", timeout=timeout)
        for cmd in cmds:
            tn.write(cmd.encode() + b"\n")
        tn.write(f"echo {token}\n".encode())
        buf = tn.read_until(token.encode(), timeout=timeout)
    return buf.decode(errors="replace")


def start_ocd(openocd: str, cfg: Path, log_file: Path, addr: int) -> Tuple[subprocess.Popen, object]:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log_file, "wb", buffering=0)
    proc = subprocess.Popen(
        [openocd, "-c", f"set XVC_DEV_ADDR 0x{addr:08x}", "-f", str(cfg)],
        stdout=fh,
        stderr=fh,
    )
    return proc, fh


def shutdown_ocd(proc, fh) -> None:
    try:
        ocd_cmd(["shutdown"], timeout=3.0)
    except Exception:
        pass
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    if fh:
        fh.close()


def flush_uart(device: str, baud: int) -> None:
    try:
        import serial

        ser = serial.Serial(device, baud, timeout=0.1)
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        ser.close()
    except Exception as exc:
        log("warning", f"Could not flush UART {device}: {exc}")


def wait_exit(gpio: xheepStaticGPIO, timeout: float) -> Tuple[int, int]:
    deadline = time.monotonic() + timeout
    exit_valid, exit_value = gpio.getExitCode()
    while not exit_valid and time.monotonic() < deadline:
        time.sleep(0.01)
        exit_valid, exit_value = gpio.getExitCode()
    return exit_valid, exit_value


def main() -> int:
    ap = argparse.ArgumentParser(description="Run X-HEEP on VPK180 through static AXI helper IPs")
    ap.add_argument("-f", "--firmware", required=True, help="Path to X-HEEP ELF or BIN")
    ap.add_argument("-l", "--linker", choices=["on_chip", "flash_load", "flash_exec"], default="on_chip")
    ap.add_argument("--jtag-addr", type=parse_int, default=parse_int(os.getenv("XHEEP_JTAG_ADDR", hex(DEFAULT_JTAG_ADDR))))
    ap.add_argument("--gpio-addr", type=parse_int, default=parse_int(os.getenv("XHEEP_GPIO_ADDR", hex(DEFAULT_GPIO_ADDR))))
    ap.add_argument("--spi-addr", type=parse_int, default=parse_int(os.getenv("XHEEP_SPI_ADDR", hex(DEFAULT_SPI_ADDR))))
    ap.add_argument("--spi-range", type=parse_int, default=parse_int(os.getenv("XHEEP_SPI_RANGE", hex(DEFAULT_SPI_RANGE))))
    ap.add_argument("--uart", default=os.getenv("XHEEP_UART", DEFAULT_UART))
    ap.add_argument("--baud", type=int, default=int(os.getenv("XHEEP_UART_BAUD", DEFAULT_BAUD)))
    ap.add_argument("--cfg", default="cfg/xheep_xilinx_xvc.cfg", help="OpenOCD AXI XVC config")
    ap.add_argument("--openocd", default=os.getenv("OPENOCD", "openocd"))
    ap.add_argument("--openocd-log", default="xheep_logs/openocd-vpk180.log")
    ap.add_argument("--verify", action="store_true", help="Verify after JTAG load or flash programming")
    ap.add_argument("--timeout", type=float, default=30.0, help="Seconds to wait for GPIO exit_valid")
    ap.add_argument("--no-wait", action="store_true", help="Do not wait for GPIO exit_valid")
    ap.add_argument("--no-uart-flush", action="store_true", help="Do not flush UART before resume")
    args = ap.parse_args()

    fw = Path(args.firmware).resolve()
    cfg = Path(args.cfg).resolve()
    ocd_log = Path(args.openocd_log).resolve()

    if not fw.is_file():
        log("error", f"Missing firmware: {fw}")
        return 2

    entry = None
    bin_file = None
    if args.linker == "on_chip":
        if not cfg.is_file():
            log("error", f"Missing OpenOCD config: {cfg}")
            return 2
        try:
            entry = elf_entry(fw)
        except ValueError as exc:
            log("error", str(exc))
            return 2
    else:
        if fw.suffix.lower() == ".elf":
            bin_file = fw.with_suffix(".bin")
            if not bin_file.exists():
                log("error", "Flash mode requires .bin file")
                return 2
        else:
            bin_file = fw

    gpio = None
    flash = None
    proc = None
    fh = None

    try:
        log("info", f"Using AXI GPIO at 0x{args.gpio_addr:08x}")
        gpio = xheepStaticGPIO(args.gpio_addr)

        if args.linker in ["flash_load", "flash_exec"]:
            log("info", f"Using AXI Quad SPI at 0x{args.spi_addr:08x}")
            flash = xheepStaticFlashProgrammer(args.spi_addr, gpio, args.spi_range)

            try:
                ok = flash.program_file(bin_file, verify=args.verify)
            except KeyboardInterrupt:
                log("warning", "Interrupted during flash programming")
                return 130
            except Exception as exc:
                log("error", f"Flash programming failed: {exc}")
                import traceback

                traceback.print_exc()
                return 1

            if not ok:
                return 1

            if not args.no_uart_flush:
                flush_uart(args.uart, args.baud)

            log("info", "Preparing X-HEEP for flash boot")
            gpio.setSpiFlashControl(False)
            gpio.resetJTAG()
            gpio.assertReset()
            if args.linker == "flash_load":
                gpio.loadFromFlash()
            else:
                gpio.execFromFlash()
            gpio.deassertReset()
            time.sleep(0.1)

            if args.no_wait:
                return 0

            log("info", "Waiting for GPIO exit_valid")
            exit_valid, exit_value = wait_exit(gpio, args.timeout)

            if not exit_valid:
                log("warning", "Timeout waiting for GPIO exit_valid")

            print(f"exit_valid={exit_valid} | exit_value={exit_value}")
            return 0 if exit_value == 0 else 1

        log("info", "Preparing X-HEEP for JTAG boot")
        gpio.setSpiFlashControl(False)
        gpio.resetJTAG()
        gpio.assertReset()
        gpio.bootFromJTAG()
        gpio.deassertReset()
        time.sleep(0.1)

        jtag = xheepStaticJTAG(args.jtag_addr)
        log("info", f"Starting OpenOCD for AXI JTAG at 0x{jtag.getAddr():08x}")
        proc, fh = start_ocd(args.openocd, cfg, ocd_log, jtag.getAddr())

        time.sleep(0.2)
        if proc.poll() is not None:
            log("error", f"OpenOCD exited early. Check {ocd_log}")
            return 1

        wait_tcp("127.0.0.1", 4444, 10.0)

        fw_quoted = "{" + str(fw).replace("}", r"\}") + "}"
        cmds = ["targets riscv0", "halt", f"load_image {fw_quoted}"]
        if args.verify:
            cmds.append(f"verify_image {fw_quoted}")

        out = ocd_cmd(cmds, timeout=60.0)
        if "error" in out.lower():
            log("warning", "OpenOCD reported an error during load; see log/output")

        if not args.no_uart_flush:
            flush_uart(args.uart, args.baud)

        log("info", f"Resuming X-HEEP at 0x{entry:x}")
        ocd_cmd(["targets riscv0", f"resume 0x{entry:x}"], timeout=15.0)

        if args.no_wait:
            return 0

        log("info", "Waiting for GPIO exit_valid")
        exit_valid, exit_value = wait_exit(gpio, args.timeout)

        if not exit_valid:
            log("warning", "Timeout waiting for GPIO exit_valid")

        print(f"exit_valid={exit_valid} | exit_value={exit_value}")
        return 0 if exit_value == 0 else 1

    except KeyboardInterrupt:
        if gpio:
            exit_valid, exit_value = gpio.getExitCode()
            print(f"\nexit_valid={exit_valid} | exit_value={exit_value}")
        return 130
    finally:
        if proc:
            shutdown_ocd(proc, fh)
        if flash:
            flash.close()
        if gpio:
            gpio.close()


if __name__ == "__main__":
    raise SystemExit(main())
