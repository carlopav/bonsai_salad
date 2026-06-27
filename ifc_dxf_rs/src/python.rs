/// PyO3 bindings — exposes ifc_dxf to Python (Bonsai/Blender).

use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use glam::{DMat4, DVec2};

use crate::geometry::CameraProjection;
use crate::geometry::project::{ProfileData, extrusion_parallel_to_view};
use crate::pipeline::{DrawingRequest, ElementData, Bucket, LayerStyle, generate_linework};
use crate::io::writer::write_document;

// ---------------------------------------------------------------------------
// Python-facing types
// ---------------------------------------------------------------------------

/// Camera projection parameters (1:1 metres, camera centre = DXF origin).
#[pyclass]
pub struct PyCameraProjection {
    pub inner: CameraProjection,
}

#[pymethods]
impl PyCameraProjection {
    /// matrix_inv_col_major: list of 16 floats (column-major 4×4).
    /// This is the inverted camera world matrix from Bonsai's
    /// tool.Drawing.get_camera_matrix(camera_obj).inverted().
    #[new]
    fn new(matrix_inv_col_major: Vec<f64>) -> PyResult<Self> {
        if matrix_inv_col_major.len() != 16 {
            return Err(PyValueError::new_err("matrix_inv_col_major must have 16 elements"));
        }
        let arr: [f64; 16] = matrix_inv_col_major.try_into().unwrap();
        let m = DMat4::from_cols_array(&arr);
        Ok(Self {
            inner: CameraProjection::new(m),
        })
    }
}

/// Full drawing request — filled by Python then passed to py_generate().
#[pyclass]
pub struct PyDrawingRequest {
    pub req: DrawingRequest,
}

#[pymethods]
impl PyDrawingRequest {
    #[new]
    fn new(
        proj: &PyCameraProjection,
        camera_dir: [f64; 3],
        camera_pos: [f64; 3],
        target_view: String,
        output_path: String,
        dxf_version: Option<String>,
    ) -> Self {
        Self {
            req: DrawingRequest {
                proj: proj.inner.clone(),
                camera_dir,
                camera_pos,
                target_view,
                elements: Vec::new(),
                output_path,
                dxf_version: dxf_version.unwrap_or_else(|| "AC1027".to_string()),
                layer_styles: std::collections::HashMap::new(),
            },
        }
    }

    /// Load per-layer style overrides from Python.
    ///
    /// styles: list of (layer_name, color_index, lineweight_hundredths_mm, linetype_name)
    /// e.g. [("IfcWall_Section", 7, 35, "Continuous"), ("IfcWall_View", 8, 13, "Dashed")]
    fn set_layer_styles(&mut self, styles: Vec<(String, i16, i16, String)>) {
        self.req.layer_styles = styles
            .into_iter()
            .map(|(name, color, lineweight, linetype)| {
                (name, LayerStyle { color, lineweight, linetype })
            })
            .collect();
    }

    // ── Bucket A — profile-based elements ──────────────────────────────────

    /// Add a Bucket-A element (IfcExtrudedAreaSolid, polygon profile).
    fn add_profile_polygon(
        &mut self,
        block_name: String,
        ifc_class: String,
        material: String,
        outer_pts: Vec<[f64; 2]>,
        holes: Vec<Vec<[f64; 2]>>,
    ) {
        self.req.elements.push(ElementData {
            id: self.req.elements.len() as u64,
            block_name,
            ifc_class,
            material,
            bucket: Bucket::Profile(ProfileData::Polygon { outer: outer_pts, holes }),
        });
    }

    /// Add a Bucket-A element with circle profile.
    fn add_profile_circle(
        &mut self,
        block_name: String,
        ifc_class: String,
        material: String,
        radius: f64,
        px: f64, py: f64,
    ) {
        self.req.elements.push(ElementData {
            id: self.req.elements.len() as u64,
            block_name,
            ifc_class,
            material,
            bucket: Bucket::Profile(ProfileData::Circle { radius, px, py }),
        });
    }

    /// Add a Bucket-A element with rectangle profile.
    fn add_profile_rectangle(
        &mut self,
        block_name: String,
        ifc_class: String,
        material: String,
        x_dim: f64, y_dim: f64,
        px: f64, py: f64, angle: f64,
    ) {
        self.req.elements.push(ElementData {
            id: self.req.elements.len() as u64,
            block_name,
            ifc_class,
            material,
            bucket: Bucket::Profile(ProfileData::Rectangle { x_dim, y_dim, px, py, angle }),
        });
    }

