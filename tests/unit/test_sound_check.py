"""sound_check（#522）单测：SC2 全局 soundglobal 配置检查。

纯函数 check_sound_global，路径注入，不碰真实文件系统外的东西。
"""

from __future__ import annotations

import pathlib

from vibecraft.server.sound_check import check_sound_global


def _write(p: pathlib.Path, text: str) -> pathlib.Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_sound_global_true_enabled(tmp_path: pathlib.Path) -> None:
    f = _write(tmp_path / "Variables.txt", "soundglobal=true\nfoo=bar\n")
    st = check_sound_global([f])
    assert st.enabled is True
    assert st.found is True
    assert st.path == f
    assert st.raw_value == "true"


def test_sound_global_false_not_enabled(tmp_path: pathlib.Path) -> None:
    f = _write(tmp_path / "Variables.txt", "soundglobal=false\n")
    st = check_sound_global([f])
    assert st.enabled is False
    assert st.found is True
    assert st.raw_value == "false"


def test_sound_global_case_insensitive_key(tmp_path: pathlib.Path) -> None:
    # SC2 实际可能写成 soundGlobal（驼峰）；键匹配应大小写不敏感
    f = _write(tmp_path / "Variables.txt", "soundGlobal=True\n")
    st = check_sound_global([f])
    assert st.enabled is True
    assert st.found is True


def test_sound_global_key_absent_found_but_disabled(tmp_path: pathlib.Path) -> None:
    # 文件在但没写 soundglobal 键 → found=True, enabled=False, raw_value=None
    f = _write(tmp_path / "Variables.txt", "musicvolume=0.5\nsfxvolume=1.0\n")
    st = check_sound_global([f])
    assert st.found is True
    assert st.enabled is False
    assert st.raw_value is None


def test_sound_global_file_not_found(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "nope" / "Variables.txt"
    st = check_sound_global([missing])
    assert st.found is False
    assert st.enabled is False
    assert st.path is None


def test_sound_global_first_existing_candidate_wins(tmp_path: pathlib.Path) -> None:
    # 第一个候选不存在，第二个存在 → 用第二个
    first = tmp_path / "a" / "Variables.txt"  # 不创建
    second = _write(tmp_path / "b" / "Variables.txt", "soundglobal=true\n")
    st = check_sound_global([first, second])
    assert st.found is True
    assert st.enabled is True
    assert st.path == second


def test_sound_global_ignores_comments_and_blanks(tmp_path: pathlib.Path) -> None:
    text = "# comment\n\n; another\nsoundglobal=true\n"
    f = _write(tmp_path / "Variables.txt", text)
    st = check_sound_global([f])
    assert st.enabled is True
