# Job file format

Version 1. Status: draft for approval.

A job file describes one panel to cut: two cross-sections (root and tip), their
placement on the machine, optional inner features, the foam block and the cut
settings. With a machine profile (see `machine-profile.md`) it fully determines
the G-code.

This document defines:

1. the file (§1 to §5),
2. the target geometry: the exact shape the panel must have (§6 to §8),
3. how the CAM core turns it into a cut (§9 to §12),
4. the G-code it writes (§13).

A tool that only needs to know what the panel should look like (for example the
simulator) needs §1 to §8 and §13. Sections §9 to §12 describe the CAM method;
the simulator must not depend on them except where §13 says so.

All terms from `machine-profile.md` (work frame, axes 1 to 4, wire points,
wire speed at a plane, kerf lookup) are used here with the same meaning.

## 1. File

- YAML only. Encoding UTF-8, extension `.yaml`, read the same way as a machine
  profile (YAML 1.1 safe loader; write text in double quotes).
- Keys are case-sensitive. Unknown keys are an error.
- Relative file paths inside a job are relative to the folder containing the
  job file. Use `/` as the separator on every operating system.

## 2. Units

Lengths in mm, angles in degrees, speeds in mm/min, as in the machine profile.
Section shapes from `.dat` files and `points` lists are in **chord fractions**
(dimensionless) until they are scaled by the chord (§6.3).

## 3. Top-level fields

```yaml
format: "foamcutter-job"
version: 1
name: "..."
kerf_table: "..."
cut_speed: 150.0
direction: "upper_first"
max_segment_length: 1.0
min_te_thickness: 0.5
lead_in: 50.0
safe_position: [600.0, 300.0, 600.0, 300.0]
mirror: false
block: { ... }
root: { ... }
tip: { ... }
```

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `format` | text | yes | Exactly `"foamcutter-job"`. |
| `version` | integer | yes | `1`. |
| `name` | text | yes | Free name, written into G-code comments. Non-empty. |
| `kerf_table` | text | yes | Name of a kerf table in the machine profile. |
| `cut_speed` | number, mm/min | yes | Wire speed at the faster of the two foam faces during every feed move (§11). > 0. |
| `direction` | text | yes | `"upper_first"` or `"lower_first"`. Order in which the outer contour is cut (§10.1). |
| `max_segment_length` | number, mm | yes | Largest length of one move along the outer contour, at either foam face, measured along the kerf-compensated contour (§10.3). > 0. A value of 1.0 is suitable for wings. |
| `min_te_thickness` | number, mm | yes | Smallest trailing-edge thickness, in mm at the actual chord (§6.2). ≥ 0. Use 0 to leave the trailing edge unchanged. |
| `lead_in` | number, mm | yes | Clearance along `x` between the block's front face and the entry point, where the wire starts and ends its cut (§10.5). > 0. |
| `safe_position` | list of 4 numbers, mm | yes | Axis 1 to axis 4 G54 positions where the program starts and ends (§10.6). |
| `mirror` | boolean | yes | `true` cuts the mirror-image panel (§12). |
| `channel_exit` | `"rapid"` or number, mm/min | no | How the wire leaves each slit once its feature is cut (§10.4). `"rapid"`: rapid moves (`G0`). A number > 0: feed moves at that speed at the faster foam face (§11). Absent: feed moves at `cut_speed`, like every other feed move. |
| `block` | mapping | yes | The foam block (§5). |
| `root` | section | yes | First section (§4). |
| `tip` | section | yes | Second section (§4). |

The names `root` and `tip` are labels only. Either may be nearer the left tower.

## 4. Sections

