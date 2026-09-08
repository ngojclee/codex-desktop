#!/usr/bin/env python3
"""Patch V - safely persist standalone app-server tool output.

`turn/start.toolOutput` is an app-server-only, unpaired output. Upstream used
to persist it as a Responses `function_call_output` with no call_id, which is
invalid for strict Responses-compatible providers when that history is replayed.

The patch deliberately has two paths:
* New standalone output remains a UI FunctionCallOutput, but is persisted as an
  assistant-commentary context item with explicit tool provenance and payload.
* Historical unpaired output is rejected locally at the provider boundary. It
  is not paired by proximity, assigned an id, removed, or silently rewritten.
"""

from __future__ import annotations

import argparse
from pathlib import Path


MARKER = "/*V:standalone-tool-output*/"
HOOK_RUNTIME = Path("codex-rs/core/src/hook_runtime.rs")
CLIENT = Path("codex-rs/core/src/client.rs")
APP_SERVER_TEST = Path("codex-rs/app-server/tests/suite/v2/thread_inject_items.rs")
CLIENT_TEST = Path("codex-rs/core/src/client_tests.rs")


def read(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f"Patch V drift: required source file is missing: {path}")
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"Patch V drift: expected exactly one {label} anchor, found {count}"
        )
    return text.replace(old, new, 1)


def patch_hook_runtime(path: Path) -> None:
    text = read(path)
    if MARKER in text:
        if "standalone_tool_output_context_item" not in text:
            raise SystemExit("Patch V verification failed: hook helper marker is incomplete")
        return

    text = replace_once(
        text,
        "use codex_protocol::models::ResponseItem;\n",
        "use codex_protocol::models::ContentItem;\n"
        "use codex_protocol::models::MessagePhase;\n"
        "use codex_protocol::models::ResponseItem;\n",
        "hook-runtime model imports",
    )

    old = """        TurnInput::FunctionCallOutput(item) => {
            sess.record_conversation_items(turn_context, std::slice::from_ref(&item))
                .await;
            if let ResponseItem::FunctionCallOutput {
"""
    new = """        TurnInput::FunctionCallOutput(item) => {
            // `turn/start.toolOutput` has no upstream call_id by protocol design.
            // Persist it as non-privileged assistant context, not as a provider
            // function_call_output. Keep the original item only for the UI event.
            let model_item = standalone_tool_output_context_item(&item);
            sess.record_conversation_items(turn_context, std::slice::from_ref(&model_item))
                .await;
            if let ResponseItem::FunctionCallOutput {
"""
    text = replace_once(text, old, new, "standalone output history write")

    anchor = """pub(crate) async fn record_pending_input(
"""
    helper = f"""// {MARKER}
// Standalone app-server tool output is informational context, not a response to
// a model-issued function call. The payload remains explicit and model-visible
// as an assistant commentary item; it never gains user/developer authority.
fn standalone_tool_output_context_item(item: &ResponseItem) -> ResponseItem {{
    let ResponseItem::FunctionCallOutput {{
        name: Some(name),
        namespace,
        output,
        ..
    }} = item
    else {{
        return item.clone();
    }};

    let source = namespace.as_deref().unwrap_or("<none>");
    ResponseItem::Message {{
        id: None,
        role: "assistant".to_string(),
        content: vec![ContentItem::OutputText {{
            text: format!(
                "[standalone tool output]\\nsource: {{source}}\\nname: {{name}}\\npayload:\\n{{output}}"
            ),
        }}],
        phase: Some(MessagePhase::Commentary),
        internal_chat_message_metadata_passthrough: None,
    }}
}}

"""
    text = replace_once(text, anchor, helper + anchor, "hook-runtime helper insertion")
    write(path, text)


