"""Owner-only Streamlit operations screen; all writes require a visible action."""
from datetime import date, datetime, time, timedelta
from uuid import uuid4

import streamlit as st

from . import store
from .operations import (JST, STAGES, acknowledgements, billing_alerts, customer_links,
                         events, financials, message_key, new_event, task_board, unacknowledged)
from .product_catalog import PRODUCTS


def _save(uid, kind, data, *, event_id=""):
    store.append_ops_event(new_event(uid, kind, data, event_id=event_id))
    st.cache_data.clear()
    st.rerun()


def render():
    st.subheader("対応と売上")
    st.caption("公式LINEの手動対応・納品は記録して初めて反映されます。表示だけで送信や請求は行いません。")
    if st.button("最新の記録を読み込む", key="ops_refresh"):
        st.cache_data.clear()
        st.rerun()
    rows = store.list_ops_events()
    users = {str(u["user_id"]): dict(u) for u in store.list_line_users()}
    label = lambda uid: f'{users.get(uid, {}).get("display_name") or "名称未登録"} · {uid[-6:]}'
    today = datetime.now(JST).date()
    with st.expander("売上・作業時間", expanded=True):
        c1, c2 = st.columns(2)
        start_date = c1.date_input("集計開始", value=today.replace(day=1))
        end_date = c2.date_input("集計終了（この日を含む）", value=today)
        if end_date < start_date:
            st.error("終了日は開始日以降にしてください。")
        else:
            report = financials(rows, datetime.combine(start_date, time.min, JST),
                                datetime.combine(end_date + timedelta(days=1), time.min, JST))
            cs = st.columns(4)
            cs[0].metric("記録済み入金", f'¥{report["receipts"]:,}')
            cs[1].metric("Stripe返金", f'¥{report["refunds"]:,}')
            cs[2].metric("入金 − Stripe返金", f'¥{report["net"]:,}')
            cs[3].metric("記録済み作業時間", f'{report["minutes"] / 60:.1f} 時間')
            st.caption("連携開始後のStripe本番決済と、確認して手入力した入金のみ。"
                       "過去の未登録取引・手動決済の返金・手数料・経費は含みません。利益や総売上ではありません。")
            if report["unlinked"]:
                st.info(f'LINEの顧客に未紐付けのStripe入金が {report["unlinked"]} 件あります。合計には含めています。')
            if report["other_currency"]:
                st.warning("円以外の取引は集計対象外：" + ", ".join(report["other_currency"]))

    tasks = task_board(rows)
    open_tasks = [t for t in tasks if t.get("status") != "done"]
    st.write(f"対応待ち：{len(open_tasks)}件")
    if open_tasks:
        st.dataframe([{"顧客": label(t["user_id"]), "対応": t["title"], "工程": t["stage"],
                       "期限": t.get("due_at", ""), "メモ": t.get("note", "")} for t in open_tasks],
                     hide_index=True, use_container_width=True)
    with st.expander("対応を登録する"):
        if users:
            with st.form("ops_new_task"):
                uid = st.selectbox("顧客", list(users), format_func=label, key="task_uid")
                title = st.text_input("必要な対応")
                stage = st.selectbox("工程", STAGES)
                due = st.date_input("期限", value=today + timedelta(days=1))
                note = st.text_input("店主向けメモ")
                if st.form_submit_button("対応を登録"):
                    if title.strip():
                        _save(uid, "task.set", {"task_id": str(uuid4()), "title": title.strip(),
                              "stage": stage, "due_at": due.isoformat(), "note": note, "status": "open"})
                    st.error("必要な対応を入力してください。")
    if tasks:
        with st.expander("対応の工程・期限・完了状態を変更する"):
            chosen = st.selectbox("対応記録", tasks, format_func=lambda t: f'{label(t["user_id"])} / {t["title"]} / {t["status"]}')
            with st.form("ops_edit_" + chosen["task_id"]):
                stage = st.selectbox("次の工程", STAGES, index=STAGES.index(chosen["stage"]))
                status = st.selectbox("状態", ["open", "done"], index=int(chosen["status"] == "done"),
                                      format_func=lambda x: "完了" if x == "done" else "対応待ち")
                due = st.date_input("新しい期限", value=date.fromisoformat(chosen["due_at"]))
                note = st.text_input("メモ", value=chosen.get("note", ""))
                if st.form_submit_button("変更を記録"):
                    _save(chosen["user_id"], "task.set", {**chosen, "stage": stage,
                          "status": status, "due_at": due.isoformat(), "note": note})

    with st.expander("LINEで手動対応した相談を記録する"):
        st.caption("直近100件のログを確認します。公式LINEで対応した範囲の最終メッセージまでを対応済みにします。")
        if users:
            uid = st.selectbox("LINEの顧客", list(users), format_func=label, key="ack_uid")
            chats = [dict(c) for c in store.recent_line_chats(uid, limit=100)]
            # Backends return recent history in chronological order.
            pending = unacknowledged(chats, acknowledgements(rows).get(uid))
            if pending:
                last = st.selectbox("ここまで手動で対応した", pending,
                     format_func=lambda c: f'{c["created_at"]} / {str(c["text"])[:100]}')
                st.text(last["text"])
                note = st.text_input("対応内容のメモ（任意）", key="ack_note")
                if st.button("選んだ受信まで対応済みと記録", key="ack_save"):
                    covered = pending[:pending.index(last) + 1]
                    _save(uid, "reply.ack", {"chat_id": str(last["id"]), "chat_keys": [message_key(c) for c in covered],
                                           "note": note, "channel": "LINE公式・手動"})
            else:
                st.info("直近の受信に未対応候補はありません。")

    with st.expander("作業時間を記録する"):
        with st.form("ops_work"):
            uid = st.selectbox("対象", [""] + list(users), format_func=lambda x: label(x) if x else "運営全体")
            category = st.selectbox("作業", ["相談返信", "鑑定作成", "納品・事務", "集客", "その他"])
            minutes = st.number_input("所要時間（分）", min_value=1, max_value=1440, value=10)
            if st.form_submit_button("時間を記録"):
                _save(uid, "work.log", {"category": category, "minutes": int(minutes)})

    with st.expander("STORES・銀行振込などの入金を確認して記録する"):
        st.caption("Stripeは自動連携と重複するため、ここには入力しません。決済の管理画面と金額を照合してください。")
        if users:
            with st.form("ops_payment"):
                uid = st.selectbox("購入者", list(users), format_func=label)
                provider = st.selectbox("決済元", ["STORES", "銀行振込"])
                reference = st.text_input("注文番号・取引ID")
                product = st.selectbox("商品", list(PRODUCTS), format_func=lambda p: PRODUCTS[p]["name"])
                amount = st.number_input("確認した実入金額（円）", min_value=1, step=1)
                paid_date = st.date_input("入金日", value=today, max_value=today)
                verified = st.checkbox("決済の管理画面で入金済みと確認した")
                if st.form_submit_button("入金を記録"):
                    active = events(rows)
                    voided = {r["data"].get("event_id") for r in active if r["kind"] == "payment.void"}
                    duplicate = any(r["kind"] == "payment.manual" and r["id"] not in voided
                                    and r["data"].get("provider") == provider
                                    and r["data"].get("reference") == reference.strip() for r in active)
                    if not verified or not reference.strip():
                        st.error("取引IDと入金確認が必要です。")
                    elif duplicate:
                        st.error("この取引は記録済みです。")
                    else:
                        record = new_event(uid, "payment.manual", {"provider": provider,
                            "reference": reference.strip(), "product": product, "amount_yen": int(amount)},
                            created_at=datetime.combine(paid_date, time(12), JST).isoformat())
                        store.append_ops_event(record)
                        st.rerun()
            recorded = [r for r in events(rows) if r["kind"] == "payment.manual"]
            if recorded:
                wrong = st.selectbox("誤入力を取り消す場合", recorded,
                       format_func=lambda r: f'{r["data"]["provider"]} / {r["data"]["reference"]} / ¥{r["data"]["amount_yen"]:,}')
                if st.button("この記録を誤入力として取り消す"):
                    _save(wrong["user_id"], "payment.void", {"event_id": wrong["id"]})

    with st.expander("Stripe連携・決済の対応待ち"):
        receipts = [r for r in events(rows) if r["kind"] == "stripe.receipt"]
        live = [r for r in receipts if r["data"].get("livemode") is True]
        st.write(f"本番イベント {len(live)} 件 / テストイベント {len(receipts) - len(live)} 件")
        alerts = billing_alerts(rows)
        st.caption("支払い失敗や解約の記録を表示します。会員の利用権限は自動変更しません。対応は上の一覧へ登録できます。")
        if alerts:
            st.dataframe([{"Stripe顧客": a.get("customer_id"), "対象": a["object_id"],
                          "状態": a.get("status", a["type"]), "期末解約": bool(a.get("cancel_at_period_end"))}
                         for a in alerts], hide_index=True, use_container_width=True)
        customers = sorted({r["data"]["customer_id"] for r in live if r["data"].get("customer_id")})
        if customers and users:
            links = customer_links(rows)
            with st.form("ops_link"):
                customer = st.selectbox("Stripeの顧客ID", customers)
                uid = st.selectbox("照合したLINEの顧客", list(users), format_func=label)
                check = st.checkbox("決済管理画面とLINEを照合し、同一人物と確認した")
                if st.form_submit_button("紐付けを記録"):
                    if links.get(customer) and links[customer] != uid:
                        st.error("別の顧客と紐付け済みです。管理者が記録を確認してください。")
                    elif check:
                        _save(uid, "customer.link", {"customer_id": customer})
                    else:
                        st.error("同一人物の確認が必要です。")
