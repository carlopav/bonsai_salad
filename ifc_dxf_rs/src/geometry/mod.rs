pub mod cleanup;
pub mod hatch;
pub mod project;

use glam::{DVec2, DVec3, DMat4};

// ---------------------------------------------------------------------------
// Camera projection
// ---------------------------------------------------------------------------

/// Orthographic camera projection: world 3D → drawing 2D (metres, 1:1).
///
/// Coordinates in the output DXF are real-world metres.  The camera centre
/// maps to (0, 0) in drawing space.  Y increases upward (standard DXF/math
/// convention), matching plan-north = positive Y.
#[derive(Debug, Clone)]
pub struct CameraProjection {
    /// Inverted camera world matrix (4×4, column-major).
    /// Transforms world-space points into camera-local space.
    pub matrix_inv: DMat4,
}

impl CameraProjection {
    pub fn new(matrix_inv: DMat4) -> Self {
        Self { matrix_inv }
    }

    /// Project a world-space point to drawing-space metres.
    /// Applies the full camera transform (rotation + translation).
    /// Use this for world-space points (e.g. element origins from ObjectPlacement).
    #[inline]
    pub fn project(&self, p: DVec3) -> DVec2 {
        let cam = self.matrix_inv.transform_point3(p);
        DVec2::new(cam.x, cam.y)
    }

    /// Project a local-space vector to block-local 2D.
    /// Applies only the camera rotation (no translation).
    /// Use this for block geometry (local coords relative to element origin).
    #[inline]
    pub fn project_local(&self, v: DVec3) -> DVec2 {
        let cam = self.matrix_inv.transform_vector3(v);
        DVec2::new(cam.x, cam.y)
    }

    /// Scale a length: 1:1 metres → metres (identity).
    #[inline]
    pub fn scale_len(&self, len: f64) -> f64 {
        len
    }
}

// ---------------------------------------------------------------------------
// 2D edge representation
// ---------------------------------------------------------------------------

/// A resolved 2-D entity ready to write to DXF/DWG.
#[derive(Debug, Clone)]
pub struct DxfEdge {
    pub kind: EdgeKind,
    pub layer: String,       // used by hatch grouping; overridden to "0" inside blocks
    pub is_cut: bool,
    pub ifc_class: String,   // e.g. "IfcWall"
    pub material: String,    // for hatch grouping
}

#[derive(Debug, Clone)]
pub enum EdgeKind {
    Line {
        start: DVec2,
        end: DVec2,
    },
    Arc {
        center: DVec2,
        radius: f64,       // metres
        start_angle: f64,  // degrees CCW from X
        end_angle: f64,
    },
    Circle {
        center: DVec2,
        radius: f64,       // metres
    },
    Ellipse {
        center: DVec2,
        major_axis: DVec2, // vector, not angle
        ratio: f64,        // minor/major
        start_param: f64,
        end_param: f64,
    },
    Polyline {
        points: Vec<DVec2>,
        closed: bool,
    },
    /// Spline approximated as polyline (BSpline curves)
    Spline {
        points: Vec<DVec2>,
    },
}

impl DxfEdge {
    pub fn line(start: DVec2, end: DVec2, layer: &str,
                ifc_class: &str, material: &str, is_cut: bool) -> Self {
        Self {
            kind: EdgeKind::Line { start, end },
            layer: layer.to_string(),
            is_cut,
            ifc_class: ifc_class.to_string(),
            material: material.to_string(),
        }
    }

    pub fn circle(center: DVec2, radius: f64, layer: &str,
                  ifc_class: &str, material: &str, is_cut: bool) -> Self {
        Self {
            kind: EdgeKind::Circle { center, radius },
            layer: layer.to_string(),
            is_cut,
            ifc_class: ifc_class.to_string(),
            material: material.to_string(),
        }
    }

    pub fn arc(center: DVec2, radius: f64,
               start_angle: f64, end_angle: f64,
               layer: &str, ifc_class: &str,
               material: &str, is_cut: bool) -> Self {
        Self {
            kind: EdgeKind::Arc { center, radius, start_angle, end_angle },
            layer: layer.to_string(),
            is_cut,
            ifc_class: ifc_class.to_string(),
            material: material.to_string(),
        }
    }

    pub fn polyline(points: Vec<DVec2>, closed: bool,
                    layer: &str, ifc_class: &str,
                    material: &str, is_cut: bool) -> Self {
        Self {
            kind: EdgeKind::Polyline { points, closed },
            layer: layer.to_string(),
            is_cut,
            ifc_class: ifc_class.to_string(),
            material: material.to_string(),
        }
    }
}

/// Layer name convention used by hatch grouping and cleanup.
pub fn layer_name(ifc_class: &str, is_cut: bool) -> String {
    let suffix = if is_cut { "CUT" } else { "PROJ" };
    format!("BBIM_{}_{}", ifc_class.to_uppercase(), suffix)
}

pub fn hatch_layer_name(material: &str) -> String {
    let clean: String = material
        .chars()
        .map(|c| if c.is_alphanumeric() { c.to_ascii_uppercase() } else { '_' })
        .take(24)
        .collect();
    format!("BBIM_HATCH_{}", clean)
}
