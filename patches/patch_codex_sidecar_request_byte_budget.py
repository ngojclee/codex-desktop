#!/usr/bin/env python3
"""Patch W1 - provider request byte budget and non-retryable size rejection.

Some gateways reject a `/v1/responses` request before provider selection because
the serialized body is too large. That rejection is deterministic: the same bytes
always produce the same failure.

Upstream maps the status into a generic `UnexpectedStatus`, which
`CodexErr::is_retryable()` treats as retryable, so the sampling loop and remote
compaction re-send an identical payload that cannot ever succeed.

This patch adds exactly two behaviours:

* A client preflight that measures the exact serialized body about to be written
  to the socket and refuses locally when it cannot fit a configured, provider
  scoped byte budget. Nothing is sent, so stored history and the drafted input
  survive untouched for the next attempt.
* One distinct non-retryable error for both the local refusal and a gateway 413,
  carrying sizes plus a single dominant top-level field name. Request content,
  credentials and image bytes never cross that boundary.

The sampling, remote-compaction and compact-fallback loops are deliberately left
untouched: they already consult `is_retryable()`, so a single classification
change stops the retry storm in all of them.

Compaction is intentionally not preflighted. It posts to a different endpoint with
a body the gateway clones and rewrites again, so a client-side byte figure there
would not be provably correct. A gateway 413 on that path is still classified
non-retryably. Bounded compaction is separate, unapproved work.

An unset budget keeps behaviour identical to upstream: no measurement and no
refusal, so this is a no-op for every user who does not configure it.
"""

from __future__ import annotations

import argparse
from pathlib import Path


MARKER = "/*W1:request-byte-budget*/"

REQUEST_SIZE = Path("codex-rs/codex-api/src/request_size.rs")
CODEX_API_LIB = Path("codex-rs/codex-api/src/lib.rs")
API_ERROR = Path("codex-rs/codex-api/src/error.rs")
API_BRIDGE = Path("codex-rs/codex-api/src/api_bridge.rs")
API_BRIDGE_TESTS = Path("codex-rs/codex-api/src/api_bridge_tests.rs")
RESPONSES_ENDPOINT = Path("codex-rs/codex-api/src/endpoint/responses.rs")
DEBUG_CONTEXT = Path("codex-rs/response-debug-context/src/lib.rs")

ERROR_SITES = (
    Path("codex-rs/cli/src/doctor.rs"),
    Path("codex-rs/ext/guardian-v2/src/async_scorer/sampler.rs"),
    Path("codex-rs/ext/guardian-v2/src/async_scorer/metrics.rs"),
)

DOCTOR_OLD = '        | ApiError::ServerOverloaded => format!("handshake error: {err}"),\n'
DOCTOR_NEW = '        | ApiError::ServerOverloaded => format!("handshake error: {err}"),\n        // /*W1:request-byte-budget*/ a local size refusal is only a handshake diagnostic here,\n        // and its Display carries sizes, never request content.\n        ApiError::PayloadTooLarge(_) => format!("handshake error: {err}"),\n'

SAMPLER_OLD = '                | ApiError::MisalignmentPolicyViolation { .. }\n                | ApiError::CyberPolicy { .. },\n'
SAMPLER_NEW = "                | ApiError::MisalignmentPolicyViolation { .. }\n                // /*W1:request-byte-budget*/ an oversized body fails identically on every attempt,\n                // so the sampler must not spend its retry budget on it. This is\n                // the guardian sampler's own loop, separate from the core\n                // retryability predicate. No trailing comma: it stays mid-chain.\n                | ApiError::PayloadTooLarge(_)\n                | ApiError::CyberPolicy { .. },\n"
SAMPLER_NEW = "                | ApiError::MisalignmentPolicyViolation { .. }\n                // /*W1:request-byte-budget*/ an oversized body fails identically on every attempt,\n                // so the sampler must not spend its retry budget on it. This is\n                // the guardian sampler's own loop, separate from the core\n                // retryability predicate. No trailing comma: it stays mid-chain.\n                | ApiError::PayloadTooLarge(_)\n                | ApiError::CyberPolicy { .. },\n"

METRICS_OLD = '            ApiError::ServerOverloaded => "server_overloaded",\n'
METRICS_NEW = '            ApiError::ServerOverloaded => "server_overloaded",\n            // /*W1:request-byte-budget*/ a constant label keeps this counter low-cardinality:\n            // no byte counts, budgets, or providers.\n            ApiError::PayloadTooLarge(_) => "request_too_large",\n'

def patch_error_sites(root: Path) -> None:
    for relative, old, new in (
        (ERROR_SITES[0], DOCTOR_OLD, DOCTOR_NEW),
        (ERROR_SITES[1], SAMPLER_OLD, SAMPLER_NEW),
        (ERROR_SITES[2], METRICS_OLD, METRICS_NEW),
    ):
        path = root / relative
        body = read(path)
        if MARKER in body:
            continue
        body = replace_once(body, old, new, f"{relative} ApiError match")
        write(path, body)


CLIENT_TESTS = Path("codex-rs/codex-api/tests/clients.rs")
PROTOCOL_ERROR = Path("codex-rs/protocol/src/error.rs")
PROTOCOL_ERROR_TESTS = Path("codex-rs/protocol/src/error_tests.rs")
CORE_CLIENT = Path("codex-rs/core/src/client.rs")
CORE_CONFIG = Path("codex-rs/core/src/config/mod.rs")
CORE_SESSION = Path("codex-rs/core/src/session/session.rs")


def read(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f"Patch W1 drift: required source file is missing: {path}")
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"Patch W1 drift: expected exactly one {label} anchor, found {count}"
        )
    return text.replace(old, new, 1)


