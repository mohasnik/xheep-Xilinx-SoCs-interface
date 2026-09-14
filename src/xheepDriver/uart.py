# Copyright 2026 Politecnico di Torino.
#
# File: uart.py
# Author: Christian Conti {christian.conti@polito.it}
# Date: 31/03/2026

import os
import sys
import time
import subprocess
import stat
from pathlib import Path

from .logger import log

class xheepUART:
    DTS_PATCHED_PATH = Path("dts/uartlite-patched.dts")
    DTBO_PATH = Path("dts/uartlite-overlay.dtbo")

    OVERLAY_NAME = "uartlite"
    OVL_DIR = Path("/sys/kernel/config/device-tree/overlays") / OVERLAY_NAME
    DRIVER_NAME = "uartlite"
    TTY_NODE = Path("/dev/ttyUL0")

    TIMEOUT_S = 3.0
    POLL_S = 0.05

    def __init__(self, memAddr: int, template_path=None):
        self.memAddr = int(memAddr)
        self.PLATFORM_DEV = f"{self.memAddr:08x}.serial"

        if template_path is not None:
            self.DTS_TEMPLATE_PATH = Path(template_path)
        else:
            board = os.getenv("BOARD", "pynq-z2").lower()
            if board == "aup-zu3":
                self.DTS_TEMPLATE_PATH = Path("dts/uartlite-ultrascale.tpl")
            else:
                self.DTS_TEMPLATE_PATH = Path("dts/uartlite-zynq.tpl")

    def _patchDts(self) -> None:
        content = self.DTS_TEMPLATE_PATH.read_text()
        patched = content.replace("########", f"{self.memAddr:08x}")
        self.DTS_PATCHED_PATH.write_text(patched)

    def _dtsCompile(self) -> None:
        argv = ["dtc", "-@", "-I", "dts", "-O", "dtb", "-o", str(self.DTBO_PATH), str(self.DTS_PATCHED_PATH)]
        cp = subprocess.run(argv, capture_output=True, text=True)
        if cp.returncode != 0:
            log("critical", f"dtc failed (rc={cp.returncode}). stderr: {cp.stderr or cp.stdout}")
            sys.exit(1)

    def _wait(self, cond_fn, timeout_s: float, what: str) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if cond_fn():
                return
            time.sleep(self.POLL_S)
        log("critical", f"Timeout waiting for: {what} (>{timeout_s:.2f}s)")
        sys.exit(1)

    def _pids_using_path(self, target: Path) -> list[tuple[int, str]]:
        if not target.exists():
            return []
        target_str = str(target)
        found: list[tuple[int, str]] = []
        for pid_s in os.listdir("/proc"):
            if not pid_s.isdigit():
                continue
            fd_dir = Path("/proc") / pid_s / "fd"
            try:
                for fd in os.listdir(fd_dir):
                    try:
                        link = os.readlink(fd_dir / fd)
                        if link == target_str or os.path.realpath(link) == os.path.realpath(target_str):
                            comm = (Path("/proc") / pid_s / "comm").read_text(encoding="utf-8", errors="ignore").strip() or "unknown"
                            found.append((int(pid_s), comm))
                            break
                    except Exception:
                        continue
            except Exception:
                continue
        return found

    def unbind(self) -> None:
        if self.TTY_NODE.exists():
            holders = self._pids_using_path(self.TTY_NODE)
            if holders:
                details = ", ".join([f"{pid}({comm})" for pid, comm in holders])
                log("critical", f"{self.TTY_NODE} is busy (open by: {details})")
                sys.exit(1)

        dev = Path("/sys/bus/platform/devices") / self.PLATFORM_DEV
        driver_link = dev / "driver"

        if driver_link.exists():
            try:
                drv_name = Path(os.readlink(driver_link)).name
                unbind_path = Path("/sys/bus/platform/drivers") / drv_name / "unbind"
                unbind_path.write_text(self.PLATFORM_DEV, encoding="utf-8")
                self._wait(lambda: not driver_link.exists(), self.TIMEOUT_S, "driver unbind")
            except Exception as e:
                log("critical", f"Driver unbind failed: {e}")
                sys.exit(1)

        if self.OVL_DIR.exists():
            try:
                os.rmdir(self.OVL_DIR)
                self._wait(lambda: not self.OVL_DIR.exists(), self.TIMEOUT_S, "overlay removal")
            except Exception as e:
                log("critical", f"Failed to remove device tree overlay: {e}")
                sys.exit(1)

        self._wait(lambda: not dev.exists(), self.TIMEOUT_S, "platform device disappearance")
        self._wait(lambda: not self.TTY_NODE.exists(), self.TIMEOUT_S, "tty node removal")

    def bind(self) -> None:
        self._patchDts()
        self._dtsCompile()

        base = Path("/sys/kernel/config/device-tree/overlays")
        if not base.exists():
            log("critical", f"Configfs DT overlays not mounted: {base}")
            sys.exit(1)

        if self.OVL_DIR.exists():
            log("critical", f"Overlay dir already exists: {self.OVL_DIR}. Try unbinding first.")
            sys.exit(1)

        try:
            self.OVL_DIR.mkdir(parents=True, exist_ok=False)
            (self.OVL_DIR / "dtbo").write_bytes(self.DTBO_PATH.read_bytes())
        except Exception as e:
            if self.OVL_DIR.exists():
                try:
                    os.rmdir(self.OVL_DIR)
                except Exception:
                    pass
            log("critical", f"Failed to load DTBO: {e}")
            sys.exit(1)

        dev = Path("/sys/bus/platform/devices") / self.PLATFORM_DEV
        self._wait(lambda: dev.exists(), self.TIMEOUT_S, "platform device appearance")

        driver_link = dev / "driver"
        if not driver_link.exists():
            bind_path = Path("/sys/bus/platform/drivers") / self.DRIVER_NAME / "bind"
            if not bind_path.exists():
                subprocess.run(["modprobe", self.DRIVER_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if not bind_path.exists():
                    log("critical", f"Driver '{self.DRIVER_NAME}' not found in kernel.")
                    sys.exit(1)
            try:
                bind_path.write_text(self.PLATFORM_DEV, encoding="utf-8")
            except OSError as e:
                if e.errno != 16:
                    log("critical", f"Driver bind failed (OSError {e.errno}): {e.strerror}")
                    log("error", "Check 'dmesg' for hardware/interrupt mismatches.")
                    sys.exit(1)
            except Exception as e:
                log("critical", f"Driver bind failed: {e}")
                sys.exit(1)

        self._wait(lambda: (dev / "driver").exists(), self.TIMEOUT_S, "driver bind confirmation")
        self._wait(lambda: self.TTY_NODE.exists(), self.TIMEOUT_S, "tty node creation")

        try:
            file_stats = self.TTY_NODE.stat()
            if not stat.S_ISCHR(file_stats.st_mode):
                log("critical", f"{self.TTY_NODE} exists but is not a character device.")
                sys.exit(1)
        except Exception as e:
            log("critical", f"Cannot access device node: {e}")
            sys.exit(1)
