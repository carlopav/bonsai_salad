// ifc_dxf — IFC to DXF/DWG serializer
// Bonsai Salad tool
//
// Architecture:
//   pipeline/   — bucket classification + main orchestration
//   geometry/   — 2D edge types, projection, cleanup, hatch union
//   io/         — DXF/DWG writer via acadrust
//   annotations/— MTEXT, DIMENSION, MULTILEADER from IfcAnnotation

pub mod annotations;
pub mod geometry;
pub mod io;
pub mod pipeline;

pub use geometry::{DxfEdge, EdgeKind, CameraProjection};
pub use pipeline::{DrawingRequest, generate_linework};
pub use io::writer::write_document;

// ---------------------------------------------------------------------------
// Python module (PyO3)
// ---------------------------------------------------------------------------

use pyo3::prelude::*;

#[pymodule]
fn ifc_dxf(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<python::PyDrawingRequest>()?;
    m.add_class::<python::PyCameraProjection>()?;
    m.add_function(wrap_pyfunction!(python::py_generate, m)?)?;
    Ok(())
}

mod python;