REQUEST_SIZE_MODULE = f"""{MARKER}
//! Provider-scoped request byte accounting for the Responses HTTP path.
//!
//! A gateway that rejects an oversized body does so before provider selection,
//! so the failure repeats identically for unchanged bytes. Measuring the
//! serialized request here lets Codex refuse locally instead of paying a round
//! trip that cannot succeed.
//!
//! Only sizes, one provider identifier and one top-level field name are
//! reported. Request content, credentials and inline media bytes are never
//! inspected, retained or logged.

use crate::common::ResponsesApiRequest;
use crate::error::ApiError;
use serde::Serialize;
use std::io::Write;

/// Byte budget for one provider's serialized `/v1/responses` request body.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestSizeBudget {{
    /// Provider display name, used only to describe the refusal.
    pub provider: String,
    /// Largest serialized body this provider accepts, in bytes.
    pub max_request_bytes: u64,
}}

impl RequestSizeBudget {{
    /// The limit is inclusive: a body of exactly `max_request_bytes` is sent.
    pub fn exceeded(&self, measured_bytes: usize) -> bool {{
        u64::try_from(measured_bytes).unwrap_or(u64::MAX) > self.max_request_bytes
    }}
}}

/// What the client knew when a request body did not fit its budget.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestSizeRejection {{
    /// Serialized body bytes measured by this client, when known.
    pub measured_bytes: Option<u64>,
    /// Configured budget bytes, when known.
    pub budget_bytes: Option<u64>,
    /// Provider the request was addressed to.
    pub provider: String,
    /// Largest top-level field name, when a breakdown was computed.
    pub dominant_field: Option<String>,
    /// Bytes attributed to `dominant_field`.
    pub dominant_bytes: Option<u64>,
    /// `client_preflight` when Codex refused before sending, `gateway` when the
    /// upstream answered with HTTP 413.
    pub stage: &'static str,
    /// HTTP status for the `gateway` stage.
    pub status: Option<u16>,
    /// Gateway request id, when the response carried one.
    pub request_id: Option<String>,
}}

impl std::fmt::Display for RequestSizeRejection {{
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {{
        let measured = self
            .measured_bytes
            .map(|bytes| format!("{{bytes}} bytes"))
            .unwrap_or_else(|| "an unknown size".to_string());
        let budget = self
            .budget_bytes
            .map(|bytes| format!("{{bytes}} bytes"))
            .unwrap_or_else(|| "an unknown limit".to_string());
        let stage = self.stage;
        write!(
            f,
            "{{measured}} against a {{budget}} request body budget for provider `{{}}` ({{stage}}",
            self.provider,
        )?;
        if let Some(status) = self.status {{
            write!(f, ", status {{status}}")?;
        }}
        write!(f, ")")?;
        if let (Some(field), Some(bytes)) = (&self.dominant_field, self.dominant_bytes) {{
            write!(f, ", largest top-level field `{{field}}` at {{bytes}} bytes")?;
        }}
        Ok(())
    }}
}}

/// Counted serialized size of each measured top-level field of a request body.
#[derive(Debug, Clone, Default)]
pub struct RequestSizeBreakdown {{
    pub fields: Vec<(String, u64)>,
}}

impl RequestSizeBreakdown {{
    /// Name the largest top-level field, but only when it holds a majority of the
    /// measured body. Calling `model` dominant because it is 90 bytes of a 20 MB
    /// request would be arithmetically true and practically misleading, so this
    /// watermark guards what we claim, not what we spend. The pass itself is
    /// allowed on every refusal: a barely oversized request is exactly the case
    /// where the user needs to know what to trim.
    pub fn dominant(&self) -> Option<RequestSizeDetail> {{
        let (field, bytes) = self.fields.iter().max_by_key(|(_, bytes)| *bytes)?;
        let total: u64 = self.fields.iter().map(|(_, bytes)| *bytes).sum();
        if total == 0 || bytes.saturating_mul(2) < total {{
            return None;
        }}
        Some(RequestSizeDetail {{
            dominant_field: field.clone(),
            dominant_bytes: *bytes,
        }})
    }}
}}

/// Sizes describing the largest top-level field of a request body.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestSizeDetail {{
    pub dominant_field: String,
    pub dominant_bytes: u64,
}}

/// Counts serialized JSON bytes without retaining a second copy of the payload.
struct ByteCounter(usize);

impl Write for ByteCounter {{
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {{
        self.0 = self.0.saturating_add(bytes.len());
        Ok(bytes.len())
    }}

    fn flush(&mut self) -> std::io::Result<()> {{
        Ok(())
    }}
}}

fn serialized_len<T: Serialize + ?Sized>(value: &T) -> u64 {{
    let mut counter = ByteCounter(0);
    serde_json::to_writer(&mut counter, value)
        .map(|()| u64::try_from(counter.0).unwrap_or(u64::MAX))
        .unwrap_or(u64::MAX)
}}

/// Per-top-level-field sizes for a fully built Responses request.
///
/// Fields are measured one at a time so a large request is never copied into a
/// second in-memory representation. Called only past the watermark.
pub fn breakdown_of_request(request: &ResponsesApiRequest) -> RequestSizeBreakdown {{
    RequestSizeBreakdown {{
        fields: vec![
            ("model".to_string(), serialized_len(&request.model)),
            (
                "instructions".to_string(),
                serialized_len(&request.instructions),
            ),
            ("input".to_string(), serialized_len(&request.input)),
            ("tools".to_string(), serialized_len(&request.tools)),
            ("reasoning".to_string(), serialized_len(&request.reasoning)),
            ("text".to_string(), serialized_len(&request.text)),
            (
                "client_metadata".to_string(),
                serialized_len(&request.client_metadata),
            ),
            ("include".to_string(), serialized_len(&request.include)),
        ],
    }}
}}

/// Returns `Err` when `measured_bytes` cannot fit `budget`.
///
/// `breakdown` runs only on the refusal path, so an accepted request pays
/// nothing beyond the length read it already has. Whether a field is named is
/// decided by `RequestSizeBreakdown::dominant`, which requires a majority share.
pub fn reject_oversized_request<F>(
    budget: Option<&RequestSizeBudget>,
    measured_bytes: usize,
    breakdown: F,
) -> Result<(), ApiError>
where
    F: FnOnce() -> RequestSizeBreakdown,
{{
    let Some(budget) = budget else {{
        // No configured budget: behaviour stays exactly as upstream.
        return Ok(());
    }};
    if !budget.exceeded(measured_bytes) {{
        return Ok(());
    }}
    let detail = breakdown().dominant();
    Err(ApiError::PayloadTooLarge(RequestSizeRejection {{
        measured_bytes: u64::try_from(measured_bytes).ok(),
        budget_bytes: Some(budget.max_request_bytes),
        provider: budget.provider.clone(),
        dominant_field: detail.as_ref().map(|detail| detail.dominant_field.clone()),
        dominant_bytes: detail.map(|detail| detail.dominant_bytes),
        stage: "client_preflight",
        status: None,
        request_id: None,
    }}))
}}

#[cfg(test)]
mod tests {{
    use super::*;

    fn budget(max_request_bytes: u64) -> RequestSizeBudget {{
        RequestSizeBudget {{
            provider: "test-provider".to_string(),
            max_request_bytes,
        }}
    }}

    #[test]
    fn budget_boundary_is_inclusive() {{
        let budget = budget(1_024);
        assert!(!budget.exceeded(1_023), "below budget must be sent");
        assert!(!budget.exceeded(1_024), "exactly at budget must be sent");
        assert!(budget.exceeded(1_025), "one byte above budget must be refused");
    }}

    #[test]
    fn advertised_gateway_ceilings_keep_their_inclusive_boundary() {{
        // The binding gateway ceiling is 32 MiB measured strictly above the limit,
        // and the plugin's own default is 20 MiB. Locking both boundaries here means
        // a drift in the gateway lane shows up as a failing test instead of a
        // silently wrong client budget.
        let gateway_ceiling = budget(32 << 20);
        assert!(!gateway_ceiling.exceeded(32 << 20));
        assert!(gateway_ceiling.exceeded((32 << 20) + 1));

        let configured_default = budget(20 << 20);
        assert!(!configured_default.exceeded(20 << 20));
        assert!(configured_default.exceeded((20 << 20) + 1));

        // A 20 MiB body must never be judged against the looser 21 MiB figure.
        let conservative = budget(21 << 20);
        assert!(!conservative.exceeded(20 << 20));
    }}

    #[test]
    fn dominant_field_requires_a_majority_share() {{
        let majority = RequestSizeBreakdown {{
            fields: vec![("input".to_string(), 3_900), ("tools".to_string(), 196)],
        }}
        .dominant()
        .expect("input holds the body");
        assert_eq!(majority.dominant_field, "input");
        assert_eq!(majority.dominant_bytes, 3_900);

        let split = RequestSizeBreakdown {{
            fields: vec![
                ("input".to_string(), 300),
                ("tools".to_string(), 300),
                ("reasoning".to_string(), 300),
            ],
        }}
        .dominant();
        assert!(
            split.is_none(),
            "without a majority holder the refusal must not blame one field"
        );
        assert!(
            RequestSizeBreakdown::default().dominant().is_none(),
            "an empty breakdown attributes nothing"
        );
    }}

    #[test]
    fn unset_budget_never_refuses() {{
        assert!(
            reject_oversized_request(None, usize::MAX, RequestSizeBreakdown::default).is_ok(),
            "an unset budget must keep upstream behaviour"
        );
    }}

    #[test]
    fn refusal_reports_sizes_and_one_field_name_without_content() {{
        let error = reject_oversized_request(
            Some(&budget(1_024)),
            4_096,
            || RequestSizeBreakdown {{
                fields: vec![
                    ("model".to_string(), 16),
                    ("input".to_string(), 3_900),
                    ("tools".to_string(), 84),
                ],
            }},
        )
        .expect_err("an oversized body must be refused");
        let ApiError::PayloadTooLarge(rejection) = error else {{
            panic!("expected a size rejection, got {{error:?}}");
        }};
        assert_eq!(rejection.measured_bytes, Some(4_096));
        assert_eq!(rejection.budget_bytes, Some(1_024));
        assert_eq!(rejection.dominant_field.as_deref(), Some("input"));
        assert_eq!(rejection.dominant_bytes, Some(3_900));
        assert_eq!(rejection.stage, "client_preflight");
        let described = rejection.to_string();
        assert!(described.contains("4096 bytes"), "{{described}}");
        assert!(!described.contains("secret"), "{{described}}");
    }}

    #[test]
    fn every_refusal_names_a_majority_field_and_accepted_requests_pay_nothing() {{
        let mut called = false;
        let outcome = reject_oversized_request(Some(&budget(1_024)), 1_025, || {{
            called = true;
            RequestSizeBreakdown {{
                fields: vec![("input".to_string(), 1_000), ("tools".to_string(), 20)],
            }}
        }});
        assert!(called, "the refusal path is where a pass is paid for");
        let ApiError::PayloadTooLarge(rejection) = outcome.expect_err("refused") else {{
            panic!("expected a size rejection")
        }};
        assert_eq!(rejection.dominant_field.as_deref(), Some("input"));

        let mut accepted_calls = 0;
        let outcome = reject_oversized_request(Some(&budget(1_024)), 1_024, || {{
            accepted_calls += 1;
            RequestSizeBreakdown::default()
        }});
        outcome.expect("exactly at the budget is sent");
        assert_eq!(accepted_calls, 0, "an accepted request never pays for a pass");
    }}

    #[test]
    fn serialized_len_counts_wire_bytes_not_characters() {{
        let accent = char::from_u32(0xE9).expect("U+00E9 is a valid scalar value");
        assert_eq!(serialized_len("hello"), 7, "JSON adds two quote bytes");
        let text = format!("h{{accent}}llo");
        assert_eq!(
            serialized_len(&text),
            8,
            "UTF-8 bytes are counted, not characters"
        );
    }}
}}
{MARKER}
"""


