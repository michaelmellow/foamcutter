"""Root and tip outlines from a STEP model: the alternative to two DXFs.

The wing's root and tip must be flat faces of the model (end caps), parallel
to each other. `read_step` lists every flat face; the operator picks two
(`suggest` offers the parallel pair farthest apart), and `sections` turns them
into two outlines in one shared 2D frame plus the distance between them, which
replaces the span typed in the DXF workflow.

The 2D frame is the cutter's section plane (job-file.md §6, machine-profile.md
§3): x along the root chord, leading edge at the smaller x, z up, and the
model mapped rigidly (never mirrored) with the root face at the smaller y. Both
outlines use the same origin, so sweep, dihedral and twist come from the model.
The model is turned the right way up (upper surface on top) by putting the
root at the axes 3-4 tower instead when needed; the warnings say so.

STEP files are read with gmsh (OpenCASCADE), installed by pip. This is not the
Brief 4 front end (src/foamstep): it does not split a wing into panels, it
cuts between two flat faces the operator picks.
"""

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .errors import DxfError
from .outline import DxfOutline, Point, outline_from_pieces

Vec = tuple[float, float, float]

PARALLEL_COS = 0.9999  # faces within about 0.8 degrees count as parallel
CURVE_STEP = 0.2  # mm between sampled points on curved edges, before simplifying
WIRE_POINTS = 60  # points per curved edge in the model's wireframe (the face picker's view)


@dataclass(frozen=True)
class StepFace:
    tag: int  # gmsh / OpenCASCADE face number, stable for a given file
    area: float  # mm2
    centre: Vec
    normal: Vec  # unit
    edges: tuple[tuple[Vec, ...], ...]  # every boundary edge, sampled, mm
    extent: float  # largest distance across the face, mm

    def label(self) -> str:
        axis = max(range(3), key=lambda k: abs(self.normal[k]))
        where = f"{'XYZ'[axis]} = {self.centre[axis]:.1f}"
        return f"face {self.tag}: {self.extent:.1f} mm across, area {self.area:.0f} mm2, at {where}"


@dataclass(frozen=True)
class StepModel:
    path: Path
    faces: tuple[StepFace, ...]  # flat faces only, largest first
    bbox: tuple[Vec, Vec]
    wire: tuple[tuple[Vec, ...], ...] = ()  # every edge of the model, coarsely sampled, for drawing

    def face(self, tag: int) -> StepFace:
        for f in self.faces:
            if f.tag == tag:
                return f
        raise DxfError(f"{self.path.name}: there is no flat face {tag} (flat faces: "
                       f"{', '.join(str(f.tag) for f in self.faces)})")

    def suggest(self) -> tuple[int, int] | None:
        """The parallel pair of flat faces farthest apart, weighted by the smaller
        face's area: normally the root and tip caps. The larger face is the root."""
        best: tuple[float, int, int] | None = None
        for i, a in enumerate(self.faces):
            for b in self.faces[i + 1:]:
                if abs(_dot(a.normal, b.normal)) < PARALLEL_COS:
                    continue
                gap = abs(_dot(_sub(b.centre, a.centre), a.normal))
                if gap < 1.0:
                    continue
                score = gap * min(a.area, b.area)
                if best is None or score > best[0]:
                    root, tip = (a, b) if a.area >= b.area else (b, a)
                    best = (score, root.tag, tip.tag)
        return None if best is None else (best[1], best[2])


@dataclass(frozen=True)
class StepSections:
    root: DxfOutline
    tip: DxfOutline
    span: float  # distance between the two faces, mm
    warnings: tuple[str, ...]


def read_step(path: Path) -> StepModel:
    """Read `path` (cached while the file is unchanged). Raises DxfError."""
    try:
        stat = path.stat()
    except OSError as error:
        raise DxfError(f"{path.name}: cannot read STEP: {error}") from None
    return _read_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=4)
