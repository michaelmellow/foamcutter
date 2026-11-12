"""Tests for firmware/build.py. Run with: python -m pytest firmware

Not part of "python check.py": the firmware brief has no check area, and
check.py collects tests from tests/ only.
"""

import importlib.util
import struct
import sys
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent


def load_build() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fw_build", HERE / "build.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fw_build"] = module
    spec.loader.exec_module(module)
    return module


build = load_build()
H743_FLASH = 2048 * 1024


def image(stack: int, reset: int, size: int = 256 * 1024) -> bytes:
    return struct.pack("<II", stack, reset) + bytes(size - 8)


def test_image_linked_for_bootloader_passes() -> None:
    assert build.verify_binary(image(0x24080000, 0x08025319), H743_FLASH) == []


def test_image_linked_at_flash_start_is_rejected() -> None:
    # An image built with the non-bootloader linker script starts at 0x08000000.
    problems = build.verify_binary(image(0x24080000, 0x08005319), H743_FLASH)
    assert any("outside the image" in p for p in problems)


def test_non_thumb_reset_vector_is_rejected() -> None:
    problems = build.verify_binary(image(0x24080000, 0x08025318), H743_FLASH)
    assert any("Thumb" in p for p in problems)


def test_stack_pointer_outside_ram_is_rejected() -> None:
    problems = build.verify_binary(image(0x08000000, 0x08025319), H743_FLASH)
    assert any("stack pointer" in p for p in problems)


def test_oversized_image_is_rejected() -> None:
    too_big = 1024 * 1024 - 256 * 1024 + 4
    problems = build.verify_binary(image(0x24050000, 0x080250A1, too_big), 1024 * 1024)
    assert any("application area" in p for p in problems)


def test_release_name_contains_core_and_driver_commits() -> None:
    name = build.release_name("h743")
    assert build.SUBMODULES["grbl"][:12] in name
    assert build.DRIVER_COMMIT[:12] in name


def test_committed_release_binaries_verify() -> None:
    assert build.verify_release()
