use tree_sitter_language::LanguageFn;

extern "C" {
    fn tree_sitter_mux() -> *const ();
}

/// The tree-sitter language function for Mux.
pub const LANGUAGE: LanguageFn = unsafe { LanguageFn::from_raw(tree_sitter_mux) };

/// Syntax highlighting query for Mux.
pub const HIGHLIGHT_QUERY: &str = include_str!("../queries/highlights.scm");
