# ifc_dxf — notes

Things that were not obvious, and cost time to find. Each entry is the symptom
first, because that is what you will have when you arrive here.

Add to this file in the same commit that fixes the thing. A bug that took an
afternoon to corner and a line to fix leaves nothing behind otherwise, and the
next person pays for it again.

---

## Projection

### A reflected ceiling plan needs the mirror in the camera, not only the serializer

**Symptom:** in an RCP, the linework is right and the text is somewhere else.
Every tag is at the exactly-right coordinate for a plan view that is not being
drawn. One real RCP put all 48 of its light tags on the wrong side of the plan.

A `REFLECTED_PLAN_VIEW` is the plan reflected in Y, and a drawing is assembled
from two halves that have to agree on that:

* linework comes from the SVG serializer, told to mirror by
  `svg-mirror-y` / `setMirrorY(True)`;
* annotations, plan symbols and hatches are projected here, through
  `camera_matrix_inv_col_major`.

Set the mirror on the serializer alone and the halves are in different frames.

**Rule:** the reflection belongs in the camera inverse, which is where Bonsai
puts it too — `tool.Drawing.get_camera_shape_matrix` negates the camera's Y for
a `REFLECTED_PLAN_VIEW`, so everything projected through that camera inherits
it. See `camera._MIRRORED_TARGET_VIEWS`.

Two things do *not* want the reflection and have to take it back out:

* `camera_plan_rotation`, because `plan_symbols` turns block-local arc start/end
  angles by `cam_rot_deg`, and a reflection has no such angle. The reflection
  travels on the INSERT instead: `_compute_insert` negates the rotation and
  flips the handedness (`Mirror . Rot(a) == Rot(-a) . Mirror`), which DXF
  expresses as `yscale -1`.
* `camera_pos_dir_ref`, which feeds the serializer — it applies its own mirror,
  so handing it a mirrored frame would mirror twice.

Worth knowing when checking the arithmetic: the camera body is centred on its
own origin (`y_max + y_min == 0`), so the serializer mirrors about that origin
and the paper→camera mapping in `accurate/pipeline._parse_hlr_svg` needs no
constant adjustment.

**How it was caught:** every DXF entity carries its GlobalId in XDATA
(`core/xdata.py`), so the drawn position of each element can be compared against
its own `ObjectPlacement` projected through the camera. For 360 elements the
as-is distance had a median of 23.8 drawing units; negating the IFC's Y brought
it to 0.15. That is not something to judge by eye.

### A reflected ceiling plan looks up, so "nearest the viewer" is not "highest"

**Symptom:** ceilings missing from an RCP. Two were reported; six were gone, and
the footprint painter had actually hidden 47 of the drawing's 52.

Bonsai flips the camera's Z axis for a `REFLECTED_PLAN_VIEW`
(`generate_drawing_matrix`: `m.col[2] *= -1`), so an RCP camera looks **up**.
The accurate pipeline inherits that for free almost everywhere, because HLR is
given the real camera and resolves occlusion itself. The exception is
`_recompose_plan_footprints`, which resolves slabs/coverings/roofs with its own
painter's algorithm — HLR z-fights on coplanar horizontal surfaces, so that case
is solved in 2.5D instead. Written as "highest z_top paints first" it is right
for a plan and exactly backwards for an RCP: every ceiling got subtracted away
by the slabs and roof above it.

**Rule:** any visibility rule that does *not* go through HLR has to read the
view direction rather than assume one. `cam_inv_np[2, 2]` gives it: +1 looking
down, -1 looking up. Only the depth flips — `_FOOTPRINT_PRIORITY` still breaks
coplanar ties the same way, since a finish shows over the structure it is fixed
to from either side.

Elements genuinely behind a nearer one stay hidden, which is the point: in this
drawing one ceiling is still dropped bottom-up, correctly, because a lower
ceiling covers it.

---

## Output

### A DXF has no viewport, so nothing clips the drawing to its frame

**Symptom:** text "consolidated to one location". Really the whole drawing was
squeezed into a sliver: zoom-to-extents covered 1926 ft of a 131 ft frame, so
the plan filled 3% of the window and its room labels piled together. Every
coordinate in the file was correct.

Bonsai's SVG has a viewport that clips at render time. A DXF has none, and the
serializer projects whatever it is handed. Frustum culling admits an element on
its *placement origin* (with a geometry-AABB rescue for the ones that reach into
view), so a degenerate element is admitted on an origin that means nothing: two
of them here, an `IfcFurniture` and an `IfcDoor` both named `0` and neither
typed, carried 591 entities ~1800 ft away.

The damage is not only cosmetic. `$EXTMIN`/`$EXTMAX` and the saved viewport are
computed from the drawn geometry, so one stray element resizes the view for
everyone who opens the file.

**Rule:** cull geometry whose bounding box misses the camera frame
(`camera_body_local_extents`) and recompute the extents from what survives --
`dxf_writer._cull_outside_the_drawing`. Only entities that miss the frame
entirely go, by bounding box, so anything crossing the border is kept whole and
no geometry is altered; an undeterminable extent is kept, never dropped on a
guess. Annotation layers are exempt, because a tag leading off the frame is a
drafting decision rather than stray linework.

