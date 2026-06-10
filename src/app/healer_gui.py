"""옛바 통합 GUI 엔트리포인트.

v5.17 리팩토링: 실제 구현은 ui.main_window + workers/* + ui/* 에 분산.
본 파일은 QApplication 생성과 main() 만 담당.

실행: py -m src.app.healer_gui
"""
from __future__ import annotations
import os
import sys

# torch/numpy를 PyQt5 전에 로드 (Windows MSVC 런타임 충돌 회피).
try:
    import torch  # noqa: F401
except Exception:
    pass
try:
    import PyQt5 as _pyqt5
    _qt_root = os.path.dirname(_pyqt5.__file__)
    for _sub in ("Qt5", "Qt"):
        _plugs = os.path.join(_qt_root, _sub, "plugins")
        if os.path.isdir(_plugs):
            os.environ["QT_PLUGIN_PATH"] = _plugs
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(_plugs, "platforms")
            break
except Exception:
    pass

from PyQt5 import QtWidgets

from ..config import load as load_cfg
from ..ui.main_window import MainWindow
from ..ui.styles import APP_QSS


import json
import pathlib

_SESSION_FILE = pathlib.Path.home() / ".oldbaram_session.json"


def _load_last() -> dict:
    try:
        return json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_last(nick: str, role: str) -> None:
    try:
        _SESSION_FILE.write_text(
            json.dumps({"nick": nick, "role": role}, ensure_ascii=False),
            encoding="utf-8")
    except Exception:
        pass


def ask_session_info():
    """GUI 시작 전 닉네임 + 역할 입력. 반환 {nick, role} 또는 None(취소)."""
    last = _load_last()
    dlg = QtWidgets.QDialog()
    dlg.setWindowTitle("세션 정보")
    lay = QtWidgets.QFormLayout(dlg)
    nick_edit = QtWidgets.QLineEdit(last.get("nick", ""))
    nick_edit.setPlaceholderText("캐릭터 닉네임")
    role_combo = QtWidgets.QComboBox()
    role_combo.addItems(["힐러", "격수"])
    if last.get("role") == "attacker":
        role_combo.setCurrentText("격수")
    lay.addRow("닉네임", nick_edit)
    lay.addRow("역할", role_combo)
    btns = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    lay.addRow(btns)
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)
    nick_edit.setFocus()
    if dlg.exec_() != QtWidgets.QDialog.Accepted:
        return None
    role = "attacker" if role_combo.currentText() == "격수" else "healer"
    nick = nick_edit.text().strip()
    _save_last(nick, role)
    return {"nick": nick, "role": role}


def main():
    cfg = load_cfg()
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    info = ask_session_info()
    if info is None:
        return
    from ..utils import logger_setup
    logger_setup.set_session(info["nick"], info["role"])
    w = MainWindow(cfg)
    # 시작 다이얼로그에서 고른 역할로 초기화 (라디오 토글 → self.role 반영).
    try:
        if info["role"] == "attacker":
            w.rb_attacker.setChecked(True)
        else:
            w.rb_healer.setChecked(True)
    except Exception:
        pass
    w._session_nick = info["nick"]
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
