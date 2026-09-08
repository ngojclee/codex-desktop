# HTTP 413 plugin ABI size limit — evidence plan and patch design (read-only round)

Date: 2026-09-08. Scope: **design only**. No implementation, no config change, no
CPA/plugin repository change, no disabling of `deepseek-vision`, no body or
credential logging, no transcript or SQLite edits.

Labelling used throughout:

- `MEASURED` — a number or fact read from a running system in this round.
- `SOURCE` — proven by reading exact code at a named revision.
- `ARITH` — arithmetic on MEASURED/SOURCE values.
- `INFERRED` — plausible but not established; never treated as a conclusion.

## 0. Provenance

| Item | Value |
| --- | --- |
| Installed release | `v26.901.51231-patched` |
| Installed asset digest | `sha256:e151b96a5c0153d0b157b3040c932fb6411d84a111e74be51920dc3bde7eff68` |
| Bundled sidecar source | `openai/codex@6af345407d9c2a568da9d01b6c4b81a9e61495c0` |
| Patch V commit (this repo) | `9c228c9` on `main`, pushed |
| Patch V CI | run `34210035364` **success**, job finished 2026-09-08T10:12:10Z |
| Published asset (same tag replaced in place) | `CodexDesktop-Patched-win-x64-v26.901.51231-patched.zip`, `sha256:d41723c618b4060def39eec0e5fd78753aefcd13cc0c70cd95f89dc24164fea3`, 791,841,359 bytes |
| Sidecar source actually built by that CI | `openai/codex@main` resolved to `d6489472f3c15e87d2d7763a5fde033545c530f8` |
| Local installed state | **unchanged**: `resources/codex.exe` still 2026-09-06T06:16:14, SHA-256 `D2D57E9A...213255`, `.sidecar-source` still `6af3454`; processes not restarted |
| Latest upstream `openai/codex` inspected | `d6489472f3c15e87d2d7763a5fde033545c530f8` (2026-09-08T05:57:24Z) |
| Gateway | CLIProxyAPI `v7.2.154` / `ba7e558`, plugin `deepseek-vision v0.3.1` / `fb30406` |

Publish state and local installed state are different facts: the release asset was
**not** replaced by anything in this round, and no process was stopped or restarted.

Owner-location correction applied: this machine is `10.11.1.1`. All 22 measured HTTP
413s came from `10.11.1.3`. The mechanism is therefore source-proven, but **this
owner's own occurrence is not in the measured set and stays unpaired** until we
correlate it.

## 1. What the 413 number actually measures

`SOURCE` (our lane, revision `6af3454`, same shape at `d6489472`):

1. `ModelClient::build_responses_request` (`core/src/client.rs`) constructs the final
   `ResponsesApiRequest`.
2. `codex-api/src/endpoint/responses.rs:115` encodes it with
   `EncodedJsonBody::encode`.
3. `http-client/src/request.rs:15-29` shows that encode is exactly
   `serde_json::to_vec`, and `as_bytes()` returns those bytes.
4. Request compression for the Responses path comes from
   `responses_request_compression` (`core/src/client.rs`), which requires
   `enable_request_compression && auth.uses_codex_backend() && provider.is_openai()`.
   Our `[model_providers.cliproxy]` is a custom provider with an API key, so
   `RequestCompression::None`.
5. `supports_websockets` defaults to `false` for a custom provider
   (`model-provider-info`), so the request is plain `POST /v1/responses` SSE — which
   matches the CPA-side records, all of which are HTTP 413 on `POST /v1/responses`.

`ARITH` therefore: on the CPA lane the number of JSON bytes the client holds after
step 2 **is** the HTTP body length. No transport encoding, no chunk re-encoding, no
compression. So a client-side byte measurement at step 3 is authoritative for the
body that arrives at the gateway's HTTP boundary.

### 1.1 Correction after the planner forward (this changes what is derivable)

`SOURCE` (planner + CPA lane, verified independently):

- The interceptor fan-out at `adapters_interceptors.go:115-119` has **no**
  model / image / format / `target_models` predicate.
