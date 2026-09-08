#!/usr/bin/env python3
"""Patch X - make inherited standalone tool outputs resumable again.

Patch V stopped the producer and refused to serialize any stored standalone tool
output that has no call_id. Measurement showed the remaining harm is
inheritance: `thread/fork` (the Desktop duplicate action) copies stored history
verbatim, so a "new" lane born from a poisoned parent hits the refusal on its
first turn. Because that refusal lives inside `build_responses_request`, which
`compact_conversation_history` also calls, such a thread cannot even compact its
way out.

X leaves V's fail-closed refusal in place and adds a request-view conversion in
front of it: a stored output with no usable call_id is rendered as the same
non-privileged assistant commentary item that V already builds for new outputs,
so payload and provenance stay model-visible without gaining user or developer
authority. It never invents an id, never pairs by proximity, never deletes an
output, and never writes to stored history, transcripts, or the database. What
cannot be represented without dropping content is left for V's refusal.
"""

from __future__ import annotations

import argparse
from pathlib import Path


MARKER = "/*X:legacy-unpaired-output-recovery*/"
HOOK_RUNTIME = Path("codex-rs/core/src/hook_runtime.rs")
CLIENT = Path("codex-rs/core/src/client.rs")
CLIENT_TEST = Path("codex-rs/core/src/client_tests.rs")

