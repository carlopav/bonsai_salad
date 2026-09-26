# dxf_ifc — notes

Things that were not obvious, and cost time to find. Each entry is the symptom
first, because that is what you will have when you arrive here.

Add to this file in the same commit that fixes the thing. A bug that took an
afternoon to corner and a line to fix leaves nothing behind otherwise, and the
next person pays for it again.

---

## IFC

### A conic's trim parameters are angles in the project's plane angle unit

**Symptom:** a 60 degree door swing renders as 300 degrees. A 90 degree arc
renders as 116.62.

`IfcTrimmedCurve.Trim1/Trim2` on an `IfcCircle` or `IfcEllipse` are angles
expressed in the project's `PLANEANGLEUNIT`. Writing raw DXF degrees into a
radian project sweeps the arc far past where it should stop.

Worse: **Bonsai does not always declare a `PLANEANGLEUNIT` at all**
(`ifcopenshell.api.unit.assign_unit` adds length, area and volume, not angle).
With none declared, IfcOpenShell renders the *complementary* arc — hence 60
becoming 300.

**Rule:** trim conics by endpoint, `MasterRepresentation="CARTESIAN"`, with an
`IfcCartesianPoint` in each of `Trim1`/`Trim2`. Cartesian trims cannot depend on
a unit that may be absent. Write the angle alongside the point for readers that
prefer `PARAMETER`. Verified correct across radian, degree and absent angle
units; parameter trims are correct only in the first two.

### Lengths are in project units, not metres

**Symptom:** the drawing imports "too faceted". Curves look like polygons.

An IFC stores lengths in the project's own length unit. `$INSUNITS` gives
metres per DXF unit, so it has to be divided by the project's unit scale
(`ifcopenshell.util.unit.calculate_unit_scale`). Writing metres into a
millimetre project makes everything 1000x too small.

The reason it reads as *faceting* rather than as a scale error: every layer
shrinks equally, so proportions look right, but the geometry is now smaller
than the tessellator's deflection tolerance and a 90 degree arc is satisfied by
one straight segment. A whole floor plan fitting inside 1.17 m is the tell.

This applies to `IfcPositiveLengthMeasure` anywhere, including `CurveWidth` on
`IfcCurveStyle`.

### ifcopenshell rejects numpy scalars

**Symptom:** entities silently missing, no error anywhere.

`attribute 'SenseAgreement' for entity 'IFC4.IfcTrimmedCurve' is expecting
value of type 'BOOL', got 'bool'` — the second `bool` is `numpy.bool`. ezdxf
returns `numpy.float64` for bulge values, so `bulge > 0` is a numpy bool.

Cast at the boundary: `bool(...)`, `float(...)`.

---

## ezdxf

### `virtual_entities()` expands exactly one level

**Symptom:** whole layers missing; a layer that exists in the DXF produces no
IFC element.

A block holding another block hands back the inner `INSERT` as a virtual
entity. If `INSERT` has no converter it is dropped, and everything inside it
goes with it. One real drawing lost 5,206 entities this way — 3,773 lines, 563
arcs, 552 splines — because three layers held nothing but nested blocks.

Expand recursively with a depth cap. See `importer._expand()`.

Note that the geometry then lands under **the inner entities' own layers**, not
the layer of the outer block reference. That is correct, and it means the set of
layers changes after this fix — which invalidates any previously generated
mapping CSV.

### A mirrored INSERT comes back in an inverted OCS

**Symptom:** half the door and furniture blocks appear mirrored, far from where
they belong.

An `INSERT` with a negative scale expands into entities whose coordinates are
given in an OCS with extrusion `(0, 0, -1)`. Read raw, they land mirrored about
the Y axis. In one drawing, 32 of 66 INSERTs were mirrored and their contents
landed ~45,000 inches from their true position.

`ezdxf.upright.upright(entity)` flips them and leaves other extrusions alone.

### Layer `0` inside a block is already resolved

AutoCAD treats layer `0` inside a block definition as "inherit the block
reference's layer". ezdxf applies this during expansion, so nothing inside a
block reports layer `0`. No action needed — but check before assuming, because
it looks exactly like a bug that needs fixing.

### Spline control points are not on the curve