- `acquireABIAdmission` (`main.go:149`) runs **before** `handleMethod` (`:166`), i.e.
  before the plugin's own target-model filter. Deployed `target_models` are three
  text-only models and `gemini-3.8-flash` appears only in `vision_fallback_models`.
  Consequence: a **text-only, non-target** request can still be 413'd before it ever
  reaches pass-through. This closes the "why does the vision plugin see my request"
  question and it is a genuine scope problem in the plugin, not in our bridge.
- Inside CPA, the request body is **cloned and replaced per interceptor**, and CPA
  rewrites thinking fields before the plugin call.
- Accepted requests emit **no** `abi_request_bytes`; there is no paired
  wire-length/ABI-length sample for a request that succeeded.

Therefore the following is now ruled out, and my earlier framing of it is withdrawn:

> `INFERRED` (withdrawn): a fixed multiplier maps client JSON bytes to
> `abi_request_bytes`, so calibration would turn the bracket into a precise ceiling.

What survives is weaker but usable: the client can measure **its own** final
serialized body exactly and deterministically (steps 1-3 above), and that number is
what the preflight must bind to. The gateway-side figure is a *different*,
post-rewrite payload, so the relationship between the two is a distribution to be
bounded conservatively, not a formula to be solved. Any client budget must therefore
sit under the smaller of the two published ceilings with margin, and must be treated
as re-validation-required whenever CPA or the plugin changes version.

`SOURCE` (CPA lane report, plugin `main.go`):

- gate: `acquireABIAdmission` at `main.go:177-201`, comparison `length >
  maxABIBudgetBytes` at `:178`, `const maxABIBudgetBytes = 32 << 20` at `:88`.
  Boundary is exclusive, so the largest admitted envelope is exactly `33,554,432`.
- `maxABIRequestBytes = ((max_configured_body_bytes + 2) / 3 * 4) +
  maxABIEnvelopeOverhead` (`main.go:79`) = `base64(32 MiB) + 4 MiB` =
  `48,933,548`. That number reproduces the observed diagnostic field exactly, which
  is what proves the envelope carries the body as **base64** plus a `<= 4 MiB`
  overhead allowance. `SOURCE`: it is never used in the decision, so it is not a
  second gate.
- `abi_request_bytes` is measured at the plugin ABI boundary, before provider
  selection and before any credential read (proven by the log-line comparison in the
  CPA report).

So the mapping we must respect is:

```text
abi_request_bytes = ceil(J / 3) * 4 + E        with 0 < E <= 4,194,304
accepted          = abi_request_bytes <= 33,554,432
J                 = client-side serialized ResponsesApiRequest JSON bytes
```

`ARITH` on the single measured rejection (`abi_request_bytes = 36,522,680`):

```text
J is bracketed to 23.12 MiB .. 26.12 MiB
  (23.12 MiB if E is the full 4 MiB reserve, 26.12 MiB if E is ~0)
```

`ARITH` on the client budget that provably passes the 32 MiB ABI gate for **any** E
inside the plugin's own reserve:

```text
J <= (33,554,432 - 4,194,304) * 3/4 = 22,020,096  = 21.00 MiB   (worst case)
J <=  33,554,432 * 3/4              = 25,165,824  = 24.00 MiB   (if E were 0)
base64(20 MiB) = 27,962,028 ; base64(21 MiB) = 29,360,128 ; base64(24 MiB) = 33,554,432
```

After section 1.1 these two figures are read as **published ceilings on a serialized
body at the plugin boundary**, not as a target for the client to compute its own
number from. The planner's independent forward reached the same three values
(tier-2 about 21.00 MiB using the plugin's own reserve, absolute 24.00 MiB, tier-1
default 20.00 MiB) by a different route, which is a useful agreement rather than new
evidence about E.

`ARITH` cross-check with tier 1: the plugin's own default
`max_request_bytes = 20 MiB` is *below* the worst-case ABI-safe JSON figure of
21.00 MiB by about 1 MiB. So a `20 MiB` budget is inside **both** gates today without
knowing E, and it is the number the preflight should bind to. The practical
consequence for the gateway side is that raising `max_request_bytes` buys only about
1 MiB before tier 2 re-asserts the same wall, which is why **no CPA config edit is
recommended** in this plan.

