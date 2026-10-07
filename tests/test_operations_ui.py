from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_owner_can_create_complete_and_reopen_task(tmp_path, monkeypatch):
    from src import store_sqlite, store
    monkeypatch.setattr(store_sqlite, "DB_PATH", tmp_path / "ui.db")
    for name in ("init_db", "list_ops_events", "append_ops_event", "list_line_users", "recent_line_chats"):
        monkeypatch.setattr(store, name, getattr(store_sqlite, name))
    store_sqlite.init_db()
    store_sqlite.upsert_line_user("u_test", display_name="表示確認用")
    app = AppTest.from_string("from src.operations_ui import render\nrender()", default_timeout=30).run()
    assert not app.exception
    next(w for w in app.text_input if w.label == "必要な対応").input("納品状況を確認")
    next(w for w in app.button if w.label == "対応を登録").click().run()
    assert not app.exception
    assert len(store_sqlite.list_ops_events()) == 1
    next(w for w in app.selectbox if w.label == "状態").select("done")
    next(w for w in app.button if w.label == "変更を記録").click().run()
    assert not app.exception
    assert "対応待ち：0件" in "\n".join(w.value for w in app.markdown)


def test_dashboard_has_only_the_two_views_in_use(tmp_path, monkeypatch):
    """★2026-10-07：「📋 対応と売上」の画面は外した（店主が使うてへん）。

    ★消したんは画面だけ。src/operations.py は残っとる。
      会員相談の画面が、手で返した記録（reply.ack）を読むのに使うからや。
      ★★一緒に消したら、手で返した分が「未返信」に化けて二重に返す。
    """
    import importlib

    from src import store, store_sqlite
    monkeypatch.setattr(store_sqlite, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setenv("APP_PASSWORD", "")
    # ★★★2026-10-07：この画面を【本番のGoogle Sheets】で動かしたらあかん。
    #   ★手元には .env があるんで STORE_BACKEND=sheets になる。
    #     app.py は会員・LINEの記録・控えを何回も読むんで、シートの機嫌次第で
    #     15秒でも60秒でも足りん時がある。★実際に二回こけた。
    #   ★★中身の不具合やのうて、よそのAPIの応答待ちで落ちるテストは、
    #     「赤くなっても誰も見んテスト」になる。それがいちばん質に悪い。
    #   ★★★せやから、ここだけ手元のsqliteに差し替えて走らせる。速うて、毎回同じ結果になる。
    monkeypatch.setenv("STORE_BACKEND", "sqlite")
    importlib.reload(store)
    try:
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"),
                                default_timeout=60)
        app.run()
    finally:
        # ★他のテストに持ち越さん。monkeypatch は環境変数しか戻さん（モジュールは戻らん）
        monkeypatch.undo()
        importlib.reload(store)
    assert not app.exception
    assert app.radio[0].options == ["💬 会員相談", "👥 会員"]
    # ★operations の読み取りは残っとること（会員相談がこれを使う）
    from src.operations import acknowledgements, events, unacknowledged  # noqa: F401


def test_empty_ledger_is_not_presented_as_zero_sales(tmp_path, monkeypatch):
    from src import store_sqlite, store
    monkeypatch.setattr(store_sqlite, 'DB_PATH', tmp_path / 'empty_ledger.db')
    for name in ('init_db','list_ops_events','append_ops_event','list_line_users','recent_line_chats'):
        monkeypatch.setattr(store, name, getattr(store_sqlite, name))
    store_sqlite.init_db()
    app=AppTest.from_string('from src.operations_ui import render\nrender()',default_timeout=30).run()
    assert not app.exception
    assert next(m for m in app.metric if m.label=='記録済み入金').value=='未集計'
    assert any('実際の売上が0円という意味ではありません' in m.value for m in app.info)


def test_recorded_payment_still_displays_amount(tmp_path, monkeypatch):
    from src import store_sqlite, store
    from src.operations import new_event
    monkeypatch.setattr(store_sqlite, 'DB_PATH', tmp_path / 'recorded_ledger.db')
    for name in ('init_db','list_ops_events','append_ops_event','list_line_users','recent_line_chats'):
        monkeypatch.setattr(store, name, getattr(store_sqlite, name))
    store_sqlite.init_db()
    store.append_ops_event(new_event('test','payment.manual',{'provider':'STORES','reference':'verified-test','amount_yen':3980}))
    app=AppTest.from_string('from src.operations_ui import render\nrender()',default_timeout=30).run()
    assert not app.exception
    assert next(m for m in app.metric if m.label=='記録済み入金').value=='¥3,980'
