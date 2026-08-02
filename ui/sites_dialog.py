"""
דיאלוג ניהול אתרים - עבור פקודת "דאוס פתח אתר <שם>" ותפריט "פתח אתר"
במגש המערכת. מציג רשימה של שם-תצוגה + כתובת, עם אפשרות להוסיף/להסיר.

שימוש (מתוך ui/overlay_window.py):
    dialog = SitesDialog(current_sites_dict, logger, parent)
    dialog.sites_saved.connect(handler)  # handler(dict)
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
    QMessageBox,
)


class SitesDialog(QDialog):
    sites_saved = Signal(dict)

    def __init__(self, sites: dict, logger: logging.Logger = None, parent=None):
        super().__init__(parent)
        self.log = logger or logging.getLogger("deus")
        self._sites = dict(sites)

        self.setWindowTitle("ניהול אתרים")
        self.resize(420, 420)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            'אתרים שנפתחים דרך "דאוס פתח אתר <שם>", או מתפריט המגש. '
            'השם משמש להתאמה קולית - כדאי לבחור שם קצר וברור.'
        ))

        self.list_widget = QListWidget()
        self._reload_list()
        layout.addWidget(self.list_widget)

        form_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("שם (למשל: פייסבוק)")
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("כתובת (למשל: https://www.facebook.com/)")
        form_row.addWidget(self.name_edit)
        form_row.addWidget(self.url_edit)
        layout.addLayout(form_row)

        buttons_row = QHBoxLayout()
        add_btn = QPushButton("הוסף / עדכן")
        add_btn.clicked.connect(self._on_add_clicked)
        remove_btn = QPushButton("הסר נבחר")
        remove_btn.clicked.connect(self._on_remove_clicked)
        buttons_row.addWidget(add_btn)
        buttons_row.addWidget(remove_btn)
        layout.addLayout(buttons_row)

        self.list_widget.itemClicked.connect(self._on_item_clicked)

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
        for name, url in self._sites.items():
            item = QListWidgetItem(f"{name}  -  {url}")
            item.setData(1000, name)
            self.list_widget.addItem(item)

    def _on_item_clicked(self, item: QListWidgetItem):
        name = item.data(1000)
        if name in self._sites:
            self.name_edit.setText(name)
            self.url_edit.setText(self._sites[name])

    def _on_add_clicked(self):
        name = self.name_edit.text().strip()
        url = self.url_edit.text().strip()
        if not name or not url:
            QMessageBox.warning(self, "חסר מידע", "יש למלא גם שם וגם כתובת.")
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        self._sites[name] = url
        self._reload_list()
        self.name_edit.clear()
        self.url_edit.clear()

    def _on_remove_clicked(self):
        selected = self.list_widget.currentItem()
        if selected is None:
            return
        name = selected.data(1000)
        self._sites.pop(name, None)
        self._reload_list()

    def _on_save_clicked(self):
        self.sites_saved.emit(dict(self._sites))
        self.accept()
