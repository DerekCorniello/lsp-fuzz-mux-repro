use std::{borrow::Cow, collections::HashSet, hash::Hash};

use derive_more::Debug;
use libafl::{
    HasMetadata,
    corpus::{Corpus, Testcase},
    events::EventFirer,
    executors::ExitKind,
    feedbacks::{Feedback, StateInitializer},
    state::{HasCorpus, HasExecutions},
};
use libafl_bolts::{
    Named,
    tuples::{Handle, Handled, MatchNameRef},
};
use lsp_types::{
    CompletionResponse, DocumentSymbolResponse, OneOf, WorkspaceSymbolResponse,
    notification::PublishDiagnostics,
};
use metadata::{Diagnostic, LspResponseInfo, ParamFragments, SymbolRange};
use tracing::warn;

use super::LspInput;
use crate::{
    execution::responses::LspOutputObserver,
    lsp::{LspMessage, message::LspResponse},
    utils::AflContext,
};

pub mod metadata;

pub mod matching;

const MAX_DIAGNOSTICS: usize = 256;
const MAX_SYMBOL_RANGES: usize = 256;
const MAX_PARAM_FRAGMENTS_PER_KIND: usize = 32;

fn insert_bounded<T>(set: &mut HashSet<T>, value: T, limit: usize)
where
    T: Eq + Hash,
{
    if set.len() < limit {
        set.insert(value);
    }
}

fn extend_bounded<T, I>(set: &mut HashSet<T>, values: I, limit: usize)
where
    T: Eq + Hash,
    I: IntoIterator<Item = T>,
{
    for value in values {
        if set.len() >= limit {
            break;
        }
        set.insert(value);
    }
}

#[derive(Debug)]
pub struct LspResponseFeedback {
    observer_handle: Handle<LspOutputObserver>,
}

impl LspResponseFeedback {
    pub fn new(observer: &LspOutputObserver) -> Self {
        Self {
            observer_handle: observer.handle(),
        }
    }
}

impl Named for LspResponseFeedback {
    fn name(&self) -> &Cow<'static, str> {
        static NAME: Cow<'static, str> = Cow::Borrowed("ResponseNoveltyFeedback");
        &NAME
    }
}

impl<State> StateInitializer<State> for LspResponseFeedback where State: HasMetadata {}

