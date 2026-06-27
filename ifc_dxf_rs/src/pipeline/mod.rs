pub mod context;

use std::collections::HashMap;

use glam::{DMat4, DVec2, DVec3};

use crate::geometry::{DxfEdge, CameraProjection};
use crate::geometry::cleanup::cleanup;
use crate::geometry::hatch::{build_hatch_regions, HatchRegion, HatchPattern};

// ---------------------------------------------------------------------------
// Public API types  (filled by Python caller)
// ---------------------------------------------------------------------------

/// Styling overrides for a single DXF layer.
#[derive(Debug, Clone)]
pub struct LayerStyle {
    /// ACI colour index (1–255; 7 = white/black)
    pub color:      i16,
    /// Lineweight in hundredths of mm (e.g. 35 = 0.35 mm; –1 = BYLAYER)
    pub lineweight: i16,
    /// Linetype name as stored in the DXF LTYPE table, e.g. "Continuous", "Dashed"
    pub linetype:   String,
}

/// Everything the Rust side needs to generate a drawing.
#[derive(Debug)]
pub struct DrawingRequest {
    pub proj: CameraProjection,
    pub camera_dir: [f64; 3],
    pub camera_pos: [f64; 3],
    pub target_view: String,
    pub elements: Vec<ElementData>,
    pub output_path: String,
    pub dxf_version: String,
    /// Per-layer style overrides loaded from layer_styles.json.
    pub layer_styles: HashMap<String, LayerStyle>,
}

/// Per-element data produced by the Python classifier.
#[derive(Debug)]
pub struct ElementData {
    pub id: u64,
    /// DXF block name: type name for shared-type elements, GlobalId otherwise.
    pub block_name: String,
    pub ifc_class: String,
    pub material: String,
    pub bucket: Bucket,
}

/// Processing bucket for an element.
#[derive(Debug, Clone)]
pub enum Bucket {
    /// A — IfcExtrudedAreaSolid, no booleans, extrusion parallel to view.
    Profile(crate::geometry::project::ProfileData),

    /// B — 2D native representation (Plan/FootPrint context), LOCAL coords.
    /// Defines the block geometry. Use BlockInsert to place each instance.
    NativeCurves {
        /// Element-local vertices: [x0,y0,z0, x1,y1,z1, ...]
        verts: Vec<[f64; 3]>,
        /// Edge index pairs: [i0,j0, i1,j1, ...]
        edges: Vec<[u32; 2]>,
    },

    /// INSERT reference for an existing block. One per element instance.
    /// Used together with NativeCurves: Python calls add_native_curves once
    /// per block name (type or element), then add_block_insert once per instance.
    BlockInsert {
        world_matrix: [f64; 16],
    },

    /// B (legacy) — 2D native BRep text (CASCADE topology format).
    NativeBrep {
        brep_text: String,
        world_matrix: [f64; 16],
    },

    /// C — Full 3D Body; HLR required. Falls back to wireframe until OCC.
    /// Geometry is world-space (world_matrix applied during BRep parsing).
    /// INSERT is placed at (0, 0) in drawing space.
    BodyBrep {
        brep_text: String,
        world_matrix: [f64; 16],
    },

    /// D — Last resort wireframe from raw BRep text.
    Wireframe {
        brep_text: String,
        world_matrix: [f64; 16],
    },

    /// Flat wall: project edges directly to drawing space, no BLOCK/INSERT.
    /// All edges written as LINE entities in model space on
    /// "<ifc_class>_Section" / "<ifc_class>_View" layers.
    WallFlat {
        verts: Vec<[f64; 3]>,   // element-local coords
        edges: Vec<[u32; 2]>,   // edge pairs
        world_matrix: [f64; 16],
    },

    /// Pre-projected 2D arc appended to an existing block definition (already in block-local space).
    BlockArc {
        cx: f64, cy: f64,
        radius: f64,
        start_angle: f64,  // degrees CCW from block-local X
        end_angle: f64,
    },