    // ── Bucket B — 2D native representations ───────────────────────────────

    /// Define a block from 2D Plan context geometry (element-LOCAL coords).
    ///
    /// block_name: IFC type name (for shared-type blocks) or element GlobalId.
    /// verts: flat LOCAL-space float list [x0,y0,z0, x1,y1,z1, ...]
    ///        from shape.geometry.verts WITHOUT use-world-coords.
    /// edges: flat uint32 list [i0,j0, i1,j1, ...] from shape.geometry.edges.
    ///
    /// Call this ONCE per block_name (first call wins), then call
    /// add_block_insert() once per element instance.
    fn add_native_curves(
        &mut self,
        block_name: String,
        ifc_class: String,
        material: String,
        verts: Vec<f64>,
        edges: Vec<u32>,
    ) {
        let n_verts = verts.len() / 3;
        let verts3: Vec<[f64; 3]> = (0..n_verts)
            .map(|i| [verts[i * 3], verts[i * 3 + 1], verts[i * 3 + 2]])
            .collect();
        let n_edges = edges.len() / 2;
        let edges2: Vec<[u32; 2]> = (0..n_edges)
            .map(|i| [edges[i * 2], edges[i * 2 + 1]])
            .collect();

        self.req.elements.push(ElementData {
            id: self.req.elements.len() as u64,
            block_name,
            ifc_class,
            material,
            bucket: Bucket::NativeCurves { verts: verts3, edges: edges2 },
        });
    }

    /// Add an INSERT for an existing block (defined by a prior add_native_curves call).
    ///
    /// world_matrix: 16-float column-major 4×4 placement matrix of the element.
    /// The INSERT position and rotation are computed from this matrix.
    fn add_block_insert(
        &mut self,
        block_name: String,
        ifc_class: String,
        world_matrix: Vec<f64>,
    ) -> PyResult<()> {
        if world_matrix.len() != 16 {
            return Err(PyValueError::new_err("world_matrix must have 16 elements"));
        }
        let wm: [f64; 16] = world_matrix.try_into().unwrap();
        self.req.elements.push(ElementData {
            id: self.req.elements.len() as u64,
            block_name,
            ifc_class,
            material: String::new(),
            bucket: Bucket::BlockInsert { world_matrix: wm },
        });
        Ok(())
    }

    /// Append a true DXF ARC entity to an existing block definition.
    ///
    /// Call after add_native_curves for the same block_name, once per block (not per instance).
    /// cx, cy: arc centre in block-local 2D space (pre-projected by Python).
    /// radius: arc radius in metres.
    /// start_angle, end_angle: degrees CCW from block-local X-axis; DXF goes CCW start→end.
    fn add_block_arc(
        &mut self,
        block_name: String,
        ifc_class: String,
        material: String,
        cx: f64, cy: f64,
        radius: f64,
        start_angle: f64,
        end_angle: f64,
    ) {
        self.req.elements.push(ElementData {
            id: self.req.elements.len() as u64,
            block_name,
            ifc_class,
            material,
            bucket: Bucket::BlockArc { cx, cy, radius, start_angle, end_angle },
        });
    }

    /// Append a true DXF CIRCLE entity to an existing block definition.
    ///
    /// cx, cy: centre in block-local 2D space (pre-projected by Python).
    fn add_block_circle(
        &mut self,
        block_name: String,
        ifc_class: String,
        material: String,
        cx: f64, cy: f64,
        radius: f64,
    ) {
        self.req.elements.push(ElementData {
            id: self.req.elements.len() as u64,
            block_name,
            ifc_class,
            material,
            bucket: Bucket::BlockCircle { cx, cy, radius },
        });
    }

    /// Add a Bucket-B element from CASCADE BRep text (legacy path).
    fn add_native_brep(
        &mut self,
        block_name: String,
        ifc_class: String,
        material: String,
        brep_text: String,
        world_matrix: [f64; 16],
    ) {
        self.req.elements.push(ElementData {
            id: self.req.elements.len() as u64,
            block_name,
            ifc_class,
            material,
            bucket: Bucket::NativeBrep { brep_text, world_matrix },
        });
    }

    // ── Flat walls (no BLOCK/INSERT) ───────────────────────────────────────