def _read_cached(name: str, _mtime: int, _size: int) -> StepModel:
    path = Path(name)
    try:
        import gmsh  # type: ignore[import-untyped, import-not-found, unused-ignore]
    except (ImportError, OSError) as error:  # OSError: gmsh's own library did not load
        raise DxfError(f"reading STEP files needs gmsh (py -3.12 -m pip install gmsh): {error}") from None

    gmsh.initialize(readConfigFiles=False, interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setString("Geometry.OCCTargetUnit", "MM")
        try:
            gmsh.model.occ.importShapes(name)
        except Exception as error:  # noqa: BLE001 - gmsh raises a bare Exception
            raise DxfError(f"{path.name}: cannot read STEP: {error}") from None
        gmsh.model.occ.synchronize()
        entities = gmsh.model.getEntities(3) or gmsh.model.getEntities(2)
        if not entities:
            raise DxfError(f"{path.name}: the file has no solids or surfaces")
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        for dim, tag in entities:
            box = gmsh.model.getBoundingBox(dim, tag)
            lo = [min(lo[k], box[k]) for k in range(3)]
            hi = [max(hi[k], box[k + 3]) for k in range(3)]
        faces = []
        for _, tag in gmsh.model.getEntities(2):
            if gmsh.model.getType(2, tag) != "Plane":
                continue
            faces.append(_face(gmsh, tag))
        wire = tuple(_curve(gmsh, tag, WIRE_POINTS) for _, tag in gmsh.model.getEntities(1))
    finally:
        gmsh.finalize()
    if not faces:
        raise DxfError(
            f"{path.name}: the model has no flat faces. The root and tip must be flat end faces; "
            "close them in CAD, or export the two sections as DXFs."
        )
    faces.sort(key=lambda f: f.area, reverse=True)
    return StepModel(path, tuple(faces), ((lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2])), wire)


def _curve(gmsh: object, tag: int, n: int) -> tuple[Vec, ...]:
    """One edge of the model as n + 1 points (2 for a straight line)."""
    model = gmsh.model  # type: ignore[attr-defined]
    t0, t1 = (float(v[0]) for v in model.getParametrizationBounds(1, tag))
    n = 1 if model.getType(1, tag) == "Line" else n
    xyz = model.getValue(1, tag, [t0 + (t1 - t0) * i / n for i in range(n + 1)])
    return tuple((float(xyz[3 * i]), float(xyz[3 * i + 1]), float(xyz[3 * i + 2])) for i in range(n + 1))


def _face(gmsh: object, tag: int) -> StepFace:
    model = gmsh.model  # type: ignore[attr-defined]
    occ = model.occ
    cx, cy, cz = (float(v) for v in occ.getCenterOfMass(2, tag))
    nx, ny, nz = (float(v) for v in model.getNormal(tag, [0.0, 0.0])[:3])
    normal = _unit((nx, ny, nz))
    edges = []
    for _, curve in model.getBoundary([(2, tag)], combined=False, oriented=False):
        curve = abs(curve)
        t0, t1 = (float(v[0]) for v in model.getParametrizationBounds(1, curve))
        if model.getType(1, curve) == "Line":
            n = 1
        else:
            n = max(8, min(4000, math.ceil(float(occ.getMass(1, curve)) / CURVE_STEP)))
        ts = [t0 + (t1 - t0) * i / n for i in range(n + 1)]
        xyz = model.getValue(1, curve, ts)
        edges.append(tuple((float(xyz[3 * i]), float(xyz[3 * i + 1]), float(xyz[3 * i + 2])) for i in range(n + 1)))
    points = [p for e in edges for p in e]
    a = max(points, key=lambda p: _dist(p, points[0]))
    b = max(points, key=lambda p: _dist(p, a))
    return StepFace(tag, float(occ.getMass(2, tag)), (cx, cy, cz), normal, tuple(edges), _dist(a, b))


