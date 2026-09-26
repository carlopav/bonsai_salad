# Bonsai Salad

This repo contains a collection of random scripts that help me with my everyday work with Bonsai and IfcOpenshell.
The scripts are developed with the support of Claude Code.
I Share them in the hope they can be useful and that they will be integrated into IfcOpenShell in the future.

## Install
An add-on for Blender 4.2 or newer, on top of Bonsai: every tool here talks to the model through `bonsai.tool` and `ifcopenshell`, so install and enable Bonsai first.

Blender installs the add-on from a zip holding a single folder named `bonsai_salad`, the one with `__init__.py` in it — that folder name becomes the module name, so the `bonsai_salad-main` GitHub's *Download ZIP* gives you has to be renamed before zipping it back. Cloning avoids the round trip:

```
git clone https://github.com/carlopav/bonsai_salad.git
zip -r bonsai_salad.zip bonsai_salad -x "*.git*" "*__pycache__*"
```

Then Edit > Preferences > Add-ons > the dropdown on the right > *Install from Disk...*, pick the zip, and tick *Bonsai Salad*.

The panel is in the 3D view sidebar (N) under *Bonsai Salad*; the tools that write schedules live under Bonsai's Schedules panel instead.

### From a clone, without the zip
To follow the repo rather than a snapshot of it, link the clone into Blender's add-ons directory instead of installing a zip: Blender reads the working tree as it stands, so a `git pull` or a branch switch is all it takes to change what loads — the branch you have checked out is the one that runs.

The directory is next to the other per-version Blender settings, `<version>` being the one you are installing into, `5.2` and so on:

