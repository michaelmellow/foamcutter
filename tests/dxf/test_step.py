"""Tests for STEP models in the DXF front end (src/foamdxf/step.py).

The STEP files are made here with gmsh (OpenCASCADE): a NACA 2412 wing lofted
with straight lines from a 180 mm root to a 130 mm tip 270 mm away, swept
20 mm, so the right answers are known.
"""

import math
import tkinter as tk
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

try:
    import gmsh  # type: ignore[import-untyped, import-not-found, unused-ignore]
except (ImportError, OSError):
    pytest.skip("gmsh is not installed or does not load", allow_module_level=True)

from foamdxf import cli, gui
from foamdxf.errors import DxfError
from foamdxf.gui import SlicerWindow, request_from_form
from foamdxf.outline import DxfOutline
from foamdxf.pipeline import make_job, run_cam
from foamdxf.project import open_project, save_project
from foamdxf.step import read_step, sections

REPO = Path(__file__).resolve().parents[2]
PROFILE = REPO / "examples" / "profiles" / "real-machine.yaml"

Vec = tuple[float, float, float]
ROOT_CHORD, TIP_CHORD, SPAN, SWEEP = 180.0, 130.0, 270.0, 20.0


def naca2412(chord: float, n: int = 60) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    m, p, t = 0.02, 0.4, 0.12
    upper, lower = [], []
    for i in range(n + 1):
        x = (1 - math.cos(math.pi * i / n)) / 2
        yt = 5 * t * (0.2969 * math.sqrt(x) - 0.126 * x - 0.3516 * x**2 + 0.2843 * x**3 - 0.1036 * x**4)
        yc = m / p**2 * (2 * p * x - x * x) if x < p else m / (1 - p) ** 2 * (1 - 2 * p + 2 * p * x - x * x)
        upper.append((x * chord, (yc + yt) * chord))
        lower.append((x * chord, (yc - yt) * chord))
    return upper, lower


def rotate(p: Vec, axis: Vec, degrees: float) -> Vec:
    """Rodrigues' rotation about `axis` through the origin."""
    n = math.sqrt(sum(a * a for a in axis))
    k = (axis[0] / n, axis[1] / n, axis[2] / n)
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    dot = sum(a * b for a, b in zip(k, p, strict=True))
    cross = (k[1] * p[2] - k[2] * p[1], k[2] * p[0] - k[0] * p[2], k[0] * p[1] - k[1] * p[0])
    return (p[0] * c + cross[0] * s + k[0] * dot * (1 - c),
            p[1] * c + cross[1] * s + k[1] * dot * (1 - c),
            p[2] * c + cross[2] * s + k[2] * dot * (1 - c))


def with_gmsh(write: Callable[[], None], path: Path) -> Path:
    gmsh.initialize(readConfigFiles=False, interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        write()
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    finally:
        gmsh.finalize()
    return path


def make_wing(path: Path, span_sign: float = 1.0, axis: Vec = (0, 0, 1), angle: float = 0.0) -> Path:
    """Chord along +X, upper side +Y, tip at Z = span_sign * SPAN; then rotated."""

    def write() -> None:
        occ = gmsh.model.occ
        wires = []
        for chord, sweep, z in ((ROOT_CHORD, 0.0, 0.0), (TIP_CHORD, SWEEP, span_sign * SPAN)):
            upper, lower = naca2412(chord)
            pu = [occ.addPoint(sweep + x, y, z) for x, y in upper]
            pl = [pu[0]] + [occ.addPoint(sweep + x, y, z) for x, y in lower[1:-1]] + [pu[-1]]
            wires.append(occ.addWire([occ.addSpline(pu), occ.addSpline(pl)]))
        solid = occ.addThruSections(wires, makeSolid=True, makeRuled=True)
        if angle:
            occ.rotate(solid, 0, 0, 0, *axis, math.radians(angle))
            occ.translate(solid, 5, -3, 12)

    return with_gmsh(write, path)


@pytest.fixture(scope="module")
def wing(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_wing(tmp_path_factory.mktemp("step") / "wing.step")


def test_faces_and_suggested_pair(wing: Path) -> None:
    model = read_step(wing)
    assert len(model.faces) == 2  # only the two end caps are flat
    big, small = model.faces
    assert big.extent == pytest.approx(ROOT_CHORD, abs=0.01)
    assert small.extent == pytest.approx(TIP_CHORD, abs=0.01)
    assert model.suggest() == (big.tag, small.tag)  # the larger face is the root
    assert "180.0 mm across" in big.label()


def _le(o: DxfOutline) -> tuple[float, float]:
    return o.points[o.le_index]


def test_sections_keep_chords_sweep_and_span(wing: Path) -> None:
    model = read_step(wing)
    pair = model.suggest()
    assert pair is not None
    cut = sections(model, *pair)
    assert cut.span == pytest.approx(SPAN, abs=1e-6)
    chords = sorted((cut.root.chord, cut.tip.chord))
    assert chords == pytest.approx([TIP_CHORD, ROOT_CHORD], abs=0.01)
    by_chord = {round(o.chord): o for o in (cut.root, cut.tip)}
    big, small = by_chord[180], by_chord[130]
    assert _le(small)[0] - _le(big)[0] == pytest.approx(SWEEP, abs=0.01)  # sweep from the model
    assert _le(small)[1] == pytest.approx(_le(big)[1], abs=0.01)  # no dihedral
    for o in (cut.root, cut.tip):  # leading edge at the smaller x, NACA 2412 thickness
        assert o.points[0][0] > _le(o)[0]
        zs = [p[1] for p in o.points]
        assert max(zs) - min(zs) == pytest.approx(0.12 * o.chord, rel=0.02)


def _triple(a: Vec, b: Vec, c: Vec) -> float:
    return a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])