```yaml
root:
  shape: { dat: "../airfoils/clarky.dat" }
  chord: 220.0
  twist: 0.0
  twist_axis: 0.25
  position: { x: 150.0, y: 200.0, z: 80.0 }
  features:
    - name: "spar"
      kind: "hole"
      slit: "upper"
      points: [[50.0, 2.0], [60.0, 2.0], [60.0, 12.0], [50.0, 12.0]]
```

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `shape` | mapping | yes | Exactly one of `dat` or `points`. |
| `shape.dat` | text | one of | Path of an airfoil coordinate file (§7). |
| `shape.points` | list of `[u, v]` pairs | one of | The outer contour as chord fractions, in the same order and convention as a Selig file (§7.1): start at the upper trailing edge, run over the upper side to the leading edge, back along the lower side to the lower trailing edge. At least 3 points. |
| `chord` | number, mm | yes | Chord length (§6.3). > 0. |
| `twist` | number, degrees | yes | Rotation of the section about the twist axis. Positive = leading edge up (§6.4). Between −45 and 45. |
| `twist_axis` | number | yes | Position of the twist axis along the chord as a fraction of the chord, measured from the leading edge. 0 ≤ `twist_axis` ≤ 1. |
| `position` | mapping | yes | Work-frame (G54) coordinates `x`, `y`, `z` (mm) of the twist-axis point (§6.5). |
| `features` | list | no | Inner features (§8). Default: none. |

Rules across the two sections:

- `root.position.y` ≠ `tip.position.y`. The two section planes are
  `y = root.position.y` and `y = tip.position.y`.
- Both section planes lie strictly between the carriage planes:
  `0 < y < wire_span`.

## 5. Foam block

```yaml
block: { x: 100.0, length: 300.0, height: 60.0 }
```

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `x` | number, mm | yes | Work-frame `x` of the block's back face (its smallest `x`). |
| `length` | number, mm | yes | Block size along `x`. > 0. |
| `height` | number, mm | yes | Block size along `z`. > 0. |

The block is a rectangular box occupying

```text
x from block.x            to block.x + block.length
y from min(root.y, tip.y) to max(root.y, tip.y)
z from table_z            to table_z + block.height
```

where `table_z` is the G54 `z` of the table top, from the machine profile
(machine profile §3.4). The block rests on the table.
**The block's two end faces
are the two section planes.** These two faces are the **foam faces**. Wire speeds
"at the foam" always mean speeds at these two planes. (If the real block is
longer along `y`, the foam beyond the section planes is cut at speeds the
program does not control.)

## 6. Target geometry of a section

This section defines the exact shape that the finished part must have in each
section plane, before any kerf compensation. The simulator compares against
this geometry. The steps are applied in this order.

### 6.1 Raw contour

The raw contour is the list of `(u, v)` points read from the `.dat` file (§7)
or from `shape.points`, in Selig order (§7.1).

Then:

1. **Remove duplicates.** Drop any point whose distance from the point before
   it is less than 1e−9.
2. **Leading edge.** The leading-edge index `i_le` is the index of the point
   with the smallest `u`. If several points share the smallest `u`, take the
   first of them.
3. **Orientation.** Let `mean_before` be the mean `v` of points `0 … i_le − 1`
   and `mean_after` the mean `v` of points `i_le + 1 … n − 1`. If
   `mean_before < mean_after`, reverse the list (the file ran lower side first)
   and recompute `i_le`.
4. **Normalise.** Translate so the leading-edge point is at `(0, 0)`. Then
   divide all `u` and `v` by `(u_max − u_min)` of the translated points.

After this, the leading edge is at `(0, 0)`. Point 0 is the upper trailing
edge, point `n − 1` is the lower trailing edge, and the chord extent is 1.

### 6.2 Trailing-edge thickness

Let `t_min = min_te_thickness / chord` (chord fraction) and
`gap = v[0] − v[n − 1]`. If `gap < t_min`, let `d = t_min − gap` and change
every point:

```text
points 0 … i_le − 1 (upper side):  v ← v + (d / 2) · u
points i_le + 1 … n − 1 (lower side): v ← v − (d / 2) · u
point i_le (leading edge): unchanged
```

Otherwise leave the contour unchanged. The new gap is `gap + d·(u[0] + u[n−1])/2`.
For a contour whose trailing-edge points are at `u = 1` this is exactly `t_min`.

### 6.3 Scale

Multiply every `u` and `v` by `chord`. The contour is now in mm in the
**section frame**: origin at the leading edge, `u` along the chord toward the
trailing edge, `v` toward the upper side.

### 6.4 Twist

Rotate about the twist-axis point `(c_t, 0)` with `c_t = twist_axis · chord` and
`θ = twist` in degrees:

```text
u' = c_t + (u − c_t)·cos θ + v·sin θ
v' =       −(u − c_t)·sin θ + v·cos θ
```

