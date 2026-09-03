#!/usr/bin/env python3

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import mmap
import os
from pathlib import Path
import random
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
from typing import Iterable, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from uart_monitor import UARTHandler


MAGIC_DDRC = 0x43524444
FOOTER_DDNE = 0x454E4444
DDR_PHYS_BASE = 0x8C0000000
DDR_WINDOW_SIZE = 0x40000000
PATCHABLE_SECTION = ".patchable_config"
UINT32_SIZE = 4
DEFAULT_UART = "/dev/ttyUL0"
DEFAULT_UART_BAUD = 115200
UART_POLL_SECONDS = 0.02
UART_DRAIN_SECONDS = 0.2


@dataclass(frozen=True)
class DdrTestVector:
    offset_bytes: int
    expected_values: tuple[int, ...]
    ddr_phys_base: int = DDR_PHYS_BASE
    magic: int = MAGIC_DDRC
    footer: int = FOOTER_DDNE

    @classmethod
    def from_values(
        cls,
        offset_bytes: int,
        expected_values: Iterable[int],
        ddr_phys_base: int = DDR_PHYS_BASE,
        magic: int = MAGIC_DDRC,
        footer: int = FOOTER_DDNE,
    ) -> "DdrTestVector":
        return cls(
            offset_bytes=offset_bytes,
            expected_values=tuple(value & 0xFFFFFFFF for value in expected_values),
            ddr_phys_base=ddr_phys_base,
            magic=magic,
            footer=footer,
        )

    @property
    def host_address(self) -> int:
        return self.ddr_phys_base + self.offset_bytes

    @property
    def byte_count(self) -> int:
        return len(self.expected_values) * UINT32_SIZE

    def patch_words(self) -> tuple[int, ...]:
        return (self.magic, self.offset_bytes, *self.expected_values, self.footer)

    def patch_bytes(self) -> bytes:
        return struct.pack(f"<{len(self.patch_words())}I", *self.patch_words())


@dataclass(frozen=True)
class DdrTestResult:
    passed: bool
    vector: DdrTestVector
    patched_elf_path: Path
    ps_initial_readback: tuple[int, ...] = ()
    xheep_uart_values: tuple[int, ...] = ()
    ps_final_readback: tuple[int, ...] = ()
    ps_initial_write_ok: bool = False
    runner_ok: Optional[bool] = None
    runner_returncode: Optional[int] = None
    xheep_exit_valid: Optional[int] = None
    xheep_exit_value: Optional[int] = None


@dataclass
class TestVectorGenerator:
    ddr_phys_base: int = DDR_PHYS_BASE
    ddr_window_size: int = DDR_WINDOW_SIZE
    alignment: int = UINT32_SIZE
    seed: Optional[int] = None
    value_min: int = 0
    value_max: int = 0xFFFFFFFF
    reserved_values: tuple[int, ...] = (FOOTER_DDNE,)
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.alignment <= 0 or self.alignment % UINT32_SIZE != 0:
            raise ValueError("alignment must be a positive multiple of 4 bytes")
        if self.ddr_window_size <= 0:
            raise ValueError("ddr_window_size must be positive")
        if not 0 <= self.value_min <= self.value_max <= 0xFFFFFFFF:
            raise ValueError("value range must fit in uint32")
        reserved = set(self.reserved_values)
        if all(value in reserved for value in range(self.value_min, self.value_max + 1)):
            raise ValueError("value range contains only reserved sentinel values")
        self._rng = random.Random(self.seed)

    def generate(self, value_count: int) -> DdrTestVector:
        if value_count <= 0:
            raise ValueError("value_count must be positive")

        byte_count = value_count * UINT32_SIZE
        if byte_count > self.ddr_window_size:
            raise ValueError("value_count does not fit in the DDR test window")

        max_offset = self.ddr_window_size - byte_count
        aligned_slots = max_offset // self.alignment
        offset_bytes = self._rng.randint(0, aligned_slots) * self.alignment
        expected_values = tuple(self._random_value() for _ in range(value_count))

        return DdrTestVector.from_values(
            offset_bytes=offset_bytes,
            expected_values=expected_values,
            ddr_phys_base=self.ddr_phys_base,
        )

    def _random_value(self) -> int:
        reserved = set(self.reserved_values)
        while True:
            value = self._rng.randint(self.value_min, self.value_max)
            if value not in reserved:
                return value