    /// Add a wall element for flat model-space output (no BLOCK/INSERT).
    ///
    /// verts: flat LOCAL-space list [x0,y0,z0, x1,y1,z1, ...]
    /// edges: flat uint32 list [i0,j0, i1,j1, ...]
    /// world_matrix: 16-float column-major 4×4 element placement matrix.
    ///
    /// Each edge is projected to drawing space via the full camera transform
    /// and written as a LINE on layer "<ifc_class>_Section".
    fn add_wall_flat(
        &mut self,
        ifc_class: String,
        material: String,
        verts: Vec<f64>,
        edges: Vec<u32>,
        world_matrix: Vec<f64>,
    ) -> PyResult<()> {
        if world_matrix.len() != 16 {
            return Err(PyValueError::new_err("world_matrix must have 16 elements"));
        }
        let n_verts = verts.len() / 3;
        let verts3: Vec<[f64; 3]> = (0..n_verts)
            .map(|i| [verts[i * 3], verts[i * 3 + 1], verts[i * 3 + 2]])
            .collect();
        let n_edges = edges.len() / 2;
        let edges2: Vec<[u32; 2]> = (0..n_edges)
            .map(|i| [edges[i * 2], edges[i * 2 + 1]])
            .collect();
        let wm: [f64; 16] = world_matrix.try_into().unwrap();
        self.req.elements.push(ElementData {
            id: self.req.elements.len() as u64,
            block_name: String::new(),
            ifc_class,
            material,
            bucket: Bucket::WallFlat { verts: verts3, edges: edges2, world_matrix: wm },
        });
        Ok(())
    }

    // ── Wall polygon (Shapely mode) ────────────────────────────────────────

    /// Add a wall polygon already projected to 2D drawing space by Python.
    ///
    /// outer: list of [x, y] points in metres (drawing space), no closing repeat.
    /// holes: list of hole rings, same format (openings / voids).
    ///
    /// Emits a closed LWPOLYLINE for outer + each hole on "<ifc_class>_Section",
    /// and a HatchRegion on "<ifc_class>_Hatches".
    fn add_wall_polygon(
        &mut self,
        ifc_class: String,
        material: String,
        outer: Vec<[f64; 2]>,
        holes: Vec<Vec<[f64; 2]>>,
        is_section: bool,
    ) -> PyResult<()> {
        let outer_pts: Vec<DVec2> = outer.iter()
            .map(|[x, y]| DVec2::new(*x, *y))
            .collect();
        let holes_pts: Vec<Vec<DVec2>> = holes.iter()
            .map(|h| h.iter().map(|[x, y]| DVec2::new(*x, *y)).collect())
            .collect();
        self.req.elements.push(ElementData {
            id: self.req.elements.len() as u64,
            block_name: String::new(),
            ifc_class,
            material,
            bucket: Bucket::WallPolygon { outer: outer_pts, holes: holes_pts, is_section },
        });
        Ok(())
    }

    // ── Bucket C / D — 3D body fallback ────────────────────────────────────

    /// Add a Bucket-C element (3D Body, wireframe until OCC HLR).
    ///
    /// Geometry is world-space (world_matrix applied during BRep parsing).
    /// INSERT is placed at (0, 0) in drawing space.
    fn add_body_brep(
        &mut self,
        block_name: String,
        ifc_class: String,
        material: String,
        brep_text: String,
        world_matrix: [f64; 16],
    ) {
        self.req.elements.push(ElementData {
            id: self.req.elements.len() as u64,
            block_name,
            ifc_class,
            material,
            bucket: Bucket::BodyBrep { brep_text, world_matrix },
        });
    }

    // ── Static helpers ──────────────────────────────────────────────────────

    /// Check if an extrusion direction qualifies for Bucket A.
    #[staticmethod]
    fn extrusion_ok(extrusion_dir: [f64; 3], camera_dir: [f64; 3]) -> bool {
        extrusion_parallel_to_view(extrusion_dir, camera_dir)
    }
}

// ---------------------------------------------------------------------------
// Main generate function
// ---------------------------------------------------------------------------

/// Generate DXF/DWG from the drawing request.
#[pyfunction]
pub fn py_generate(
    req: &PyDrawingRequest,
    _annotations: Option<Vec<Py<PyAny>>>,
) -> PyResult<()> {
    let (element_blocks, element_hatches, wall_hatches, flat_walls) = generate_linework(&req.req);

    write_document(
        &element_blocks,
        &element_hatches,
        &wall_hatches,
        &flat_walls,
        &req.req.output_path,
        &req.req.dxf_version,
        &req.req.layer_styles,
    )
    .map_err(|e| PyValueError::new_err(e.to_string()))?;

    Ok(())
}