With θ > 0 the leading edge `(0, 0)` moves to `v' = c_t·sin θ > 0`, i.e. up.
Feature points (§8) are rotated by the same formula.

### 6.5 Placement in the machine

A point `(u', v')` of a section with `position = (X, Y, Z)` maps to the work-frame
point

```text
x = X + (u' − c_t)
y = Y
z = Z + v'
```

So the twist-axis point lands exactly on `position`, the chord runs toward +x
(leading edge toward the back, trailing edge toward the front) and the upper
side faces +z. Each section lies in its plane `y = Y`.

### 6.6 Target outer contour

The target outer contour is the **closed polygon** through the placed points in
order 0 … n − 1, closed by the straight edge from point `n − 1` back to point 0
(the trailing-edge face). Between listed points the contour is straight.

### 6.7 Validity

- The target outer contour must not cross itself.
- Every point of both target contours must lie within the block's `x` and `z`
  range (§5). If not, the job is invalid. The error names the section and the
  largest distance outside.

## 7. Airfoil `.dat` files

Two layouts are accepted. Lines are split on spaces and tabs. Blank lines are
ignored except as noted.

### 7.1 Selig

```text
CLARK Y AIRFOIL
1.0000  0.0006
0.9500  0.0140
...
0.0000  0.0000
...
1.0000 -0.0006
```

- Line 1 is the name.
- Every following non-blank line holds `u v`, starting at the upper trailing
  edge, running over the upper side to the leading edge and back along the
  lower side to the lower trailing edge.

### 7.2 Lednicer

```text
NACA 2412
       35.       35.

0.000000 0.000000
0.012500 0.024300
...
1.000000 0.001300

0.000000 0.000000
0.012500 -0.016500
...
1.000000 -0.001300
```

- Line 1 is the name.
- Line 2 holds two numbers `n_upper n_lower`, both ≥ 2. They may be written
  with a decimal point.
- Then `n_upper` points of the upper side from leading edge to trailing edge,
  then `n_lower` points of the lower side from leading edge to trailing edge.
  Blank lines between the blocks are ignored.
- Converted to Selig order: the upper points reversed, followed by the lower
  points. If the first lower point equals the last reversed upper point (the
  shared leading edge), it is dropped.

### 7.3 Detection

The file is Lednicer if the second non-blank line holds exactly two numbers and
both are greater than 1.5. Otherwise it is Selig. Any line after the name that
does not hold exactly two numbers is an error naming the file and line number.

## 8. Features

Features are inner cuts: spar slots, lightening holes, hinge cuts. Each feature
is listed under both sections with the same `name`. Root and tip features are
matched by name.

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `name` | text | yes | Unique within the section. Must appear in both sections. |
| `kind` | text | yes | `"hole"` or `"open"`. Same in both sections. |
| `slit` | text | yes | `"upper"` or `"lower"`: the side of the outer contour the wire comes from. Same in both sections. |
| `points` | list of `[u, v]` pairs, mm | yes | Feature vertices in the **untwisted section frame**: mm, origin at the leading edge after §6.1 to §6.3, `u` toward the trailing edge, `v` toward the upper side. Root and tip lists must have the same number of points. |
| `channel` | list of `[u, v]` pairs, mm | no | The path of the slit when it is not straight along `±v` (see **Channels** below). Same frame as `points`. At least 1 point. Root and tip lists must have the same number of points, and either both sections give a channel or neither does. |

- `kind: "hole"` is a closed polygon: the vertices in order, closed by an edge
  from the last back to the first. Do not repeat the first point at the end.
  At least 3 points. The material inside is removed (a spar slot or a
  lightening hole). The hole must lie strictly inside the target outer contour
  and must not touch any other feature.
- `kind: "open"` is a polyline cut through the material, with no material
  removed (for example a hinge cut). At least 2 points. It must lie strictly
  inside the target outer contour.
- Feature points go through §6.4 and §6.5 like the outer contour. The target
  shape of a feature is the placed polygon or polyline.

**Slits.** The wire reaches each feature through a straight slit. The slit
starts at the feature's first point and runs in the untwisted section frame
along `+v` (`slit: "upper"`) or `−v` (`slit: "lower"`) to the first point
where it meets the outer contour, after §6.2 and §6.3. That point is the
**slit point**. It must lie on the matching side: between points 0 and `i_le`
for `"upper"`, between `i_le` and `n − 1` for `"lower"`. Otherwise the job is
invalid. The slit is part of the cut but **not** part of the target geometry.
It leaves a thin cut in the finished part, and the simulator should exclude it
when comparing.