X_TESTS = r"""
// /*X:legacy-unpaired-output-recovery*/
// A history shaped like a real forked lane: one properly paired call and output,
// plus one stored standalone output that arrived without a call_id.
fn inherited_unpaired_history() -> Vec<ResponseItem> {
    let paired_call: ResponseItem = serde_json::from_value(serde_json::json!({
        "type": "function_call",
        "call_id": "call-paired",
        "name": "shell",
        "arguments": "{}",
    }))
    .expect("paired call");
    let paired_output: ResponseItem = serde_json::from_value(serde_json::json!({
        "type": "function_call_output",
        "call_id": "call-paired",
        "output": "paired output",
    }))
    .expect("paired output");
    let inherited: ResponseItem = serde_json::from_value(serde_json::json!({
        "type": "function_call_output",
        "name": "send_message_to_thread",
        "namespace": "codex_app",
        "output": "inherited brief from the parent task",
    }))
    .expect("inherited standalone output");
    vec![paired_call, paired_output, inherited]
}

fn commentary_payload_text(item: &ResponseItem) -> Option<String> {
    match item {
        ResponseItem::Message {
            role,
            content,
            phase: Some(codex_protocol::models::MessagePhase::Commentary),
            ..
        } if role == "assistant" => content.iter().find_map(|item| match item {
            ContentItem::OutputText { text } => Some(text.clone()),
            _ => None,
        }),
        _ => None,
    }
}

#[test]
fn inherited_unpaired_output_is_sanitized_without_inventing_identity() {
    let original = inherited_unpaired_history();
    let mut input = original.clone();
    ModelClient::sanitize_legacy_standalone_tool_outputs(&mut input).expect("sanitize");

    assert_eq!(input.len(), 3, "sanitizing must not add or drop items");
    assert_eq!(input[0], original[0], "a paired call must survive untouched");
    assert_eq!(
        input[1], original[1],
        "a paired output must keep its own call_id"
    );

    let text = commentary_payload_text(&input[2])
        .expect("the inherited output must become an assistant commentary item");
    assert!(text.contains("[standalone tool output]"), "{text}");
    assert!(text.contains("source: codex_app"), "{text}");
    assert!(
        text.contains("name: send_message_to_thread"),
        "the tool name is part of the provenance: {text}"
    );
    assert!(
        text.contains("inherited brief from the parent task"),
        "the payload must survive verbatim: {text}"
    );
    assert!(!text.contains("call_id"), "no identity may be manufactured: {text}");
    assert_eq!(
        input
            .iter()
            .filter(|item| ModelClient::legacy_standalone_output_is_unpaired(item))
            .count(),
        0,
        "nothing unpaired may remain in the request view"
    );
    ModelClient::reject_legacy_unpaired_function_call_outputs(&input)
        .expect("the sanitized request view must satisfy the shipped refusal");
}

#[test]
fn binary_payload_standalone_output_still_refuses_before_any_provider_request() {
    let screenshot: ResponseItem = serde_json::from_value(serde_json::json!({
        "type": "function_call_output",
        "name": "computer-use:screenshot",
        "namespace": "codex_app",
        "output": [{ "type": "input_image", "image_url": "data:image/png;base64,AAAA" }],
    }))
    .expect("image-bearing standalone output");
    let mut input = vec![screenshot.clone()];

    ModelClient::sanitize_legacy_standalone_tool_outputs(&mut input).expect("sanitize runs");
    assert_eq!(
        input,
        vec![screenshot],
        "a payload that cannot become text without dropping content must stay as stored"
    );

    let error = ModelClient::reject_legacy_unpaired_function_call_outputs(&input)
        .expect_err("the image-bearing row must still be refused");
    assert!(
        error
            .to_string()
            .contains("No provider request was sent and history was left unchanged"),
        "the shipped recovery message must be preserved: {error}"
    );
}

#[test]
fn custom_and_tool_search_shapes_with_an_unusable_call_id_are_sanitized() {
    let custom: ResponseItem = serde_json::from_value(serde_json::json!({
        "type": "custom_tool_call_output",
        "call_id": "",
        "name": "apply_patch",
        "output": "custom result text",
    }))
    .expect("empty-id custom tool output");
    let tool_search: ResponseItem = serde_json::from_value(serde_json::json!({
        "type": "tool_search_output",
        "status": "completed",
        "execution": "local",
        "tools": [],
    }))
    .expect("tool search output without an id");
    let mut input = vec![custom, tool_search];

    ModelClient::sanitize_legacy_standalone_tool_outputs(&mut input).expect("sanitize");
    let first = commentary_payload_text(&input[0]).expect("custom output must convert");
    assert!(first.contains("custom result text"), "{first}");
    let second = commentary_payload_text(&input[1]).expect("tool search output must convert");
    assert!(second.contains("\"status\":\"completed\""), "{second}");
    ModelClient::reject_legacy_unpaired_function_call_outputs(&input)
        .expect("converted defensive shapes must be provider-safe");
}

#[tokio::test]
async fn inherited_unpaired_history_compacts_over_real_http() -> anyhow::Result<()> {
    // The shipped refusal lives inside build_responses_request, which
    // compact_conversation_history also calls, so a thread with an inherited row
    // could not even compact itself. This drives the real client over loopback
    // HTTP and asserts what the provider actually receives.
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1/agent/register"))
        .respond_with(ResponseTemplate::new(/*status*/ 503))
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path("/v1/responses/compact"))
        .respond_with(ResponseTemplate::new(/*status*/ 200).set_body_json(json!({
            "output": []
        })))
        .expect(/*requests*/ 1)
        .mount(&server)
        .await;

    let codex_home = TempDir::new()?;
    let auth_manager = chatgpt_auth_manager(&codex_home, server.uri()).await;
    let mut provider = ModelProviderInfo::create_openai_provider(/*base_url*/ None);
    provider.base_url = Some(format!("{}/v1", server.uri()));
    provider.supports_websockets = false;
    let client = ModelClient::new(
        Some(auth_manager),
        AgentIdentityAuthPolicy::ChatGptAuth,
        ThreadId::new(),
        provider,
        SessionSource::Cli,
        "test_originator".to_string(),
        /*model_verbosity*/ None,
        /*content_item_kinds_enabled*/ true,
        /*enable_request_compression*/ false,
        /*include_timing_metrics*/ false,
        /*beta_features_header*/ None,
        /*concurrent_reasoning_summaries_enabled*/ false,
        /*attestation_provider*/ None,
        HttpClientFactory::new(OutboundProxyPolicy::ReqwestDefault),
    );
    let prompt = Prompt {
        input: inherited_unpaired_history(),
        base_instructions: BaseInstructions {
            text: "base instructions".to_string(),
            provenance: None,
        },
        ..Default::default()
    };
    let responses_metadata = test_responses_metadata_for_client(
        &client,
        /*turn_id*/ None,
        format!("{}:0", client.state.thread_id),
        /*parent_thread_id*/ None,
        TestCodexResponsesRequestKind::Turn,
    );

    let output = client
        .compact_conversation_history(
            &prompt,
            &test_model_info(),
            /*turn_state*/ None,
            CompactConversationRequestSettings {
                effort: None,
                summary: codex_protocol::config_types::ReasoningSummary::None,
                service_tier: None,
            },
            &test_session_telemetry(),
            &CompactionTraceContext::disabled(),
            &responses_metadata,
        )
        .await?;
    assert!(output.is_empty(), "the mocked compact returns no items");

    let requests = server
        .received_requests()
        .await
        .expect("server should record requests");
    let body = requests
        .iter()
        .find(|request| request.url.path() == "/v1/responses/compact")
        .expect("compact request should be captured");
    let parsed: serde_json::Value = serde_json::from_slice(&body.body).expect("request json");
    let items = parsed["input"].as_array().expect("request input array");
    assert_eq!(items.len(), 3, "item count and order must reach the provider");
    for item in items {
        if item["type"] == "function_call_output" {
            assert!(
                item["call_id"]
                    .as_str()
                    .is_some_and(|call_id| !call_id.trim().is_empty()),
                "no function_call_output may reach the provider without a call_id: {item}"
            );
        }
    }
    assert_eq!(items[0]["type"], "function_call", "pairing must stay intact");
    assert_eq!(items[1]["call_id"], "call-paired");
    assert_eq!(items[2]["type"], "message");
    assert_eq!(items[2]["role"], "assistant");
    let serialized = items[2].to_string();
    assert!(
        serialized.contains("inherited brief from the parent task"),
        "the payload must arrive intact: {serialized}"
    );
    assert!(
        serialized.contains("source: codex_app"),
        "the provenance must arrive intact: {serialized}"
    );
    Ok(())
}
"""