`SOURCE` why the earlier 24 MiB suggestion was wrong to hardcode: it is inside the
ABI gate only under the optimistic assumption E is near zero, and it exceeds the
plugin's own tier-1 default, so it would trade the opaque ABI 413 for the actionable
planner 413 without any gain.

## 2. Where the growth comes from

`MEASURED` on this machine (`10.11.1.1`), sizes only, no content read or copied:

| Rollout | File | Last compaction boundary | Records after it |
| --- | --- | --- | --- |
| planner thread `01a067db` | 91.95 MiB | 22 compactions recorded | ~2.51 MiB of records after it, of which 0.25 MiB `message` and 0.07 MiB `function_call_output` |
| `01a07f81` (6.85 MiB, 279 records) | 6.85 MiB | one `compacted` | 2.66 MiB messages (2.34 MiB text), 2.47 MiB `event_msg`, 1.46 MiB compacted payload |
| whole planner file, cumulative | 90.82 MiB records | - | `event_msg` 22.43, `function_call_output` 20.45, `session_meta` 15.57, `reasoning` 7.57, `compacted` 7.57, `message` 6.17, `function_call` 5.71 |

Two consequences, both `MEASURED`/`ARITH`:

1. Rollout file size is an **upper bound on the log**, not on the request. A 92 MiB
   thread replays roughly a few MiB because compaction replaced the earlier
   context. Text-dominated sessions on this machine sit about an order of magnitude
   below the 20 MiB budget.
2. Neither file inspected contains a single `data:image` payload (`MEASURED`:
   `dataurl = 0 MiB`), and both stay small. So on `10.11.1.1` there is no local
   composition evidence yet, consistent with the absence of any local 413.

`SOURCE` the asymmetry that makes bytes and tokens diverge — this is the causal
mechanism, independent of any one session:

- `context_manager/history.rs:812-880` estimates every item from its serialized JSON
  bytes and then **subtracts the real base64 payload** and substitutes a fixed
  per-image estimate: `RESIZED_IMAGE_BYTES_ESTIMATE = 7,373` bytes
  (`history.rs:818`), i.e. about `1,844` tokens per image at the 4-bytes/token
  heuristic.
- For `detail: "original"` the substitute is a patch count capped at
  `ORIGINAL_IMAGE_MAX_PATCHES = 10,000` (`history.rs:~835`), still tiny in bytes.
- The real payload is allowed to be enormous:
  `MAX_PROMPT_IMAGE_INPUT_BYTES = 1 GiB` and `MAX_DIMENSION = 2048`,
  `ORIGINAL_DETAIL` limits `6000` px / `10,000` patches
  (`utils/image/src/lib.rs:26,31,75-83`). The code comment calls the 1 GiB value "a
  high sanity guard ... not a target upload size".
- The same discount applies to images inside tool outputs
  (`history.rs:1000-1010`), which is where Codex Desktop's Computer Use screenshots
  land.

`ARITH` amplification table (per-image token estimate is constant at ~1,844 while
wire bytes are not):

| Per-image base64 | Estimated tokens/image | Images needed to reach 20 MiB | Estimated tokens at that point |
| --- | --- | --- | --- |
| 50 KiB | 1,844 | ~410 | ~755k (already over most compaction limits) |
| 150 KiB | 1,844 | ~137 | ~252k |
| 300 KiB | 1,844 | ~68 | ~126k |
| 1 MiB | 1,844 | ~20 | ~37k |
| 3 MiB | 1,844 | ~7 | ~13k |

Read the last two rows together: a thread can exceed the 20 MiB budget with
**7-20 screenshots** while the estimator believes it is using 13k-37k tokens, i.e.
nowhere near a 334.8k-token auto-compaction limit for GPT-5.6. That is the concrete
proof that token-safe is not byte-safe, and it explains why nothing in the current
code reacts to this failure.

`INFERRED` and explicitly **not** claimed: that images dominated the 22 rejections on
`10.11.1.3`. Composition of those requests was never measured, and no body was read.
The table above shows the mechanism exists and is cheap to reach; it does not show
which field carried the observed 23-26 MiB. Section 4 is the step that decides it.

## 3. Existing client behaviour on 413 (why it retries forever)

`SOURCE` at the installed revision:

- `codex-api/src/api_bridge.rs:169-183`: any non-special status, including 413,
  becomes `CodexErr::UnexpectedStatus`.
- `protocol/src/error.rs:397-406`: `UnexpectedStatus(_)` is in the **retryable**
  group.
- `session/turn.rs:1438-1518`: sampling loop retries while `is_retryable()`;
  `compact_remote_v2.rs:379-413` does the same for compaction, and
  `compact_remote.rs:403-...` / `should_retry_with_current_model` also treat
  `UnexpectedStatus` as retryable.

So one mapping fix at `api_bridge.rs` + `is_retryable` repairs all three loops at
once; there is no need to touch the retry loops themselves. Rejection is pre-provider
and pre-credential, so nothing server-side is corrupted; the cost of the current
behaviour is pure wasted work plus an unhelpful UI state.

`SOURCE` compaction cannot rescue an oversized thread today:

- pre-turn compaction fires only on `token_status.token_limit_reached`
  (`context_window.rs:104-119`, `turn.rs:1097-1105`) — a token test.
- the compaction request is built by cloning history and replaying it through the
  same provider and same serialization
  (`compact_remote_request.rs:30-92` -> `client.rs:580`).
- its only byte-ish reduction, `trim_function_call_history_to_fit_context_window`
  (`compact_remote.rs:403-...`), runs only when estimated tokens exceed the model
  context, rewrites **tool output text** only, and never touches `InputImage`
  payloads or encrypted reasoning.
- compaction v2's retained-image budget is also in tokens
  (`compact_remote_v2.rs:491-513`), so images survive compaction as far as bytes are
  concerned.

## 4. Measurement plan (answer to "can the client know before sending")

Yes, exactly and cheaply. Steps, all read-only or behind temporary instrumentation
that requires separate approval:

1. **Total, authoritative**: log `EncodedJsonBody::as_bytes().len()` at
   `codex-api/src/endpoint/responses.rs` `stream_request`. `SOURCE` proves this equals
   the HTTP body length on our route, so this is the number to publish to the owner.
2. **Per top-level field**: in `build_responses_request`, after the struct exists, use
   the existing non-retaining helper `serialized_json_bytes`
   (`core/src/utils/json.rs:15-21`) on `model`, `instructions`, `input`, `tools`,
   `reasoning`, `text`, `client_metadata`, `include`, `stream_options`. Numbers only.
3. **Inside `input`**, bucket by variant using the same helper plus the existing
   `parse_base64_image_data_url` (`history.rs:~925`): message text, image data-URL
   payload bytes and count, `function_call` arguments, `function_call_output`
   text vs image content items, reasoning `encrypted_content`.
4. **Cost control**: measure exactly (step 1) always, since it is a length read of an
   already-materialized buffer; compute the field breakdown only when that total is
   above a low watermark (for example 50% of budget). Avoids a second full
   serialization on every small request.
 5. **Conservatism check with the CPA lane, not a multiplier.** Section 1.1 rules out
    solving for E, so the only honest version of this step is a *bound*, not a fit:
    send a deterministic payload with no secret and no user content from `10.11.1.1`,
    record our J, and ask the CPA lane for `abi_request_bytes` on the same request id.
    Because accepted requests emit no `abi_request_bytes`, a pair exists only when the
    request is rejected. That makes the usable experiment: push deliberately past the
    wall at a few sizes (for example J just under 20 MiB, just over 20 MiB, and near
    21 MiB), and read off the smallest J that ever gets rejected. If any J at or below
    our budget is rejected, the budget is wrong and must come down. The preflight is
    safe to ship **before** this step exists, because it binds to our own measured
    bytes and never to a CPA-derived figure; this step only tells us whether the
    margin is adequate or can be relaxed later.
 6. **Pair the owner's local occurrence**: from `10.11.1.1` logs, take our recorded J,
    thread id and timestamp and match against CPA 413 records for this host. Until a
    match exists, keep saying "unmeasured locally" rather than reusing the 10.11.1.3
    figures.

## 5. Preflight design (no dead threads)

Proposed behaviour, one new knob, three code sites. Nothing here is applied now.