**Channels.** A feature with `channel` replaces the straight slit with a
polyline: from the feature's first point through the `channel` points in
order. Every channel point except the last must lie strictly inside the target
outer contour (after §6.2 and §6.3), and the last strictly outside it, so only
the last segment crosses the outer contour. The slit point is the first point
where that segment meets the outer contour, and the slit ends there; the part
of the segment beyond it is not cut. The slit point must lie on the side named
by `slit`, as above. The channel must not cross itself, must not cross or touch
any other feature, and must not cross its own feature's edges except at its
first point. Channel points go through §6.4 and §6.5 like feature points. Like
a straight slit, a channel is cut but is not part of the target geometry. A
straight slit in any direction is a channel of one point.

**Channels into another hole.** A channel may instead end inside another
feature of `kind: "hole"`, its **parent**: every channel point except the last
lies strictly inside the outer contour and outside every hole, and the last
lies strictly inside the parent. Only the last segment crosses the parent's
edges; the slit point is the first point where it does, and it must lie on
the same edge of the parent (the same pair of neighbouring points) at root and
tip. The channel must not cross the outer contour, any other feature, or
itself, and must not cross its own feature's edges except at its first point.
`slit` is not checked for such a channel. The parent must have the same name
at root and tip, and following parents from feature to feature must end at a
feature whose slit meets the outer contour; a loop of features that only lead
into each other is invalid. A hole may be the parent of several features. The
slit point is inserted into the parent's points as an extra vertex.

**Order.** Along the cutting direction (§10.1) the slit points of root and tip
must occur in the same order of feature names. If not, the job is invalid.

## 9. Validation

A job is valid only if every rule in §3 to §8 holds and the machine profile
contains `kerf_table`. The validator reports every violation it finds, each with
the field path (for example `tip.features[0].points`) and the reason.

---

The rest of this document describes how the CAM core builds the cut. The
simulator must not depend on §10 to §12, except to understand §13.

## 10. Path construction (CAM)

### 10.1 Traversal

The outer contour is cut once, all the way round:

- `"upper_first"`: start at point 0 (upper trailing edge), run 1, 2, …, n − 1,
  then the trailing-edge face back to point 0.
- `"lower_first"`: start at point n − 1 (lower trailing edge), run n − 2, …, 0,
  then the trailing-edge face back to point n − 1.

The start point is called `S`.

### 10.2 Fixed pairs

These points are matched one to one between root and tip: `S`, the leading
edge, the far trailing-edge point, the slit points, every vertex of every
feature (with the slit points of channels into another hole inserted, §8), and
every channel point except the last. The slit points are inserted into the outer contour of §6.3 as extra
vertices. Kerf compensation (§10.7) and synchronisation (§10.3) then work on
the placed contours of §6.5, in each section plane. Twist and placement are a
rotation and a translation, so lengths and angles are the same in both frames.

The fixed points divide the outer contour into **stretches**: from each fixed
point to the next in the traversal direction (§10.1). The last stretch runs
from the far trailing-edge point over the trailing-edge face back to `S`.
Root stretch number *k* and tip stretch number *k* are cut by the same moves.

### 10.3 Synchronisation

Synchronisation pairs points of the two **offset contours**: the outer contour
of each section after kerf compensation (§10.7), so the lengths below are the
lengths the wire actually travels at each foam face.

For one stretch, let the root piece of the offset contour have arc length `Lr`
and the tip piece `Lt`. A point of a piece is given by its arc-length fraction
`f`, from 0 at the stretch's first fixed point to 1 at its last.

The **breakpoints** of the stretch are, in increasing `f`:

1. the fraction of every vertex of the root piece;
2. the fraction of every vertex of the tip piece;
3. between two consecutive breakpoints `a < b` from 1 and 2 (and 0 and 1),
   extra equal steps: the interval is divided into
   `m = max(1, ceil((b − a) · max(Lr, Lt) / max_segment_length))` equal parts.