def patch_request_size_module(root: Path) -> None:
    path = root / REQUEST_SIZE
    if path.exists():
        if MARKER in path.read_text(encoding="utf-8"):
            return
        raise SystemExit("Patch W1 drift: request_size.rs exists without the marker")
    path.parent.mkdir(parents=True, exist_ok=True)
    write(path, REQUEST_SIZE_MODULE)


def patch_codex_api_lib(path: Path) -> None:
    text = read(path)
    if MARKER in text:
        return
    text = replace_once(
        text,
        "pub(crate) mod requests;\n",
        f"pub(crate) mod requests;\npub mod request_size; // {MARKER}\n",
        "codex-api module declaration",
    )
    text = replace_once(
        text,
        "pub use crate::provider::Provider;\n",
        "pub use crate::provider::Provider;\n"
        f"{MARKER}\n"
        "pub use crate::request_size::RequestSizeBreakdown;\n"
        "pub use crate::request_size::RequestSizeBudget;\n"
        "pub use crate::request_size::RequestSizeDetail;\n"
        "pub use crate::request_size::RequestSizeRejection;\n"
        "pub use crate::request_size::breakdown_of_request;\n"
        "pub use crate::request_size::reject_oversized_request;\n",
        "codex-api re-exports",
    )
    write(path, text)