class ReservedDdrMemory:
    def __init__(
        self,
        ddr_phys_base: int = DDR_PHYS_BASE,
        ddr_window_size: int = DDR_WINDOW_SIZE,
        mem_path: Path | str = "/dev/mem",
    ) -> None:
        self.ddr_phys_base = ddr_phys_base
        self.ddr_window_size = ddr_window_size
        self.mem_path = Path(mem_path)

        if self.ddr_window_size <= 0:
            raise ValueError("ddr_window_size must be positive")

    def physical_address(self, offset_bytes: int) -> int:
        self._validate_access(offset_bytes, UINT32_SIZE)
        return self.ddr_phys_base + offset_bytes

    def write_vector(self, vector: DdrTestVector) -> None:
        self.write_words(vector.offset_bytes, vector.expected_values)

    def read_vector(self, vector: DdrTestVector) -> tuple[int, ...]:
        return self.read_words(vector.offset_bytes, len(vector.expected_values))

    def verify_vector(self, vector: DdrTestVector) -> bool:
        return self.read_vector(vector) == vector.expected_values

    def write_words(self, offset_bytes: int, values: Iterable[int]) -> None:
        words = tuple(value & 0xFFFFFFFF for value in values)
        if not words:
            raise ValueError("values must contain at least one word")

        payload = struct.pack(f"<{len(words)}I", *words)
        fd, mem, page_offset = self._open_mapping(offset_bytes, len(payload))
        try:
            mem.seek(page_offset)
            mem.write(payload)
        finally:
            mem.close()
            os.close(fd)

    def read_words(self, offset_bytes: int, word_count: int) -> tuple[int, ...]:
        if word_count <= 0:
            raise ValueError("word_count must be positive")

        byte_count = word_count * UINT32_SIZE
        fd, mem, page_offset = self._open_mapping(offset_bytes, byte_count)
        try:
            mem.seek(page_offset)
            data = mem.read(byte_count)
        finally:
            mem.close()
            os.close(fd)

        return struct.unpack(f"<{word_count}I", data)

    def write_and_read_words(
        self,
        offset_bytes: int,
        values: Iterable[int],
    ) -> tuple[int, ...]:
        words = tuple(value & 0xFFFFFFFF for value in values)
        self.write_words(offset_bytes, words)
        return self.read_words(offset_bytes, len(words))

    def _open_mapping(self, offset_bytes: int, byte_count: int) -> tuple[int, mmap.mmap, int]:
        self._validate_access(offset_bytes, byte_count)

        phys_addr = self.ddr_phys_base + offset_bytes
        page_size = mmap.PAGESIZE
        page_mask = page_size - 1
        map_base = phys_addr & ~page_mask
        page_offset = phys_addr - map_base
        map_size = page_offset + byte_count

        fd = os.open(str(self.mem_path), os.O_RDWR | os.O_SYNC)
        try:
            mem = mmap.mmap(
                fd,
                map_size,
                mmap.MAP_SHARED,
                mmap.PROT_READ | mmap.PROT_WRITE,
                offset=map_base,
            )
        except Exception:
            os.close(fd)
            raise

        return fd, mem, page_offset

    def _validate_access(self, offset_bytes: int, byte_count: int) -> None:
        if offset_bytes < 0:
            raise ValueError("offset_bytes must be non-negative")
        if offset_bytes % UINT32_SIZE != 0:
            raise ValueError("offset_bytes must be 4-byte aligned")
        if byte_count <= 0:
            raise ValueError("byte_count must be positive")
        if offset_bytes + byte_count > self.ddr_window_size:
            raise ValueError("DDR access exceeds the reserved DDR window")


