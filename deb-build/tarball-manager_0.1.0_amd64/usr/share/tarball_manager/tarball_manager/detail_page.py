# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

"""App detail page — shows metadata, uninstall, and update controls."""

import gettext
import threading

import gi
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib

from .update_checker import UpdateChecker, is_newer

_ = gettext.gettext


class DetailPage:
    """Detail view for a single installed app.

    Shows: metadata, GitHub repo config, update status, uninstall button.
    """

    def __init__(self, app_name, store, service, on_uninstalled, on_update_requested):
        self._app_name = app_name
        self._store = store
        self._service = service
        self._checker = UpdateChecker(store)
        self._on_uninstalled = on_uninstalled
        self._on_update_requested = on_update_requested
        self._metadata = store.get_install(app_name) or {}
        self._repo_save_id = None  # debounce timer
        self._build()

    @property
    def widget(self):
        return self._page

    def _build(self):
        self._page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                     vexpand=True)
        self._page.append(scroll)

        clamp = Adw.Clamp(maximum_size=600, margin_start=24, margin_end=24,
                          margin_top=20, margin_bottom=24)
        scroll.set_child(clamp)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        clamp.set_child(content)

        # ── App Details Group ────────────────────────
        details_group = Adw.PreferencesGroup(title=_('Application Details'))

        m = self._metadata
        details = [
            (_('Display Name'), m.get('display_name', self._app_name)),
            (_('Version'), m.get('version', _('Unknown'))),
            (_('Architecture'), m.get('architecture', _('Unknown'))),
            (_('Install Scope'), _('System') if m.get('scope') == 'system' else _('User')),
            (_('Install Path'), m.get('install_dir', '—')),
            (_('Installed'), self._format_date(m.get('installed_at', ''))),
        ]
        if m.get('updated_at'):
            details.append((_('Last Updated'), self._format_date(m['updated_at'])))
        if m.get('symlink'):
            details.append((_('Terminal Command'), m['symlink']))

        for label, value in details:
            row = Adw.ActionRow(title=label, subtitle=str(value))
            details_group.add(row)

        content.append(details_group)

        # ── Update Settings Group ────────────────────
        update_group = Adw.PreferencesGroup(
            title=_('Update Settings'),
            description=_('Choose how to check for new versions'),
        )

        # Mode dropdown: GitHub / Web Page / Manual
        source_type = m.get('update_source_type', 'github')
        mode_model = Gtk.StringList.new([_('GitHub'), _('Web Page'), _('Manual')])
        self._mode_row = Adw.ComboRow(
            title=_('Check Method'),
            subtitle=_('How to detect new versions'),
            model=mode_model,
        )
        mode_map = {'github': 0, 'webpage': 1, 'manual': 2}
        self._mode_row.set_selected(mode_map.get(source_type, 0))
        self._mode_row.connect('notify::selected', self._on_mode_changed)
        update_group.add(self._mode_row)

        # Source entry (content changes based on mode)
        self._source_entry = Adw.EntryRow(title=_('Source'))
        self._source_entry.set_text(m.get('update_source', ''))
        self._source_hint = Gtk.Label(css_classes=['dim-label'])
        self._source_entry.add_suffix(self._source_hint)
        self._source_entry.connect('notify::text', self._on_source_changed)
        update_group.add(self._source_entry)

        # Apply the right hint + visibility for current mode
        self._update_entry_for_mode()

        # Check for updates button
        self._check_row = Adw.ActionRow(title=_('Check for Updates'),
                                         activatable=True)
        self._check_spinner = Gtk.Spinner()
        self._check_row.add_suffix(self._check_spinner)
        check_icon = Gtk.Image(icon_name='emblem-synchronizing-symbolic')
        self._check_row.add_suffix(check_icon)
        self._check_row.connect('activated', self._on_check_update)
        update_group.add(self._check_row)

        # Update status row
        self._update_status_row = Adw.ActionRow(title=_('Status'))
        self._update_badge = Gtk.Label(css_classes=['app-status-badge'])
        self._update_status_row.add_suffix(self._update_badge)
        self._refresh_status_badge()
        update_group.add(self._update_status_row)

        # Update button (always available — manual tarball upload)
        self._update_btn_row = Adw.ActionRow(
            title=_('Update via Tarball'),
            subtitle=_('Drop a new tarball to update this app'),
            activatable=True,
            css_classes=['update-action-row'],
        )
        update_icon = Gtk.Image(icon_name='software-update-available-symbolic')
        self._update_btn_row.add_suffix(update_icon)
        self._update_btn_row.connect('activated', self._on_update_clicked)
        update_group.add(self._update_btn_row)

        content.append(update_group)

        # ── Actions Group ────────────────────────────
        actions_group = Adw.PreferencesGroup(title=_('Actions'))

        # Uninstall row
        uninstall_row = Adw.ActionRow(
            title=_('Uninstall Application'),
            subtitle=_('Remove all installed files'),
            activatable=True,
            css_classes=['destructive-action-row'],
        )
        uninstall_icon = Gtk.Image(icon_name='user-trash-symbolic',
                                    css_classes=['error-icon'])
        uninstall_row.add_suffix(uninstall_icon)
        uninstall_row.connect('activated', self._on_uninstall_clicked)
        actions_group.add(uninstall_row)

        content.append(actions_group)

    def _update_entry_for_mode(self):
        """Updates the source entry hint and visibility based on selected mode."""
        mode_idx = self._mode_row.get_selected()
        if mode_idx == 0:  # GitHub
            self._source_entry.set_title(_('GitHub Repository'))
            self._source_hint.set_label('owner/repo')
            self._source_entry.set_visible(True)
            if hasattr(self, '_check_row'):
                self._check_row.set_visible(True)
        elif mode_idx == 1:  # Web Page
            self._source_entry.set_title(_('Download Page URL'))
            self._source_hint.set_label('https://...')
            self._source_entry.set_visible(True)
            if hasattr(self, '_check_row'):
                self._check_row.set_visible(True)
        else:  # Manual
            self._source_entry.set_visible(False)
            if hasattr(self, '_check_row'):
                self._check_row.set_visible(False)

    def _refresh_status_badge(self):
        """Updates the status badge based on cached data."""
        m = self._metadata
        version = m.get('version', '')
        latest = m.get('latest_version')
        source_type = m.get('update_source_type', 'github')
        source = m.get('update_source', '')

        if source_type == 'manual':
            self._update_status_row.set_subtitle(_('Manual mode — upload tarball to update'))
            self._update_badge.set_label('—')
            self._update_badge.set_css_classes(['app-status-badge'])
        elif not source:
            self._update_status_row.set_subtitle(_('Enter a source to enable checking'))
            self._update_badge.set_label('')
            self._update_badge.set_css_classes(['app-status-badge'])
        elif latest and is_newer(latest, version):
            self._update_status_row.set_subtitle(
                _('Update available: v%s → v%s') % (version, latest)
            )
            self._update_badge.set_label(f'⬆ v{latest}')
            self._update_badge.set_css_classes(['app-status-badge', 'badge-update'])
        elif latest:
            self._update_status_row.set_subtitle(_('Up to date'))
            self._update_badge.set_label('✓')
            self._update_badge.set_css_classes(['app-status-badge', 'badge-ok'])
        else:
            self._update_status_row.set_subtitle(_('Not checked yet'))
            self._update_badge.set_label('—')
            self._update_badge.set_css_classes(['app-status-badge'])

    def _on_mode_changed(self, row, _pspec):
        """Saves the selected mode and updates UI."""
        mode_map = {0: 'github', 1: 'webpage', 2: 'manual'}
        mode = mode_map.get(row.get_selected(), 'github')
        self._store.update_metadata(self._app_name, {'update_source_type': mode})
        self._metadata['update_source_type'] = mode
        self._update_entry_for_mode()
        self._refresh_status_badge()

    def _on_source_changed(self, entry, _pspec):
        """Debounced save — waits 500ms after user stops typing."""
        if self._repo_save_id:
            GLib.source_remove(self._repo_save_id)
        self._repo_save_id = GLib.timeout_add(500, self._save_source)

    def _save_source(self):
        """Actually persists the source text to disk."""
        self._repo_save_id = None
        value = self._source_entry.get_text().strip()
        self._store.set_update_source(self._app_name, value)
        return GLib.SOURCE_REMOVE

    def _on_check_update(self, _row):
        """Checks this app for updates in a background thread."""
        self._check_spinner.start()

        def _worker():
            result = self._checker.check_one(self._app_name)
            GLib.idle_add(self._on_update_result, result)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

    def _on_update_result(self, result):
        """Callback when single-app update check completes."""
        self._check_spinner.stop()
        self._metadata = self._store.get_install(self._app_name) or {}
        self._refresh_status_badge()

        if result.get('error'):
            dialog = Adw.AlertDialog(
                heading=_('Check Failed'),
                body=result['error'],
            )
            dialog.add_response('ok', _('OK'))
            dialog.present(self._page.get_root())

    def _on_update_clicked(self, _row):
        """Starts the update flow — user provides new tarball."""
        self._on_update_requested(self._app_name)

    def _on_uninstall_clicked(self, _row):
        """Shows confirmation dialog, then uninstalls."""
        display_name = self._metadata.get('display_name', self._app_name)

        dialog = Adw.AlertDialog(
            heading=_('Uninstall %s?') % display_name,
            body=_('This will remove the application, its desktop entry, icon, and command-line shortcut. This cannot be undone.'),
        )
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('uninstall', _('Uninstall'))
        dialog.set_response_appearance('uninstall', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', self._on_uninstall_confirmed)
        dialog.present(self._page.get_root())

    def _on_uninstall_confirmed(self, dialog, response):
        """Handles uninstall after user confirms."""
        if response != 'uninstall':
            return

        m = self._metadata
        scope = m.get('scope', 'user')

        def _worker():
            try:
                result = self._service.uninstall(self._app_name)
                GLib.idle_add(self._on_uninstall_done, result)
            except Exception as e:
                GLib.idle_add(self._on_uninstall_done, {'success': False, 'error': str(e)})

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

    def _on_uninstall_done(self, result):
        """Callback after uninstall completes."""
        if result.get('success'):
            self._on_uninstalled(self._app_name)
        else:
            dialog = Adw.AlertDialog(
                heading=_('Uninstall Failed'),
                body=result.get('error', _('Unknown error')),
            )
            dialog.add_response('ok', _('OK'))
            dialog.present(self._page.get_root())

    @staticmethod
    def _format_date(iso_string):
        """Formats an ISO date string to a human-readable form."""
        if not iso_string:
            return '—'
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(iso_string)
            return dt.strftime('%B %d, %Y at %H:%M')
        except (ValueError, TypeError):
            return iso_string