A breakpoint less than 0.001 mm after the one before it at both faces, that is
`(f − f_previous) · max(Lr, Lt) < 0.001`, is dropped. If the dropped breakpoint
is the end of the stretch (`f = 1`), it takes the place of the one before it
instead, unless that one is the start.

Both pieces are evaluated at every breakpoint. Root point `j` is paired with
tip point `j`. So each offset contour passes through every one of its own
vertices, except those within 0.001 mm of another breakpoint. With zero kerf
the offset contour is the target contour, and the path passes through every
target vertex.

Between two consecutive breakpoints neither piece has a vertex. Every move is
therefore a straight part of the offset contour at both faces, `(b − a)·Lr`
long at the root and `(b − a)·Lt` at the tip. Its speed at each face (§11) is
`cut_speed · L / max(Lr, Lt)` with that face's `L`: the same for every move of
the stretch.

Feature vertices are not resampled.

### 10.4 Order of moves

1. From `S` along the outer contour in the traversal direction.
2. On reaching a slit point: along the slit to the feature's first point (one
   move for a straight slit; for a channel, one move per channel segment,
   through the channel points in reverse order), then
   - hole: around the feature through all vertices and back to its first point;
     on reaching the slit point of a feature whose channel ends in this hole
     (§8), that feature is cut the same way (into it along its channel, around
     it, and out again to the slit point) before going on around this hole;
   - open: along the vertices to the last one, then back through them in reverse
     to the first;
   then back along the slit the same way to the slit point (the **channel
   exit**), and on along the contour.
3. Back at `S` after the trailing-edge face.

Each move goes from one paired point to the next, at root and tip at the same
time. Channel points are not kerf compensated (§10.7): the slit is not part of
the target, and the wire leaves it along the same line it came in.

The channel exit follows the slit just cut, so it removes no further material
except what the hot wire melts while passing. With `channel_exit: "rapid"` its
moves are rapid moves (`G0`); with a number they are feed moves at that speed
(§11); without the field they are ordinary feed moves at `cut_speed`. A rapid
channel exit lies inside the block by design, so the rapid check of §10.6 does
not apply to it.

### 10.5 Lead-in and exit

Both sections use the same entry `x`:

```text
x_E = block.x + block.length + lead_in
```

The entry point `E` of each section is at `x = x_E` and at the `z` of that
section's kerf-compensated `S` (§10.7). Because `lead_in > 0`, `E` always lies
outside the block, in front of its front face.

The **lead-in** is one feed move from `E` to `S` at both foam faces at once.
The **exit** is one feed move from `S` back to `E` at both faces at once, along
the same line. Both are timed by the same rule as every other feed move (§11).
They cut only waste foam in front of the part. When the panel is tapered or
swept their two face lengths differ, so their speed ratio can be large. They
are therefore exempt from the speed-ratio check (§11.1). Their speed ratio is
given in the text report instead.

### 10.6 Safe position

The program starts with a rapid move to `safe_position`, then a rapid move to
the entry points. It ends with a rapid move from the entry points back to
`safe_position`. The CAM rejects the job if either rapid move would pass
through the block (§5, from `z = table_z`) at any plane between the two foam
faces.

Before running the program the operator must make sure the wire can travel in
a straight line from where it is to `safe_position` without touching foam.

### 10.7 Kerf compensation

1. **Kerf groups.** Each stretch of the outer contour (§10.2) has one kerf at
   the root face and one at the tip face, used for all its edges. Each edge of
   a hole has its own kerf at each face: feature edges are not resampled, so
   each one is one move.
2. **Speed of a group.** Let `L_r` and `L_t` be the group's lengths in the
   offset contours: the stretch's arc lengths for a stretch, the edge's lengths
   for a hole edge. Its speed at a face is `cut_speed · L / max(L_r, L_t)` with
   that face's `L`. This is the speed of every move of the group at that face
   (§10.3, §11). A group of zero length at a face has kerf 0 there.
3. **Kerf.** The kerf of a group at a face is the kerf-table lookup (machine
   profile §7) of its speed at that face. Each edge is offset by half its
   group's kerf.