@pytest.mark.parametrize(
    ("span_sign", "axis", "angle", "turned_over"),
    [
        (1.0, (0, 0, 1), 0.0, True),  # this hand has the upper side down at the axes 1-2 tower...
        (-1.0, (0, 0, 1), 0.0, False),  # ...the other hand does not
        (1.0, (1, 1, 0), 70.0, True),  # any rigid placement in the file gives the same answer
        (-1.0, (0.3, -1, 0.5), 125.0, False),
    ],
)
def test_mapping_is_rigid_and_upright(
    tmp_path: Path, span_sign: float, axis: Vec, angle: float, turned_over: bool
) -> None:
    path = make_wing(tmp_path / "w.step", span_sign, axis, angle)
    model = read_step(path)
    pair = model.suggest()
    assert pair is not None
    cut = sections(model, *pair)
    assert any("turned over" in w for w in cut.warnings) == turned_over
    assert cut.span == pytest.approx(SPAN, abs=1e-6)

    # the root face (the 180 mm one in the model) is at the axes 3-4 tower when turned over
    model_root, model_tip = (cut.tip, cut.root) if turned_over else (cut.root, cut.tip)
    assert model_root.chord == pytest.approx(ROOT_CHORD, abs=0.01)
    y_root, y_tip = (SPAN, 0.0) if turned_over else (0.0, SPAN)

    def cutter(o: DxfOutline, y: float, p: tuple[float, float]) -> Vec:
        return (p[0], y, p[1])

    def place(p: Vec) -> Vec:
        return rotate(p, axis, angle) if angle else p

    up_root = max(model_root.points, key=lambda p: p[1])
    c_le, c_te = cutter(model_root, y_root, _le(model_root)), cutter(model_root, y_root, model_root.points[0])
    c_tip, c_up = cutter(model_tip, y_tip, _le(model_tip)), cutter(model_root, y_root, up_root)
    m_le, m_te = place((0.0, 0.0, 0.0)), place((ROOT_CHORD, 0.0, 0.0))
    m_tip = place((SWEEP, 0.0, span_sign * SPAN))
    m_up = place((up_root[0] - _le(model_root)[0], 0.08 * ROOT_CHORD, 0.0))  # some point on the upper side

    def rel(a: Vec, o: Vec) -> Vec:
        return (a[0] - o[0], a[1] - o[1], a[2] - o[2])

    det_cutter = _triple(rel(c_te, c_le), rel(c_tip, c_le), rel(c_up, c_le))
    det_model = _triple(rel(m_te, m_le), rel(m_tip, m_le), rel(m_up, m_le))
    assert det_cutter * det_model > 0  # same handedness: not mirrored
    assert math.dist(c_le, c_tip) == pytest.approx(math.dist(m_le, m_tip), abs=0.01)

    # upright: at the axes 1-2 tower the upper side is on top
    first = cut.root
    zs = [p[1] for p in first.points]
    assert max(zs) - _le(first)[1] > _le(first)[1] - min(zs)


