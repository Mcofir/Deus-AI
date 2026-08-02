"""
דיאלוג ניהול חריגים לניקוי זיכרון - רשימת שמות תהליכים/אפליקציות
שלעולם לא ייסגרו על ידי "דאוס נקה זיכרון" (ai_engine/ram_cleaner.py),
מעבר לרשימת ההגנה הקבועה (תהליכי מערכת, Python, PowerShell וכו').

שימוש (מתוך ui/overlay_window.py):
    dialog = RamCleanDialog(current_exclude_list, logger, parent)
    dialog.excludes_saved.connect(handler)  # handler(list[str])
    dialog.exec()
"""

import logging

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QLineEdit,
    QLabel,
)


class RamCleanDialog(QDialog):
    excludes_saved = Signal(list)

    def __init__(self, exclude_list, logger: logging.Logger = None, parent=None):
        super().__init__(parent)
        self.log = logger or logging.getLogger("deus")
        self._excludes = list(exclude_list or [])

        self.setWindowTitle("ניהול חריגים לניקוי זיכרון")
        self.resize(400, 400)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "שמות תהליכים/אפליקציות שלא ייסגרו על ידי \"דאוס נקה זיכרון\" "
            "(בנוסף לתהליכי המערכת שתמיד מוגנים). אפשר להזין חלק משם "
            "התהליך, למשל 'discord' יתפוס גם Discord.exe."
        ))

        self.list_widget = QListWidget()
        self._reload_list()
        layout.addWidget(self.list_widget)

        add_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("שם תהליך (למשל: CareUEyes)")
        add_btn = QPushButton("הוסף")
        add_btn.clicked.connect(self._on_add_clicked)
        add_row.addWidget(self.name_edit)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        remove_btn = QPushButton("הסר נבחר")
        remove_btn.clicked.connect(self._on_remove_clicked)
        layout.addWidget(remove_btn)

        bottom_row = QHBoxLayout()
        save_btn = QPushButton("שמור וסגור")
        save_btn.clicked.connect(self._on_save_clicked)
        cancel_btn = QPushButton("ביטול")
        cancel_btn.clicked.connect(self.reject)
        bottom_row.addWidget(save_btn)
        bottom_row.addWidget(cancel_btn)
        layout.addLayout(bottom_row)

    def _reload_list(self):
        self.list_widget.clear()
        for name in self._excludes:
            self.list_widget.addItem(QListWidgetItem(name))

    def _on_add_clicked(self):
        name = self.name_edit.text().strip()
        if not name:
            return
        if name not in self._excludes:
            self._excludes.append(name)
            self._reload_list()
        self.name_edit.clear()

    def _on_remove_clicked(self):
        selected = self.list_widget.currentItem()
        if selected is None:
            return
        name = selected.text()
        if name in self._excludes:
            self._excludes.remove(name)
        self._reload_list()

    def _on_save_clicked(self):
        self.excludes_saved.emit(list(self._excludes))
        self.accept()