def patch_client(path: Path) -> None:
    text = read(path)
    if MARKER in text:
        if "reject_legacy_unpaired_function_call_outputs" not in text:
            raise SystemExit("Patch V verification failed: client guard marker is incomplete")
        return

    anchor = """    fn build_responses_request(
"""
    helper = f"""    // {MARKER}
    // Older Desktop histories can contain an app-server standalone output in
    // the provider-shaped FunctionCallOutput variant. It has no trustworthy
    // call_id, so stop before serializing a malformed provider request. Do not
    // infer a pair, manufacture an id, mutate stored history, or discard data.
    fn reject_legacy_unpaired_function_call_outputs(input: &[ResponseItem]) -> Result<()> {{
        let invalid = input.iter().any(|item| {{
            matches!(
                item,
                ResponseItem::FunctionCallOutput {{ call_id, .. }}
                    if !call_id
                        .as_deref()
                        .is_some_and(|call_id| !call_id.trim().is_empty())
            )
        }});
        if invalid {{
            return Err(CodexErr::InvalidRequest(
                "Cannot resume this task because its stored standalone tool output has no call_id. "
                    .to_string()
                    + "No provider request was sent and history was left unchanged; recover from a clean task.",
            ));
        }}
        Ok(())
    }}

"""
    text = replace_once(text, anchor, helper + anchor, "client guard insertion")
    old = """        let mut input = prompt.get_formatted_input_for_request(model_info.use_responses_lite);
        let is_openai = self.state.provider.info().is_openai();
"""
    new = """        let mut input = prompt.get_formatted_input_for_request(model_info.use_responses_lite);
        Self::reject_legacy_unpaired_function_call_outputs(&input)?;
        let is_openai = self.state.provider.info().is_openai();
"""
    text = replace_once(text, old, new, "client guard call")
    write(path, text)


def patch_integration_test(path: Path) -> None:
    text = read(path)
    if MARKER in text:
        if "standalone tool output must serialize as assistant context" not in text:
            raise SystemExit("Patch V verification failed: integration test marker is incomplete")
        return

    # This test already covers injected assistant/developer items. An injected
    # legacy unpaired tool output belongs to the separate core guard test below;
    # retaining it here would intentionally block this test's ordinary request.
    text = replace_once(
        text,
        """    let named_tool_output = json!({
        "type": "function_call_output",
        "name": "send_message_to_thread",
        "namespace": "codex_app",
        "output": "Another agent delegated this task.",
    });
    let named_tool_item: ResponseItem = serde_json::from_value(named_tool_output.clone())?;

""",
        "",
        "legacy injected-output setup removal",
    )
    text = replace_once(
        text,
        """                serde_json::to_value(&marker_shaped_developer_item)?,
                named_tool_output.clone(),
""",
        """                serde_json::to_value(&marker_shaped_developer_item)?,
""",
        "legacy injected-output request removal",
    )
    text = replace_once(
        text,
        """                || item == &marker_shaped_developer_item
                || item == &named_tool_item
""",
        """                || item == &marker_shaped_developer_item
""",
        "legacy injected-output history filter removal",
    )
    text = replace_once(
        text,
        """            (marker_shaped_developer_item.clone(), Some(true)),
            (named_tool_item, None),
""",
        """            (marker_shaped_developer_item.clone(), Some(true)),
""",
        "legacy injected-output expected-history removal",
    )
    text = replace_once(
        text,
        """    assert!(
        model_input.contains(&named_tool_output),
        "named unpaired tool output should be sent in the next model request"
    );

""",
        "",
        "legacy injected-output provider assertion removal",
    )
    text = replace_once(
        text,
        """    assert_eq!(
        state_db
            .get_thread_memory_mode(ThreadId::from_string(&thread.id)?)
            .await?
            .as_deref(),
        Some("polluted")
    );
""",
        """    assert_eq!(
        state_db
            .get_thread_memory_mode(ThreadId::from_string(&thread.id)?)
            .await?
            .as_deref(),
        Some("enabled")
    );
""",
        "standalone output memory-mode assertion",
    )

    old = """    assert!(
        delegated_mock
            .single_request()
            .input()
            .into_iter()
            .any(|item| item["type"] == "function_call_output"
                && item["output"] == "Start a delegated turn."),
        "the delegated turn must preserve the original model-visible tool output"
    );
"""
    new = f"""    let delegated_input = delegated_mock.single_request().input();
    assert!(
        delegated_input.iter().any(|item| {{
            item["type"] == "message"
                && item["role"] == "assistant"
                && item["content"][0]["type"] == "output_text"
                && item["content"][0]["text"].as_str().is_some_and(|text| {{
                    text.contains("source: codex_app")
                        && text.contains("name: send_message_to_thread")
                        && text.contains("Start a delegated turn.")
                }})
        }}),
        "standalone tool output must serialize as assistant context with provenance"
    );
    assert!(
        delegated_input.iter().all(|item| {{
            item["type"] != "function_call_output"
                || item["call_id"]
                    .as_str()
                    .is_some_and(|call_id| !call_id.trim().is_empty())
        }}),
        "strict provider-shaped input must never contain an unpaired function_call_output"
    );
    // {MARKER}
"""
    text = replace_once(text, old, new, "standalone app-server integration assertion")
    write(path, text)


