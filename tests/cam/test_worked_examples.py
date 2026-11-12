"""The worked examples in examples/jobs/ must match their G-code in examples/gcode/.

Brief 5 checks these files with an independent simulator, so they must never
drift from the code. Regenerate them with:

    python -m foamcam cut examples/jobs/<name>.yaml --profile examples/profiles/sample-machine.yaml --out output/examples

and copy the .nc file to examples/gcode/.
"""

from pathlib import Path

import pytest

from foamcam import generate, load_job, load_profile

ROOT = Path(__file__).resolve().parents[2]
JOBS = sorted((ROOT / "examples" / "jobs").glob("*.yaml"))
PROFILE = ROOT / "examples" / "profiles" / "sample-machine.yaml"


def test_three_worked_examples_exist() -> None:
    assert [p.stem for p in JOBS] == ["straight-wing", "tapered-spar", "tapered-swept-washout"]


@pytest.mark.parametrize("job_path", JOBS, ids=[p.stem for p in JOBS])
def test_committed_gcode_matches_the_job(job_path: Path) -> None:
    expected = (ROOT / "examples" / "gcode" / f"{job_path.stem}.nc").read_text(encoding="utf-8").splitlines()
    actual = generate(load_job(job_path), load_profile(PROFILE)).gcode.splitlines()
    assert len(actual) == len(expected)
    for number, (got, want) in enumerate(zip(actual, expected), start=1):
        if got == want:
            continue
        # Allow a last-digit difference from floating-point rounding on another platform.
        got_words, want_words = got.split(), want.split()
        assert [w[0] for w in got_words] == [w[0] for w in want_words], f"line {number}: {got} != {want}"
        for g, w in zip(got_words, want_words):
            if g[0] == "G":
                assert g == w, f"line {number}"
            elif g[0] == "F":
                assert float(g[1:]) == pytest.approx(float(w[1:]), rel=1e-5), f"line {number}"
            elif g[0] != "(":
                assert float(g[1:]) == pytest.approx(float(w[1:]), abs=2e-4), f"line {number}"
