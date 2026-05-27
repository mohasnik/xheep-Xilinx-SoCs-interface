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


BAUD_RATES = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}


def clear_flag(value: int, name: str) -> int:
    return value & ~getattr(termios, name, 0)


def configure_uart(fd: int, baud: int):
    if baud not in BAUD_RATES:
        raise ValueError(f"Unsupported baud rate: {baud}")

    old = termios.tcgetattr(fd)
    new = old[:]

    iflag, oflag, cflag, lflag, ispeed, ospeed, cc = new

    for flag in ("IGNBRK", "BRKINT", "PARMRK", "ISTRIP", "INLCR", "IGNCR", "ICRNL", "IXON", "IXOFF"):
        iflag = clear_flag(iflag, flag)
    oflag = clear_flag(oflag, "OPOST")
    for flag in ("ECHO", "ECHONL", "ICANON", "ISIG", "IEXTEN"):
        lflag = clear_flag(lflag, flag)
    for flag in ("CSIZE", "PARENB", "CSTOPB", "CRTSCTS"):
        cflag = clear_flag(cflag, flag)

    cflag |= termios.CS8 | termios.CLOCAL | termios.CREAD
    speed = BAUD_RATES[baud]

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


def monitor(device: str, baud: int, seconds: float, configure: bool, strict_config: bool) -> int:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0)
    fd = os.open(device, flags)
    old_attrs = None

    try:
        if configure:
            try:
                old_attrs = configure_uart(fd, baud)
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

            readable, _, _ = select.select([fd], [], [], min(remaining, 0.25))
            if not readable:
                continue

            try:
                data = os.read(fd, 4096)
            except BlockingIOError:
                continue

            if data:
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()

        return 0
    finally:
        if old_attrs is not None:
            try:
                termios.tcsetattr(fd, termios.TCSANOW, old_attrs)
            except Exception:
                pass
        os.close(fd)


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
