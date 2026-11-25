use std::{env, fs};

use lsp_fuzz::text_document::Metadata;
use lsp_fuzz_grammars::Language;
use tree_sitter::{InputEdit, Point};

fn main() {
    let args: Vec<String> = env::args().collect();
    let mode = &args[1];
    let previous = fs::read(&args[2]).unwrap();
    let incremental = fs::read(&args[3]).unwrap();

    if mode == "fresh" {
        let metadata = Metadata::generate(Language::Mux, &incremental);
        println!(
            "fresh nodes={}",
            metadata.parse_tree.root_node().child_count()
        );
        return;
    }

    let edit = InputEdit {
        start_byte: 11,
        old_end_byte: 133,
        new_end_byte: 12,
        start_position: Point { row: 11, column: 0 },
        old_end_position: Point { row: 21, column: 0 },
        new_end_position: Point { row: 11, column: 1 },
    };
    let mut tree = Language::Mux
        .tree_sitter_parser()
        .parse(&previous, None)
        .expect("fresh parse failed");
    tree.edit(&edit);
    let edited = Language::Mux
        .tree_sitter_parser()
        .parse(&incremental, Some(&tree))
        .expect("incremental parse failed");
    println!(
        "fresh nodes={} incremental nodes={}",
        tree.root_node().child_count(),
        edited.root_node().child_count()
    );
}