- Binding quantity (planner design constraint, adopted): the **client's own final
  serialized request-body bytes**, measured where section 4 step 1 measures. Not the
  wire figure, and not any inferred ABI figure. Budget: **strictly under 20 MiB with
  margin**, which is below both the plugin's tier-1 default and the 21.00 MiB
  worst-case tier-2 serialized ceiling.
- Configuration: a **per-provider** byte budget, mirroring the plugin's own term, e.g.
  `[model_providers.cliproxy] max_request_bytes = 20971520`. Keys of this kind are
  only honoured inside the provider block (established previously for
  `stream_max_retries`), so the same placement rule applies. Unset means "no client
  preflight", so the patch is a no-op for every other user of this build.
  The value is a client-side safety margin against a ceiling we cannot derive
  (section 1.1), so it stays documented as provisional and version-dependent: it must
  be re-checked whenever CLIProxyAPI or `deepseek-vision` changes version.
- Check site: right where section 4 step 1 measures, i.e. after final serialization
  and before the transport call, so instructions, tool schemas, Responses Lite prefix,
  metadata and provider transforms are all included. A check earlier would under-count.
- Refusal: return a dedicated non-retryable error carrying measured bytes, budget,
  provider id and endpoint, and set `is_retryable() = false`. This also converts the
  gateway-side 413 (if it still happens, e.g. because CPA's rewritten body grew past
  the wall even though ours fit) into the same non-retryable class instead of
  `UnexpectedStatus`.
- State: on refusal the turn ends before any provider call. The user's input is
  already part of history, so it is not lost and the next attempt includes it;
  pending input and task state remain intact. The error must be presented as a turn
  error with explicit actions, not as a generic failure.
  Pending user input in particular must survive the refusal; losing a drafted prompt
  would turn a transport problem into a data-loss problem.
- UI: actionable message naming the measured size and the budget, and offering the
  recovery actions in section 6. The existing context-window surface stays; a byte
  indicator appears only when the provider declares a byte budget, so token and byte
  pressure are never conflated.
- Do **not** treat transport compression as a mitigation: `SOURCE` shows it is
  disabled for this provider anyway, and `INFERRED` (CPA report) the limit applies to
  the decoded, re-marshalled payload.

## 6. Can an already oversized thread be recovered? Plain answer

**With today's shipped code: partially, and only via two existing paths that do not
require a provider call. No, compaction will not save it.**

What exists and works (user-visible, no database editing):

1. **Revert / rollback turns.** `app-server/src/request_processors/thread_processor.rs`
   implements `Op::ThreadRollback { num_turns }`, and the Desktop renderer already
   calls `thread/rollback` with `numTurns` for its revert actions. `MEASURED` (string
   inspection of the installed `app.asar`): the `thread/rollback` method and
   `thread/compact/start` are both present in the shipped bundle. This removes whole
   turns and therefore whole oversized items. Cost: the reverted turns' content is
   gone from the active context; the user chooses how many.
2. **One-turn switch to a text-only model.** `normalize_history` calls
   `strip_images_when_unsupported` / `strip_audio_when_unsupported`
   (`context_manager/history.rs:706-720`), and `for_prompt` is on the sampling path
   (`session/turn.rs:1460`) as well as the compaction path
   (`compact_remote_request.rs:61`). Selecting a model whose `input_modalities` lack
   `image` therefore removes inline media **from the request only**; stored history is
   untouched. If images are the oversized category, this is a real unblock, and it is
   visible to the owner (the model simply cannot see the images that turn). It also
   doubles as a diagnostic: if a text-only model succeeds where the vision model got
   413, media was dominant.

What does not work, and should not be promised:

- Manual or automatic compaction, because the compact request replays the same
  oversized history through the same gate (section 3).
- The existing output trim, because it is token-gated and never reduces image
  payloads (section 3).
- Any claim that one single item larger than the budget can be salvaged in place. If a
  turn holds, say, a 25 MiB payload by itself, no pairing-preserving reduction fits it,
  and the honest outcome is revert or start a bounded new thread.

`UNOBSERVED`, stated plainly per the planner forward: whether any specific oversized
thread can actually be brought under the wall has never been demonstrated end to end.
Paths 1 and 2 above are `SOURCE`-proven **mechanisms** (the code does what the section
says), not verified recoveries of a real rejected thread. Nobody should tell the owner
"revert or switch model will fix it" as a fact until one observed case is walked
through with the measured before/after byte figures on both lanes.

