/// Mirrors operator.py get_linework_contexts() logic.
/// Called from Python — context IDs are resolved there.
/// This module only defines the types used to communicate
/// the context priority list to Python.

#[derive(Debug, Clone)]
pub struct LineworkContexts {
    /// Body context IDs in priority order (each group is a Vec<i64>)
    pub body: Vec<Vec<i64>>,
    /// Annotation context IDs in priority order
    pub annotation: Vec<Vec<i64>>,
}

/// Which IfcGeometricRepresentationSubContext types are considered 2D native.
pub fn is_2d_context(context_type: &str, context_identifier: &str) -> bool {
    context_type == "Plan"
        || matches!(context_identifier, "Annotation" | "FootPrint" | "Axis")
}
