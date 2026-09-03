#!/usr/bin/env python3
# Copyright 2026 Politecnico di Torino.
#
# File: uart_monitor.py
# Timed UART monitor for minimal PetaLinux systems.

import argparse
import os
import select
import sys
import termios
import time

class UARTHandler:
    BAUD_RATES = {
        9600: termios.B9600,
        19200: termios.B19200,
        38400: termios.B38400,
        57600: termios.B57600,
        115200: termios.B115200,
    }

    def __init__(self, device: str, baudrate: int) -> None:
        self._device = device
        self._baudrate = baudrate
        self._flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0)
        self._fd = None

    def open(self) -> None:
        self._fd = os.open(self._device, self._flags)

    def close(self) -> None:
        if self._fd is None:
            return

        os.close(self._fd)
        self._fd = None

    def setbaud(self) -> list:
        if self._baudrate not in self.BAUD_RATES:
            raise ValueError(f"Unsupported baud rate: {self._baudrate}")

        fd = self._require_open()
        old = termios.tcgetattr(fd)
        new = old[:]
    
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = new
    
        for flag in ("IGNBRK", "BRKINT", "PARMRK", "ISTRIP", "INLCR", "IGNCR", "ICRNL", "IXON", "IXOFF"):
            iflag = self._clear_flag(iflag, flag)
        oflag = self._clear_flag(oflag, "OPOST")
        for flag in ("ECHO", "ECHONL", "ICANON", "ISIG", "IEXTEN"):
            lflag = self._clear_flag(lflag, flag)
        for flag in ("CSIZE", "PARENB", "CSTOPB", "CRTSCTS"):
            cflag = self._clear_flag(cflag, flag)
    
        cflag |= termios.CS8 | termios.CLOCAL | termios.CREAD
        speed = self.BAUD_RATES[self._baudrate]
    
        new[0] = iflag
        new[1] = oflag
        new[2] = cflag
        new[3] = lflag
        new[4] = speed
        new[5] = speed
        new[6][termios.VMIN] = 0
        new[6][termios.VTIME] = 0
    
        termios.tcsetattr(fd, termios.TCSANOW, new)
        return old

    def restore(self, old_attrs: list) -> None:
        termios.tcsetattr(self._require_open(), termios.TCSANOW, old_attrs)

    def read_uart(self) -> bytes:
        fd = self._require_open()
        chunks = bytearray()

        while True:
            readable, _, _ = select.select([fd], [], [], 0)
            if not readable:
                break

            try:
                data = os.read(fd, 4096)
            except BlockingIOError:
                break

            if not data:
                break

            chunks.extend(data)

        return bytes(chunks)

    def _require_open(self) -> int:
        if self._fd is None:
            raise RuntimeError("UART device is not open")
        return self._fd

    @staticmethod
    def _clear_flag(value: int, name: str) -> int:
        return value & ~getattr(termios, name, 0)

def monitor(device: str, baud: int, seconds: float, configure: bool, strict_config: bool) -> int:
    uart = UARTHandler(device, baud)
    old_attrs = None

    try:
        uart.open()

        if configure:
            try:
                old_attrs = uart.setbaud()
            except Exception as exc:
                print(f"warning: could not configure {device}: {exc}", file=sys.stderr)
                if strict_config:
                    return 2

        print(f"monitoring {device} for {seconds:g} seconds", file=sys.stderr)
        deadline = time.monotonic() + seconds

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            data = uart.read_uart()
            if data:
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
            else:
                time.sleep(min(remaining, 0.05))

        return 0
    finally:
        if old_attrs is not None:
            try:
                uart.restore(old_attrs)
            except Exception:
                pass
        uart.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor UART output for a fixed duration")
    parser.add_argument("-d", "--device", default="/dev/ttyUL0", help="UART device")
    parser.add_argument("-b", "--baud", type=int, default=9600, help="UART baud rate")
    parser.add_argument("-t", "--seconds", type=float, default=5.0, help="Monitor duration")
    parser.add_argument("--no-config", action="store_true", help="Read without changing UART settings")
    parser.add_argument("--strict-config", action="store_true", help="Fail if UART configuration is rejected")
    args = parser.parse_args()

    return monitor(args.device, args.baud, args.seconds, not args.no_config, args.strict_config)


if __name__ == "__main__":
    raise SystemExit(main())
