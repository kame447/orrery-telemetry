# Read-only reconciliation of fresh send outcomes (fork extension)

The fork contract `orrery-fresh-message-receipt-v1` reads a committed send outcome without replaying a request. It is not claimed to be an upstream-standardized operation.

Use `python -m agentstack_mail.fresh_client receipt --context <same-context> --request-id <saved-UUID>` or `await client.receipt(request_id)` with the already prepared protected client. The client reads its owner-private original send request and derives intent/content hashes using the published send handler's defaults. It does not accept externally chosen credentials, senders or instances. Other write operations are unsupported.

The backend tool `get_message_receipt` accepts send_request_id, expected_intent_sha256 and expected_content_sha256 with the fixed protected instance/auth proof. It is not added to the usual provider-facing fresh proxy. The server reads its own store through the existing transaction boundary, not through arbitrary database access.

One read transaction verifies the current instance, sender credentials/generation and retirement state, then selects only that sender's fresh-send receipt. Another sender's UUID does not reveal their receipt. An accepted result additionally requires the stored Message, sender, recipients, thread and matching body/subject/flag content hash. Missing or changed content cannot be called accepted. Reads never send, replay, register, notify, repair or update stored data.

Status is accepted, refused or not_observed. Accepted proves stored Mail acceptance, not agent execution. Refused exposes only a fixed refusal code, not candidate lists or arbitrary details. Not_observed means no record existed in that read snapshot; a concurrent in-flight send may still commit. It is not evidence that sending again under a new UUID is safe.

The response omits bodies, credentials, local paths, storage projects and free-form errors. Content-derived hashes are not intended as publicly shareable metadata. Missing/invalid/foreign local requests, changed authority or inconsistent stored results fail closed.

Existing replay remains a separate explicit operation. Receipt never invokes it or automatically resends after an error or not_observed result. This patch does not restart or convert existing fresh Mail services; the selected service must explicitly support this fork contract.