def patch_client_tests(path: Path) -> None:
    text = read(path)
    if MARKER in text:
        if "inherited_unpaired_history_compacts_over_real_http" not in text:
            raise SystemExit("Patch X verification failed: client test block is incomplete")
        return
    write(path, text.rstrip() + "\n" + X_TESTS.lstrip("\n").rstrip() + "\n")


def read(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f"Patch X drift: required source file is missing: {path}")
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Patch X drift: expected exactly one {label} anchor, found {count}")
    return text.replace(old, new, 1)


# The exact helper Patch V installed. X replaces it with a version that keeps
# the same output format but also covers the other standalone output shapes.
V_HELPER = r"""fn standalone_tool_output_context_item(item: &ResponseItem) -> ResponseItem {
    let ResponseItem::FunctionCallOutput {
        name: Some(name),
        namespace,
        output,
        ..
    } = item
    else {
        return item.clone();
    };

    let source = namespace.as_deref().unwrap_or("<none>");
    ResponseItem::Message {
        id: None,
        role: "assistant".to_string(),
        content: vec![ContentItem::OutputText {
            text: format!(
                "[standalone tool output]\nsource: {source}\nname: {name}\npayload:\n{output}"
            ),
        }],
        phase: Some(MessagePhase::Commentary),
        internal_chat_message_metadata_passthrough: None,
    }
}
"""

