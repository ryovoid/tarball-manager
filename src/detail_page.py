# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

"""App detail page — identity, update settings, and the destructive bits."""

import gettext
import os
import threading

import gi
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gio, GLib

from .update_checker import UpdateChecker, is_newer
from . import widgets

_ = gettext.gettext

MODES = ['github', 'webpage', 'manual']


class DetailPage:
    """Detail view for a single installed app.

    Leads with what the app is, then how it updates, then how to remove it.
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
        return self._view

    # ── Layout ──────────────────────────────────────────

    def _build(self):
        metadata = self._metadata
        self._view = Adw.ToolbarView()

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(
            title=metadata.get('display_name', self._app_name),
            subtitle=metadata.get('version', '')))
        self._view.add_top_bar(header)

        self._banner = Adw.Banner(button_label=_('Update'), revealed=False)
        self._banner.connect('button-clicked', self._on_update_clicked)
        self._view.add_top_bar(self._banner)

        self._toast_overlay = Adw.ToastOverlay()
        self._view.set_content(self._toast_overlay)

        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    vexpand=True)
        self._toast_overlay.set_child(scroll)

        clamp = Adw.Clamp(maximum_size=620, margin_start=18, margin_end=18,
                          margin_top=18, margin_bottom=24)
        scroll.set_child(clamp)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        clamp.set_child(content)

        content.append(self._build_hero())
        content.append(self._build_details_group())
        content.append(self._build_update_group())
        content.append(self._build_actions_group())

        self._refresh_status()

    def _build_hero(self):
        metadata = self._metadata
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)

        pills = [(metadata.get('version') or _('Unknown version'), 'accent')]
        if metadata.get('architecture'):
            pills.append((metadata['architecture'], None))
        pills.append((widgets.scope_label(metadata.get('scope', 'user')), None))

        box.append(widgets.hero(
            icon_name=metadata.get('icon_name'),
            icon_path=metadata.get('icon_path'),
            name=metadata.get('display_name', self._app_name),
            pills=pills,
        ))

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
                          halign=Gtk.Align.CENTER)
        launch_btn = Gtk.Button(
            child=widgets.button_content(_('Launch'), 'media-playback-start-symbolic'),
            css_classes=['suggested-action', 'pill'])
        launch_btn.connect('clicked', self._on_launch_clicked)
        launch_btn.set_visible(self._launch_target() is not None)
        buttons.append(launch_btn)

        check_btn = Gtk.Button(
            child=widgets.button_content(_('Check for Updates'),
                                         'emblem-synchronizing-symbolic'),
            css_classes=['pill'])
        check_btn.connect('clicked', self._on_check_update)
        self._check_btn = check_btn
        buttons.append(check_btn)

        box.append(buttons)
        return box

    def _build_details_group(self):
        metadata = self._metadata
        group = Adw.PreferencesGroup(title=_('Installation'))

        group.add(widgets.detail_row(
            _('Location'), metadata.get('install_dir', '—'), copyable=True))

        symlink = metadata.get('symlink_path') or metadata.get('symlink')
        if symlink:
            group.add(widgets.detail_row(
                _('Terminal Command'), os.path.basename(symlink), copyable=True))

        group.add(widgets.detail_row(
            _('Installed'), self._format_date(metadata.get('installed_at', ''))))
        if metadata.get('updated_at'):
            group.add(widgets.detail_row(
                _('Last Updated'), self._format_date(metadata['updated_at'])))
        if metadata.get('tarball_name'):
            group.add(widgets.detail_row(_('From'), metadata['tarball_name']))

        return group

    def _build_update_group(self):
        metadata = self._metadata
        group = Adw.PreferencesGroup(
            title=_('Updates'),
            description=_('How Tarball Manager looks for new versions'))

        source_type = metadata.get('update_source_type', 'github')
        self._mode_row = Adw.ComboRow(
            title=_('Check Method'),
            model=Gtk.StringList.new([_('GitHub Releases'), _('Web Page'),
                                      _('Manual only')]))
        self._mode_row.set_selected(
            MODES.index(source_type) if source_type in MODES else 0)
        self._mode_row.connect('notify::selected', self._on_mode_changed)
        group.add(self._mode_row)

        self._source_entry = Adw.EntryRow(title=_('Source'))
        self._source_entry.set_text(metadata.get('update_source', ''))
        self._source_entry.connect('notify::text', self._on_source_changed)
        group.add(self._source_entry)

        self._status_row = Adw.ActionRow(title=_('Status'))
        self._status_badge = widgets.pill('')
        self._status_row.add_suffix(self._status_badge)
        self._check_spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        self._status_row.add_suffix(self._check_spinner)
        group.add(self._status_row)

        self._update_entry_for_mode()
        return group

    def _build_actions_group(self):
        group = Adw.PreferencesGroup(title=_('Manage'))

        update_row = Adw.ActionRow(
            title=_('Update from a Tarball'),
            subtitle=_('Install a newer build over this one, keeping its settings'),
            activatable=True)
        update_row.add_suffix(Gtk.Image(icon_name='go-next-symbolic',
                                        css_classes=['dim-label']))
        update_row.connect('activated', self._on_update_clicked)
        group.add(update_row)

        uninstall_row = Adw.ActionRow(
            title=_('Uninstall'),
            subtitle=_('Remove the app, its launcher, icon and shortcut'),
            activatable=True,
            css_classes=['tm-destructive-row'])
        uninstall_row.add_suffix(Gtk.Image(icon_name='user-trash-symbolic'))
        uninstall_row.connect('activated', self._on_uninstall_clicked)
        group.add(uninstall_row)

        return group

    # ── Update settings ─────────────────────────────────

    def _update_entry_for_mode(self):
        """Shapes the source entry to whichever check method is selected."""
        mode = MODES[self._mode_row.get_selected()]
        if mode == 'github':
            self._source_entry.set_title(_('GitHub Repository'))
            self._source_entry.set_tooltip_text(_('For example: ryovoid/tarball-manager'))
            self._source_entry.set_visible(True)
        elif mode == 'webpage':
            self._source_entry.set_title(_('Download Page URL'))
            self._source_entry.set_tooltip_text(_('For example: https://example.com/download'))
            self._source_entry.set_visible(True)
        else:
            self._source_entry.set_visible(False)

    def _refresh_status(self):
        """Recomputes the status row and the update banner."""
        metadata = self._metadata
        version = metadata.get('version', '')
        latest = metadata.get('latest_version')
        mode = metadata.get('update_source_type', 'github')
        source = metadata.get('update_source', '')

        self._status_badge.set_css_classes(['tm-pill'])
        has_update = bool(latest and is_newer(latest, version))

        if mode == 'manual':
            self._status_row.set_subtitle(_('Checks are off — update by hand'))
            self._status_badge.set_label(_('Manual'))
        elif not source:
            self._status_row.set_subtitle(_('Add a source to enable checking'))
            self._status_badge.set_label(_('Not set up'))
        elif has_update:
            self._status_row.set_subtitle(
                _('%(current)s is installed') % {'current': version})
            self._status_badge.set_label(_('v%s available') % latest)
            self._status_badge.add_css_class('accent')
        elif latest:
            self._status_row.set_subtitle(self._checked_subtitle())
            self._status_badge.set_label(_('Up to date'))
            self._status_badge.add_css_class('success')
        else:
            self._status_row.set_subtitle(_('Never checked'))
            self._status_badge.set_label('—')

        self._banner.set_title(
            _('Version %s is available') % latest if has_update else '')
        self._banner.set_revealed(has_update)

    def _checked_subtitle(self):
        checked = self._metadata.get('last_checked')
        if not checked:
            return _('Up to date')
        return _('Last checked %s') % self._format_date(checked)

    def _on_mode_changed(self, row, _pspec):
        mode = MODES[row.get_selected()]
        self._store.update_metadata(self._app_name, {'update_source_type': mode})
        self._metadata['update_source_type'] = mode
        self._update_entry_for_mode()
        self._refresh_status()

    def _on_source_changed(self, _entry, _pspec):
        """Debounced save — waits 500ms after the user stops typing."""
        if self._repo_save_id:
            GLib.source_remove(self._repo_save_id)
        self._repo_save_id = GLib.timeout_add(500, self._save_source)

    def _save_source(self):
        self._repo_save_id = None
        value = self._source_entry.get_text().strip()
        self._store.set_update_source(self._app_name, value)
        self._metadata['update_source'] = value
        self._refresh_status()
        return GLib.SOURCE_REMOVE

    def _on_check_update(self, _widget):
        """Checks this app for updates in a background thread."""
        self._check_spinner.start()
        self._check_btn.set_sensitive(False)

        def _worker():
            result = self._checker.check_one(self._app_name)
            GLib.idle_add(self._on_update_result, result)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_update_result(self, result):
        self._check_spinner.stop()
        self._check_btn.set_sensitive(True)
        self._metadata = self._store.get_install(self._app_name) or {}
        self._refresh_status()

        if result.get('error'):
            dialog = Adw.AlertDialog(heading=_('Check Failed'), body=result['error'])
            dialog.add_response('ok', _('OK'))
            dialog.present(self._view.get_root())
        elif not result.get('has_update'):
            self._toast_overlay.add_toast(
                Adw.Toast(title=_('No newer version found'), timeout=3))

    # ── Launch ──────────────────────────────────────────

    def _launch_target(self):
        desktop_path = self._metadata.get('desktop_entry_path')
        if desktop_path and os.path.exists(desktop_path):
            return Gio.DesktopAppInfo.new_from_filename(desktop_path)
        return None

    def _on_launch_clicked(self, _btn):
        app_info = self._launch_target()
        if not app_info:
            return
        try:
            app_info.launch(None, None)
        except GLib.Error as error:
            self._toast_overlay.add_toast(
                Adw.Toast(title=_('Could not launch: %s') % error.message, timeout=4))

    # ── Actions ─────────────────────────────────────────

    def _on_update_clicked(self, _widget):
        """Starts the update flow — the user provides a new tarball."""
        self._on_update_requested(self._app_name)

    def _on_uninstall_clicked(self, _row):
        """Shows the confirmation dialog, then uninstalls."""
        display_name = self._metadata.get('display_name', self._app_name)

        dialog = Adw.AlertDialog(
            heading=_('Uninstall %s?') % display_name,
            body=_('This removes the application, its desktop entry, icon and '
                   'command-line shortcut. It cannot be undone.'),
        )
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('uninstall', _('Uninstall'))
        dialog.set_response_appearance('uninstall', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', self._on_uninstall_confirmed)
        dialog.present(self._view.get_root())

    def _on_uninstall_confirmed(self, _dialog, response):
        if response != 'uninstall':
            return

        def _worker():
            try:
                result = self._service.uninstall(self._app_name)
            except Exception as error:
                result = {'success': False, 'error': str(error)}
            GLib.idle_add(self._on_uninstall_done, result)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_uninstall_done(self, result):
        if result.get('success'):
            self._on_uninstalled(self._app_name)
        else:
            dialog = Adw.AlertDialog(
                heading=_('Uninstall Failed'),
                body=result.get('error', _('Unknown error')))
            dialog.add_response('ok', _('OK'))
            dialog.present(self._view.get_root())

    @staticmethod
    def _format_date(iso_string):
        """Formats an ISO date string into something readable."""
        if not iso_string:
            return '—'
        try:
            from datetime import datetime
            return datetime.fromisoformat(iso_string).strftime('%d %B %Y, %H:%M')
        except (ValueError, TypeError):
            return iso_string
