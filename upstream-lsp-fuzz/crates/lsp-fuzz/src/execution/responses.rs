use std::{
    borrow::Cow,
    io::{self, BufRead, BufReader},
};

use libafl::observers::Observer;
use libafl_bolts::Named;
use serde::{Deserialize, Serialize};

use crate::lsp::json_rpc::JsonRPCMessage;

const MAX_CAPTURED_OUTPUT_BYTES: u64 = 1024 * 1024;

#[derive(Debug, Default, Serialize, Deserialize)]
pub struct LspOutputObserver {
    captured_messages: Vec<JsonRPCMessage>,
}

impl Named for LspOutputObserver {
    fn name(&self) -> &Cow<'static, str> {
        static NAME: Cow<'static, str> = Cow::Borrowed("ResponsesObserver");
        &NAME
    }
}

impl LspOutputObserver {
    pub fn new() -> Self {
        Self {
            captured_messages: Vec::new(),
        }
    }

    pub fn captured_messages(&self) -> &[JsonRPCMessage] {
        &self.captured_messages
    }

    pub fn capture_stdout_content<R: BufRead>(&mut self, reader: R) -> io::Result<()> {
        let mut limited_reader = BufReader::new(reader.take(MAX_CAPTURED_OUTPUT_BYTES));
        while let Ok(message) = JsonRPCMessage::read_lsp_payload(&mut limited_reader) {
            self.captured_messages.push(message);
        }
        if limited_reader.get_ref().limit() == 0 {
            tracing::warn!(
                max_bytes = MAX_CAPTURED_OUTPUT_BYTES,
                "Truncated target output capture"
            );
        }
        Ok(())
    }
}

impl<I, State> Observer<I, State> for LspOutputObserver {
    fn pre_exec(&mut self, _state: &mut State, _input: &I) -> Result<(), libafl::Error> {
        self.captured_messages.clear();
        Ok(())
    }
}
