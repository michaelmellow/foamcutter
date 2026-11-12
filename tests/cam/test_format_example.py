"""The complete example in docs/formats/job-file.md §14 must always work.

The YAML block is read from the document exactly as written, so the document
cannot drift from the code without this test failing.
"""

from pathlib import Path

from foamcam import generate, load_job_text, load_profile

ROOT = Path(__file__).resolve().parents[2]
JOB_DOC = ROOT / "docs" / "formats" / "job-file.md"
SAMPLE_PROFILE = ROOT / "examples" / "profiles" / "sample-machine.yaml"


def example_yaml() -> str:
    text = JOB_DOC.read_text(encoding="utf-8")
    section = text.split("## 14. Complete example", 1)[1]
    return section.split("```yaml\n", 1)[1].split("```", 1)[0]


def test_job_file_example_validates_and_produces_a_program() -> None:
    # The example refers to "../airfoils/clarky.dat", i.e. a job stored in
    # examples/jobs/.
    job = load_job_text(example_yaml(), base_dir=ROOT / "examples" / "jobs")
    result = generate(job, load_profile(SAMPLE_PROFILE))

    lines = result.gcode.splitlines()
    commands = [line.split()[0] for line in lines if not line.startswith("(")]
    assert commands[:4] == ["G21", "G54", "G90", "G93"]
    assert commands[-1] == "G94"
    assert commands.count("G1") > 100