def patch_api_error(path: Path) -> None:
    text = read(path)
    if MARKER in text:
        return
    text = replace_once(
        text,
        """    #[error("server overloaded")]
    ServerOverloaded,
}
""",
        f"""    #[error("server overloaded")]
    ServerOverloaded,
    // {MARKER}
    /// A serialized request body that cannot fit its provider byte budget.
    #[error("request body exceeds the provider byte limit: {{0}}")]
    PayloadTooLarge(crate::request_size::RequestSizeRejection),
}}
""",
        "ApiError variant insertion",
    )
    write(path, text)


def patch_responses_endpoint(path: Path) -> None:
    text = read(path)
    if MARKER in text:
        return

    text = replace_once(
        text,
        """    pub extra_headers: HeaderMap,
    pub compression: Compression,
    pub turn_state: Option<Arc<OnceLock<String>>>,
}
""",
        f"""    pub extra_headers: HeaderMap,
    pub compression: Compression,
    pub turn_state: Option<Arc<OnceLock<String>>>,
    // {MARKER} inclusive byte budget for the serialized body; `None` keeps
    // upstream behaviour exactly: no measurement and no refusal.
    pub max_request_bytes: Option<crate::request_size::RequestSizeBudget>,
}}
""",
        "ResponsesOptions budget field",
    )

    text = replace_once(
        text,
        "        let ResponsesOptions {\n"
        "            session_id,\n"
        "            thread_id,\n"
        "            session_source,\n"
        "            extra_headers,\n"
        "            compression,\n"
        "            turn_state,\n"
        "        } = options;\n"
        "        let body = EncodedJsonBody::encode(&request)\n"
        '            .map_err(|e| ApiError::Stream(format!("failed to encode responses request: {e}")))?;\n',
        f"""        let ResponsesOptions {{
            session_id,
            thread_id,
            session_source,
            extra_headers,
            compression,
            turn_state,
            max_request_bytes,
        }} = options;
        let body = EncodedJsonBody::encode(&request)
            .map_err(|e| ApiError::Stream(format!("failed to encode responses request: {{e}}")))?;
        {MARKER}
        // Refuse before the transport call using the exact bytes that were about to
        // be written. No provider request is made and no history is modified.
        crate::request_size::reject_oversized_request(
            max_request_bytes.as_ref(),
            body.as_bytes().len(),
            || crate::request_size::breakdown_of_request(&request),
        )?;
""",
        "stream_request preflight",
    )
    write(path, text)