On the drawing above this removed 1,274 of 2,154 entities and brought the extent
from 1926 ft to 195 ft. Still wider than the 131 ft frame: entities *crossing*
the border are kept whole, and trimming those is a separate job.

### Scales in ACAD_SCALELIST are not keyed by their name

**Symptom:** 53 scale entries in an export, twelve of them duplicate pairs --
two `1:1`, two `1:2`, and so on.

AutoCAD keys these dictionary entries `A0`, `A1`, ... and keeps the scale's name
in group 300 inside the `SCALE` object. Deduplicating on the dictionary key
therefore recognises none of the template's own 17 scales and adds a second copy
of every one the exporter also knows.

The same mistake was hiding in the returned `{name: handle}` map, which the
writer looks up by name to make an entity annotative
(`current_scale_handle = scale_handles.get(scale_name)`). Keyed by dictionary
key, it missed every template scale -- a 1:100 drawing only found its `SCALE`
object because the duplicate had just been added *under its name*. Fixing the
dedupe without fixing the map would have left those drawings with no annotative
scale at all.

**Rule:** read scale names from group 300 — `dxf_template._scale_entries`.

---

## Annotations

### Resolve a text literal's formulas before its variables, and pass the product

**Symptom:** tag text comes out empty. 40 of 48 annotation ATTRIBs held `''`.

A Bonsai text literal mixes two syntaxes: `{{...}}` variables and
`` ``...`` `` formulas. Substituting the variables first turns

```
``concat({{type.Name}}, " (", substr({{EPset_Status.Status}}, 2, 4), ")")``
```

into `concat(L2, " (", substr(EXISTING, 2, 4), ")")`, and
`ifcopenshell.util.selector.format` then raises
`UnexpectedCharacters: No terminal matches 'L' at col 8` — the substituted
values are bare words where its grammar wants quoted strings.

**Rule:** do what `tool.Drawing.replace_text_literal_variables` does — hand each
formula to `format()` *with the product*, and let it resolve the `{{...}}`
inside, quoting and typing each value the way its own grammar needs. Resolve
whatever `{{...}}` remain afterwards. `format(query, element=None)` takes an
element on both the 0.8 and 0.9 lanes; a comment in this repo once claimed
otherwise and the empty strings followed from believing it.

A list-valued property (`IfcPropertyEnumeratedValue`) resolves to a list, which
needs joining before it reaches a DXF string.

### Only `align_point` (group code 11) positions aligned text

For a TEXT or ATTDEF with a non-default `halign`/`valign` — anything but
left/baseline — DXF readers take the position from group code 11 and ignore
code 10. Set `insert` alone and the text lands at the origin. IFC
`BoxAlignment` maps to those two codes via
`annotations._BOX_ALIGN_TO_TEXT`; note the values are `"top-middle"` and
`"bottom-middle"` but a bare `"center"` for the middle row.

---

## Testing

### A test that cannot fail is worse than no test

Both of the bugs above were first "verified" by assertions that passed either
way:

* an annotation count that read 0 empty strings because the helper did not
  follow `IfcMappedItem` to the literal, so it was counting nothing;
* a reflected-plan test written against the approximate pipeline, which
  projects *both* halves of the drawing through the camera and so stayed
  self-consistent while wrong. The bug only exists where the two halves come
  from different places, i.e. the accurate pipeline;
* a stray-geometry test that placed its stray element far away, where frustum
  culling dropped it long before the writer saw it. Reproducing that bug needs
  an element whose *placement* is in view and whose geometry is not, which is
  what a degenerate element actually looks like.

**Rule:** break the fix and watch the new test fail before committing it. For
the mirror, disabling `_MIRRORED_TARGET_VIEWS` has to leave the linework
reflected and the text behind — the user's exact symptom — or the test is not
testing it.

### Keep the `ifcopenshell.file` referenced, or the 0.9 lane dies in the kernel

**Symptom:** `Windows fatal exception: access violation`, killing the whole
pytest run. Only on the 0.9 lane; the same test passed on 0.8.

A test helper built some entities and returned them *without* the file it opened
them from. Once the helper returned, the file was collected and the entities
dangled. On 0.9 `ifcopenshell.util.placement.get_placement` goes through the
geometry kernel (`ifcopenshell.geom.create_shape`), which dereferences that
freed memory; 0.8's `get_local_placement` is pure Python and never notices.

**Rule:** whatever holds entities has to hold the file too, for as long as it
uses them. The faulthandler stack names the real culprit — read it rather than
guessing, it points straight at `create_shape` under `get_placement`.

### `sys.executable` is not a usable interpreter inside Blender

`tests/ifc_dxf/test_cli.py::test_list_runs_headless` spawns
`sys.executable -m ifc_dxf`. Run the suite inside Blender and that is Blender's
bare bundled `python.exe`, which has no `ifcopenshell` on its path — the
extension's wheels are only on `sys.path` within the running Blender process.
The failure is environmental; the test passes under a standalone interpreter
that has ifcopenshell installed.