They are a hull the curve is pulled towards. Joining them draws a different
shape. Use `Spline.flattening(distance)`, which evaluates the real curve.

### Bulge arcs: the centre is at the apothem, not the radius

The centre sits on the chord's perpendicular bisector at
`sqrt(r^2 - (d/2)^2)`, and which side depends on whether `|bulge| > 1` (a major
arc). Getting this wrong puts the endpoints off the circle entirely — a
hand-rolled version had endpoints 44.09 from a centre with radius 36.

Delegate to `ezdxf.math.bulge_to_arc`. It returns a **counter-clockwise**
arc, swapping the angles for a negative bulge rather than reversing direction,
so `SenseAgreement` is then always `True`.

### A partial ELLIPSE is the norm, not the exception

**Symptom:** enormous circles over the drawing.

In one drawing all 206 ellipses were elliptical *arcs*. Drawn untrimmed, a 1.3
degree sliver of a 624-foot ellipse becomes the entire 624-foot ellipse. A
shallow arc always belongs to a very large ellipse, so this is at its worst
exactly where it is least expected.

`start_param`/`end_param` are the **parametric** angle, not the polar angle.
The point at parameter `t` is
`centre + a·cos(t)·û + b·sin(t)·v̂`, where `v̂` is `û` turned a quarter turn.

---

## Blender

- **Operator properties persist between runs.** A Source mode or layer chosen
  once stays chosen, and will silently filter the next import. When something
  imports nothing, check the remembered settings first.
- **A file-select operator cannot open a second file browser.** A
  `subtype="FILE_PATH"` field inside a dialog opened with `fileselect_add()`
  fails with *"Cannot activate a file selector dialog, one already open"*. Put
  the second path on a panel property instead.
- **`filepath` may hold a directory.** If the accept button is pressed without a
  file highlighted, or the operator is called bare from the console (which runs
  `execute()` without `invoke()`), `filepath` is the folder. Use the `directory`
  and `filename` properties as a fallback and resolve sensibly.
- **Modules stay loaded for the session.** Toggling the add-on off and on does
  not re-import `core.*`. Restart Blender. To confirm what is actually loaded,
  compare the `.pyc` header's embedded source mtime and size against the `.py` —
  it is conclusive where "I restarted it" is not.
- **`--open-last` is fatal when the last file is gone.** Blender exits with an
  error rather than opening a blank scene. If a save was interrupted, the
  complete data may still be sitting in `name.blend@`, the temp file Blender
  renames into place on success.

---

## Working practice

### Do not duplicate traversal logic

Block expansion existed in three places: the importer, the mapping-template
generator, and the Source dropdown scanner. Fixing the importer left the other
two one level deep, so the dropdown offered layers that import nothing, and a
regenerated template was byte-identical to the stale one it replaced. Both
surfaced as "the fix didn't work".

`importer._expand()` is now the single implementation. Keep it that way.

### Blanket `except Exception` hides real bugs

`dxf_entity_to_ifc` catches everything and returns `None`. That is reasonable
for genuinely unsupported entities, and it is also why the numpy-bool bug
discarded every curved polyline in a drawing without a word. When something is
missing rather than wrong, suspect a swallowed exception and call the converter
directly to see it.

### Check against ground truth, not against expectations

The checks that actually caught things:

- the multiset of every arc sweep and radius, compared with the DXF's own
  (865 arcs, total sweep 29925.64 degrees, matching exactly)
- the rendered extent against `ezdxf.bbox.extents` of the expanded entity set
- the same import into millimetre, metre, centimetre and foot projects, with
  radian, degree and absent angle units — results must be identical

`core/` imports no `bpy`, which is what makes all of this runnable outside
Blender. Keep that property; it is the difference between checking a fix in
seconds and checking it by hand in the UI.

### Measure an idea before shipping it

Scoring layer *descriptions* as free text to guess IFC classes looked
promising and resolved 387 more codes. It was also wrong: "Water supply" scored
to `IfcBoiler`, "Fences: barbed wire" to `IfcTendon`, "Roadways: fire lane" to
`IfcFireSuppressionTerminal`. Class and predefined-type *names* carry the
signal; prose does not. The idea was dropped on the evidence.