def patch_protocol_error(path: Path) -> None:
    text = read(path)
    if "RequestTooLargeError" in text:
        return

    text = replace_once(
        text,
        "    /// Retry limit exceeded.\n"
        '    #[error("{0}")]\n'
        "    RetryLimit(RetryLimitReachedError),\n",
        f"""    /// Retry limit exceeded.
    #[error("{{0}}")]
    RetryLimit(RetryLimitReachedError),
    // {MARKER}
    /// A request body that cannot fit a byte budget which retrying cannot fix.
    #[error("{{0}}")]
    RequestTooLarge(RequestTooLargeError),
""",
        "protocol error variant",
    )

    text = replace_once(
        text,
        "            | CodexErrorDetails::MisalignmentPolicyViolation { .. } => false,\n",
        "            | CodexErrorDetails::MisalignmentPolicyViolation { .. }\n"
        f"            // {MARKER} retrying an identical oversized body fails identically.\n"
        "            | CodexErrorDetails::RequestTooLarge(_) => false,\n",
        "protocol retryability",
    )

    text = replace_once(
        text,
        "        RetryLimit(error: RetryLimitReachedError),\n",
        "        RetryLimit(error: RetryLimitReachedError),\n"
        "        RequestTooLarge(error: RequestTooLargeError),\n",
        "protocol tuple constructor",
    )

    struct_text = f"""{MARKER}
/// Sizes describing a request body that did not fit its byte budget.
///
/// `stage` is `client_preflight` when Codex refused before sending and `gateway`
/// when the upstream answered with HTTP 413. Only sizes, one provider identifier
/// and one top-level field name are carried; request content never appears here.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestTooLargeError {{
    pub measured_bytes: Option<u64>,
    pub budget_bytes: Option<u64>,
    pub provider: String,
    pub dominant_field: Option<String>,
    pub dominant_bytes: Option<u64>,
    pub stage: &'static str,
    pub status: Option<u16>,
    pub request_id: Option<String>,
}}

impl std::fmt::Display for RequestTooLargeError {{
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {{
        let measured = self
            .measured_bytes
            .map(|bytes| format!("{{bytes}} bytes"))
            .unwrap_or_else(|| "an unknown number of bytes".to_string());
        let budget = self
            .budget_bytes
            .map(|bytes| format!("{{bytes}} bytes"))
            .unwrap_or_else(|| "an unknown limit".to_string());
        let stage = self.stage;
        write!(
            f,
            "This request is too large for the provider to accept ({{measured}} against {{budget}} for provider `{{}}`, {{stage}}",
            self.provider,
        )?;
        if let Some(status) = self.status {{
            write!(f, ", status {{status}}")?;
        }}
        if let (Some(field), Some(bytes)) = (&self.dominant_field, self.dominant_bytes) {{
            write!(f, ", largest top-level field `{{field}}` at {{bytes}} bytes")?;
        }}
        write!(f, ").")?;
        if self.stage == "client_preflight" {{
            write!(
                f,
                " Codex did not send it and did not change your saved history: revert earlier turns, or continue in a new task with a shorter brief."
            )
        }} else {{
            write!(
                f,
                " Codex did not retry it and your saved history is unchanged: revert earlier turns, or continue in a new task with a shorter brief."
            )
        }}
    }}
}}

"""
    text = replace_once(
        text,
        "#[derive(Debug)]\npub struct RetryLimitReachedError {",
        struct_text + "#[derive(Debug)]\npub struct RetryLimitReachedError {",
        "protocol struct insertion",
    )
    write(path, text)


def patch_protocol_error_tests(path: Path) -> None:
    text = read(path)
    if "RequestTooLarge" in text:
        return
    text = replace_once(
        text,
        """    let errors = [
        (CodexErr::ServerOverloaded, false),
""",
        """    let errors = [
        (CodexErr::ServerOverloaded, false),
        (
            CodexErr::RequestTooLarge(RequestTooLargeError {
                measured_bytes: Some(22_020_097),
                budget_bytes: Some(20_971_520),
                provider: "cliproxy".to_string(),
                dominant_field: Some("input".to_string()),
                dominant_bytes: Some(20_000_000),
                stage: "client_preflight",
                status: None,
                request_id: None,
            }),
            false,
        ),
""",
        "protocol retryability table",
    )
    write(path, text)


def patch_api_bridge(path: Path) -> None:
    text = read(path)
    if MARKER in text:
        return

    text = replace_once(
        text,
        "use codex_protocol::error::RetryLimitReachedError;\n",
        "use codex_protocol::error::RetryLimitReachedError;\n"
        f"use codex_protocol::error::RequestTooLargeError; // {MARKER}\n",
        "api_bridge import",
    )

    helper = f"""
// {MARKER}
/// Builds the non-retryable size rejection reported by a gateway.
///
/// The gateway measures a body it has already cloned and rewritten, so no client
/// byte figure is claimed here and the gateway response body is never attached.
fn gateway_size_rejection(
    status: http::StatusCode,
    url: Option<String>,
    request_id: Option<String>,
) -> CodexErr {{
    CodexErr::RequestTooLarge(RequestTooLargeError {{
        measured_bytes: None,
        budget_bytes: None,
        provider: url.unwrap_or_else(|| "provider".to_string()),
        dominant_field: None,
        dominant_bytes: None,
        stage: "gateway",
        status: Some(status.as_u16()),
        request_id,
    }})
}}

impl From<crate::request_size::RequestSizeRejection> for RequestTooLargeError {{
    fn from(rejection: crate::request_size::RequestSizeRejection) -> Self {{
        Self {{
            measured_bytes: rejection.measured_bytes,
            budget_bytes: rejection.budget_bytes,
            provider: rejection.provider,
            dominant_field: rejection.dominant_field,
            dominant_bytes: rejection.dominant_bytes,
            stage: rejection.stage,
            status: rejection.status,
            request_id: rejection.request_id,
        }}
    }}
}}

"""
    text = replace_once(
        text,
        "pub fn map_api_error(err: ApiError) -> CodexErr {",
        helper.lstrip("\n") + "pub fn map_api_error(err: ApiError) -> CodexErr {",
        "api_bridge helper insertion",
    )

    text = replace_once(
        text,
        "        ApiError::ServerOverloaded => CodexErr::ServerOverloaded,\n",
        "        ApiError::ServerOverloaded => CodexErr::ServerOverloaded,\n"
        "        // W1: a size refusal is deterministic for unchanged bytes, so it must\n"
        "        // never be retried. `is_retryable()` carries that to every loop.\n"
        "        ApiError::PayloadTooLarge(rejection) => {\n"
        "            CodexErr::RequestTooLarge(RequestTooLargeError::from(rejection))\n"
        "        }\n",
        "ApiError preflight arm",
    )

    text = replace_once(
        text,
        """        ApiError::Api { status, message } => {
            let user_message = api_error_user_message(status, &message);
""",
        """        ApiError::Api { status, message } => {
            // W1: classify a gateway size rejection instead of folding it into the
            // retryable `UnexpectedStatus` bucket.
            if status == http::StatusCode::PAYLOAD_TOO_LARGE {
                return gateway_size_rejection(status, None, None);
            }
            let user_message = api_error_user_message(status, &message);
""",
        "Api 413 classification",
    )

    text = replace_once(
        text,
        "                let body_text = body.unwrap_or_default();\n",
        "                let body_text = body.unwrap_or_default();\n"
        "                // W1: the CLIProxy plugin ABI limit surfaces here. Only the\n"
        "                // status, url and request id are used; the response body is not\n"
        "                // echoed to the user.\n"
        "                if status == http::StatusCode::PAYLOAD_TOO_LARGE {\n"
        "                    return gateway_size_rejection(\n"
        "                        status,\n"
        "                        url.clone(),\n"
        "                        extract_request_tracking_id(headers.as_ref()),\n"
        "                    );\n"
        "                }\n",
        "transport 413 classification",
    )
    write(path, text)