Proposed byte-bounded reduction (new, needs its own approval round). Deterministic,
pairing-preserving, ordered, and only committed after success:

1. Work from the exact per-item measurement in section 4, grouped with the existing
   `history_item_groups`, so a `function_call` and its output are never split.
2. Tier 1 - truncate eligible tool-output text with the existing
   `truncated_output_payload` helper, preserving `call_id`, `name`, `namespace` and
   `success`.
3. Tier 2 - replace the **oldest** retained inline image payloads with an explicit
   elision notice, following the precedent of `IMAGE_TOO_LARGE_PLACEHOLDER`
   (`image_preparation.rs:27`) so the model is told an image was elided instead of the
   item silently shrinking.
4. Tier 3 - run compaction over a byte-budgeted suffix window (newest turns first,
   plus existing summary) so the compact request itself fits, and replace persisted
   history only after that bounded call succeeds.
5. Stop condition - if the plan still cannot fit, return the non-retryable
   size error naming the dominant category and its byte count. Never invent IDs, never
   pair by proximity, never drop an output without its call, never rewrite history on
   a failed attempt.

Controlled clean-thread fallback when no reduction fits: export a reviewed summary of
the context into a new thread through the existing handoff/review path, with the owner
told exactly what is lost (raw tool outputs, images, and the malformed rows), and
without replaying the oversized or malformed history. This is a last resort, not an
automatic repair.

## 7. Upstream duplication check and external report

`SOURCE` at `d6489472` (latest upstream `main`): no request byte preflight, no 413
handling. Searching `codex-rs` for `TOO_LARGE`, `payload_too_large`,
`request_too_large` or a byte-budget preflight returns only per-tool-output
`byte_budget()` in `core/src/tools/context.rs:450-527`, image placeholders, and the
1 GiB image input sanity guard. Nothing else on the installed revision either.
Conclusion: our patch does not duplicate a shipped upstream fix. The future
implementation should still carry a regression assertion that upstream has no 413
arm, so that if OpenAI adds one we notice and retire ours.

Draft for `github.com/zesuy/Plugin-Deepseek-Vision` — **prepared for owner approval,
deliberately not posted**:

> **Two issues with the ABI admission rejection path in v0.3.1 (`fb30406`)**
>
> 1. `traceABIAdmission` reports `abi_request_limit_bytes` (48,933,548 derived from
>    `main.go:79`) although the decision at `main.go:178` uses
>    `maxABIBudgetBytes` (33,554,432). The reported figure implies 46.67 MiB of
>    headroom that the gate does not grant, and the field name
>    `abi_process_budget_bytes` is used for the *request* check
>    (`limit_kind=request_budget`), which is the reverse of the naming used by the
>    aggregate check at `main.go:192`. Either enforce the reported limit or rename it
>    so operators do not read a non-existent ceiling.
> 2. Because the ABI check runs first, a client body above `max_request_bytes` but
>    whose base64 envelope exceeds 32 MiB gets the non-configurable ABI 413 instead of
>    the actionable planner message. Including the configured limit and the remedy in
>    the 413 body (or letting tier 1 surface) would make this self-explanatory.
> 3. Minor: the comment at `main.go:80-87` refers to a "configured 100 MiB logical
>    body ceiling" while the code ceiling is 32 MiB.
>
> Measured case: `abi_request_bytes=36,522,680`,
> `abi_process_budget_bytes=33,554,432`, `abi_in_flight_bytes=0`. We are not asking
> for the 32 MiB constant to be raised; we understand it is the pre-decode
> amplification guard behind `max_inflight_vision_requests: 4`.

Explicitly out of scope this round: disabling or bypassing `deepseek-vision`, forking
or vendoring it, and requesting a budget increase before quantifying what raising the
constant costs in memory.

`SOURCE` (planner forward) one more scope point worth putting in that report: because
admission runs before target-model filtering and the interceptor fan-out carries no
predicate, the plugin can reject request shapes that have nothing to do with vision.
That is a plugin-side scope question, and it is the strongest argument in the draft.

