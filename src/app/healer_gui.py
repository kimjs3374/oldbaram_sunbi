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


def main():
    cfg = load_cfg()
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    w = MainWindow(cfg)
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