def patch_api_bridge_tests(path: Path) -> None:
    text = read(path)
    if "payload_too_large" in text:
        return
    tests = f"""// {MARKER}
#[test]
fn map_api_error_classifies_gateway_payload_too_large_as_non_retryable() {{
    let err = map_api_error(ApiError::Transport(TransportError::Http {{
        status: http::StatusCode::PAYLOAD_TOO_LARGE,
        url: Some("http://127.0.0.1:8317/v1/responses".to_string()),
        headers: None,
        body: Some("intercepted request exceeds the plugin ABI size limit".to_string()),
    }}));

    assert!(
        !err.is_retryable(),
        "an oversized body fails identically on retry and must not be re-sent"
    );
    let CodexErrorDetails::RequestTooLarge(rejection) = err.details() else {{
        panic!("expected RequestTooLarge, got {{err:?}}");
    }};
    assert_eq!(rejection.status, Some(413));
    assert_eq!(rejection.stage, "gateway");
    assert!(
        rejection.measured_bytes.is_none(),
        "no client byte count may be claimed for a gateway-side measurement"
    );
    assert!(
        !err.to_string().contains("plugin ABI size limit"),
        "the gateway response body must not be echoed to the user"
    );
    assert!(
        err.to_string().contains("did not retry it"),
        "the message must say the request was not retried: {{err}}"
    );
}}

#[test]
fn map_api_error_classifies_client_preflight_as_non_retryable() {{
    let err = map_api_error(ApiError::PayloadTooLarge(
        crate::request_size::RequestSizeRejection {{
            measured_bytes: Some(22_020_097),
            budget_bytes: Some(20_971_520),
            provider: "CLIProxy".to_string(),
            dominant_field: Some("input".to_string()),
            dominant_bytes: Some(20_000_000),
            stage: "client_preflight",
            status: None,
            request_id: None,
        }},
    ));

    assert!(!err.is_retryable());
    let message = err.to_string();
    assert!(message.contains("22020097 bytes"), "{{message}}");
    assert!(message.contains("20971520 bytes"), "{{message}}");
    assert!(message.contains("CLIProxy"), "{{message}}");
    assert!(message.contains("`input`"), "{{message}}");
    assert!(
        message.contains("did not send it and did not change your saved history"),
        "the message must say the refusal was local: {{message}}"
    );
}}
// {MARKER}

"""
    text = replace_once(
        text,
        "#[test]\nfn map_api_error_maps_server_overloaded() {",
        tests + "#[test]\nfn map_api_error_maps_server_overloaded() {",
        "api_bridge tests insertion",
    )
    write(path, text)


def patch_debug_context(path: Path) -> None:
    """Keep the telemetry match exhaustive for the new ApiError variant.

    `telemetry_api_error_message` has no wildcard arm upstream, so adding
    `ApiError::PayloadTooLarge` without a label here breaks the build of every
    crate that depends on `codex-response-debug-context`. The label stays a
    constant so the telemetry counter keeps low cardinality and no request body,
    field breakdown, or measured byte count is ever logged.
    """
    text = read(path)
    if MARKER in text:
        return
    old = '        ApiError::ServerOverloaded => "server overloaded".to_string(),\n    }\n}\n'
    new = (
        '        ApiError::ServerOverloaded => "server overloaded".to_string(),\n'
        f"        // {MARKER}\n"
        '        ApiError::PayloadTooLarge(_) => "request body too large".to_string(),\n'
        "    }\n"
        "}\n"
    )
    text = replace_once(text, old, new, "telemetry api error match")
    write(path, text)


