from types import SimpleNamespace

import pytest

from src import llm
from src.learning_output import write_learning
from src.reading_summary import FIELDS, page, validate


@pytest.mark.parametrize("cache", [False, True])
def test_truncation_rejected_in_both_llm_paths(monkeypatch, cache):
    msg = SimpleNamespace(stop_reason="max_tokens", content=[SimpleNamespace(type="text", text="途中")])
    monkeypatch.setattr(llm, "client", lambda: SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: msg)))
    monkeypatch.setattr(llm, "_cache_supported", True)
    with pytest.raises(llm.IncompleteGeneration):
        llm.complete("指示" * 2000, "test", cache=cache, require_complete=True)
    assert llm.complete("test", "test", cache=cache) == "途中"


def test_bad_learning_preserves_old_file(tmp_path):
    path = tmp_path / "rules.md"
    path.write_text("元の記録", encoding="utf-8")
    with pytest.raises(ValueError):
        write_learning(path, "# header\n", "2027-05中旬" + "記録" * 100, "2026-09-08")
    assert path.read_text() == "元の記録"
    write_learning(path, "# header\n", "# duplicate\n" + "観測した範囲の記録。" * 20, "2026-09-08")
    assert path.read_text().count("# ") == 1
    assert len(list(tmp_path.iterdir())) == 1


def test_summary_rejects_missing_fields_and_escapes_html():
    with pytest.raises(ValueError):
        validate({"situation": "test"})
    with pytest.raises(ValueError):
        validate({k: "x" * 161 for k in FIELDS})
    assert "&lt;script&gt;" in page({k: "<script>" for k in FIELDS})


def test_summary_is_inserted_before_toc_without_renumbering_chapters():
    from src.kantei import build_html
    chapters = [{"key": "test", "title": "第一章の見出し", "body": "テスト本文"}]
    plain = build_html("表示確認用", chapters, "2026-09-08")
    out = build_html("表示確認用", chapters, "2026-09-08", summary={k: "表示確認用の短い文章。" for k in FIELDS})
    assert 'class="page summary"' not in plain
    assert out.index('class="page summary"') < out.index('class="page toc"')
    assert out.count("第一章の見出し") == 2
