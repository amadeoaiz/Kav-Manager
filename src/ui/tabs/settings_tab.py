"""
Settings Tab — Fully functional editor for UnitConfig and Role registry.
Changes to engine tuning take effect on the next reconcile.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox,
    QGroupBox, QFormLayout, QScrollArea, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy,
    QTabWidget, QFileDialog, QDateEdit,
)
from PyQt6.QtCore import Qt, QDate

from src.services.config_service import ConfigService
from src.services.soldier_service import SoldierService
from src.services.template_service import TemplateService


class SettingsTab(QWidget):
    def __init__(self, db, main_window):
        super().__init__()
        self.db = db
        self._config_svc = ConfigService(db)
        self._tpl_svc = TemplateService(db)
        self.mw = main_window
        self._setup_ui()
        self._load_config()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        inner_tabs = QTabWidget()
        inner_tabs.setDocumentMode(True)

        inner_tabs.addTab(self._build_unit_page(),      " UNIT ")
        inner_tabs.addTab(self._build_engine_page(),    " ENGINE ")
        inner_tabs.addTab(self._build_roles_page(),     " ROLES ")
        inner_tabs.addTab(self._build_backup_page(),    " BACKUP ")
        inner_tabs.addTab(self._build_exports_page(),   " EXPORTS ")
        inner_tabs.addTab(self._build_templates_page(), " SAVED TASKS ")
        inner_tabs.addTab(self._build_matrix_page(),    " MATRIX CHAT ")

        outer.addWidget(inner_tabs)

    # ── Unit identity ──────────────────────────────────────────────────────────

    def _build_unit_page(self) -> QWidget:
        page, scroll, form = self._scroll_form_page()

        self._unit_codename    = QLineEdit()
        self._commander_combo  = QComboBox()
        self._commander_combo.setMinimumWidth(200)
        self._default_arrival  = QLineEdit()
        self._default_departure = QLineEdit()
        self._avail_buffer     = QSpinBox()
        self._avail_buffer.setRange(0, 480)
        self._avail_buffer.setSuffix(" min")

        form.addRow("Unit codename:",           self._unit_codename)
        form.addRow("Unit commander:",         self._commander_combo)
        form.addRow("Default arrival time (HH:MM):",   self._default_arrival)
        form.addRow("Default departure time (HH:MM):", self._default_departure)
        form.addRow("Availability buffer after arrival:", self._avail_buffer)

        note = QLabel(
            "Availability buffer: soldiers are not eligible for tasks until\n"
            "this many minutes have passed since their planned arrival."
        )
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        form.addRow(note)

        # ── Reserve period ────────────────────────────────────────────────
        sep = QLabel("— Reserve period —")
        sep.setObjectName("dimLabel")
        sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        form.addRow(sep)

        self._reserve_start = QDateEdit()
        self._reserve_start.setCalendarPopup(True)
        self._reserve_start.setDisplayFormat("dd MMM yyyy")
        self._reserve_start.setSpecialValueText("Auto")
        self._reserve_start.setMinimumDate(QDate(2020, 1, 1))

        start_row = QHBoxLayout()
        start_row.addWidget(self._reserve_start)
        clear_start = QPushButton("Clear")
        clear_start.setMinimumWidth(80)
        clear_start.clicked.connect(
            lambda: self._reserve_start.setDate(self._reserve_start.minimumDate())
        )
        start_row.addWidget(clear_start)
        form.addRow("Period start:", start_row)

        self._reserve_end = QDateEdit()
        self._reserve_end.setCalendarPopup(True)
        self._reserve_end.setDisplayFormat("dd MMM yyyy")
        self._reserve_end.setSpecialValueText("Auto")
        self._reserve_end.setMinimumDate(QDate(2020, 1, 1))

        end_row = QHBoxLayout()
        end_row.addWidget(self._reserve_end)
        clear_end = QPushButton("Clear")
        clear_end.setMinimumWidth(80)
        clear_end.clicked.connect(
            lambda: self._reserve_end.setDate(self._reserve_end.minimumDate())
        )
        end_row.addWidget(clear_end)
        form.addRow("Period end:", end_row)

        rp_note = QLabel(
            "Define the unit's active reserve rotation period.\n"
            "Used in 'Whole period' stats view and the grid right-click menu.\n"
            "Leave as 'Auto' to detect from existing draft/presence data."
        )
        rp_note.setObjectName("dimLabel")
        rp_note.setWordWrap(True)
        form.addRow(rp_note)

        # ── Chain of command ──────────────────────────────────────────────
        chain_sep = QLabel("— Chain of command —")
        chain_sep.setObjectName("dimLabel")
        chain_sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        form.addRow(chain_sep)

        chain_hint = QLabel(
            "The active commander is the highest-priority soldier who is present.\n"
            "Tasks with 'Include commander' unchecked will exclude the active\n"
            "commander from assignment."
        )
        chain_hint.setObjectName("dimLabel")
        chain_hint.setWordWrap(True)
        form.addRow(chain_hint)

        self._chain_combos: list[QComboBox] = []
        labels = ["Primary commander:", "Second-in-command:", "Third-in-command:"]
        for label_text in labels:
            combo = QComboBox()
            combo.setMinimumWidth(200)
            self._chain_combos.append(combo)
            form.addRow(label_text, combo)

        save_btn = QPushButton("[ SAVE UNIT SETTINGS ]")
        save_btn.clicked.connect(self._save_config)
        form.addRow(save_btn)

        return page

    def _populate_chain_combos(self):
        """Fill the chain-of-command dropdowns with active soldiers."""
        soldiers = SoldierService(self.db).list_active_soldiers()
        config = self._config_svc.get_config()
        chain = config.command_chain or []

        for i, combo in enumerate(self._chain_combos):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("— None —", None)
            for s in soldiers:
                combo.addItem(s.name or f"#{s.id}", s.id)
            # Pre-select from chain
            if i < len(chain) and chain[i] is not None:
                idx = combo.findData(chain[i])
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    # ── Engine tuning ──────────────────────────────────────────────────────────

    def _build_engine_page(self) -> QWidget:
        page, scroll, form = self._scroll_form_page()

        self._night_start = QSpinBox()
        self._night_start.setRange(0, 23)
        self._night_end   = QSpinBox()
        self._night_end.setRange(0, 23)
        self._min_assign  = QSpinBox()
        self._min_assign.setRange(5, 240)
        self._min_assign.setSuffix(" min")
        self._adj_bonus   = QDoubleSpinBox()
        self._adj_bonus.setRange(-100.0, 0.0)
        self._adj_bonus.setSingleStep(1.0)
        self._wakeup_base = QDoubleSpinBox()
        self._wakeup_base.setRange(0.0, 200.0)
        self._wakeup_alpha = QDoubleSpinBox()
        self._wakeup_alpha.setRange(0.1, 10.0)
        self._wakeup_alpha.setSingleStep(0.1)

        form.addRow("Night start hour (24h):", self._night_start)
        form.addRow("Night end hour (24h):",   self._night_end)
        form.addRow("Min. assignment duration:", self._min_assign)

        sep_lbl = QLabel("— Night scoring weights —")
        sep_lbl.setObjectName("dimLabel")
        sep_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        form.addRow(sep_lbl)

        form.addRow("Adjacency bonus:",        self._adj_bonus)
        form.addRow("Wake-up penalty base:",   self._wakeup_base)
        form.addRow("Wake-up decay alpha:",    self._wakeup_alpha)

        note = QLabel(
            "Changes take effect on the next Reconcile.\n"
            "Adjacency bonus: negative = reward for keeping same soldier on consecutive shifts.\n"
            "Wake-up penalty: how harshly waking someone mid-sleep is penalized."
        )
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        form.addRow(note)

        save_btn = QPushButton("[ SAVE ENGINE SETTINGS ]")
        save_btn.clicked.connect(self._save_config)
        form.addRow(save_btn)

        return page

    # ── Role registry ──────────────────────────────────────────────────────────

    def _build_roles_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        hdr = QLabel("ROLE REGISTRY")
        hdr.setObjectName("sectionHeader")
        layout.addWidget(hdr)

        note = QLabel(
            "Roles form an inheritance tree (parent → child). "
            "A task requiring 'Observer' will accept any child role (e.g. Navigator) automatically.\n"
            "Double-click a row to edit. Right-click for delete."
        )
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

        self._roles_table = QTableWidget()
        self._roles_table.setColumnCount(3)
        self._roles_table.setHorizontalHeaderLabels(["NAME", "DESCRIPTION", "EXTENDS (PARENT)"])
        self._roles_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._roles_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._roles_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._roles_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._roles_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._roles_table.verticalHeader().setVisible(False)
        self._roles_table.doubleClicked.connect(self._on_edit_role)
        self._roles_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._roles_table.customContextMenuRequested.connect(self._on_roles_context_menu)
        layout.addWidget(self._roles_table, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        edit_role_btn = QPushButton("[ EDIT SELECTED ]")
        edit_role_btn.clicked.connect(lambda: self._on_edit_role(None))
        del_role_btn  = QPushButton("[ DELETE SELECTED ]")
        del_role_btn.clicked.connect(self._on_delete_role)
        add_role_btn  = QPushButton("[ + ADD ROLE ]")
        add_role_btn.clicked.connect(self._on_add_role)
        for b in (edit_role_btn, del_role_btn, add_role_btn):
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

        self._refresh_roles_table()
        return page

    def _refresh_roles_table(self):
        roles = self._config_svc.list_roles()
        role_map = {r.id: r.name for r in roles}
        self._roles_table.setRowCount(len(roles))
        for row, r in enumerate(roles):
            parent_name = role_map.get(r.parent_role_id, "—")
            for col, val in enumerate([r.name, r.description or "—", parent_name]):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setData(Qt.ItemDataRole.UserRole, r.id)
                self._roles_table.setItem(row, col, item)

    def _selected_role_id(self) -> int | None:
        rows = self._roles_table.selectedItems()
        return rows[0].data(Qt.ItemDataRole.UserRole) if rows else None

    def _on_add_role(self):
        _RoleDialog(self._config_svc, role=None, parent=self).exec_and_refresh(self._refresh_roles_table)

    def _on_edit_role(self, _index=None):
        rid = self._selected_role_id()
        if rid is None:
            QMessageBox.information(self, "Select a role", "Click a row first.")
            return
        role = self._config_svc.get_role(rid)
        if role:
            _RoleDialog(self._config_svc, role=role, parent=self).exec_and_refresh(self._refresh_roles_table)

    def _on_delete_role(self):
        rid = self._selected_role_id()
        if rid is None:
            QMessageBox.information(self, "Select a role", "Click a row first.")
            return
        role = self._config_svc.get_role(rid)
        if not role:
            return
        ans = QMessageBox.question(
            self, "Delete role",
            f"Delete role '{role.name}'?\n"
            "Children of this role will lose their parent link.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self._config_svc.delete_role(rid)
        self._refresh_roles_table()

    def _on_roles_context_menu(self, pos):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction("Edit",   lambda: self._on_edit_role(None))
        menu.addAction("Delete", self._on_delete_role)
        menu.exec(self._roles_table.viewport().mapToGlobal(pos))

    # ── Saved Tasks (templates) ──────────────────────────────────────────────

    def _build_templates_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        hdr = QLabel("SAVED TASKS")
        hdr.setObjectName("sectionHeader")
        layout.addWidget(hdr)

        note = QLabel(
            "Saved task templates for recurring assignments. "
            "Use these to quickly create tasks with pre-filled fields."
        )
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

        self._tpl_table = QTableWidget()
        self._tpl_table.setColumnCount(6)
        self._tpl_table.setHorizontalHeaderLabels([
            "NAME", "TIME", "SOLDIERS", "DIFFICULTY", "ROLES", "FRACTIONABLE"
        ])
        self._tpl_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for col in range(1, 6):
            self._tpl_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self._tpl_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tpl_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._tpl_table.verticalHeader().setVisible(False)
        self._tpl_table.doubleClicked.connect(lambda: self._on_edit_template())
        layout.addWidget(self._tpl_table, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        for label, handler in [
            ("[ + NEW ]",       self._on_new_template),
            ("[ EDIT ]",        self._on_edit_template),
            ("[ DUPLICATE ]",   self._on_duplicate_template),
            ("[ DELETE ]",      self._on_delete_template),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(handler)
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        self._refresh_templates_table()
        return page

    def _refresh_templates_table(self):
        templates = self._tpl_svc.list_templates()
        self._tpl_table.setRowCount(len(templates))
        for row, tpl in enumerate(templates):
            time_str = f"{tpl.start_time_of_day}–{tpl.end_time_of_day}"
            roles = tpl.required_roles_list or []
            if isinstance(roles, dict):
                role_str = ", ".join(f"{k}×{v}" for k, v in roles.items())
            elif isinstance(roles, list):
                role_str = ", ".join(str(r) for r in roles)
            else:
                role_str = ""
            vals = [
                tpl.name,
                time_str,
                str(tpl.required_count or 1),
                str(tpl.hardness or 3),
                role_str or "—",
                "Yes" if tpl.is_fractionable else "No",
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setData(Qt.ItemDataRole.UserRole, tpl.id)
                self._tpl_table.setItem(row, col, item)

    def _selected_template_id(self) -> int | None:
        rows = self._tpl_table.selectedItems()
        return rows[0].data(Qt.ItemDataRole.UserRole) if rows else None

    def _on_new_template(self):
        _TemplateEditDialog(self._tpl_svc, template=None, parent=self).exec_and_refresh(
            self._refresh_templates_table
        )

    def _on_edit_template(self):
        tid = self._selected_template_id()
        if tid is None:
            QMessageBox.information(self, "Select a template", "Click a row first.")
            return
        tpl = self._tpl_svc.get_template(tid)
        if tpl:
            _TemplateEditDialog(self._tpl_svc, template=tpl, parent=self).exec_and_refresh(
                self._refresh_templates_table
            )

    def _on_duplicate_template(self):
        tid = self._selected_template_id()
        if tid is None:
            QMessageBox.information(self, "Select a template", "Click a row first.")
            return
        self._tpl_svc.duplicate_template(tid)
        self._refresh_templates_table()

    def _on_delete_template(self):
        tid = self._selected_template_id()
        if tid is None:
            QMessageBox.information(self, "Select a template", "Click a row first.")
            return
        tpl = self._tpl_svc.get_template(tid)
        if not tpl:
            return
        ans = QMessageBox.question(
            self, "Delete template",
            f"Delete template '{tpl.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._tpl_svc.delete_template(tid)
            self._refresh_templates_table()

    # ── Backup & Restore ───────────────────────────────────────────────────────

    def _build_backup_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        hdr = QLabel("BACKUP & RESTORE")
        hdr.setObjectName("sectionHeader")
        layout.addWidget(hdr)

        run_btn = QPushButton("[ RUN MAINTENANCE NOW ]")
        run_btn.clicked.connect(self._run_maintenance)
        layout.addWidget(run_btn)

        layout.addWidget(QLabel("Available backups:"))

        self._backup_table = QTableWidget()
        self._backup_table.setColumnCount(2)
        self._backup_table.setHorizontalHeaderLabels(["FILE", "RESTORE"])
        self._backup_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._backup_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._backup_table.setColumnWidth(1, 160)
        self._backup_table.verticalHeader().setDefaultSectionSize(40)
        self._backup_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._backup_table.verticalHeader().setVisible(False)
        layout.addWidget(self._backup_table, 1)

        self._refresh_backup_list()
        return page

    def _refresh_backup_list(self):
        import os
        from src.core.database import DB_PATH
        backup_dir = os.path.join(os.path.dirname(DB_PATH), "backups")
        self._backup_table.setRowCount(0)
        if not os.path.exists(backup_dir):
            return
        files = sorted(
            [f for f in os.listdir(backup_dir) if f.endswith('.db')],
            reverse=True
        )
        self._backup_table.setRowCount(len(files))
        for row, fname in enumerate(files):
            self._backup_table.setItem(row, 0, QTableWidgetItem(fname))
            btn = QPushButton("[ RESTORE ]")
            btn.setMinimumWidth(120)
            btn.clicked.connect(lambda _, f=fname: self._restore(f))
            self._backup_table.setCellWidget(row, 1, btn)

    def _run_maintenance(self):
        from src.utils.maintenance import MaintenanceManager
        from src.core.database import DB_PATH
        import os
        backup_dir = os.path.join(os.path.dirname(DB_PATH), "backups")
        mgr = MaintenanceManager(self.db, DB_PATH, backup_dir)
        mgr.run_full_maintenance(tag="manual")
        mgr.update_last_run()
        self._refresh_backup_list()
        QMessageBox.information(self, "Maintenance", "Maintenance complete.")

    def _restore(self, filename: str):
        reply = QMessageBox.question(
            self, "Restore Backup",
            f"Restore from {filename}?\nThe app will need to restart.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from src.utils.maintenance import MaintenanceManager
        from src.core.database import DB_PATH
        import os
        backup_dir = os.path.join(os.path.dirname(DB_PATH), "backups")
        mgr = MaintenanceManager(self.db, DB_PATH, backup_dir)
        ok, msg = mgr.restore_from_backup(filename)
        QMessageBox.information(self, "Restore", msg)

    # ── Export settings ────────────────────────────────────────────────────────

    def _build_exports_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        hdr = QLabel("EXPORT SETTINGS")
        hdr.setObjectName("sectionHeader")
        layout.addWidget(hdr)

        hint = QLabel(
            "Configure defaults for Google Sheets and PDF/CSV exports. "
            "These values are stored in the database and used automatically — "
            "you will not be prompted every time."
        )
        hint.setObjectName("dimLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Google Sheets group
        sheets_grp = QGroupBox("GOOGLE SHEETS")
        sheets_form = QFormLayout(sheets_grp)
        sheets_form.setSpacing(8)

        self._sheets_id = QLineEdit()
        self._sheets_id.setPlaceholderText("Paste spreadsheet URL or ID here")
        sheets_form.addRow("Spreadsheet ID / URL:", self._sheets_id)

        creds_row = QHBoxLayout()
        self._sheets_creds = QLineEdit()
        self._sheets_creds.setPlaceholderText("Path to service-account JSON file")
        creds_browse = QPushButton("[ BROWSE ]")
        creds_browse.setFixedWidth(90)
        creds_browse.clicked.connect(self._browse_creds)
        creds_row.addWidget(self._sheets_creds)
        creds_row.addWidget(creds_browse)
        sheets_form.addRow("Credentials file:", creds_row)

        creds_note = QLabel(
            "The credentials file is a service-account JSON from Google Cloud Console.\n"
            "See docs/GOOGLE_SHEETS_EXPORT_TUTORIAL.md for setup instructions."
        )
        creds_note.setObjectName("dimLabel")
        creds_note.setWordWrap(True)
        sheets_form.addRow(creds_note)

        layout.addWidget(sheets_grp)

        # File export group
        file_grp = QGroupBox("FILE EXPORTS  (PDF / CSV)")
        file_form = QFormLayout(file_grp)
        file_form.setSpacing(8)

        dir_row = QHBoxLayout()
        self._export_dir = QLineEdit()
        self._export_dir.setPlaceholderText("Default directory for saved files (optional)")
        dir_browse = QPushButton("[ BROWSE ]")
        dir_browse.setFixedWidth(90)
        dir_browse.clicked.connect(self._browse_export_dir)
        dir_row.addWidget(self._export_dir)
        dir_row.addWidget(dir_browse)
        file_form.addRow("Default export directory:", dir_row)

        layout.addWidget(file_grp)

        save_btn = QPushButton("[ SAVE EXPORT SETTINGS ]")
        save_btn.clicked.connect(self._save_export_settings)
        layout.addWidget(save_btn)
        layout.addStretch()
        return page

    def _browse_creds(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select credentials file", "", "JSON files (*.json)"
        )
        if path:
            self._sheets_creds.setText(path)

    def _browse_export_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select export directory")
        if path:
            self._export_dir.setText(path)

    def _save_export_settings(self):
        self._config_svc.save_config(
            google_sheets_id=self._sheets_id.text().strip() or None,
            google_creds_path=self._sheets_creds.text().strip() or None,
            default_export_dir=self._export_dir.text().strip() or None,
        )
        QMessageBox.information(self, "Settings", "Export settings saved.")

    # ── Matrix chat ──────────────────────────────────────────────────────────

    def _build_matrix_page(self) -> QWidget:
        page, scroll, form = self._scroll_form_page()

        hdr = QLabel("MATRIX ENCRYPTED CHAT")
        hdr.setObjectName("sectionHeader")
        form.addRow(hdr)

        hint = QLabel(
            "Create a bot account on a Matrix server (e.g., matrix.org),\n"
            "then enter the homeserver URL and bot credentials below.\n"
            "All messages are end-to-end encrypted."
        )
        hint.setObjectName("dimLabel")
        hint.setWordWrap(True)
        form.addRow(hint)

        # Bot status indicator
        status_row = QHBoxLayout()
        self._bot_status_label = QLabel("Bot: STOPPED")
        self._bot_status_label.setObjectName("dimLabel")
        status_row.addWidget(self._bot_status_label)
        status_row.addStretch()
        self._bot_toggle_btn = QPushButton("[ START BOT ]")
        self._bot_toggle_btn.setFixedWidth(140)
        self._bot_toggle_btn.clicked.connect(self._toggle_bot)
        status_row.addWidget(self._bot_toggle_btn)
        form.addRow("Status:", status_row)

        self._matrix_homeserver = QLineEdit()
        self._matrix_homeserver.setPlaceholderText("https://abc-xyz.trycloudflare.com")
        form.addRow("Homeserver URL:", self._matrix_homeserver)

        self._matrix_bot_user = QLineEdit()
        self._matrix_bot_user.setPlaceholderText("@kavbot:kavmanager.local")
        form.addRow("Bot user ID:", self._matrix_bot_user)

        self._matrix_bot_token = QLineEdit()
        self._matrix_bot_token.setPlaceholderText("access token from login")
        self._matrix_bot_token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Bot access token:", self._matrix_bot_token)

        token_toggle = QCheckBox("Show token")
        token_toggle.toggled.connect(
            lambda checked: self._matrix_bot_token.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        form.addRow("", token_toggle)

        self._swap_timeout = QSpinBox()
        self._swap_timeout.setRange(1, 120)
        self._swap_timeout.setSuffix(" min")
        self._swap_timeout.setValue(15)
        form.addRow("Swap approval timeout:", self._swap_timeout)

        timeout_note = QLabel(
            "How long a soldier has to accept or decline a swap request\n"
            "before it expires automatically."
        )
        timeout_note.setObjectName("dimLabel")
        timeout_note.setWordWrap(True)
        form.addRow(timeout_note)

        save_btn = QPushButton("[ SAVE MATRIX SETTINGS ]")
        save_btn.clicked.connect(self._save_matrix_settings)
        form.addRow(save_btn)

        self._refresh_bot_status()
        return page

    def _get_bot_runner(self):
        return getattr(self.mw, 'bot_runner', None)

    def _refresh_bot_status(self):
        runner = self._get_bot_runner()
        if runner and runner.running:
            self._bot_status_label.setText("Bot: RUNNING  (E2E encrypted)")
            self._bot_toggle_btn.setText("[ STOP BOT ]")
        else:
            error = getattr(runner, '_error', None) if runner else None
            label = "Bot: STOPPED"
            if error:
                label += f"  (error: {error[:60]})"
            self._bot_status_label.setText(label)
            self._bot_toggle_btn.setText("[ START BOT ]")

    def _toggle_bot(self):
        runner = self._get_bot_runner()
        if runner and runner.running:
            runner.stop()
            self._refresh_bot_status()
            return

        homeserver = self._matrix_homeserver.text().strip()
        bot_user = self._matrix_bot_user.text().strip()
        bot_token = self._matrix_bot_token.text().strip()

        if not homeserver or not bot_user or not bot_token:
            QMessageBox.warning(self, "Missing fields",
                                "Enter homeserver URL, bot user ID, and access token.")
            return

        from src.api.bot import MatrixBotRunner
        new_runner = MatrixBotRunner(homeserver, bot_user, bot_token)
        new_runner.start()
        self.mw.bot_runner = new_runner
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(2000, self._refresh_bot_status)

    def _save_matrix_settings(self):
        config = self._config_svc.get_config()

        old_hs = config.matrix_homeserver_url or ""
        old_user = config.matrix_bot_user or ""
        old_token = config.matrix_bot_token or ""

        new_hs = self._matrix_homeserver.text().strip() or None
        new_user = self._matrix_bot_user.text().strip() or None
        new_token = self._matrix_bot_token.text().strip() or None

        self._config_svc.save_config(
            matrix_homeserver_url=new_hs,
            matrix_bot_user=new_user,
            matrix_bot_token=new_token,
            swap_approval_timeout_minutes=self._swap_timeout.value(),
        )

        runner = self._get_bot_runner()
        creds_changed = (old_hs != (new_hs or "") or
                         old_user != (new_user or "") or
                         old_token != (new_token or ""))

        if creds_changed and new_hs and new_token and runner and runner.running:
            ans = QMessageBox.question(
                self, "Restart bot?",
                "Matrix credentials changed. Restart the bot?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans == QMessageBox.StandardButton.Yes:
                runner.stop()
                from src.api.bot import MatrixBotRunner
                new_runner = MatrixBotRunner(new_hs, new_user, new_token)
                new_runner.start()
                self.mw.bot_runner = new_runner
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(2000, self._refresh_bot_status)

        QMessageBox.information(self, "Settings", "Matrix settings saved.")

    # ── Config load / save ─────────────────────────────────────────────────────

    def _load_config(self):
        config = self._config_svc.get_config()

        self._populate_chain_combos()
        self._unit_codename.setText(config.unit_codename or "")
        # Populate commander dropdown with active soldiers
        soldiers = SoldierService(self.db).list_active_soldiers()
        self._commander_combo.clear()
        self._commander_combo.addItem("— None —", None)
        for s in soldiers:
            self._commander_combo.addItem(s.name or f"#{s.id}", s.id)
        # Set selection by commander_soldier_id
        if config.commander_soldier_id is not None:
            idx = self._commander_combo.findData(config.commander_soldier_id)
            if idx >= 0:
                self._commander_combo.setCurrentIndex(idx)
            else:
                self._commander_combo.setCurrentIndex(0)
        else:
            # One-time backfill by legacy commander_codename is no longer performed;
            # commander_soldier_id is the single source of truth.
            self._commander_combo.setCurrentIndex(0)
        self._default_arrival.setText(config.default_arrival_time or "12:00")
        self._default_departure.setText(config.default_departure_time or "12:00")
        self._avail_buffer.setValue(config.availability_buffer_minutes or 60)

        self._night_start.setValue(config.night_start_hour or 23)
        self._night_end.setValue(config.night_end_hour or 7)
        self._min_assign.setValue(config.minimum_assignment_minutes or 30)
        self._adj_bonus.setValue(config.adjacency_bonus or -15.0)
        self._wakeup_base.setValue(config.wake_up_penalty_base or 50.0)
        self._wakeup_alpha.setValue(config.wake_up_decay_alpha or 2.0)

        self._sheets_id.setText(config.google_sheets_id or "")
        self._sheets_creds.setText(config.google_creds_path or "")
        self._export_dir.setText(config.default_export_dir or "")

        self._matrix_homeserver.setText(config.matrix_homeserver_url or "")
        self._matrix_bot_user.setText(config.matrix_bot_user or "")
        self._matrix_bot_token.setText(config.matrix_bot_token or "")
        self._swap_timeout.setValue(config.swap_approval_timeout_minutes or 15)

        # Reserve period
        if config.reserve_period_start:
            d = config.reserve_period_start
            self._reserve_start.setDate(QDate(d.year, d.month, d.day))
        else:
            self._reserve_start.setDate(self._reserve_start.minimumDate())
        if config.reserve_period_end:
            d = config.reserve_period_end
            self._reserve_end.setDate(QDate(d.year, d.month, d.day))
        else:
            self._reserve_end.setDate(self._reserve_end.minimumDate())

    def _save_config(self):
        # Validate time fields
        for val, field in [
            (self._default_arrival.text(), "Default arrival time"),
            (self._default_departure.text(), "Default departure time"),
        ]:
            try:
                h, m = map(int, val.split(':'))
                assert 0 <= h <= 23 and 0 <= m <= 59
            except Exception:
                QMessageBox.warning(
                    self, "Invalid Input",
                    f"{field} must be in HH:MM format (e.g. 12:00)."
                )
                return

        # Reserve period (min date = "Auto" / cleared)
        from datetime import datetime as _dt
        rs = self._reserve_start.date()
        reserve_start = _dt(rs.year(), rs.month(), rs.day()) if rs > self._reserve_start.minimumDate() else None
        re_ = self._reserve_end.date()
        reserve_end = _dt(re_.year(), re_.month(), re_.day()) if re_ > self._reserve_end.minimumDate() else None

        # Chain of command — validate no duplicates
        chain: list[int] = []
        seen: set[int] = set()
        for combo in self._chain_combos:
            sid = combo.currentData()
            if sid is not None:
                if sid in seen:
                    QMessageBox.warning(
                        self, "Duplicate",
                        "A soldier can only appear once in the chain of command."
                    )
                    return
                seen.add(sid)
                chain.append(sid)

        unit_codename = self._unit_codename.text().strip()

        self._config_svc.save_config(
            unit_codename=unit_codename,
            commander_soldier_id=self._commander_combo.currentData(),
            default_arrival_time=self._default_arrival.text().strip(),
            default_departure_time=self._default_departure.text().strip(),
            availability_buffer_minutes=self._avail_buffer.value(),
            night_start_hour=self._night_start.value(),
            night_end_hour=self._night_end.value(),
            minimum_assignment_minutes=self._min_assign.value(),
            adjacency_bonus=self._adj_bonus.value(),
            wake_up_penalty_base=self._wakeup_base.value(),
            wake_up_decay_alpha=self._wakeup_alpha.value(),
            reserve_period_start=reserve_start,
            reserve_period_end=reserve_end,
            command_chain=chain,
        )

        # Update window title and top-bar label if unit codename changed
        self.mw.update_unit_name(unit_codename or "")

        QMessageBox.information(self, "Settings", "Settings saved.")

    def refresh(self):
        self._load_config()
        self._refresh_bot_status()

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _scroll_form_page() -> tuple[QWidget, QScrollArea, QFormLayout]:
        """Creates a scrollable form page and returns (page, scroll, form_layout)."""
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        form = QFormLayout(inner)
        form.setContentsMargins(20, 20, 20, 20)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        scroll.setWidget(inner)
        page_layout.addWidget(scroll)
        return page, scroll, form


# ── Role edit dialog (used by settings_tab roles page) ────────────────────────

class _RoleDialog:
    """Small modal to create or edit a Role."""

    def __init__(self, config_svc, role=None, parent=None):
        from PyQt6.QtWidgets import (
            QDialog, QFormLayout, QDialogButtonBox, QLineEdit, QComboBox
        )
        self._config_svc = config_svc
        self._role = role

        dlg = QDialog(parent)
        dlg.setWindowTitle("Edit Role" if role else "Add Role")
        dlg.setModal(True)
        dlg.setMinimumWidth(380)
        self._dlg = dlg

        form = QFormLayout(dlg)
        form.setSpacing(10)
        form.setContentsMargins(16, 16, 16, 16)

        self._name  = QLineEdit(role.name if role else "")
        self._name.setPlaceholderText("e.g. FPV Drone Operator")
        self._desc  = QLineEdit(role.description if role else "")
        self._desc.setPlaceholderText("Short description")

        # Parent dropdown — sorted alphabetically, with "(none)" option
        self._parent_combo = QComboBox()
        self._parent_combo.addItem("(none)", userData=None)
        all_roles = config_svc.list_roles()
        for r in all_roles:
            if role and r.id == role.id:
                continue          # can't be own parent
            self._parent_combo.addItem(r.name, userData=r.id)

        # Pre-select current parent
        if role and role.parent_role_id:
            for i in range(self._parent_combo.count()):
                if self._parent_combo.itemData(i) == role.parent_role_id:
                    self._parent_combo.setCurrentIndex(i)
                    break

        form.addRow("Name *:",         self._name)
        form.addRow("Description:",    self._desc)
        form.addRow("Extends (parent):", self._parent_combo)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

    def _on_save(self):
        from PyQt6.QtWidgets import QMessageBox
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self._dlg, "Validation", "Name is required.")
            return
        if not self._role:
            if self._config_svc.get_role_by_name(name):
                QMessageBox.warning(self._dlg, "Duplicate", f"Role '{name}' already exists.")
                return
            self._config_svc.create_role(
                name=name,
                description=self._desc.text().strip() or None,
                parent_role_id=self._parent_combo.currentData(),
            )
        else:
            self._config_svc.update_role(
                self._role.id,
                name=name,
                description=self._desc.text().strip() or None,
                parent_role_id=self._parent_combo.currentData(),
            )
        self._dlg.accept()

    def exec_and_refresh(self, refresh_fn):
        from PyQt6.QtWidgets import QDialog
        if self._dlg.exec() == QDialog.DialogCode.Accepted:
            refresh_fn()


class _TemplateEditDialog:
    """Modal dialog to create or edit a TaskTemplate."""

    def __init__(self, tpl_svc, template=None, parent=None):
        from PyQt6.QtWidgets import (
            QDialog, QFormLayout, QDialogButtonBox, QLineEdit, QCheckBox
        )
        from src.ui.widgets import NoScrollSpinBox
        from src.ui.widgets.searchable_select import SearchableSelectWidget
        from src.services.config_service import ConfigService

        self._tpl_svc = tpl_svc
        self._template = template

        dlg = QDialog(parent)
        dlg.setWindowTitle("Edit Template" if template else "New Template")
        dlg.setModal(True)
        dlg.setMinimumWidth(440)
        self._dlg = dlg

        form = QFormLayout(dlg)
        form.setSpacing(10)
        form.setContentsMargins(16, 16, 16, 16)

        self._name = QLineEdit(template.name if template else "")
        self._name.setPlaceholderText("Template name")
        form.addRow("Name *:", self._name)

        # Start time (hour + minute on 15-min grid)
        from PyQt6.QtWidgets import QHBoxLayout, QWidget
        start_row = QWidget()
        start_layout = QHBoxLayout(start_row)
        start_layout.setContentsMargins(0, 0, 0, 0)
        start_layout.setSpacing(4)
        self._start_hour = NoScrollSpinBox()
        self._start_hour.setRange(0, 23)
        self._start_hour.setFixedWidth(72)
        self._start_minute = NoScrollSpinBox()
        self._start_minute.setRange(0, 45)
        self._start_minute.setSingleStep(15)
        self._start_minute.setFixedWidth(72)
        start_layout.addWidget(self._start_hour)
        start_layout.addWidget(QLabel(":"))
        start_layout.addWidget(self._start_minute)
        start_layout.addStretch()
        form.addRow("Start time:", start_row)

        end_row = QWidget()
        end_layout = QHBoxLayout(end_row)
        end_layout.setContentsMargins(0, 0, 0, 0)
        end_layout.setSpacing(4)
        self._end_hour = NoScrollSpinBox()
        self._end_hour.setRange(0, 23)
        self._end_hour.setFixedWidth(72)
        self._end_minute = NoScrollSpinBox()
        self._end_minute.setRange(0, 45)
        self._end_minute.setSingleStep(15)
        self._end_minute.setFixedWidth(72)
        end_layout.addWidget(self._end_hour)
        end_layout.addWidget(QLabel(":"))
        end_layout.addWidget(self._end_minute)
        end_layout.addStretch()
        form.addRow("End time:", end_row)

        self._count = QSpinBox()
        self._count.setRange(1, 50)
        self._count.setFixedWidth(90)
        form.addRow("Soldiers:", self._count)

        self._hardness = QSpinBox()
        self._hardness.setRange(1, 5)
        self._hardness.setValue(3)
        self._hardness.setFixedWidth(90)
        form.addRow("Difficulty (1–5):", self._hardness)

        self._fractionable = QCheckBox("Fractionable")
        self._fractionable.setChecked(True)
        form.addRow("", self._fractionable)

        # Role selector
        from PyQt6.QtWidgets import QGroupBox, QVBoxLayout
        roles_group = QGroupBox("ROLE REQUIREMENTS")
        roles_layout = QVBoxLayout(roles_group)
        roles_layout.setContentsMargins(4, 4, 4, 4)
        self._roles_widget = SearchableSelectWidget(show_quantity=True)
        db = tpl_svc.db
        config_svc = ConfigService(db)
        roles = config_svc.list_roles_for_picker()
        self._roles_widget.set_items([(r.name, r.name) for r in roles])
        roles_layout.addWidget(self._roles_widget)
        form.addRow(roles_group)

        # Load existing template data
        if template:
            sh, sm = map(int, template.start_time_of_day.split(':'))
            eh, em = map(int, template.end_time_of_day.split(':'))
            self._start_hour.setValue(sh)
            self._start_minute.setValue(sm)
            self._end_hour.setValue(eh)
            self._end_minute.setValue(em)
            self._count.setValue(template.required_count or 1)
            self._hardness.setValue(template.hardness or 3)
            self._fractionable.setChecked(bool(template.is_fractionable))
            rl = template.required_roles_list or {}
            if isinstance(rl, dict):
                self._roles_widget.set_selected(rl)
            elif isinstance(rl, list):
                self._roles_widget.set_selected({v: 1 for v in rl})

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

    def _on_save(self):
        from PyQt6.QtWidgets import QMessageBox
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self._dlg, "Validation", "Name is required.")
            return

        start_tod = f"{self._start_hour.value():02d}:{self._start_minute.value():02d}"
        end_tod = f"{self._end_hour.value():02d}:{self._end_minute.value():02d}"
        roles = {n: q for n, q in self._roles_widget.get_selected()}

        try:
            if not self._template:
                self._tpl_svc.create_template(
                    name=name,
                    start_time_of_day=start_tod,
                    end_time_of_day=end_tod,
                    is_fractionable=self._fractionable.isChecked(),
                    required_count=self._count.value(),
                    required_roles_list=roles,
                    hardness=self._hardness.value(),
                )
            else:
                self._tpl_svc.update_template(
                    self._template.id,
                    name=name,
                    start_time_of_day=start_tod,
                    end_time_of_day=end_tod,
                    is_fractionable=self._fractionable.isChecked(),
                    required_count=self._count.value(),
                    required_roles_list=roles,
                    hardness=self._hardness.value(),
                )
        except ValueError as e:
            QMessageBox.warning(self._dlg, "Validation", str(e))
            return

        self._dlg.accept()

    def exec_and_refresh(self, refresh_fn):
        from PyQt6.QtWidgets import QDialog
        if self._dlg.exec() == QDialog.DialogCode.Accepted:
            refresh_fn()
