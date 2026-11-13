"""Checks that the firmware documents hold what the brief and the director require.

Run with: python -m pytest firmware
"""

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

COMMISSIONING = (HERE / "commissioning.md").read_text()
BENCH = (HERE / "bench-test.md").read_text()
DOCS = ["README.md", "wiring.md", "bench-test.md", "commissioning.md", "baseline-settings.txt"]


def test_commissioning_ends_with_the_g54_zeros() -> None:
    headings = re.findall(r"^## (.+)$", COMMISSIONING, re.MULTILINE)
    assert headings[-2] == "8. Set the four G54 zeros"
    assert headings[-1] == "Machine record"


def test_commissioning_sets_each_zero_with_g10_l20() -> None:
    section = COMMISSIONING.split("## 8.")[1]
    for letter in "XYZA":
        assert f"G10 L20 P1 {letter}0" in section
    # x zeros (axes 1 and 3) before z zeros (axes 2 and 4), as in machine-profile.md §3.3
    assert section.index("G10 L20 P1 X0") < section.index("G10 L20 P1 Y0")
    assert "wire clear of the table" in section or "clear of the table" in section
    assert "clear of the x reference face" in section
    assert "paper" in section


def test_commissioning_says_when_to_recheck_the_zeros() -> None:
    section = COMMISSIONING.split("### When the zeros must be re-checked")[1]
    assert "After every homing cycle" in section
    assert "homing repeatability" in section
    assert "whenever a reference, a wire point or the wire is changed" in section


def test_commissioning_covers_the_brief_topics() -> None:
    for topic in ("Steps per mm", "Motor current", "Direction", "Speed and acceleration",
                  "Stall sensitivity", "Homing", "Travel and soft limits"):
        assert re.search(rf"^## \d\. {topic}", COMMISSIONING, re.MULTILINE), topic


def test_bench_test_runs_both_excerpts() -> None:
    for name in ("excerpt-a-g93-with-g0.nc", "excerpt-b-short-moves.nc"):
        assert f"bench/{name}" in BENCH
        assert (HERE / "bench" / name).exists()
    assert "1769fcc" in BENCH


def test_cited_source_files_exist_in_the_build_checkout_layout() -> None:
    # Citations name files relative to the driver checkout or its submodules.
    cited = set()
    for doc in DOCS:
        cited |= set(re.findall(r"`?((?:grbl/|motors/|trinamic/|boards/|Inc/)?[a-z_0-9.]+\.(?:c|h)):\d+", (HERE / doc).read_text()))
    assert cited, "no source citations found"
    checkout = ROOT / "output" / "firmware-build" / "STM32H7xx"
    if not checkout.exists():
        return  # build checkout not present; nothing to check against
    for path in cited:
        matches = list(checkout.glob(f"**/{Path(path).name}"))
        assert matches, f"cited file {path} not found in {checkout}"


def test_docs_reference_existing_files() -> None:
    for doc in DOCS:
        for ref in re.findall(r"`((?:bench|release)/[^`*]+)`", (HERE / doc).read_text()):
            assert (HERE / ref).exists(), f"{doc} refers to missing {ref}"


WIRING = (HERE / "wiring.md").read_text()


def sections(text: str) -> list[str]:
    return re.findall(r"^## (\d+)\.", text, re.MULTILINE)


def test_wiring_states_the_driver_voltage_limit_up_front() -> None:
    top = WIRING.split("## Driver slots")[0]
    assert "WARNING" in top
    assert "24 V maximum" in top
    assert "4.75–28 V" in top
    power = " ".join(WIRING.split("## Power")[1].split("\n## ")[0].split())
    assert "4.75V---28V DC" in power  # quoted from BTT's EZ2209 page
    assert "for other drivers" in power
    assert "must not be used with the EZ2209" in power
    assert "Leave unconnected" in power
    assert "§3.2.1" in power and "continuity" in power


def test_wiring_requires_the_estop_to_open_the_wire_supply() -> None:
    flat = " ".join(WIRING.split())
    assert "Required: the e-stop must also open the hot-wire supply" in flat
    assert "should also open the hot-wire" not in flat


def test_bench_inputs_run_last_with_a_temporary_estop_inversion() -> None:
    assert sections(BENCH) == ["0", "1", "2", "4", "5", "6", "3", "7"]
    before_inputs = BENCH.split("## 3. Inputs")[0]
    inputs = BENCH.split("## 3. Inputs")[1].split("## 7.")[0]
    assert "Nothing connected to EXP2" in before_inputs
    assert "| 1.4 | `$14=70`" in before_inputs
    assert "1 kΩ resistor" in inputs  # pins identified with a meter first
    assert inputs.index("3A.1") < inputs.index("`$14=6`, then soft reset")
    assert "$14=6" in BENCH.split("## 7.")[1]


def test_bench_states_clearance_before_100_mm_jogs() -> None:
    row = next(line for line in BENCH.splitlines() if line.startswith("| 4.6 |"))
    assert "at least 110 mm" in row


def test_commissioning_final_profile_checks() -> None:
    checks = " ".join(COMMISSIONING.split("**8.8 Final profile checks.**")[1].split())
    assert '`"X"`, `"Y"`, `"Z"`, `"A"` in that order' in checks
    assert "`max_rate` is at or below `$11n`" in checks
    assert "Check (b)" in COMMISSIONING.split("## 4.")[1].split("## 5.")[0]


def test_commissioning_tests_the_estop_heat_cut_first() -> None:
    step0 = COMMISSIONING.split("## 0. Before you start")[1].split("## 1.")[0]
    assert "E-stop cuts wire heat" in step0