    /// Pre-projected 2D circle appended to an existing block definition.
    BlockCircle {
        cx: f64, cy: f64,
        radius: f64,
    },

    /// Wall polygon already projected to 2D drawing space by Python (Shapely mode).
    /// outer / holes are in metres, drawing-space coords.
    /// is_section=true  → wall cut by section plane → _Section layer + hatch
    /// is_section=false → wall below section plane → _View layer, no hatch
    WallPolygon {
        outer:      Vec<DVec2>,
        holes:      Vec<Vec<DVec2>>,
        is_section: bool,
    },
}

// ---------------------------------------------------------------------------
// Output types
// ---------------------------------------------------------------------------

/// Resolved INSERT placement for a block instance.
#[derive(Debug, Clone)]
pub struct InsertData {
    /// Projected position of the element's world origin in drawing space.
    pub position: DVec2,
    /// Rotation angle in degrees (CCW), computed from element's world matrix.
    pub rotation: f64,
}

/// Per-block data: geometry already projected in block-local 2D space,
/// plus one InsertData for each element instance of this block.
#[derive(Debug)]
pub struct ElementBlock {
    pub block_name: String,
    pub ifc_class: String,
    pub edges: Vec<DxfEdge>,
    pub inserts: Vec<InsertData>,
}

/// A merged wall (or slab, etc.) outline in drawing space.
/// Written directly to model space as LWPOLYLINE + hatch — no BLOCK/INSERT.
#[derive(Debug)]
pub struct MergedContour {
    /// Layer for the outline polylines, e.g. "BBIM_IFCWALL_CUT".
    pub outline_layer: String,
    /// Layer for the hatch, e.g. "IfcWall_Hatches".
    pub hatch_layer: String,
    pub material: String,
    pub exterior: Vec<DVec2>,
    pub interiors: Vec<Vec<DVec2>>,
}

// ---------------------------------------------------------------------------
// Main entry point
// ---------------------------------------------------------------------------