def patch_clients_tests(path: Path) -> None:
    text = read(path)
    if "RequestSizeBudget" in text:
        return

    text = replace_once(
        text,
        """                compression: Compression::None,
                turn_state: None,
            },
""",
        """                compression: Compression::None,
                turn_state: None,
                max_request_bytes: None,
            },
""",
        "clients.rs ResponsesOptions literal",
    )

    text = replace_once(
        text,
        "use codex_api::ResponsesOptions;\n",
        "use codex_api::ResponsesOptions;\n"
        "use codex_api::RequestSizeBudget; // W1\n",
        "clients.rs imports",
    )

    tests = f"""// {MARKER}
fn oversized_w1_request() -> ResponsesApiRequest {{
    ResponsesApiRequest {{
        model: "gpt-test".into(),
        instructions: "Say hi".into(),
        input: vec![ResponseItem::Message {{
            id: Some(ResponseItemId::with_suffix("msg", "1")),
            role: "user".into(),
            content: vec![
                ContentItem::InputText {{
                    text: "a".repeat(4096),
                }},
            ],
            phase: None,
            internal_chat_message_metadata_passthrough: None,
        }}],
        tools: Some(empty_tools().into()),
        tool_choice: "auto".into(),
        parallel_tool_calls: false,
        reasoning: None,
        store: false,
        stream: true,
        stream_options: None,
        include: Vec::new(),
        service_tier: None,
        prompt_cache_key: None,
        text: None,
        client_metadata: None,
        access_programs: None,
    }}
}}

#[tokio::test]
async fn oversized_request_is_refused_before_any_transport_call() -> Result<()> {{
    let state = RecordingState::default();
    let transport = RecordingTransport::new(state.clone());
    let client = ResponsesClient::new(transport, provider("cliproxy"), Arc::new(NoAuth));

    let outcome = client
        .stream_request(
            oversized_w1_request(),
            ResponsesOptions {{
                max_request_bytes: Some(RequestSizeBudget {{
                    provider: "cliproxy".to_string(),
                    max_request_bytes: 64,
                }}),
                ..Default::default()
            }},
        )
        .await;
    let error = match outcome {{
        Ok(_stream) => panic!("an oversized request must be refused locally"),
        Err(error) => error,
    }};

    let ApiError::PayloadTooLarge(rejection) = error else {{
        panic!("expected a size rejection, got {{error:?}}");
    }};
    assert_eq!(rejection.stage, "client_preflight");
    assert_eq!(rejection.budget_bytes, Some(64));
    assert!(
        rejection.measured_bytes.is_some(),
        "the measured body size must be reported"
    );
    assert_eq!(
        rejection.dominant_field.as_deref(),
        Some("input"),
        "only the dominant field name is reported, never its content"
    );
    assert!(
        state.take_stream_requests().is_empty(),
        "a locally refused request must never reach the transport"
    );
    Ok(())
}}

#[tokio::test]
async fn body_exactly_at_byte_budget_is_sent() -> Result<()> {{
    let state = RecordingState::default();
    let transport = RecordingTransport::new(state.clone());
    let client = ResponsesClient::new(transport, provider("cliproxy"), Arc::new(NoAuth));

    let request = oversized_w1_request();
    let measured = serde_json::to_vec(&request)?.len() as u64;
    let _stream = client
        .stream_request(
            request,
            ResponsesOptions {{
                max_request_bytes: Some(RequestSizeBudget {{
                    provider: "cliproxy".to_string(),
                    max_request_bytes: measured,
                }}),
                ..Default::default()
            }},
        )
        .await?;
    assert_eq!(state.take_stream_requests().len(), 1);
    Ok(())
}}

#[tokio::test]
async fn unset_byte_budget_keeps_upstream_behaviour() -> Result<()> {{
    let state = RecordingState::default();
    let transport = RecordingTransport::new(state.clone());
    let client = ResponsesClient::new(transport, provider("openai"), Arc::new(NoAuth));

    let _stream = client
        .stream_request(oversized_w1_request(), ResponsesOptions::default())
        .await?;
    assert_eq!(state.take_stream_requests().len(), 1);
    Ok(())
}}
// {MARKER}

"""
    text = replace_once(
        text,
        "#[tokio::test]\nasync fn streaming_client_adds_auth_headers() -> Result<()> {",
        tests + "#[tokio::test]\nasync fn streaming_client_adds_auth_headers() -> Result<()> {",
        "clients.rs test insertion",
    )
    write(path, text)


def patch_core_client(path: Path) -> None:
    text = read(path)
    if MARKER in text:
        return

    text = replace_once(
        text,
        """    free_guardian_enabled: bool,
    event_sender: Option<Sender<ProtocolEvent>>,
    http_client_factory: HttpClientFactory,
}
""",
        f"""    free_guardian_enabled: bool,
    event_sender: Option<Sender<ProtocolEvent>>,
    http_client_factory: HttpClientFactory,
    // {MARKER} inclusive serialized-body byte budget resolved from provider config.
    max_request_bytes: Option<u64>,
}}
""",
        "ModelClient budget field",
    )

    text = replace_once(
        text,
        """            free_guardian_enabled: false,
            event_sender: None,
            http_client_factory,
        }
""",
        """            free_guardian_enabled: false,
            event_sender: None,
            http_client_factory,
            max_request_bytes: None,
        }
""",
        "ModelClient budget initialization",
    )

    text = replace_once(
        text,
        "    pub(crate) fn with_free_guardian_enabled(mut self, free_guardian_enabled: bool) -> Self {\n",
        f"""    // {MARKER}
    /// Sets the provider request byte budget. A builder keeps every existing
    /// `ModelClient::new` call site and signature untouched. Zero or `None`
    /// disables the preflight, which is upstream behaviour.
    pub(crate) fn with_max_request_bytes(mut self, max_request_bytes: Option<u64>) -> Self {{
        self.max_request_bytes = max_request_bytes.filter(|bytes| *bytes > 0);
        self
    }}

    pub(crate) fn with_free_guardian_enabled(mut self, free_guardian_enabled: bool) -> Self {{
""",
        "ModelClient budget builder",
    )

    text = replace_once(
        text,
        """            compression,
            turn_state: Some(Arc::clone(&self.turn_state)),
        }
""",
        f"""            compression,
            turn_state: Some(Arc::clone(&self.turn_state)),
            // {MARKER}
            max_request_bytes: self.client.max_request_bytes.map(|max_request_bytes| {{
                codex_api::RequestSizeBudget {{
                    provider: self.client.state.provider.info().name.clone(),
                    max_request_bytes,
                }}
            }}),
        }}
""",
        "ResponsesOptions budget wiring",
    )
    write(path, text)


def patch_core_config(path: Path) -> None:
    text = read(path)
    if "provider_max_request_bytes" in text:
        return
    text = replace_once(
        text,
        """    /// Whether Guardian may use the unmetered Codex inference endpoints.
    pub fn free_guardian_enabled(&self) -> bool {
""",
        f"""    // {MARKER}
    /// Reads the active provider's request byte budget from the merged config.
    ///
    /// `max_request_bytes` is read from raw TOML rather than `ModelProviderInfo`
    /// on purpose: serde already ignores that unknown key, so this adds no schema
    /// churn and keeps the patch small enough to survive upstream drift. `None`
    /// means no preflight, which is upstream behaviour.
    pub fn provider_max_request_bytes(&self) -> Option<u64> {{
        self.config_layer_stack
            .effective_config()
            .get("model_providers")?
            .get(self.model_provider_id.as_str())?
            .get("max_request_bytes")?
            .as_integer()
            .filter(|value| *value > 0)
            .map(|value| value as u64)
    }}

    /// Whether Guardian may use the unmetered Codex inference endpoints.
    pub fn free_guardian_enabled(&self) -> bool {{
""",
        "Config budget accessor",
    )
    write(path, text)