class ElfPatcher:
    def __init__(
        self,
        elf_path: Path | str,
        section_name: str = PATCHABLE_SECTION,
        magic: int = MAGIC_DDRC,
        footer: int = FOOTER_DDNE,
    ) -> None:
        self.elf_path = Path(elf_path)
        self.section_name = section_name
        self.magic = magic
        self.footer = footer

    def read_template(self) -> DdrTestVector:
        section = self.find_section()
        data = self._read_section(section)
        words = self._unpack_words(data)

        if len(words) < 4:
            raise ValueError(f"{self.section_name} is too small for magic, offset, value, footer")
        if words[0] != self.magic:
            raise ValueError(f"unexpected magic 0x{words[0]:08x}")

        try:
            footer_index = words.index(self.footer, 2)
        except ValueError as exc:
            raise ValueError(f"footer 0x{self.footer:08x} not found in {self.section_name}") from exc

        return DdrTestVector.from_values(
            offset_bytes=words[1],
            expected_values=words[2:footer_index],
            magic=self.magic,
            footer=self.footer,
        )

    def expected_value_count(self) -> int:
        return len(self.read_template().expected_values)

    def patch(self, vector: DdrTestVector, output_path: Path | str) -> Path:
        section = self.find_section()
        payload = vector.patch_bytes()

        if len(payload) != section["size"]:
            raise ValueError(
                f"payload is {len(payload)} bytes, but {self.section_name} is {section['size']} bytes"
            )
        if vector.magic != self.magic:
            raise ValueError(f"unexpected vector magic 0x{vector.magic:08x}")
        if vector.footer != self.footer:
            raise ValueError(f"unexpected vector footer 0x{vector.footer:08x}")

        output = Path(output_path)
        if self.elf_path.resolve() != output.resolve():
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.elf_path, output)

        with output.open("r+b") as elf:
            elf.seek(section["offset"])
            elf.write(payload)

        return output

    def find_section(self) -> dict[str, int]:
        data = self.elf_path.read_bytes()
        if len(data) < 0x34 or data[:4] != b"\x7fELF":
            raise ValueError(f"{self.elf_path} is not an ELF file")
        if data[5] != 1:
            raise ValueError("only little-endian ELF files are supported")

        elf_class = data[4]
        if elf_class == 1:
            shoff = struct.unpack_from("<I", data, 0x20)[0]
            shentsize = struct.unpack_from("<H", data, 0x2E)[0]
            shnum = struct.unpack_from("<H", data, 0x30)[0]
            shstrndx = struct.unpack_from("<H", data, 0x32)[0]
            section_struct = "<IIIIIIIIII"
        elif elf_class == 2:
            shoff = struct.unpack_from("<Q", data, 0x28)[0]
            shentsize = struct.unpack_from("<H", data, 0x3A)[0]
            shnum = struct.unpack_from("<H", data, 0x3C)[0]
            shstrndx = struct.unpack_from("<H", data, 0x3E)[0]
            section_struct = "<IIQQQQIIQQ"
        else:
            raise ValueError("unknown ELF class")

        if shnum == 0 or shstrndx >= shnum:
            raise ValueError("ELF section table is missing or invalid")

        headers = [
            self._section_header(data, shoff + index * shentsize, section_struct)
            for index in range(shnum)
        ]
        shstr = headers[shstrndx]
        names = data[shstr["offset"] : shstr["offset"] + shstr["size"]]

        for header in headers:
            name = self._read_c_string(names, header["name_offset"])
            if name == self.section_name:
                return {
                    "address": header["address"],
                    "offset": header["offset"],
                    "size": header["size"],
                }

        raise ValueError(f"section {self.section_name!r} not found")

    def _read_section(self, section: dict[str, int]) -> bytes:
        with self.elf_path.open("rb") as elf:
            elf.seek(section["offset"])
            return elf.read(section["size"])

    @staticmethod
    def _unpack_words(data: bytes) -> tuple[int, ...]:
        if len(data) % UINT32_SIZE != 0:
            raise ValueError("patchable section size must be a multiple of 4 bytes")
        return struct.unpack(f"<{len(data) // UINT32_SIZE}I", data)

    @staticmethod
    def _section_header(data: bytes, offset: int, section_struct: str) -> dict[str, int]:
        fields = struct.unpack_from(section_struct, data, offset)
        return {
            "name_offset": fields[0],
            "address": fields[3],
            "offset": fields[4],
            "size": fields[5],
        }

    @staticmethod
    def _read_c_string(data: bytes, offset: int) -> str:
        end = data.find(b"\x00", offset)
        if end == -1:
            raise ValueError("unterminated section name in ELF string table")
        return data[offset:end].decode("ascii")