/// Generate all element blocks, hatch regions, and flat wall edges.
///
/// Returns `(element_blocks, element_hatches, wall_hatches, flat_walls)`.
/// Kept separate so the writer can emit wall_hatches *before* flat_walls
/// (wall outlines), ensuring hatches sit visually below their outlines.
pub fn generate_linework(req: &DrawingRequest) -> (Vec<ElementBlock>, Vec<HatchRegion>, Vec<HatchRegion>, Vec<DxfEdge>) {
    // block_name → (ifc_class, edges)
    let mut block_defs: HashMap<String, (String, Vec<DxfEdge>)> = HashMap::new();
    // block_name → inserts
    let mut block_inserts: HashMap<String, Vec<InsertData>> = HashMap::new();
    // maintain insertion order
    let mut block_order: Vec<String> = Vec::new();

    // Flat wall edges projected directly to drawing space (no BLOCK/INSERT).
    let mut flat_wall_edges: Vec<DxfEdge> = Vec::new();
    // Hatch regions for WallPolygon entries (accumulated, then appended to main hatches).
    let mut wall_poly_hatches: Vec<HatchRegion> = Vec::new();

    for elem in &req.elements {
        let name = &elem.block_name;

        match &elem.bucket {
            Bucket::NativeCurves { verts, edges } => {
                // Define block geometry once (first occurrence wins)
                block_defs.entry(name.clone()).or_insert_with(|| {
                    block_order.push(name.clone());
                    let raw = project_local_curves(verts, edges, &req.proj);
                    let clean = cleanup(raw);
                    (elem.ifc_class.clone(), clean)
                });
                // Each NativeCurves entry does NOT add an insert by itself;
                // that is done by a following BlockInsert entry.
            }

            Bucket::BlockInsert { world_matrix } => {
                let insert = compute_insert(&req.proj, world_matrix);
                block_inserts.entry(name.clone()).or_default().push(insert);
            }

            Bucket::BodyBrep { brep_text, world_matrix } => {
                // World-space geometry; INSERT fixed at drawing origin (0,0).
                block_defs.entry(name.clone()).or_insert_with(|| {
                    block_order.push(name.clone());
                    use crate::io::brep_parser::parse_edges;
                    let m = mat4_from_col_major(world_matrix);
                    let raw = parse_edges(
                        brep_text, &m, &req.proj,
                        &elem.ifc_class, &elem.material, false,
                    );
                    let clean = cleanup(raw);
                    (elem.ifc_class.clone(), clean)
                });
                block_inserts.entry(name.clone()).or_default()
                    .push(InsertData { position: DVec2::ZERO, rotation: 0.0 });
            }

            Bucket::NativeBrep { brep_text, world_matrix }
            | Bucket::Wireframe { brep_text, world_matrix } => {
                block_defs.entry(name.clone()).or_insert_with(|| {
                    block_order.push(name.clone());
                    use crate::io::brep_parser::parse_edges;
                    let m = mat4_from_col_major(world_matrix);
                    let raw = parse_edges(
                        brep_text, &m, &req.proj,
                        &elem.ifc_class, &elem.material, true,
                    );
                    let clean = cleanup(raw);
                    (elem.ifc_class.clone(), clean)
                });
                block_inserts.entry(name.clone()).or_default()
                    .push(InsertData { position: DVec2::ZERO, rotation: 0.0 });
            }

            Bucket::Profile(profile) => {
                use crate::geometry::project::profile_to_edges;
                block_defs.entry(name.clone()).or_insert_with(|| {
                    block_order.push(name.clone());
                    let raw = profile_to_edges(
                        profile,
                        &DMat4::IDENTITY,
                        &req.proj,
                        &elem.ifc_class,
                        &elem.material,
                    );
                    let clean = cleanup(raw);
                    (elem.ifc_class.clone(), clean)
                });
                block_inserts.entry(name.clone()).or_default()
                    .push(InsertData { position: DVec2::ZERO, rotation: 0.0 });
            }

            Bucket::WallFlat { verts, edges, world_matrix } => {
                let m = mat4_from_col_major(world_matrix);
                // Project every edge endpoint to drawing space and emit a LINE.
                // All wall edges go on the Section layer for now; View layer
                // (projected visible faces) requires OCC analysis (TODO).
                let section_layer = format!("{}_Section", elem.ifc_class);
                for [i, j] in edges {
                    let p0 = req.proj.project(m.transform_point3(DVec3::from(verts[*i as usize])));
                    let p1 = req.proj.project(m.transform_point3(DVec3::from(verts[*j as usize])));
                    if p0.distance(p1) > 1e-9 {
                        flat_wall_edges.push(DxfEdge::line(
                            p0, p1, &section_layer,
                            &elem.ifc_class, &elem.material, true,
                        ));
                    }
                }
            }

            Bucket::BlockArc { cx, cy, radius, start_angle, end_angle } => {
                // Append an ARC entity to an already-defined block (must follow NativeCurves).
                // Coordinates are pre-projected to block-local space by Python.
                if let Some((_, edges)) = block_defs.get_mut(name) {
                    edges.push(DxfEdge::arc(
                        DVec2::new(*cx, *cy), *radius,
                        *start_angle, *end_angle,
                        "0", "", "", false,
                    ));
                }
            }

            Bucket::BlockCircle { cx, cy, radius } => {
                // Append a CIRCLE entity to an already-defined block.
                if let Some((_, edges)) = block_defs.get_mut(name) {
                    edges.push(DxfEdge::circle(
                        DVec2::new(*cx, *cy), *radius,
                        "0", "", "", false,
                    ));
                }
            }

            Bucket::WallPolygon { outer, holes, is_section } => {
                let outline_layer = if *is_section {
                    format!("{}_Section", elem.ifc_class)
                } else {
                    format!("{}_View", elem.ifc_class)
                };
                let hatch_layer = format!("{}_Hatches", elem.ifc_class);

                if outer.len() >= 2 {
                    flat_wall_edges.push(DxfEdge::polyline(
                        outer.clone(), true,
                        &outline_layer, &elem.ifc_class, &elem.material, true,
                    ));
                }
                for hole in holes.iter() {
                    if hole.len() >= 2 {
                        flat_wall_edges.push(DxfEdge::polyline(
                            hole.clone(), true,
                            &outline_layer, &elem.ifc_class, &elem.material, true,
                        ));
                    }
                }
                // Hatch only for cut walls (section), not for viewed walls.
                if *is_section && outer.len() >= 3 {
                    wall_poly_hatches.push(HatchRegion {
                        layer:     hatch_layer,
                        pattern:   HatchPattern::for_material(&elem.material),
                        exterior:  outer.clone(),
                        interiors: holes.clone(),
                    });
                }
            }
        }
    }

    // Assemble ElementBlock in original insertion order
    let element_blocks: Vec<ElementBlock> = block_order
        .into_iter()
        .filter_map(|name| {
            let (ifc_class, edges) = block_defs.remove(&name)?;
            let inserts = block_inserts.remove(&name).unwrap_or_default();
            Some(ElementBlock { block_name: name, ifc_class, edges, inserts })
        })
        .collect();

    // Collect all edges for hatch region building (non-wall elements)
    let all_edges: Vec<DxfEdge> = element_blocks
        .iter()
        .flat_map(|b| b.edges.iter().cloned())
        .collect();

    let element_hatches = build_hatch_regions(&all_edges);

    (element_blocks, element_hatches, wall_poly_hatches, flat_wall_edges)
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Project element-local 3D vertices to 2D block-local drawing space.
///
/// Local coords are relative to the element's own origin, so only the
/// camera's rotation should be applied — NOT the translation.
/// Using transform_vector3 extracts the rotation-only part of matrix_inv,
/// keeping block geometry centred on the block origin (0,0).
fn project_local_curves(
    verts: &[[f64; 3]],
    edges: &[[u32; 2]],
    proj: &CameraProjection,
) -> Vec<DxfEdge> {
    edges.iter().filter_map(|[i, j]| {
        let v0 = DVec3::from(verts[*i as usize]);
        let v1 = DVec3::from(verts[*j as usize]);
        // transform_vector3 applies only rotation (no translation) — correct
        // for vectors/offsets relative to the element's local origin.
        let p0 = proj.project_local(v0);
        let p1 = proj.project_local(v1);
        if p0.distance(p1) < 1e-9 {
            None
        } else {
            Some(DxfEdge::line(p0, p1, "0", "", "", true))
        }
    }).collect()
}

/// Compute INSERT position and rotation from an element world matrix.
///
/// Position: camera projection of the world-space origin of the element.
/// Rotation: angle of the element's local X-axis in world XY.
///
/// The block geometry is already projected through the camera rotation
/// (via project_local = R_cam^T * v_local).  For plan views, both the camera
/// and element rotations are around Z, so they commute:
///   R_insert = R_cam^T * R_elem * R_cam = R_elem
/// Using the camera-projected angle (R_cam^T * R_elem) would double-count
/// the camera rotation and displace all inserts for non-identity cameras.
fn compute_insert(proj: &CameraProjection, world_matrix: &[f64; 16]) -> InsertData {
    let m = mat4_from_col_major(world_matrix);

    // Projected world origin = INSERT position
    let origin_world = m.transform_point3(DVec3::ZERO);
    let position = proj.project(origin_world);

    // Element rotation in world XY — independent of camera rotation.
    let local_x_world = m.transform_vector3(DVec3::X);
    let rotation = local_x_world.y.atan2(local_x_world.x);

    InsertData { position, rotation }
}

fn mat4_from_col_major(data: &[f64; 16]) -> DMat4 {
    DMat4::from_cols_array(data)
}
