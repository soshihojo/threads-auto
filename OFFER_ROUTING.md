# Offer routing

The three automatic offer paths in `line_bot._auto_reply` now call the same router. The old unconditional recommendation of Shiomi and generated three-reason upsell have been removed.

The classifier reads the current follow session, including the incoming message. It must cite exact customer text; assistant claims cannot serve as evidence. Classification generates no customer-facing copy, prices or URLs. Fixed copy supplies:

- Individual reading: JPY 3,980, one-off.
- Shiomi: JPY 9,800, one-off, includes the reading plus the 90-day calendar and guide.
- Delivery: within two business days after receiving answers to our questions.
- Post-delivery questions: up to two replies.
- The offer supplement links to https://note.com/tsubaki_honne/n/ne55eb9fcc57c.

Missing situation, goal or scope is asked once per question, with successful sends recorded in the existing chat log. Explicit product preferences skip unnecessary questions. Timing anxiety or a meeting alone must not select Shiomi. A plain yes to the scope choice does not select the expensive product. General price questions get the comparison immediately. Unclear answers after the scope question get a neutral comparison, not repeated questioning or a forced recommendation. Declines stop sales; classifier errors transfer to the owner without a recommendation.

Question replies resume routing before the ordinary purchase-signal and free-diagnosis paths. Questions keep `bot=on`; a successfully sent offer/comparison or handoff sets `bot=hold`. Failed sends do not advance state. Owner holds, new messages, existing offers and membership/minor guards are checked before sending. Striped process-local locks serialize same-customer webhook/sweep routing; they are not a distributed lock for multiple server processes.

Validation: offline tests cover all three entry paths, follow-up answers, duplicate and failed sends, owner holds, customer evidence, ambiguity and exact terms. Synthetic live-model evaluations supplement these tests; language classification remains probabilistic. No live customer messages are sent during verification.

The general draft checker flags the delivery-time sentence. It is retained intentionally: these are sales offers, and the owner explicitly specified this delivery term. No other draft issues were reported.
# 2026-09-08：従来オファーへ復元

店主の指示により、自動の商品分類・商品選択用の追加質問は停止。
`generate_offer` は導入前（`c8854c6^`）の冒頭・個別目次・潮見の理由・二商品メニューを復元し、
note URLだけ `ne55eb9fcc57c` に変更した。新しい分類器 `offer_routing.route` は本番送信から呼ばない。
送信済みの追加質問に回答が来た場合だけ、既存の検出で受け取り、復元したオファーへ進む。
オファーの送信タイミング、送信直前の状態確認・二重送信防止、通常会話の改善は維持。

以下は停止した出し分け機能の記録。
