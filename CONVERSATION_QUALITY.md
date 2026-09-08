# LINE conversation quality

Version: quality-20260908-v1. Product scope, prices, new note URL, delivery after hearing answers within two business days, and two post-delivery replies are unchanged.

- Before sales, classify corrections, closure, refusal, complaints, and questions about automation. Corrections receive acknowledgement and a revised answer without an offer. Satisfaction plus a closing thank-you is distinguished from a thank-you followed by another question.
- Automation-use questions are held for private owner review. No automatic evasion, denial, disclosure, or internal handoff copy is sent. The owner dashboard exposes these review tasks. Explicit refusal or complaints also pause sales.
- Nurture replies answer the actual question with useful, bounded advice. Only missing information is requested, one question per reply. Up to 200 recent messages are provided so earlier answers are not lost after six turns. Multiple question marks trigger regeneration; a still-invalid draft is not sent.
- Simple price questions bypass both classifiers and use fixed correct prices. A request for advice is not treated as a purchase request. Once needs are understood, the existing product router asks only missing questions. The old repeated hidden-price choice is no longer generated. If the conversation continues to the existing free limit, the reply can include one transparent, priced invitation. Long unresolved conversations become private review tasks rather than repeated pressure.
- Follow-ups require a new recorded awaiting state and one identifiable question. They quote that question once. Legacy conversations are not automatically reactivated. Closed, declined, complaint, and review states are excluded. Same-customer automatic replies and pre-offer follow-ups share process-local locks and check fresh messages before sending.
- Private operations events record versioned states, actual offer sends/products, and follow/unfollow times. The dashboard separates classified outcomes from owner-confirmed reasons and shows seven-day mature offer cohorts with confirmed linked cash. Unrecorded payments are unknown, not assumed non-purchases. This is an offer-origin measure, not registration-origin conversion, and linked cash is not necessarily attributable to the offered product.
- Owner review/resume is in the operations view; an explicit confirmation clears the review marker before automation resumes. Membership permissions and billing are not changed.

## Validation and limits

Offline regression tests cover routing, refusal/closure, corrected speakers, stale and failed sends, price-classifier independence, follow-up repetition, cohort maturity, payment voids, and dashboard rendering. Model-backed synthetic cases supplement deterministic tests; classifications remain probabilistic. Outbound internal-handoff rejection is deterministic. No customer test messages are sent.

Customer text and production analysis remain outside this public repository. No raw model output or customer transcript is written to public logs. Error categories and private review tasks support investigation.