def patch_core_session(path: Path) -> None:
    text = read(path)
    if "with_max_request_bytes" in text:
        return
    text = replace_once(
        text,
        "                .with_free_guardian_enabled(config.free_guardian_enabled())\n",
        "                .with_free_guardian_enabled(config.free_guardian_enabled())\n"
        f"                .with_max_request_bytes(config.provider_max_request_bytes()) // {MARKER}\n",
        "session budget wiring",
    )
    write(path, text)


def verify(root: Path) -> None:
    for relative in (
        REQUEST_SIZE,
        CODEX_API_LIB,
        API_ERROR,
        API_BRIDGE,
        API_BRIDGE_TESTS,
        RESPONSES_ENDPOINT,
        CLIENT_TESTS,
        PROTOCOL_ERROR,
        CORE_CLIENT,
        CORE_CONFIG,
        CORE_SESSION,
    ):
        if MARKER not in read(root / relative):
            raise SystemExit(f"Patch W1 verification failed: marker missing from {relative}")

    # The protocol retryability table is a data row inside an existing test, so it
    # carries no marker of its own; assert the row itself instead.
    protocol_tests = read(root / PROTOCOL_ERROR_TESTS)
    if "CodexErr::RequestTooLarge(RequestTooLargeError" not in protocol_tests:
        raise SystemExit(
            "Patch W1 verification failed: protocol retryability row is missing from error_tests.rs"
        )

    responses = read(root / RESPONSES_ENDPOINT)
    if "reject_oversized_request(" not in responses:
        raise SystemExit("Patch W1 verification failed: preflight call is missing")
    if "max_request_bytes: Option<crate::request_size::RequestSizeBudget>" not in responses:
        raise SystemExit("Patch W1 verification failed: budget field is missing")

    debug = read(root / DEBUG_CONTEXT)
    if 'ApiError::PayloadTooLarge(_) => "request body too large".to_string()' not in debug:
        raise SystemExit(
            "Patch W1 verification failed: telemetry arm for PayloadTooLarge is missing"
        )

    for relative in ERROR_SITES:
        site = read(root / relative)
        if MARKER not in site or "ApiError::PayloadTooLarge" not in site:
            raise SystemExit(
                f"Patch W1 verification failed: {relative} needs a PayloadTooLarge arm"
            )

    bridge = read(root / API_BRIDGE)
    if bridge.count("StatusCode::PAYLOAD_TOO_LARGE") < 2:
        raise SystemExit("Patch W1 verification failed: gateway 413 classification is incomplete")
    if "ApiError::PayloadTooLarge(rejection) =>" not in bridge:
        raise SystemExit("Patch W1 verification failed: preflight mapping is missing")

    error = read(root / PROTOCOL_ERROR)
    if "CodexErrorDetails::RequestTooLarge(_) => false" not in error:
        raise SystemExit("Patch W1 verification failed: non-retryable arm is missing")
    for guidance in ("did not send it", "did not retry it"):
        if guidance not in error:
            raise SystemExit(
                f"Patch W1 verification failed: recovery guidance `{guidance}` is missing"
            )

    size = read(root / REQUEST_SIZE)
    for required in (
        "pub fn reject_oversized_request",
        "pub fn breakdown_of_request",
        "struct ByteCounter",
        "budget_boundary_is_inclusive",
    ):
        if required not in size:
            raise SystemExit(f"Patch W1 verification failed: `{required}` is missing")

    core_client = read(root / CORE_CLIENT)
    if "max_request_bytes: self.client.max_request_bytes.map(" not in core_client:
        raise SystemExit("Patch W1 verification failed: budget is not wired into options")

    config = read(root / CORE_CONFIG)
    if 'pub fn provider_max_request_bytes(&self) -> Option<u64>' not in config:
        raise SystemExit("Patch W1 verification failed: config accessor is missing")

    session = read(root / CORE_SESSION)
    if ".with_max_request_bytes(config.provider_max_request_bytes())" not in session:
        raise SystemExit("Patch W1 verification failed: session wiring is missing")

    clients = read(root / CLIENT_TESTS)
    if "oversized_request_is_refused_before_any_transport_call" not in clients:
        raise SystemExit("Patch W1 verification failed: zero-transport-call test is missing")
    if "unset_byte_budget_keeps_upstream_behaviour" not in clients:
        raise SystemExit("Patch W1 verification failed: no-op test is missing")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    root = args.source_dir.resolve()

    if not args.verify_only:
        patch_request_size_module(root)
        patch_codex_api_lib(root / CODEX_API_LIB)
        patch_api_error(root / API_ERROR)
        patch_protocol_error(root / PROTOCOL_ERROR)
        patch_protocol_error_tests(root / PROTOCOL_ERROR_TESTS)
        patch_api_bridge(root / API_BRIDGE)
        patch_api_bridge_tests(root / API_BRIDGE_TESTS)
        patch_responses_endpoint(root / RESPONSES_ENDPOINT)
        patch_debug_context(root / DEBUG_CONTEXT)
        patch_error_sites(root)
        patch_clients_tests(root / CLIENT_TESTS)
        patch_core_client(root / CORE_CLIENT)
        patch_core_config(root / CORE_CONFIG)
        patch_core_session(root / CORE_SESSION)
    verify(root)
    print("Patch W1 verified: oversized Responses bodies are refused locally.")


if __name__ == "__main__":
    main()