class DDRTester:
    def __init__(
        self,
        elf_path: Path | str,
        uart_device: str = DEFAULT_UART,
        uart_baud: int = DEFAULT_UART_BAUD,
    ) -> None:
        self.elf_path = Path(elf_path)
        self.patched_elf_path = self.elf_path.with_name(
            f"{self.elf_path.stem}_patched{self.elf_path.suffix}"
        )
        self.uart_device = uart_device
        self.uart_baud = uart_baud
        self.vector_generator = TestVectorGenerator()
        self.ddr_memory = ReservedDdrMemory()
        self.elf_patcher = ElfPatcher(self.elf_path)
        self.uart_handler = UARTHandler(self.uart_device, self.uart_baud)
        self.last_uart_output = ""
        self.last_uart_written_values = ()
        self.last_vector: Optional[DdrTestVector] = None
        self.last_ps_initial_readback = ()
        self.last_ps_final_readback = ()
        self.last_result: Optional[DdrTestResult] = None

    def prepare(self) -> DdrTestVector:
        value_count = self.elf_patcher.expected_value_count()
        return self.vector_generator.generate(value_count)

    def write_ddr(self, vector: DdrTestVector) -> bool:
        readback = self.ddr_memory.write_and_read_words(
            vector.offset_bytes,
            vector.expected_values,
        )
        self.last_ps_initial_readback = readback

        return readback == vector.expected_values

    def patch_elf(self, vector: DdrTestVector) -> Path:
        return self.elf_patcher.patch(vector, self.patched_elf_path)

    def read_xheep_written_values(self, vector: DdrTestVector) -> tuple[int, ...]:
        word_count = len(self.last_uart_written_values)
        if word_count == 0:
            self.last_ps_final_readback = ()
            return ()

        readback = self.ddr_memory.read_words(vector.offset_bytes, word_count)
        self.last_ps_final_readback = readback
        return readback

    def validate_xheep_write(self, vector: DdrTestVector) -> bool:
        if len(self.last_uart_written_values) != len(vector.expected_values):
            self.last_ps_final_readback = ()
            return False

        readback = self.read_xheep_written_values(vector)
        return readback == self.last_uart_written_values

    def run_vpk180(self, elf_path: Path | str) -> bool:
        firmware = Path(elf_path).resolve()
        if not firmware.is_file():
            raise FileNotFoundError(firmware)

        runner_script = _REPO_ROOT / "src" / "xheepRun_vpk180.py"
        if not runner_script.is_file():
            raise FileNotFoundError(runner_script)

        uart_stop = threading.Event()
        uart_ready = threading.Event()
        uart_chunks: list[bytes] = []
        uart_errors: list[Exception] = []
        uart_thread = threading.Thread(
            target=self._capture_uart,
            args=(uart_stop, uart_ready, uart_chunks, uart_errors),
            daemon=True,
        )
        uart_thread.start()
        if not uart_ready.wait(timeout=2.0):
            uart_stop.set()
            uart_thread.join(timeout=2.0)
            raise RuntimeError("UART capture did not become ready")
        if uart_errors:
            raise RuntimeError(f"UART capture failed: {uart_errors[0]}")

        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(runner_script),
                    "-f",
                    str(firmware),
                    "-l",
                    "on_chip",
                    "--uart",
                    self.uart_device,
                    "--baud",
                    str(self.uart_baud),
                    "--no-uart-flush",
                ],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
            )
        finally:
            time.sleep(UART_DRAIN_SECONDS)
            uart_stop.set()
            uart_thread.join(timeout=2.0)

        if uart_thread.is_alive():
            raise RuntimeError("UART capture thread did not stop")
        if uart_errors:
            raise RuntimeError(f"UART capture failed: {uart_errors[0]}")

        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        self.last_runner_stdout = completed.stdout
        self.last_runner_stderr = completed.stderr
        self.last_runner_output = output
        self.last_runner_returncode = completed.returncode
        self.last_uart_output = b"".join(uart_chunks).decode(errors="replace")
        self.last_uart_written_values = self.parse_uart_write_values(self.last_uart_output)

        match = re.search(
            r"exit_valid\s*=\s*(true|false|[-+]?(?:0x[0-9a-fA-F]+|\d+))"
            r"\s*\|\s*exit_value\s*=\s*([-+]?(?:0x[0-9a-fA-F]+|\d+))",
            output,
            re.IGNORECASE,
        )
        if not match:
            tail = output[-4000:]
            raise RuntimeError(
                "runvpk180 output did not contain exit_valid/exit_value "
                f"(returncode={completed.returncode}):\n{tail}"
            )

        exit_valid_text = match.group(1).lower()
        if exit_valid_text == "true":
            exit_valid = 1
        elif exit_valid_text == "false":
            exit_valid = 0
        else:
            exit_valid = int(exit_valid_text, 0)
        exit_value = int(match.group(2), 0)

        self.last_xheep_exit_valid = exit_valid
        self.last_xheep_exit_value = exit_value

        return completed.returncode == 0 and exit_valid == 1 and exit_value == 0

    def _capture_uart(
        self,
        stop_event: threading.Event,
        ready_event: threading.Event,
        chunks: list[bytes],
        errors: list[Exception],
    ) -> None:
        old_attrs = None
        try:
            self.uart_handler.open()
            old_attrs = self.uart_handler.setbaud()
            self.uart_handler.read_uart()
            ready_event.set()

            while not stop_event.is_set():
                data = self.uart_handler.read_uart()
                if data:
                    chunks.append(data)
                else:
                    time.sleep(UART_POLL_SECONDS)

            while True:
                data = self.uart_handler.read_uart()
                if not data:
                    break
                chunks.append(data)
        except Exception as exc:
            errors.append(exc)
            ready_event.set()
        finally:
            if old_attrs is not None:
                try:
                    self.uart_handler.restore(old_attrs)
                except Exception as exc:
                    errors.append(exc)
            try:
                self.uart_handler.close()
            except Exception as exc:
                errors.append(exc)

    @staticmethod
    def parse_uart_write_values(uart_output: str) -> tuple[int, ...]:
        values_by_index: dict[int, int] = {}
        for match in re.finditer(
            r"^\s*-WT-I-(\d+)\s*:\s*([0-9a-fA-F]{1,8})\s*$",
            uart_output,
            re.MULTILINE,
        ):
            index = int(match.group(1), 10)
            value = int(match.group(2), 16)
            if index in values_by_index:
                raise ValueError(f"duplicate UART write value for index {index}")
            values_by_index[index] = value

        if not values_by_index:
            return ()

        indices = sorted(values_by_index)
        expected_indices = list(range(indices[-1] + 1))
        if indices != expected_indices:
            raise ValueError(f"incomplete UART write value indices: {indices}")

        return tuple(values_by_index[index] for index in expected_indices)

    def run(self) -> bool:
        vector = self.prepare()
        self.last_vector = vector
        self.last_ps_initial_readback = ()
        self.last_ps_final_readback = ()
        self.last_result = None

        ps_initial_write_ok = self.write_ddr(vector)
        if not ps_initial_write_ok:
            self.last_result = DdrTestResult(
                passed=False,
                vector=vector,
                patched_elf_path=self.patched_elf_path,
                ps_initial_readback=self.last_ps_initial_readback,
                ps_initial_write_ok=False,
            )
            return False

        patched_elf = self.patch_elf(vector)
        runner_ok = self.run_vpk180(patched_elf)
        xheep_write_ok = False
        if runner_ok:
            xheep_write_ok = self.validate_xheep_write(vector)

        passed = ps_initial_write_ok and runner_ok and xheep_write_ok
        self.last_result = DdrTestResult(
            passed=passed,
            vector=vector,
            patched_elf_path=patched_elf,
            ps_initial_readback=self.last_ps_initial_readback,
            xheep_uart_values=self.last_uart_written_values,
            ps_final_readback=self.last_ps_final_readback,
            ps_initial_write_ok=ps_initial_write_ok,
            runner_ok=runner_ok,
            runner_returncode=getattr(self, "last_runner_returncode", None),
            xheep_exit_valid=getattr(self, "last_xheep_exit_valid", None),
            xheep_exit_value=getattr(self, "last_xheep_exit_value", None),
        )
        return passed