def sections(model: StepModel, root_tag: int, tip_tag: int, simplify_tol: float = 0.01) -> StepSections:
    """The two faces as outlines in the cutter's section frame, and the span. Raises DxfError."""
    if root_tag == tip_tag:
        raise DxfError("pick two different faces for the root and the tip")
    root, tip = model.face(root_tag), model.face(tip_tag)
    if abs(_dot(root.normal, tip.normal)) < PARALLEL_COS:
        angle = math.degrees(math.acos(min(1.0, abs(_dot(root.normal, tip.normal)))))
        raise DxfError(
            f"faces {root_tag} and {tip_tag} are not parallel ({angle:.1f} deg); the wire cuts between "
            "parallel section planes"
        )
    offset = _dot(_sub(tip.centre, root.centre), root.normal)
    span = abs(offset)
    if span < 1.0:
        raise DxfError(f"faces {root_tag} and {tip_tag} lie in the same plane")
    d = root.normal if offset > 0 else _neg(root.normal)  # root -> tip

    # chord direction: across the root face's longest extent, leading edge first
    pts = [p for e in root.edges for p in e]
    a = max(pts, key=lambda p: _dist(p, pts[0]))
    b = max(pts, key=lambda p: _dist(p, a))
    u = _unit(_sub(b, a))
    if _thickness_near(pts, u, d, b) > _thickness_near(pts, u, d, a):
        u = _neg(u)  # the trailing edge is the thinner end; put it at the larger x
    v = _cross(u, d)  # rigid: x × z = -y, so u × v must be -d

    warnings: list[str] = []
    upright = _camber(pts, u, v, root.centre) >= 0
    if not upright:  # turn it over: rotate 180 deg about the chord, root goes to the far tower
        v, d = _neg(v), _neg(d)
        root, tip = tip, root
        warnings.append(
            f"{model.path.name}: face {root_tag}'s upper side faced down, so the model is turned over: "
            f"face {tip_tag} is cut at the axes 1-2 tower and face {root_tag} at the axes 3-4 tower"
        )
    origin = root.centre

    def outline(face: StepFace) -> DxfOutline:
        pieces: list[list[Point]] = [
            [(_dot(_sub(p, origin), u), _dot(_sub(p, origin), v)) for p in edge] for edge in face.edges
        ]
        return outline_from_pieces(f"{model.path.name} face {face.tag}", pieces, simplify_tol=simplify_tol)

    r, t = outline(root), outline(tip)
    return StepSections(r, t, span, tuple(warnings) + r.warnings + t.warnings)


def _thickness_near(pts: list[Vec], u: Vec, d: Vec, end: Vec) -> float:
    """Width of the outline across the chord within 5 % of the chord from `end`."""
    w = _cross(d, u)
    s = [_dot(_sub(p, end), u) for p in pts]
    chord = max(abs(x) for x in s)
    near = [_dot(_sub(p, end), w) for p, x in zip(pts, s, strict=True) if abs(x) <= 0.05 * chord]
    return max(near) - min(near) if near else 0.0


def _camber(pts: list[Vec], u: Vec, v: Vec, origin: Vec) -> float:
    """Mean height of the outline above the line joining its chord ends: > 0 when the upper side is up."""
    xs = [_dot(_sub(p, origin), u) for p in pts]
    zs = [_dot(_sub(p, origin), v) for p in pts]
    i, j = xs.index(min(xs)), xs.index(max(xs))
    if xs[j] - xs[i] < 1e-9:
        return 0.0
    slope = (zs[j] - zs[i]) / (xs[j] - xs[i])
    return sum(z - (zs[i] + slope * (x - xs[i])) for x, z in zip(xs, zs, strict=True)) / len(xs)


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _neg(a: Vec) -> Vec:
    return (-a[0], -a[1], -a[2])


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dist(a: Vec, b: Vec) -> float:
    return math.dist(a, b)


def _unit(a: Vec) -> Vec:
    n = math.sqrt(_dot(a, a))
    if n == 0:
        raise DxfError("degenerate face (zero normal or zero size)")
    return (a[0] / n, a[1] / n, a[2] / n)