## 8. Patch inventory and the smallest durable boundary

Current registered patch set in this repo:

```text
A  B  B2  C(v3)  D  G  H  I  J  K  L  M  N  O  P  Q  R  S  T  U  V
```

- Renderer/asar lane: A, C, D, G, H, J, K, L, M, O, P, Q, R, S, T, U, B2.
- Source-built sidecar lane (`resources/codex.exe`): I, N, V.
- Patch V (this round's implementation lane) is committed at `9c228c9`; its CI build
  was still running when this document was written, so it is **not** in any installed
  artifact yet.

The 413 work is a **new sidecar-lane patch**, proposed name Patch W, following exactly
the existing mechanism (source patcher registered in
`.github/workflows/auto-repatch-release.yml` before the Cargo build, fail loudly on
anchor drift, idempotent through a marker, verify-only mode, tests run in CI like
Patch V's). Nothing about it belongs in the asar lane.

Smallest durable boundary that removes the owner-visible pain, in two independently
approvable slices:

| Slice | Files (upstream paths) | Solves |
| --- | --- | --- |
| W1 — classify and refuse | `codex-api/src/api_bridge.rs`, `protocol/src/error.rs`, `core/src/client.rs` (preflight + measurement), `core/src/config` (provider `max_request_bytes`), `core/src/model_provider_info` if the key lives there | retry storm, opaque failure, dead-thread-by-retry |
| W2 — recover in place | `core/src/compact_remote_request.rs`, `core/src/compact_remote.rs`, `core/src/context_manager/history.rs`, `app-server` error surface for the actionable UI state | salvaging an existing oversized thread without losing pairings |

W1 is the recommendation for the next implementation round because it is small, it is
provably safe at 20 MiB without knowing E, and it converts an infinite retry into one
clear message. W2 needs the calibration from section 4 first.

## 9. Tests required before any W implementation is accepted

1. Boundary lock against the plugin semantics: budget checks at 33,554,431 /
   33,554,432 / 33,554,433 ABI-equivalent values, so an inclusive-vs-exclusive drift
   fails loudly.
2. Token-safe but byte-oversized fixture (few large inline images, low estimated
   tokens) produces a **local** refusal and **zero** provider calls.
3. Normal small request unchanged, including no extra serialization pass beyond the
   cheap length read.
4. `function_call` / output pairs remain paired and ordered after every reduction
   tier; a fixture with multiple parallel calls per turn.
5. The compaction request itself respects a lower byte budget, and a failing
   compaction leaves persisted history byte-identical.
6. Deterministic 413 is attempted exactly once by the sampling loop and once by
   remote compaction.
7. Error text and payload expose measured bytes, budget, provider and dominant
   category, and no request content.
8. Unset `max_request_bytes` keeps behaviour byte-for-byte identical to upstream
   (no-op guarantee for other users of the build).
9. Regression assertion that upstream still has no 413 arm, so Patch W gets retired
   rather than duplicated.

## 10. Open questions, stated as unknowns

1. Actual value of E, and whether CPA re-marshals the JSON body in a way that changes
   its byte length. Bracket only until section 4 step 5 runs.
2. Which field carried the 23-26 MiB in the `10.11.1.3` rejections. Unmeasured; no
   claim made.
3. Whether the owner's own 413s are the same ABI magnitude. Unpaired.
4. Whether the `10.11.1.3` sessions are Computer Use threads (image-in-tool-output
   hypothesis) or a different shape. Not established from our side.
5. Which model selections reach the vision interceptor, and whether text-only
   sessions are being intercepted at all. That is a CPA/plugin scope question; the
   Desktop lane cannot answer it and should not guess.
6. Whether the plugin's `emergency_max_images_per_request: 256` interacts with our
   history at all. Hypothesis in the CPA report, not a cause.
7. Whether upstream will add a byte preflight before our next release, which would
   replace W1.

## 11. Files touched in this round

None, except this document. Working tree intentionally still carries the unrelated
untracked items `runtime/Diagnose-Codex-Desktop.ps1` and `tmp_scripts/`, which are not
part of any lane here.

This document is left **untracked on purpose** because the round is read-only; commit
it with the W1 implementation round if the owner prefers it versioned.