X_HELPER = r"""pub(crate) fn standalone_tool_output_context_item(item: &ResponseItem) -> ResponseItem {
    let Some(payload) = standalone_tool_output_payload_text(item) else {
        // Rendering this item as commentary would lose content, or it is not a
        // standalone output at all. Leave it alone so the provider-boundary
        // refusal stays authoritative.
        return item.clone();
    };
    let (source, name) = match item {
        ResponseItem::FunctionCallOutput { name, namespace, .. } => (
            namespace.as_deref().unwrap_or("<none>"),
            name.as_deref().unwrap_or("<none>"),
        ),
        ResponseItem::CustomToolCallOutput { name, .. } => {
            ("<none>", name.as_deref().unwrap_or("<none>"))
        }
        ResponseItem::ToolSearchOutput { .. } => ("<none>", "tool_search_output"),
        _ => return item.clone(),
    };
    ResponseItem::Message {
        id: None,
        role: "assistant".to_string(),
        content: vec![ContentItem::OutputText {
            text: format!(
                "[standalone tool output]\nsource: {source}\nname: {name}\npayload:\n{payload}"
            ),
        }],
        phase: Some(MessagePhase::Commentary),
        internal_chat_message_metadata_passthrough: None,
    }
}

/// Plain-text rendering of a standalone output payload, or `None` when no
/// lossless text rendering exists.
///
/// `FunctionCallOutputPayload::to_text` is intentionally lossy: it drops image
/// and audio entries, so it cannot be used here. `Display` on the same payload
/// emits JSON for structured bodies, which preserves every byte. Binary items
/// are still refused rather than re-encoded, because a screenshot turned into
/// prose both misrepresents the tool result and inflates the request body past
/// the budget the provider already rejects.
fn standalone_tool_output_payload_text(item: &ResponseItem) -> Option<String> {
    use codex_protocol::models::FunctionCallOutputBody;
    use codex_protocol::models::FunctionCallOutputContentItem;

    match item {
        ResponseItem::FunctionCallOutput { output, .. }
        | ResponseItem::CustomToolCallOutput { output, .. } => match &output.body {
            FunctionCallOutputBody::Text(content) => Some(content.clone()),
            FunctionCallOutputBody::ContentItems(items) => {
                let lossless = items.iter().all(|item| {
                    matches!(
                        item,
                        FunctionCallOutputContentItem::InputText { .. }
                            | FunctionCallOutputContentItem::EncryptedContent { .. }
                    )
                });
                lossless.then(|| output.to_string())
            }
        },
        ResponseItem::ToolSearchOutput {
            status,
            execution,
            tools,
            ..
        } => serde_json::to_string(&serde_json::json!({
            "status": status,
            "execution": execution,
            "tools": tools,
        }))
        .ok(),
        _ => None,
    }
}
"""


def patch_hook_runtime(path: Path) -> None:
    text = read(path)
    if MARKER in text:
        if "standalone_tool_output_payload_text" not in text:
            raise SystemExit("Patch X verification failed: hook-runtime helper is incomplete")
        return
    text = replace_once(text, V_HELPER, X_HELPER, "hook-runtime standalone formatter")
    text = replace_once(
        text,
        "// /*V:standalone-tool-output*/\n",
        "// /*V:standalone-tool-output*/\n"
        f"// {MARKER}: one formatter for the live injection path and legacy request view.\n",
        "hook-runtime marker",
    )
    write(path, text)


# Patch V's provider-boundary refusal, kept exactly as shipped. X adds a
# converter in front of it instead of editing it, so a thread whose stored
# output cannot be represented still fails closed with the same message.
V_GUARD = r"""    // /*V:standalone-tool-output*/
    // Older Desktop histories can contain an app-server standalone output in
    // the provider-shaped FunctionCallOutput variant. It has no trustworthy
    // call_id, so stop before serializing a malformed provider request. Do not
    // infer a pair, manufacture an id, mutate stored history, or discard data.
    fn reject_legacy_unpaired_function_call_outputs(input: &[ResponseItem]) -> Result<()> {
        let invalid = input.iter().any(|item| {
            matches!(
                item,
                ResponseItem::FunctionCallOutput { call_id, .. }
                    if !call_id
                        .as_deref()
                        .is_some_and(|call_id| !call_id.trim().is_empty())
            )
        });
        if invalid {
            return Err(CodexErr::InvalidRequest(
                "Cannot resume this task because its stored standalone tool output has no call_id. "
                    .to_string()
                    + "No provider request was sent and history was left unchanged; recover from a clean task.",
            ));
        }
        Ok(())
    }
"""

X_SANITIZER = r"""    // /*X:legacy-unpaired-output-recovery*/
    // `thread/fork`, including the Desktop duplicate action, copies stored
    // history verbatim, so a thread that never called the app-server tool itself
    // can inherit a pre-Patch-V standalone output with no call_id. Rewrite only
    // this request's view of those items into the same assistant commentary the
    // live path persists, which keeps the payload and its provenance model-visible
    // without user or developer authority and without inventing an id, pairing by
    // proximity, dropping an output, or editing stored history. Compaction builds
    // its request through this function too, so this is also what lets a poisoned
    // thread compact itself. Anything still unpaired afterwards is left for the
    // refusal above.
    fn legacy_standalone_output_is_unpaired(item: &ResponseItem) -> bool {
        match item {
            // Option<String>: absent, null, or blank is not a usable identity.
            ResponseItem::FunctionCallOutput { call_id, .. } => {
                !call_id.as_deref().is_some_and(|call_id| !call_id.trim().is_empty())
            }
            // String: a missing identity can only arrive as an empty value.
            ResponseItem::CustomToolCallOutput { call_id, .. } => call_id.trim().is_empty(),
            // Defensive; measured zero occurrences on real histories.
            ResponseItem::ToolSearchOutput { call_id, .. } => {
                !call_id.as_deref().is_some_and(|call_id| !call_id.trim().is_empty())
            }
            _ => false,
        }
    }

    fn sanitize_legacy_standalone_tool_outputs(input: &mut [ResponseItem]) -> Result<()> {
        for item in input.iter_mut() {
            if !Self::legacy_standalone_output_is_unpaired(item) {
                continue;
            }
            let replacement = crate::hook_runtime::standalone_tool_output_context_item(item);
            *item = replacement;
        }
        Ok(())
    }
"""

