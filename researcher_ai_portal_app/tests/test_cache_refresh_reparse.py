from __future__ import annotations

from pathlib import Path


def test_start_parse_and_tasks_thread_force_reparse_flag():
    views_path = Path(__file__).resolve().parents[1] / "views.py"
    tasks_path = Path(__file__).resolve().parents[1] / "tasks.py"

    views_text = views_path.read_text(encoding="utf-8")
    tasks_text = tasks_path.read_text(encoding="utf-8")

    assert 'request.POST.get("force_reparse")' in views_text
    assert "if not force_reparse:" in views_text
    assert "force_reparse=force_reparse" in views_text
    assert "force_reparse: bool = False" in tasks_text
    assert "force_reparse=force_reparse" in tasks_text
