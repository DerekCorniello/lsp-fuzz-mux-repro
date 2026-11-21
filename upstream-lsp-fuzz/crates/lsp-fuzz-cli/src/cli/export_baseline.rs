use std::{
    fs,
    path::{Path, PathBuf},
    sync::OnceLock,
};

use anyhow::Context;
use libafl::{
    generators::NautilusContext,
    inputs::{Input, InputToBytes, NautilusBytesConverter},
};
use lsp_fuzz::{
    baseline::{
        BaselineByteConverter,
        two_dim::{TwoDimBaselineInput, TwoDimInputConverter},
    },
    execution::workspace_observer::HasWorkspace,
};
use lsp_fuzz_grammars::Language;
use tracing::info;

use super::GlobalOptions;

/// Converts two-dimensional baseline inputs into replayable LSP transcripts.
#[derive(Debug, clap::Parser)]
pub struct ExportBaseline {
    /// Directory containing the two-dimensional baseline inputs.
    #[clap(long, short)]
    solution_dir: PathBuf,

    /// Directory to write `<input>/workspace/` and `<input>/transcript.lsp`.
    #[clap(long, short)]
    output_dir: PathBuf,

    #[clap(long)]
    language: Language,

    #[clap(long)]
    input_prefix: Option<String>,
}

impl ExportBaseline {
    pub fn run(self, _global_options: GlobalOptions) -> anyhow::Result<()> {
        info!(?self);
        let input_files = fs::read_dir(&self.solution_dir)
            .with_context(|| format!("Reading {}", self.solution_dir.display()))?
            .map(Result::unwrap)
            .filter(|entry| {
                entry.metadata().is_ok_and(|metadata| metadata.is_file())
                    && self.input_prefix.as_ref().is_none_or(|prefix| {
                        entry.file_name().to_string_lossy().starts_with(prefix)
                    })
            })
            .map(|entry| entry.path());

        static NAUTILUS_CONTEXT: OnceLock<NautilusContext> = OnceLock::new();
        let nautilus_context = NAUTILUS_CONTEXT.get_or_init(|| {
            let mut nautilus_ctx = NautilusContext {
                ctx: lsp_fuzz::lsp::metamodel::get_nautilus_context(),
            };
            nautilus_ctx.ctx.initialize(65535);
            nautilus_ctx
        });

        for input_path in input_files {
            let input_id = input_path
                .file_name()
                .and_then(|name| name.to_str())
                .with_context(|| format!("Invalid input file name: {}", input_path.display()))?
                .to_owned();
            let input = TwoDimBaselineInput::from_file(&input_path)
                .with_context(|| format!("Loading {}", input_path.display()))?;
            let case_dir = self.output_dir.join(&input_id);
            if case_dir.exists() {
                fs::remove_dir_all(&case_dir)
                    .with_context(|| format!("Removing {}", case_dir.display()))?;
            }
            let workspace_dir = case_dir.join("workspace");
            fs::create_dir_all(&workspace_dir)?;
            input
                .setup_workspace(&workspace_dir)
                .with_context(|| format!("Preparing workspace for {input_id}"))?;

            let mut converter = TwoDimInputConverter::new(
                workspace_dir.clone(),
                self.language.lsp_language_id().to_owned(),
                BaselineByteConverter::new(NautilusBytesConverter::new(nautilus_context)),
            );
            let transcript = converter.to_bytes(&input).to_vec();
            write_transcript(&case_dir, &transcript)?;
            info!("Exported {}", case_dir.display());
        }

        Ok(())
    }
}

fn write_transcript(case_dir: &Path, transcript: &[u8]) -> anyhow::Result<()> {
    let path = case_dir.join("transcript.lsp");
    fs::write(&path, transcript).with_context(|| format!("Writing {}", path.display()))?;
    Ok(())
}