def test_bad_face_choices(tmp_path: Path, wing: Path) -> None:
    model = read_step(wing)
    tag = model.faces[0].tag
    with pytest.raises(DxfError, match="two different faces"):
        sections(model, tag, tag)
    with pytest.raises(DxfError, match="no flat face 999"):
        sections(model, tag, 999)

    box = read_step(with_gmsh(lambda: gmsh.model.occ.addBox(0, 0, 0, 100, 20, 50), tmp_path / "box.step"))
    assert len(box.faces) == 6
    a = box.faces[0]
    side = next(f for f in box.faces if abs(sum(x * y for x, y in zip(f.normal, a.normal, strict=True))) < 0.1)
    with pytest.raises(DxfError, match="not parallel"):
        sections(box, a.tag, side.tag)

    ball = tmp_path / "ball.step"
    with pytest.raises(DxfError, match="no flat faces"):
        read_step(with_gmsh(lambda: gmsh.model.occ.addSphere(0, 0, 0, 30), ball))
    with pytest.raises(DxfError, match="cannot read STEP"):
        read_step(tmp_path / "nothing.step")


def test_pipeline_and_cli(tmp_path: Path, wing: Path, capsys: pytest.CaptureFixture[str]) -> None:
    values = dict(gui.DEFAULTS) | {
        "source": "step", "step": str(wing), "profile": str(PROFILE), "stock_length": "220",
        "stock_height": "40", "span": "", "root_face": "", "tip_face": "",
    }
    model = read_step(wing)
    pair = model.suggest()
    assert pair is not None
    values |= {"root_face": str(pair[0]), "tip_face": str(pair[1])}
    request = request_from_form(values)
    assert request.faces == pair and request.stem == f"wing-f{pair[0]}-f{pair[1]}"
    plan = make_job(request)
    assert any(f"span {SPAN:g} mm" in line for line in plan.generated.summary)  # measured, not typed
    assert run_cam(plan).total_time > 0

    assert cli.main(["--step", str(wing), "--list-faces"]) == 0
    assert "suggested root,tip" in capsys.readouterr().out
    out = tmp_path / "out"
    code = cli.main(["--step", str(wing), "--stock", "220,40", "--profile", str(PROFILE), "--out", str(out)])
    assert code == 0
    assert (out / f"wing-f{pair[0]}-f{pair[1]}.nc").is_file()
    assert cli.main(["--step", str(wing), "--faces", f"{pair[0]},{pair[0]}", "--stock", "220,40",
                     "--profile", str(PROFILE), "--out", str(out)]) == 1


@pytest.fixture
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SlicerWindow]:
    monkeypatch.setattr(gui, "SETTINGS_FILE", tmp_path / "settings.json")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tkinter")
    root.withdraw()
    win = SlicerWindow(root)
    yield win
    root.destroy()


def test_window_step_workflow(window: SlicerWindow, tmp_path: Path, wing: Path) -> None:
    window.vars["profile"].set(str(PROFILE))
    window.vars["source"].set("step")
    window._source_changed()
    window.vars["step"].set(str(wing))
    window._load_step()
    pair = read_step(wing).suggest()
    assert pair is not None
    assert (window.vars["root_face"].get(), window.vars["tip_face"].get()) == (str(pair[0]), str(pair[1]))
    assert window.vars["span"].get() == "270"
    assert str(window.span_entry.cget("state")) == "readonly"
    assert window.face_text["root_face"].get().startswith(f"face {pair[0]}:")

    # picking the faces the other way round still measures the span
    window.face_text["root_face"].set(read_step(wing).face(pair[1]).label())
    window._face_picked("root_face")
    assert window.vars["root_face"].get() == str(pair[1])
    window.face_text["tip_face"].set(read_step(wing).face(pair[0]).label())
    window._face_picked("tip_face")
    assert window.vars["span"].get() == "270"

    window.vars["stock_length"].set("220")
    window.vars["stock_height"].set("40")
    assert window.preview()
    assert window.geometry is not None

    project = tmp_path / "proj" / "wing.foamproj"
    save_project(project, window.values())
    wing.rename(wing.with_name("moved.step"))
    try:
        opened = open_project(project)
        assert Path(opened.values["step"]).parent == tmp_path / "proj" / "wing_files"
        assert window.open_project(project)
        assert window.vars["source"].get() == "step"
        assert window.vars["root_face"].get() == str(pair[1])
        assert window.geometry is not None
    finally:
        wing.with_name("moved.step").rename(wing)
