fn main() {
    let src_dir = std::path::Path::new("src");
    let mut cc = cc::Build::new();
    cc.include(src_dir);
    cc.flag_if_supported("-Wno-unused-parameter");
    cc.file(src_dir.join("parser.c"));
    cc.compile("tree-sitter-mux");
    println!("cargo:rerun-if-changed=src/parser.c");
}