4. **Transition vertices.** A **join** is a fixed point of the outer contour,
   where one stretch ends and the next begins, so the kerf may change there. A
   join is **nearly in line** if the target contour turns there by less than
   **20°** (the angle between the directions of its two edges). At every
   nearly-in-line join, before offsetting, a vertex is added to the target
   contour on each of the two edges that meet there, at `max_segment_length`
   from the join along that edge, if the edge is longer than that. The kerf
   change between the two stretches is then made within `max_segment_length`
   of the join on each side. The added vertices are vertices for §10.3.
5. **Offset.** In each section plane, offset every edge of the outer contour
   outward, and every edge of a hole into the hole, by its half kerf. Let `n1`
   and `n2` be the unit normals of the two edges at vertex `v`, pointing away
   from the material, `h1` and `h2` their half kerfs and `c = n1·n2`. The new
   vertex is `v + a·n1 + b·n2` with:
   - if `h1 = h2`, or the two edges turn by 20° or more: the intersection of
     the two offset edges,
     `a = (h1 − c·h2) / (1 − c²)`, `b = (h2 − c·h1) / (1 − c²)`
     (for `h1 = h2` this is `a = b = h1 / (1 + c)`);
   - otherwise (nearly in line, with different kerfs, where the intersection
     would lie far away): the mean half kerf,
     `a = b = (h1 + h2) / 2 / (1 + c)`.

   Edges of zero length are skipped.
6. **Iteration.** The first lookup uses the speeds of the target contours
   (step 2 with the target lengths). Then, repeatedly: offset from the target
   contours with the current kerfs (step 5), compute the speeds of the offset
   contours (step 2) and look the kerfs up again (step 3). When no half kerf
   of any group at either face changes by more than 0.001 mm, the kerfs have
   settled and the offset contours just computed are used. If they have not
   settled after 10 lookups from offset contours, the CAM stops with an error
   naming the contour or feature. Each outer contour and each hole settles on
   its own.
7. Slits, lead-ins, exits and `open` features are not offset. A slit runs
   straight from the offset slit point to the offset first vertex of its
   feature. For a channel into another hole the slit point is the parent's
   offset inserted vertex.
8. If a vertex moves by more than 4 × the larger half kerf of its two edges,
   the CAM stops with an error naming the section and the point, rather than
   cutting a spike.

At a nearly-in-line join where the kerf changes, the moves within
`max_segment_length` of the join have a kerf between the kerfs of the two
stretches. Everywhere else every move has the kerf of its own speed.

### 10.8 Projection to the towers

For each paired point, root `P_r` at `y = y_r` and tip `P_t` at `y = y_t`, the
wire is the line through them. Axis positions:

```text
s(y) = (y − y_r) / (y_t − y_r)
x(y) = x_r + s(y)·(x_t − x_r)
z(y) = z_r + s(y)·(z_t − z_r)

a1 = x(0)          a2 = z(0)
a3 = x(wire_span)  a4 = z(wire_span)
```

## 11. Timing (CAM)

For each feed move, let `d_r` and `d_t` be the distances moved by the
compensated wire points at the two foam faces. The duration is

```text
T = max(d_r, d_t) / cut_speed        (minutes)
```

so the faster face moves at exactly `cut_speed`. Channel-exit feed moves
(§10.4) with a numeric `channel_exit` use that speed in place of `cut_speed`.
Moves with `d_r = d_t = 0`
are dropped. Total cut time = sum of `T` = (path length at the faster face) /
`cut_speed`, where that path length is the sum of `max(d_r, d_t)` over all
feed moves.

After rounding the positions for output (§13), a `G1` line whose four axis
words equal those of the motion line before it is not written. Its duration
is added to the next `G1` line, so the total cut time is unchanged.

### 11.1 Checks

The CAM refuses to write a program, and names the line, axis and amount, if
any of these fails:

- **Soft limits.** Every axis position on every motion line (including rapids
  and the safe position) lies within that axis's `min` … `max`. The error
  names the axis number, its letter, the limit and by how much it is exceeded.
- **Wire angle.** At every motion line's end position the wire angle (machine
  profile §6) is at most `max_wire_angle`.
- **Speed ratio.** For every feed move except the lead-in, the exit (§10.5)
  and the channel exits (§10.4), the speed ratio at the foam faces is at most
  `max_speed_ratio`.
- **Axis rate.** For every feed move, each axis's speed `|Δa| / T` is at most
  that axis's `max_rate`.