- Windows: `%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\`
- macOS: `~/Library/Application Support/Blender/<version>/scripts/addons/`
- Linux: `~/.config/blender/<version>/scripts/addons/`

The link has to be named `bonsai_salad`, whatever the clone is called on disk, since the name is the module name. On macOS and Linux a symlink does it:

```
ln -s ~/src/bonsai_salad ~/.config/blender/5.2/scripts/addons/bonsai_salad
```

On Windows use a junction rather than a symlink — `mklink /D` wants administrator rights or Developer Mode, `mklink /J` wants neither, and works across drives just as well:

```
mklink /J "%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\bonsai_salad" "D:\src\bonsai_salad"
```

Enable it as above; the add-ons list shows it tagged *Legacy*, this being a `bl_info` add-on and not an extension. Edits to the code land on the next Blender restart. Should the clone's `__pycache__/` have been written by another python, delete it if imports start failing.

To undo the link, remove the link itself and not what it points at. On Windows that means `rmdir`, never a recursive delete: PowerShell's `Remove-Item -Recurse` follows a junction and empties the repo behind it.

```
cmd /c rmdir "%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\bonsai_salad"
```

Some tools need a package Bonsai does not bring: `ezdxf` for ifc_dxf and dxf_ifc, `odfpy` for the ODS schedules, `Pillow` for the rasters ifc_dxf reads, `typst` for sheets_to_pdf. A missing one stops its own tool, not the add-on. Install them into Blender's python, e.g. on Windows

```
"C:\Program Files\Blender Foundation\Blender 4.2\4.2\python\bin\python.exe" -m pip install ezdxf odfpy
```

## License
Gpl 3.

## More info
Each tool has it's own README.md file you can reference. 

# Sheets to pdf
Convert all existing sheets to pdf. It reads the built sheets, the ones Bonsai writes under sheets/ with Create Sheets, so drawings, schedules and titleblock are all in — sheets never built are skipped, not converted from their layout.
Multi-line headings of a schedule are folded onto their own baseline before rendering: typst reads SVG through resvg, which misplaces the first letter of a line offset from the one below it.
Require Typst available in python blender. You can install it by getting https://extensions.blender.org/add-ons/typst-importer/

# Ifc_dxf
The goal is to export a fully editable and clean dxf from a bonsai drawing view, including cuts, hatches and annotations.
Geometries should be kept as originals, without tassellation.
Dxf layer structure does match Ifc Classes, and can be customized using a template dxf created with your favourite dxf editor.
Two export methods are available: **Accurate** (default) uses ifcopenshell's native OCC/HLR serializer for real wall/column section cuts (fused and hatched), with native 2D plan symbols for everything else; **Approximate** is a pure-Python fallback (Shapely profile projection, no OCC) for when accuracy on complex geometry matters less than speed.

# Mep (WIP)
Generate MEP systems in Bonsai from a topological graph (appliances → collectors → outfall), with norm-based sizing (UNI EN 12056-2 for gravity drainage), parametric pipe/fitting geometry and quantity takeoff.
The pure-Python core (graph, norm tables with citations, sizing engine, routing sketch, IFC export with connected ports) is implemented and tested. No external Python dependencies (data tables are json).

# Urban_parameters
Measures the IfcSpatialZones used to model a buildable volume: gross area (the footprint projected along world Z), mean height (the volume over that area) and the urban volume the two multiply to.
Lives under Schedules > IfcSpatialZones.
A table covers one ObjectType at a time, picked from a dropdown listing the types the project's zones actually carry: totalling zones of different kinds together says nothing.
One button writes the measures onto each zone as a "Parametri urbanistici" quantity set, another writes the table to schedules/&lt;ObjectType&gt;.ods next to the IFC — overwriting the one already there — and registers it among Bonsai's schedules, ready to be built and placed on a sheet. Bonsai names no directory for schedules of its own, so that one is this tool's convention, shaped like its sheets/ and drawings/.
The same quantity set carries a Coefficiente, seeded at 1 and never overwritten afterwards: -1 detracts the zone from the others, a fraction counts it in part. It is the table's second column, next to the values it weighs. The table splits the detracted zones into their own block and closes on a net total; subtotals and totals are spreadsheet formulas, not frozen numbers.

# Daylight_ventilation
Checks the *rapporto aeroilluminante* of every IfcSpace: the ratio between window area and net floor area, one for daylight and one for ventilation, against a requirement set per room.
Lives under Schedules > Rapporti aeroilluminanti.
The clear opening of each window is measured as the smallest section of its void across the wall's thickness; the room a window serves is recorded as an IfcRelSpaceBoundary and never overwritten once written, so a correction made by hand survives every recalculation. A window whose opening cannot be measured leaves its room without a verdict rather than a guessed one, until the area is typed in by hand.
The lighting and ventilation areas of a window are overrides, absent until you write one: a button prepares them from the measured clear opening so there is a value to edit rather than a property to type from nothing.
Exports an ODS schedule grouped by storey, with the ratios and the verdict as live formulas, and the measured clear opening beside the counted areas so a correction made by hand is visible in the table.

# Ifc_spaces
Numbers the selected IfcSpaces as prefix01, prefix02, and so on, writing nothing but their Name.
Lives under Schedules > IfcSpaces.
One counter climbs the storeys in the same order Bonsai lists them in its spatial tree, the placement height ahead of the optional Elevation attribute — the first storey takes 01 to 07, the next one carries on from 08 — and inside a storey the numbering starts at the largest room and goes on each time to the nearest room still unnumbered, the way you would walk the plan. Two digits up to 99, three from 100 on.
A room that already carries a name can be left alone: it still guides the order of the ones around it, but consumes no number, so the renamed ones stay contiguous.
A room without a storey, or whose geometry cannot be read, stops the whole run before anything is written and is left selected in the viewport.
Exports an *abaco dei locali* as well, one ODS per storey under schedules/ next to the IFC, each registered among Bonsai's schedules ready to be placed on a sheet: code, name, net floor area, volume, the aeroilluminante area the room owes and the two it has, and the verdict.
The table is a reading of the model and nothing else — every column is a query IfcCsv resolves against the room, the quantities from Qto_SpaceBaseQuantities and the rest from the pset the Rapporti aeroilluminanti check writes — so a quantity nobody took off prints a dash instead of a zero.
Balconies, parking bays and GFA overlays are not rooms and stay out; a room belonging to no storey lands in no table and is reported.
The sheet is styled as it is written: columns totalling the 190 mm the table is given on a sheet with the long name taking the slack, a framed heading of double height over rows ruled above and below, the code and the verdict centred and the measures aligned right, and a print range around the content. Bonsai's schedule renderer reads all of that off the ODS, so the drawing follows the sheet — and since every export restyles it, there is no formatting to preserve.

# Dxf_ifc
The inverse of ifc_dxf: import a DXF file as an IFC representation directly on a selected Bonsai element.
DXF entities (lines, polylines, arcs, circles, ellipses, splines, hatches, inserts, text) are converted to native IFC geometry (IfcPolyline, IfcTrimmedCurve, IfcCircle, IfcMappedItem, etc.) and assigned to the chosen representation subcontext (default: Plan / Annotation / PLAN_VIEW).
Layer colours and lineweights are preserved as IfcPresentationLayerWithStyle.