impl<EM, OT, State> Feedback<EM, LspInput, OT, State> for LspResponseFeedback
where
    State: HasMetadata + HasExecutions + HasCorpus<LspInput>,
    OT: MatchNameRef,
    EM: EventFirer<LspInput, State>,
{
    fn is_interesting(
        &mut self,
        _state: &mut State,
        _manager: &mut EM,
        _input: &LspInput,
        _observers: &OT,
        _exit_kind: &ExitKind,
    ) -> Result<bool, libafl::Error> {
        Ok(false)
    }

    fn append_metadata(
        &mut self,
        state: &mut State,
        _manager: &mut EM,
        observers: &OT,
        testcase: &mut Testcase<LspInput>,
    ) -> Result<(), libafl::Error> {
        state
            .corpus()
            .load_input_into(testcase)
            .afl_context("Loading input to the test case")?;
        let input = testcase
            .input()
            .as_ref()
            .expect("We loaded the input just now.");

        let response_observer = observers
            .get(&self.observer_handle)
            .afl_context("LspResponseObserver not attached")?;
        let received_messages = response_observer.captured_messages();
        let Ok(matching) = matching::RequestResponseMatching::match_messages(
            input.messages.iter(),
            received_messages.iter(),
        ) else {
            warn!("Failed to match messages");
            return Ok(());
        };

        let mut diagnostics = HashSet::new();

        for pub_diag in matching.find_notifications::<PublishDiagnostics>() {
            if diagnostics.len() >= MAX_DIAGNOSTICS {
                break;
            }
            let uri = LspInput::lift_uri(&pub_diag.uri);
            for diag_item in pub_diag.diagnostics.iter() {
                if diagnostics.len() >= MAX_DIAGNOSTICS {
                    break;
                }
                insert_bounded(
                    &mut diagnostics,
                    Diagnostic {
                        uri: uri.as_ref().clone(),
                        range: diag_item.range,
                    },
                    MAX_DIAGNOSTICS,
                );
            }
        }

        let mut param_fragments = ParamFragments::default();
        let mut symbol_ranges = HashSet::new();

        for (req, res) in matching.responses {
            use LspResponse::*;
            match res {
                CodeActionRequest(Some(cas)) => {
                    for ca in cas {
                        match ca {
                            lsp_types::CodeActionOrCommand::Command(command) => {
                                insert_bounded(
                                    &mut param_fragments.commands,
                                    command,
                                    MAX_PARAM_FRAGMENTS_PER_KIND,
                                );
                            }
                            lsp_types::CodeActionOrCommand::CodeAction(code_action) => {
                                insert_bounded(
                                    &mut param_fragments.code_actions,
                                    code_action,
                                    MAX_PARAM_FRAGMENTS_PER_KIND,
                                );
                            }
                        }
                    }
                }
                InlayHintRequest(Some(inlay_hints)) => {
                    extend_bounded(
                        &mut param_fragments.inlay_hints,
                        inlay_hints,
                        MAX_PARAM_FRAGMENTS_PER_KIND,
                    );
                }
                Completion(Some(res)) => {
                    let items = match res {
                        CompletionResponse::Array(items) => items,
                        CompletionResponse::List(list) => list.items,
                    };
                    extend_bounded(
                        &mut param_fragments.completion_items,
                        items,
                        MAX_PARAM_FRAGMENTS_PER_KIND,
                    );
                }
                CodeLensRequest(Some(code_lens)) => {
                    extend_bounded(
                        &mut param_fragments.code_lens,
                        code_lens,
                        MAX_PARAM_FRAGMENTS_PER_KIND,
                    );
                }
                WorkspaceSymbolRequest(Some(WorkspaceSymbolResponse::Nested(symbols))) => {
                    for sym in symbols {
                        let range = match &sym.location {
                            OneOf::Left(it) => Some((it.uri.clone(), it.range)),
                            OneOf::Right(_) => None,
                        };
                        insert_bounded(
                            &mut param_fragments.workspace_symbols,
                            sym,
                            MAX_PARAM_FRAGMENTS_PER_KIND,
                        );
                        if let Some((uri, range)) = range {
                            insert_bounded(
                                &mut symbol_ranges,
                                SymbolRange::new(uri, range),
                                MAX_SYMBOL_RANGES,
                            );
                        }
                        if param_fragments.workspace_symbols.len() >= MAX_PARAM_FRAGMENTS_PER_KIND
                            && symbol_ranges.len() >= MAX_SYMBOL_RANGES
                        {
                            break;
                        }
                    }
                }
                WorkspaceSymbolRequest(Some(WorkspaceSymbolResponse::Flat(symbols)))
                | DocumentSymbolRequest(Some(DocumentSymbolResponse::Flat(symbols))) => {
                    for sym in symbols {
                        if symbol_ranges.len() >= MAX_SYMBOL_RANGES {
                            break;
                        }
                        insert_bounded(
                            &mut symbol_ranges,
                            SymbolRange::new(sym.location.uri.clone(), sym.location.range),
                            MAX_SYMBOL_RANGES,
                        );
                    }
                }
                DocumentSymbolRequest(Some(DocumentSymbolResponse::Nested(symbols))) => {
                    if let LspMessage::DocumentSymbolRequest(req) = req {
                        let mut stack = symbols;
                        while let Some(mut symbol) = stack.pop() {
                            if symbol_ranges.len() >= MAX_SYMBOL_RANGES {
                                break;
                            }
                            if let Some(children) = symbol.children.take() {
                                stack.extend(children);
                            }
                            insert_bounded(
                                &mut symbol_ranges,
                                SymbolRange::new(
                                    req.text_document.uri.clone(),
                                    symbol.selection_range,
                                ),
                                MAX_SYMBOL_RANGES,
                            );
                        }
                    }
                }
                TypeHierarchyPrepare(Some(items)) => {
                    extend_bounded(
                        &mut param_fragments.type_hierarchy_items,
                        items,
                        MAX_PARAM_FRAGMENTS_PER_KIND,
                    );
                }
                CallHierarchyPrepare(Some(items)) => {
                    extend_bounded(
                        &mut param_fragments.call_hierarchy_items,
                        items,
                        MAX_PARAM_FRAGMENTS_PER_KIND,
                    );
                }
                DocumentLinkRequest(Some(links)) => {
                    extend_bounded(
                        &mut param_fragments.document_links,
                        links,
                        MAX_PARAM_FRAGMENTS_PER_KIND,
                    );
                }
                _ => {}
            }
        }

        let response_info = LspResponseInfo {
            diagnostics,
            param_fragments,
            symbol_ranges,
        };
        testcase.add_metadata(response_info);
        Ok(())
    }
}
