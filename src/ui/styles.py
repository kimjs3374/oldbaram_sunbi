"""애플리케이션 전역 QSS — modern refined light theme.

Linear / Notion / Raycast 계열 톤. 다크X, 디스코드X.

팔레트:
  canvas      #f6f7f9    — 윈도우 전체 배경 (오프 화이트)
  surface     #ffffff    — 카드/입력/테이블 (화이트)
  surface-2   #eef0f4    — subtle fill (토글 off/hover 배경)
  surface-3   #e4e7ec    — divider 강조용
  border      #e4e7ec    — 외곽 hairline
  border-str  #d0d5dc    — 강조 border
  text        #101828    — primary
  text-2      #475467    — secondary
  text-mute   #98a2b3    — muted/placeholder

  accent      #6366f1    — indigo (primary action)
  accent-dk   #4f46e5
  accent-lt   #eef2ff    — tinted bg (활성 탭 배경 등)
  ok          #16a34a
  warn        #d97706
  danger      #e11d48

타이포: Pretendard > Inter > Malgun Gothic. 기본 10pt.
라운드: input 8px, button 8px, card/groupbox 12px.
"""
from __future__ import annotations

APP_QSS = """
* {
    outline: 0;
}
QMainWindow, QDialog {
    background: #f6f7f9;
}
QWidget {
    background: #f6f7f9;
    color: #101828;
    font-family: 'Pretendard', 'Inter', 'Malgun Gothic', 'Segoe UI', sans-serif;
    font-size: 9pt;
}
QLabel {
    background: transparent;
    color: #101828;
}
QLabel:disabled {
    color: #98a2b3;
}

/* --- GroupBox: 카드 --- */
QGroupBox {
    background: #ffffff;
    border: 1px solid #e4e7ec;
    border-radius: 8px;
    margin-top: 10px;
    padding: 6px 6px 5px 6px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    top: 0px;
    padding: 0 3px;
    color: #475467;
    background: transparent;
    font-weight: 600;
    font-size: 8pt;
    letter-spacing: 0.4px;
    text-transform: uppercase;
}

/* --- Buttons --- */
QPushButton {
    background: #ffffff;
    border: 1px solid #d0d5dc;
    border-radius: 5px;
    padding: 2px 8px;
    color: #101828;
    font-weight: 500;
    min-height: 0;
}
QPushButton:hover {
    background: #f2f4f7;
    border: 1px solid #b7bec9;
}
QPushButton:pressed {
    background: #e4e7ec;
}
QPushButton:disabled {
    background: #f2f4f7;
    color: #98a2b3;
    border: 1px solid #eaecf0;
}
QPushButton:checked {
    background: #6366f1;
    border: 1px solid #4f46e5;
    color: #ffffff;
    font-weight: 600;
}
QPushButton:checked:hover {
    background: #4f46e5;
}
QPushButton#primaryBtn {
    background: #6366f1;
    border: 1px solid #4f46e5;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#primaryBtn:hover { background: #4f46e5; border-color: #4338ca; }
QPushButton#primaryBtn:pressed { background: #4338ca; }
QPushButton#dangerBtn {
    background: #e11d48;
    border: 1px solid #be123c;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#dangerBtn:hover { background: #be123c; }
QPushButton#successBtn {
    background: #16a34a;
    border: 1px solid #15803d;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#successBtn:hover { background: #15803d; }
QPushButton#ghostBtn {
    background: transparent;
    border: 1px solid transparent;
    color: #475467;
}
QPushButton#ghostBtn:hover { background: #eef0f4; color: #101828; }

/* --- Inputs --- */
QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QComboBox {
    background: #ffffff;
    border: 1px solid #d0d5dc;
    border-radius: 6px;
    padding: 3px 8px;
    color: #101828;
    selection-background-color: #6366f1;
    selection-color: #ffffff;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {
    border: 1px solid #6366f1;
    background: #ffffff;
}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QPlainTextEdit:disabled, QTextEdit:disabled, QComboBox:disabled {
    background: #f2f4f7;
    color: #98a2b3;
    border: 1px solid #eaecf0;
}
QComboBox::drop-down {
    border: 0;
    width: 24px;
}
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #e4e7ec;
    border-radius: 8px;
    color: #101828;
    selection-background-color: #eef2ff;
    selection-color: #4338ca;
    padding: 4px;
}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    width: 18px;
    background: transparent;
    border: 0;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
    background: #eef0f4;
    border-radius: 4px;
}

/* --- Checkbox/Radio --- */
QCheckBox, QRadioButton {
    color: #101828;
    spacing: 8px;
    background: transparent;
    padding: 2px 0;
}
QCheckBox:disabled, QRadioButton:disabled {
    color: #98a2b3;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 18px;
    height: 18px;
    border: 1.5px solid #b7bec9;
    background: #ffffff;
}
QCheckBox::indicator {
    border-radius: 5px;
}
QRadioButton::indicator {
    border-radius: 11px;
}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {
    border-color: #6366f1;
}
QCheckBox::indicator:checked {
    background: #6366f1;
    border: 1.5px solid #4f46e5;
    image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><path d='M3.5 8.5 l3 3 6-6.5' stroke='white' stroke-width='2.2' fill='none' stroke-linecap='round' stroke-linejoin='round'/></svg>");
}
QCheckBox::indicator:checked:hover {
    background: #4f46e5;
}
QRadioButton::indicator:checked {
    border: 1.5px solid #6366f1;
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 #6366f1, stop:0.5 #6366f1,
        stop:0.52 #ffffff, stop:1 #ffffff);
}
QRadioButton::indicator:checked:hover {
    border-color: #4f46e5;
}

/* --- Slider --- */
QSlider::groove:horizontal {
    background: #e4e7ec;
    height: 4px;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #6366f1;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #ffffff;
    border: 1.5px solid #6366f1;
    width: 16px;
    height: 16px;
    margin: -7px 0;
    border-radius: 9px;
}
QSlider::handle:horizontal:hover {
    background: #eef2ff;
}

/* --- Tables/Lists --- */
QTableWidget, QTableView, QListWidget, QListView, QTreeView {
    background: #ffffff;
    alternate-background-color: #f9fafb;
    border: 1px solid #e4e7ec;
    border-radius: 10px;
    gridline-color: #eef0f4;
    color: #101828;
    selection-background-color: #eef2ff;
    selection-color: #3730a3;
}
QTableWidget::item, QListWidget::item, QTreeView::item {
    padding: 4px 8px;
    border: 0;
}
QTableWidget::item:selected, QListWidget::item:selected,
QTreeView::item:selected {
    background: #eef2ff;
    color: #3730a3;
}
QTableWidget::item:hover, QListWidget::item:hover,
QTreeView::item:hover {
    background: #f7f8fa;
}
QHeaderView::section {
    background: #f9fafb;
    color: #475467;
    padding: 4px 8px;
    border: 0;
    border-right: 1px solid #eef0f4;
    border-bottom: 1px solid #e4e7ec;
    font-weight: 600;
    font-size: 8pt;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}
QHeaderView::section:first { border-top-left-radius: 10px; }
QHeaderView::section:last {
    border-top-right-radius: 10px;
    border-right: 0;
}

/* --- ScrollBar --- */
QScrollBar:vertical {
    background: transparent;
    width: 12px;
    margin: 2px;
    border-radius: 6px;
}
QScrollBar::handle:vertical {
    background: #d0d5dc;
    min-height: 36px;
    border-radius: 5px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover {
    background: #b7bec9;
}
QScrollBar:horizontal {
    background: transparent;
    height: 12px;
    margin: 2px;
    border-radius: 6px;
}
QScrollBar::handle:horizontal {
    background: #d0d5dc;
    min-width: 36px;
    border-radius: 5px;
    margin: 2px;
}
QScrollBar::handle:horizontal:hover {
    background: #b7bec9;
}
QScrollBar::add-line, QScrollBar::sub-line { background: transparent; border: 0; width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* --- Tabs --- */
QTabWidget::pane {
    background: #ffffff;
    border: 1px solid #e4e7ec;
    border-radius: 12px;
    top: -1px;
}
QTabBar::tab {
    background: transparent;
    color: #475467;
    padding: 4px 12px;
    border: 0;
    border-bottom: 2px solid transparent;
    font-weight: 500;
}
QTabBar::tab:hover { color: #101828; }
QTabBar::tab:selected {
    color: #4338ca;
    border-bottom: 2px solid #6366f1;
    font-weight: 600;
}

/* --- Menu --- */
QMenu {
    background: #ffffff;
    border: 1px solid #e4e7ec;
    border-radius: 10px;
    padding: 6px;
    color: #101828;
}
QMenu::item {
    padding: 7px 14px;
    border-radius: 6px;
}
QMenu::item:selected {
    background: #eef2ff;
    color: #3730a3;
}
QMenu::separator {
    height: 1px;
    background: #eef0f4;
    margin: 4px 8px;
}
QMenuBar {
    background: #ffffff;
    border-bottom: 1px solid #e4e7ec;
    padding: 2px 4px;
    color: #101828;
}
QMenuBar::item { padding: 6px 10px; border-radius: 6px; background: transparent; }
QMenuBar::item:selected { background: #eef0f4; }

/* --- ToolTip --- */
QToolTip {
    background: #101828;
    color: #f2f4f7;
    border: 0;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 9pt;
}

/* --- Progress --- */
QProgressBar {
    background: #eef0f4;
    border: 0;
    border-radius: 4px;
    text-align: center;
    color: #101828;
    height: 8px;
}
QProgressBar::chunk {
    background: #6366f1;
    border-radius: 4px;
}

/* --- Splitter --- */
QSplitter::handle {
    background: #eef0f4;
}
QSplitter::handle:horizontal { width: 2px; }
QSplitter::handle:vertical { height: 2px; }

/* --- Frame/Divider --- */
QFrame[frameShape="4"], QFrame[frameShape="5"] { /* HLine / VLine */
    background: #e4e7ec;
    color: #e4e7ec;
    border: 0;
}
QFrame#divider {
    background: #eef0f4;
    min-height: 1px; max-height: 1px;
}
QFrame#card {
    background: #ffffff;
    border: 1px solid #e4e7ec;
    border-radius: 12px;
}
QFrame#sidebar {
    background: #ffffff;
    border: 1px solid #e4e7ec;
    border-radius: 12px;
}

/* --- Named labels --- */
QLabel#sectionTitle {
    color: #475467;
    font-weight: 700;
    font-size: 8pt;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    padding: 2px 2px;
}
QLabel#pageTitle {
    color: #101828;
    font-weight: 700;
    font-size: 11pt;
    letter-spacing: -0.2px;
}
QLabel#mutedLabel { color: #98a2b3; }
QLabel#valueLabel { color: #101828; font-weight: 600; }
QLabel#successLabel { color: #16a34a; font-weight: 600; }
QLabel#warnLabel { color: #d97706; font-weight: 600; }
QLabel#dangerLabel { color: #e11d48; font-weight: 600; }
QLabel#accentLabel { color: #4f46e5; font-weight: 600; }

/* --- Status pill --- */
QLabel#pillOk {
    background: #ecfdf5; color: #15803d; font-weight: 600;
    padding: 2px 10px; border-radius: 10px; border: 1px solid #bbf7d0;
}
QLabel#pillWarn {
    background: #fef3c7; color: #92400e; font-weight: 600;
    padding: 2px 10px; border-radius: 10px; border: 1px solid #fde68a;
}
QLabel#pillDanger {
    background: #fee2e2; color: #b91c1c; font-weight: 600;
    padding: 2px 10px; border-radius: 10px; border: 1px solid #fecaca;
}
QLabel#pillIdle {
    background: #f2f4f7; color: #475467; font-weight: 600;
    padding: 2px 10px; border-radius: 10px; border: 1px solid #e4e7ec;
}
"""