- **Kerf table range.** Only the settled lookup of §10.7 step 6 counts.
  During the iteration, a speed outside the kerf table takes the kerf at the
  nearer end of the table, and the iteration carries on. If a speed of the
  settled lookup is outside the table, the error has the exact message of
  machine profile §7, and no program is written. So that the other checks
  can be reported in the same run, the CAM continues with the nearer-end kerf;
  that value is used only to let the other checks run.

Nothing is clipped or adjusted to make a check pass.

## 12. Mirror (CAM)

`mirror: true` produces the reflection of the panel in the plane
`y = wire_span / 2`, the plane midway between the towers. The job is written
un-mirrored. The output is the un-mirrored program with axis 1 and axis 3
values exchanged and axis 2 and axis 4 values exchanged on every line,
including the safe position. Durations and feeds are unchanged. Soft limits are
checked after the exchange.

## 13. G-code produced by the CAM core

The CAM writes only the following. Any tool reading foamcam G-code (the
simulator) may reject anything else.

- One statement per line, lines ending in `\n`.
- Comment lines: `(` text `)` alone on a line. The text contains no `(` or `)`.
- `G21`: millimetre units. Written once, before any motion.
- `G54`: select work coordinate system 1, the work frame of the machine
  profile (§3). Written once, before any motion.
- `G90`: absolute positions. Written once, before any motion.
- `G93`: inverse-time feed. Written once, before the first `G1`.
- `G0 <a1> <a2> <a3> <a4>`: rapid move. All four axis words. Between the
  lead-in and the exit a `G0` is a channel exit (§10.4): it runs back along a
  slit that has just been cut.
- `G1 <a1> <a2> <a3> <a4> F<f>`: feed move. All four axis words and `F` on
  every line.
- `G94`: units-per-minute feed. Written once, after the last motion, so the
  controller is left in its normal mode.

Here `<aN>` is the axis-N letter from the profile followed by the axis-N
position. Positions are G54 work coordinates in mm, written in plain decimal
with exactly 4 decimal places (for example `X123.4560`, `A-5.0000`). Axis words
are always in order axis 1, 2, 3, 4.

`F<f>` is inverse time: the move takes `1 / f` minutes. `f` is written in plain
decimal (never with an exponent) with at least 6 significant digits.

Not written, and therefore not needed by a reader: other work coordinate
systems (`G55` to `G59.3`), `G10`, `G92`, `G53`, `M` codes, dwell (`G4`),
relative mode (`G91`) and spindle or heater commands. The wire heat is controlled
outside the program.

Order of a program:

```text
(foamcam <version>)
(job: <name>)
(profile: <name>)
(... further comment lines ...)
G21
G54
G90
G93
G0 <safe_position>
G0 <entry points E, projected>
G1 ... F...      (lead-in, E to S)
G1 ... F...      (contour, slits and features, §10.4;
                  G0 for each channel exit with channel_exit: "rapid")
G1 ... F...      (exit, S to E)
G0 <safe_position>
G94
```

## 14. Complete example

```yaml
format: "foamcutter-job"
version: 1
name: "tapered wing with spar slot"
kerf_table: "eps-20-sample"     # from examples/profiles/sample-machine.yaml
cut_speed: 150.0
direction: "upper_first"
max_segment_length: 1.0
min_te_thickness: 0.6
lead_in: 20.0
safe_position: [550.0, 250.0, 550.0, 250.0]
mirror: false
block: { x: 100.0, length: 300.0, height: 60.0 }
root:
  shape: { dat: "../airfoils/clarky.dat" }
  chord: 250.0
  twist: 0.0
  twist_axis: 0.25
  position: { x: 182.5, y: 150.0, z: 25.0 }
  features:
    - name: "spar"
      kind: "hole"
      slit: "upper"
      points: [[70.0, 4.0], [80.0, 4.0], [80.0, 14.0], [70.0, 14.0]]
tip:
  shape: { dat: "../airfoils/clarky.dat" }
  chord: 160.0
  twist: -2.0
  twist_axis: 0.25
  position: { x: 160.0, y: 750.0, z: 25.0 }
  features:
    - name: "spar"
      kind: "hole"
      slit: "upper"
      points: [[42.0, 3.0], [52.0, 3.0], [52.0, 10.0], [42.0, 10.0]]
```
