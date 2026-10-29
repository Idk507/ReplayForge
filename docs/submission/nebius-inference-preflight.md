# One-call Nebius Token Factory inference checkpoint

Prepared 2026-10-29. **No call was made.** A separate explicit scoped approval
is required before the one billable inference request.

## Local integration inspected

- `packages/providers/nebius.py` validates the API key without logging it,
  restricts HTTPS destinations to the Token Factory hosts, and disables proxy
  environment trust and automatic retries.
- `packages/providers/triage.py:request_triage` submits one JSON-mode chat
  completion, uses `store: false`, temperature zero, no tools, a 30-second
  timeout, and a 1,200-token completion ceiling. It returns a typed proposal;
  the result remains untrusted model output and must not be executed.
- `packages/providers/request_identity.py` hashes the exact canonical request
  bytes into a `sha256:` request identity. A `before_submit` callback can retain
  this identity. The caller can record UTC time and elapsed latency without
  recording a credential or raw request headers.
- The local protected config was validated offline: model
  `nvidia/nemotron-3-super-120b-a12b`, endpoint
  `https://api.tokenfactory.us-central1.nebius.com/v1/`, key present. The key
  value was not printed. This does not authenticate it with the provider.

## Exact bounded request proposed

One POST to
`https://api.tokenfactory.us-central1.nebius.com/v1/chat/completions` through
the existing `request_triage` client, with no catalog GET, no retry, no
repository, files, customer data, sandbox operation or follow-up inference.

The exact fixed user context is:

> Synthetic demonstration only: a fictional online checkout retries a payment
> after a timeout and one order is charged twice. No repository, source files,
> or customer data are supplied. Suggest one plausible cause and a regression
> test; explicitly say no code was inspected and no test was run.

The existing system prompt requires a JSON triage proposal. Any returned test
text is inert and must be labeled model-generated and unverified. It must not
be run. The capture should retain the request SHA256 identity, model ID,
endpoint, UTC timestamp, elapsed latency, successful HTTP status, returned
proposal and token usage when supplied by the API; credential-bearing headers
and raw provider error bodies must be omitted.

## Cost estimate and authorization boundary

Nebius currently lists this model at $0.30 per million input tokens and $0.90
per million output tokens. The exact prepared JSON request body is 1,591 bytes;
with the 1,200-token output cap, a conservative byte-based estimate is about
$0.00157 before taxes, billing rounding or credits. Pricing and account access
can change; the estimate is not a provider quote or hard billing guarantee.
The proposed authorization ceiling is **one request and USD $0.01 maximum**;
stop before sending if the current published rate or account terms differ.

**Approval checkpoint:** authorize one fixed synthetic `request_triage` POST
to the model and endpoint above, no more than 1,200 completion tokens, no
retries, and a USD $0.01 ceiling. Without this scoped approval, remain offline.

Official model pricing: https://nebius.com/services/token-factory/models/nvidia-nemotron-models-inference