def parse_int(value: str) -> int:
    return int(value, 0)


def format_words(words: Iterable[int]) -> str:
    return "[" + ", ".join(f"0x{word:08x}" for word in words) + "]"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the X-HEEP reserved DDR ELF test")
    parser.add_argument("elf", help="Input X-HEEP ELF to patch and run")
    parser.add_argument("--uart", default=DEFAULT_UART, help="UART device used by X-HEEP")
    parser.add_argument("--baud", type=int, default=DEFAULT_UART_BAUD, help="UART baud rate")
    parser.add_argument("--mem", default="/dev/mem", help="Memory device to mmap")
    parser.add_argument("--ddr-base", type=parse_int, default=DDR_PHYS_BASE)
    parser.add_argument("--ddr-size", type=parse_int, default=DDR_WINDOW_SIZE)
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducible vectors")
    parser.add_argument("--show-output", action="store_true", help="Print captured runner and UART output")
    args = parser.parse_args(argv)

    tester = DDRTester(args.elf, uart_device=args.uart, uart_baud=args.baud)
    tester.vector_generator = TestVectorGenerator(
        ddr_phys_base=args.ddr_base,
        ddr_window_size=args.ddr_size,
        seed=args.seed,
    )
    tester.ddr_memory = ReservedDdrMemory(
        ddr_phys_base=args.ddr_base,
        ddr_window_size=args.ddr_size,
        mem_path=args.mem,
    )

    try:
        passed = tester.run()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if getattr(tester, "last_runner_output", ""):
            print(tester.last_runner_output, file=sys.stderr)
        if getattr(tester, "last_uart_output", ""):
            print(tester.last_uart_output, file=sys.stderr)
        return 1

    result = tester.last_result
    if result is None:
        print("ERROR: test did not produce a result", file=sys.stderr)
        return 1

    vector = result.vector
    print(f"patched_elf={result.patched_elf_path}")
    print(f"ddr_offset=0x{vector.offset_bytes:08x}")
    print(f"ddr_physical=0x{vector.host_address:x}")
    print(f"ps_initial_expected={format_words(vector.expected_values)}")
    print(f"ps_initial_readback={format_words(result.ps_initial_readback)}")
    print(f"xheep_uart_values={format_words(result.xheep_uart_values)}")
    print(f"ps_final_readback={format_words(result.ps_final_readback)}")
    print(f"exit_valid={result.xheep_exit_valid}")
    print(f"exit_value={result.xheep_exit_value}")
    print(f"result={'PASS' if passed else 'FAIL'}")

    if args.show_output:
        print("\n--- runvpk180 output ---")
        print(tester.last_runner_output, end="" if tester.last_runner_output.endswith("\n") else "\n")
        print("\n--- UART output ---")
        print(tester.last_uart_output, end="" if tester.last_uart_output.endswith("\n") else "\n")

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
