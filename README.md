# Bonsai Salad

This repo contains a collection of random scripts that help me with my everyday work with Bonsai and IfcOpenshell.
The scripts are developed with the support of Cloud Code.
I Share them in the hope they can be useful and that they will be integrated into IfcOpenShell in the future.

## License
Gpl 3.

## More info
Each tool has it's own README.md file you can reference. 

# Sheets to pdf
Convert all existing sheets to pdf.
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

# Dxf_ifc
The inverse of ifc_dxf: import a DXF file as an IFC representation directly on a selected Bonsai element.
DXF entities (lines, polylines, arcs, circles, ellipses, splines, hatches, inserts, text) are converted to native IFC geometry (IfcPolyline, IfcTrimmedCurve, IfcCircle, IfcMappedItem, etc.) and assigned to the chosen representation subcontext (default: Plan / Annotation / PLAN_VIEW).
Layer colours and lineweights are preserved as IfcPresentationLayerWithStyle.