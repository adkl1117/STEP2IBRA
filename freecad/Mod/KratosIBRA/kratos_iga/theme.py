"""Scoped native Qt theme; never changes the surrounding FreeCAD theme."""
from pathlib import Path


def stylesheet():
    icons = (Path(__file__).resolve().parents[1]/"Resources").as_posix()
    return """
    QWidget#igaPanel { background: #f3f5f9; color: #26324b; font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 12px; }
    QWidget#igaPanel QLabel, QWidget#igaPanel QCheckBox { color: #34435c; background: transparent; }
    QWidget#brand { background: #172b48; border-radius: 14px; }
    QWidget#brand QLabel#title { color: #ffffff; font-family: 'Segoe UI'; font-size: 25px; font-weight: 700; }
    QWidget#brand QLabel#brandCaption { color: #a7bdd3; font-size: 11px; }
    QWidget#brand QLabel#brandTag { color: #9ff1da; background: #29455c; border-radius: 8px; padding: 5px 9px; font-size: 10px; font-weight: 600; }
    QLabel#hint { color: #78859a; font-size: 11px; }
    QLabel#sectionTitle { color: #26324b; font-size: 18px; font-weight: 700; padding-bottom: 2px; }
    QLabel#eyebrow { color: #13a187; font-family: 'Segoe UI'; font-size: 10px; font-weight: 700; }
    QLabel#projectSummary { color: #60738b; font-size: 11px; padding: 3px 2px; }
    QLabel#status { color: #62758c; font-size: 11px; }
    QPushButton { padding: 7px 11px; border: 1px solid #e1e6ef; border-radius: 8px; background: #ffffff; color: #40516a; font-size: 12px; }
    QPushButton:hover { background: #edf8f5; border-color: #9eddd0; color: #137f6d; }
    QPushButton:pressed { background: #d7eee8; }
    QPushButton:disabled { color: #a7b0c1; background: #edf0f5; }
    QPushButton#primary { background: #159b82; border-color: #159b82; color: white; font-weight: 600; }
    QPushButton#primary:hover { background: #0d8974; }
    QPushButton#export { background: #172b48; border-color: #172b48; color: white; padding: 10px 14px; font-weight: 600; }
    QPushButton#export:hover { background: #264b73; }
    QPushButton#quiet { color: #7c8aa0; background: transparent; border-color: transparent; padding: 4px 7px; font-size: 11px; }
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { min-height: 20px; padding: 6px 8px; color: #263b54; background: #f6f8fb; border: 1px solid #e1e7f0; border-radius: 7px; selection-background-color: #cdece4; selection-color: #125d52; }
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #48b9a2; background: white; }
    QLineEdit:read-only { color: #278578; background: #eef8f5; border-color: #d5ede7; }
    QLineEdit:disabled { color: #a0acbc; background: #f0f2f6; }
    QComboBox::drop-down { border: none; width: 21px; }
    QComboBox::down-arrow { image: url(ICONS/chevron.svg); width: 10px; height: 10px; }
    QComboBox QAbstractItemView { color: #263b54; background: white; selection-background-color: #def2ed; selection-color: #146b5c; border: 1px solid #e0e8ee; }
    QTabWidget::pane { border: none; background: transparent; }
    QScrollArea { border: none; background: transparent; }
    QWidget#contentPage { background: white; border-radius: 12px; }
    QListWidget#navigation { border: none; background: transparent; outline: none; font-size: 12px; }
    QListWidget#navigation::item { border-radius: 9px; padding: 10px 5px; margin: 2px 0; color: #8290a5; }
    QListWidget#navigation::item:hover { color: #2c766c; background: #e8eeef; }
    QListWidget#navigation::item:selected { background: #dcf2eb; color: #11826e; font-weight: 600; }
    QTableWidget, QListWidget, QPlainTextEdit { background: white; color: #334964; border: 1px solid #e7ecf3; border-radius: 8px; alternate-background-color: #f8fafc; gridline-color: #f0f3f8; outline: none; selection-background-color: #dff2ec; selection-color: #116f60; }
    QHeaderView::section { background: #f4f7fb; color: #75869c; padding: 8px 7px; border: none; font-size: 11px; }
    QTableWidget::item { padding: 6px; border: none; }
    QListWidget::item { padding: 10px; border-bottom: 1px solid #f0f3f8; }
    QCheckBox { spacing: 7px; }
    QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #c6d1e0; border-radius: 5px; background: white; }
    QCheckBox::indicator:checked { background: #159b82; border-color: #159b82; image: url(ICONS/check.svg); }
    QProgressBar { background: #e3eee9; border: none; border-radius: 2px; max-height: 4px; }
    QProgressBar::chunk { background: #18ab8d; border-radius: 2px; }
    QScrollBar:vertical { background: transparent; width: 7px; margin: 0; }
    QScrollBar::handle:vertical { background: #d3dce6; min-height: 30px; border-radius: 3px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
    QScrollBar:horizontal { background: #f4f7fa; height: 7px; margin: 0; }
    QScrollBar::handle:horizontal { background: #d3dce6; min-width: 30px; border-radius: 3px; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
    QToolTip { background: #172b48; color: white; border: none; padding: 7px; }
    """.replace("ICONS", icons)
