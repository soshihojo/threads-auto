# Conversation rollback and retained safeguards

The experimental conversation-quality classifier no longer runs in the customer auto-reply path. Its outputs cannot send a canned closure/complaint/decline, stop replies, or trigger an early offer. The extra free-limit-plus-three stop and the rewritten nurture prompt are removed. The established pre-quality conversation flow is restored, including its existing offer timing and manual/member boundaries.

Retained changes:
- Product routing and the new note URL, agreed prices and delivery terms.
- Deterministic direct price answers.
- Internal handoff text blocked at transport; retired closure/automation-stop templates are also blocked in case they reappear from history.
- Product-classification failure creates a private review task without customer copy or a new persistent hold. Existing unanswered-message retry can recover. Classified refusal sends no new canned refusal and imposes no persistent hold.
- Same-customer serialization, stale-message checks, passive offer records, and the operations dashboard.
- Existing closure/refusal/review markers still suppress proactive follow-ups. Historical holds are not blindly reset; the owner can review and restart them.

The legacy prompt's incorrect delivery start date remains corrected. Instructions to pretend to be human or evade direct identity questions are not restored. No new fallback customer message is introduced.

The quality module is available for offline analysis only. Historical dashboard classifications are not new decisions and are not measured customer satisfaction. Runtime regression tests prove the classifier is not called, two replies cannot trigger the removed early-offer branch, the added ten-reply stop is absent, old markers cannot override an operator restart, and handoff/retired copy cannot reach transport. Product and payment tests remain in place.

This removes the identified newly introduced failure paths; it does not guarantee arbitrary generated text is error-free. No customer test messages are sent.