def patch_client_test(path: Path) -> None:
    text = read(path)
    if MARKER in text:
        if "rejects_legacy_unpaired_function_call_output_before_provider_request" not in text:
            raise SystemExit("Patch V verification failed: client recovery test marker is incomplete")
        return

    test = f"""

#[test]
fn rejects_legacy_unpaired_function_call_output_before_provider_request() {{
    let legacy: ResponseItem = serde_json::from_value(serde_json::json!({{
        "type": "function_call_output",
        "name": "send_message_to_thread",
        "namespace": "codex_app",
        "output": "legacy output",
    }}))
    .expect("legacy standalone output should deserialize");
    let paired: ResponseItem = serde_json::from_value(serde_json::json!({{
        "type": "function_call_output",
        "call_id": "call-paired",
        "output": "paired output",
    }}))
    .expect("paired output should deserialize");

    let error = ModelClient::reject_legacy_unpaired_function_call_outputs(&[legacy])
        .expect_err("legacy unpaired output must stop before a provider request");
    assert!(
        error
            .to_string()
            .contains("No provider request was sent and history was left unchanged"),
        "controlled recovery text must explain that history was not mutated: {{error}}"
    );
    ModelClient::reject_legacy_unpaired_function_call_outputs(&[paired])
        .expect("paired output must remain provider-compatible");
}}
// {MARKER}
"""
    text = text.rstrip() + test.rstrip() + "\n"
    write(path, text)


def verify(root: Path) -> None:
    for relative in (HOOK_RUNTIME, CLIENT, APP_SERVER_TEST, CLIENT_TEST):
        text = read(root / relative)
        if MARKER not in text:
            raise SystemExit(f"Patch V verification failed: marker missing from {relative}")
    hook = read(root / HOOK_RUNTIME)
    client = read(root / CLIENT)
    test = read(root / APP_SERVER_TEST)
    required = (
        "standalone_tool_output_context_item",
        'role: "assistant".to_string()',
        "source: {source}",
    )
    if not all(value in hook for value in required):
        raise SystemExit("Patch V verification failed: standalone output context helper is incomplete")
    if "reject_legacy_unpaired_function_call_outputs(&input)?" not in client:
        raise SystemExit("Patch V verification failed: provider-boundary guard call is missing")
    if "No provider request was sent and history was left unchanged" not in client:
        raise SystemExit("Patch V verification failed: controlled legacy recovery message is missing")
    if "strict provider-shaped input must never contain" not in test:
        raise SystemExit("Patch V verification failed: strict integration assertion is missing")
    client_test = read(root / CLIENT_TEST)
    if "rejects_legacy_unpaired_function_call_output_before_provider_request" not in client_test:
        raise SystemExit("Patch V verification failed: controlled legacy recovery test is missing")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    root = args.source_dir.resolve()

    if not args.verify_only:
        patch_hook_runtime(root / HOOK_RUNTIME)
        patch_client(root / CLIENT)
        patch_integration_test(root / APP_SERVER_TEST)
        patch_client_test(root / CLIENT_TEST)
    verify(root)
    print("Patch V verified: standalone app-server tool output is provider-safe.")


if __name__ == "__main__":
    main()
