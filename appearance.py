"""Shared Qt surfaces and controls, following Loop's neutral workspace palette."""

STYLESHEET = """
    QWidget { color:#2a2a2a; font-size:14px; background:transparent; }
    QMainWindow, QDialog { background:#f6f6f6; }
    QFrame#windowFrame { background:#f6f6f6; border:1px solid #d8d8d8; }
    QFrame#header, QFrame#toolbar { background:#f6f6f6; }
    QFrame#workspace { background:#ffffff; border:1px solid #e3e3e3; border-radius:12px; }
    QLabel { border:0; padding:2px 0; }
    QLabel#brand { font-size:15px; font-weight:400; }
    QLabel#secondary, QLabel#metricCaption { color:#606060; font-size:14px; }
    QLabel#targetTitle { font-size:16px; font-weight:400; padding:2px 0; }
    QLabel#captureState, QLabel#dialogTitle { font-size:20px; font-weight:400; padding:2px 0; }
    QLabel#metricValue { font-size:24px; font-weight:400; padding:2px 0; }
    QLabel#captureStatus { color:#606060; font-size:14px; padding:2px 0; }
    QFrame#sessionPicker { background:#ededed; border:1px solid transparent; border-radius:8px; }
    QFrame#sessionPicker:hover:enabled { background:#e6e6e6; }
    QFrame#sessionPicker:focus { border-color:#808080; }
    QFrame#sessionPicker:disabled { background:#f0f0f0; }
    QFrame#metricStrip { border-top:1px solid #e3e3e3; border-bottom:1px solid #e3e3e3; }
    QFrame#metricDivider, QFrame#sectionRule { background:#e3e3e3; border:0; }
    QFrame#savedFile { background:#fafafa; border:1px solid #e3e3e3; border-radius:8px; }
    QPushButton { background:#ffffff; border:1px solid #d8d8d8; border-radius:6px;
        padding:8px 14px; font-weight:400; }
    QPushButton:hover { background:#f3f3f3; border-color:#c8c8c8; }
    QPushButton:pressed { background:#e8e8e8; }
    QPushButton:focus { border-color:#808080; }
    QPushButton:disabled { background:#f0f0f0; border-color:#e3e3e3; color:#878787; }
    QPushButton#primary { background:#365b48; color:#ffffff; border:1px solid transparent; }
    QPushButton#primary:hover { background:#2c4d3b; }
    QPushButton#primary:pressed { background:#23412f; }
    QPushButton#primary:focus { border-color:#2a2a2a; }
    QPushButton#primary:disabled { background:#ededed; color:#878787; border-color:#e3e3e3; }
    QPushButton#dialogPrimary { background:#333333; color:#ffffff; border-color:transparent; }
    QPushButton#dialogPrimary:hover { background:#474747; }
    QPushButton#dialogPrimary:focus { border-color:#808080; }
    QPushButton#dialogPrimary:disabled { background:#ededed; color:#878787; border-color:#e3e3e3; }
    QPushButton#copyPath { background:transparent; border:1px solid transparent;
        padding:4px 0; text-align:left; color:#606060; }
    QPushButton#copyPath:hover { color:#2a2a2a; background:#eaeaea; }
    QPushButton#copyPath:focus { border-color:#808080; }
    QPushButton#textButton, QPushButton#updateButton { background:transparent;
        border:1px solid transparent; padding:5px 8px; color:#606060; }
    QPushButton#textButton:hover, QPushButton#updateButton:hover { background:#eaeaea; color:#2a2a2a; }
    QPushButton#textButton:focus, QPushButton#updateButton:focus { border-color:#808080; }
    QPushButton#iconButton, QPushButton#pinButton { background:transparent;
        border:1px solid transparent; padding:0; border-radius:5px; }
    QPushButton#iconButton:hover, QPushButton#pinButton:hover, QPushButton#pinButton:checked { background:#e6e6e6; }
    QPushButton#iconButton:focus, QPushButton#pinButton:focus { border-color:#808080; }
    QPushButton#windowControl, QPushButton#windowClose { background:transparent;
        border:0; border-radius:0; padding:0; }
    QPushButton#windowControl:hover { background:#e6e6e6; }
    QPushButton#windowClose:hover { background:#e0e0e0; }
    QPushButton#windowControl:focus, QPushButton#windowClose:focus { border:1px solid #808080; }
    QDialog QLabel, QDialog QPushButton, QDialog QProgressBar { font-size:16px; font-weight:400; }
    QDialog QLabel#brand { font-size:16px; }
    QDialog QLabel#dialogTitle { font-size:20px; }
    QDialog QPushButton { min-height:24px; padding:8px 14px; }
    QFrame#dialogBody { background:#ffffff; border:1px solid #e3e3e3; border-radius:10px; }
    QProgressBar { border:1px solid #d8d8d8; border-radius:4px;
        background:#f0f0f0; text-align:center; min-height:24px; }
    QProgressBar::chunk { background:#d4d4d4; border-radius:3px; }
    QMenu { background:#ffffff; border:1px solid #d8d8d8; padding:5px; }
    QMenu::item { padding:7px 16px; border-radius:4px; }
    QMenu::item:selected { color:#2a2a2a; background:#ededed; }
    QToolTip { background:#ffffff; color:#2a2a2a; border:1px solid #d8d8d8; padding:6px 8px; }
"""