V_CALL = """        let mut input = prompt.get_formatted_input_for_request(model_info.use_responses_lite);
        Self::reject_legacy_unpaired_function_call_outputs(&input)?;
"""

X_CALL = """        let mut input = prompt.get_formatted_input_for_request(model_info.use_responses_lite);
        Self::sanitize_legacy_standalone_tool_outputs(&mut input)?;
        Self::reject_legacy_unpaired_function_call_outputs(&input)?;
"""


def patch_client(path: Path) -> None:
    text = read(path)
    if MARKER in text:
        if "sanitize_legacy_standalone_tool_outputs(&mut input)" not in text:
            raise SystemExit("Patch X verification failed: client sanitizer call is missing")
        return
    text = replace_once(text, V_GUARD, V_GUARD + "\n" + X_SANITIZER, "client refusal block")
    text = replace_once(text, V_CALL, X_CALL, "client request-build call site")
    write(path, text)


# Acceptance tests. The wire-level test drives a real ModelClient against a real
# local HTTP endpoint and asserts on the serialized request body, so it proves
# the provider-boundary behaviour rather than a mock's self-consistency.


def verify(root: Path) -> None:
    hook = read(root / HOOK_RUNTIME)
    client = read(root / CLIENT)
    tests = read(root / CLIENT_TEST)
    for relative, text in ((HOOK_RUNTIME, hook), (CLIENT, client), (CLIENT_TEST, tests)):
        if MARKER not in text:
            raise SystemExit(f"Patch X verification failed: marker missing from {relative}")
    if "pub(crate) fn standalone_tool_output_context_item" not in hook:
        raise SystemExit("Patch X verification failed: shared formatter is not crate-visible")
    for required in ("standalone_tool_output_payload_text", "role: \"assistant\".to_string()"):
        if required not in hook:
            raise SystemExit(f"Patch X verification failed: `{required}` is missing from hook_runtime")
    # Patch V's anchors must survive X so the shipped refusal stays verifiable.
    if "Self::reject_legacy_unpaired_function_call_outputs(&input)?" not in client:
        raise SystemExit("Patch X verification failed: Patch V refusal call was removed")
    if "No provider request was sent and history was left unchanged" not in client:
        raise SystemExit("Patch X verification failed: recovery message changed")
    if "Self::sanitize_legacy_standalone_tool_outputs(&mut input)?" not in client:
        raise SystemExit("Patch X verification failed: sanitizer is not wired into the request build")
    if "sanitize_legacy_standalone_tool_outputs" not in client:
        raise SystemExit("Patch X verification failed: sanitizer helper is missing")
    for required in (
        "inherited_unpaired_output_is_sanitized_without_inventing_identity",
        "binary_payload_standalone_output_still_refuses_before_any_provider_request",
        "custom_and_tool_search_shapes_with_an_unusable_call_id_are_sanitized",
        "inherited_unpaired_history_compacts_over_real_http",
    ):
        if required not in tests:
            raise SystemExit(f"Patch X verification failed: test `{required}` is missing")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    root = args.source_dir.resolve()

    if not args.verify_only:
        patch_hook_runtime(root / HOOK_RUNTIME)
        patch_client(root / CLIENT)
        patch_client_tests(root / CLIENT_TEST)
    verify(root)
    print("Patch X verified: inherited standalone tool outputs are provider-safe.")


if __name__ == "__main__":
    main()
