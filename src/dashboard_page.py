# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

"""Dashboard page — shows all installed apps with status badges."""

import gettext
import threading

import gi
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gio

from .update_checker import UpdateChecker, is_newer, SOURCE_MANUAL

_ = gettext.gettext


class DashboardPage:
    """Builds and manages the installed-apps dashboard.

    Displayed as the home/root page in the navigation view.
    """

    def __init__(self, store, on_install_clicked, on_app_clicked):
        self._store = store
        self._checker = UpdateChecker(store)
        self._on_install_clicked = on_install_clicked
        self._on_app_clicked = on_app_clicked
        self._rows = {}  # app_name → Adw.ActionRow
        self._build()

    @property
    def widget(self):
        return self._page

    def _build(self):
        self._page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header bar with actions
        header = Adw.HeaderBar()
        header.set_centering_policy(Adw.CenteringPolicy.STRICT)

        # + Install button (left)
        install_btn = Gtk.Button(icon_name='list-add-symbolic',
                                 tooltip_text=_('Install new app'))
        install_btn.connect('clicked', lambda _: self._on_install_clicked())
        header.pack_start(install_btn)

        # Refresh button (right)
        self._refresh_btn = Gtk.Button(icon_name='view-refresh-symbolic',
                                       tooltip_text=_('Check all for updates'))
        self._refresh_btn.connect('clicked', self._on_check_all_updates)
        header.pack_end(self._refresh_btn)

        # Menu button (right)
        menu_btn = Gtk.MenuButton(icon_name='open-menu-symbolic',
                                   tooltip_text=_('Main menu'))
        menu = Gio.Menu()
        menu.append(_('About Tarball Manager'), 'app.about')
        menu_btn.set_menu_model(menu)
        header.pack_end(menu_btn)

        self._page.append(header)

        # Toast overlay for notifications
        self._toast_overlay = Adw.ToastOverlay()
        self._page.append(self._toast_overlay)

        # Scrollable content
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                     vexpand=True)
        self._toast_overlay.set_child(scroll)

        clamp = Adw.Clamp(maximum_size=600, margin_start=24, margin_end=24,
                          margin_top=20, margin_bottom=24)
        scroll.set_child(clamp)

        self._content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        clamp.set_child(self._content_box)

        # Empty state
        self._empty_state = Adw.StatusPage(
            icon_name='package-x-generic-symbolic',
            title=_('No Apps Installed'),
            description=_('Install your first app from a tarball'),
        )
        empty_btn = Gtk.Button(label=_('Install App'), css_classes=['btn-primary'],
                               halign=Gtk.Align.CENTER)
        empty_btn.connect('clicked', lambda _: self._on_install_clicked())
        self._empty_state.set_child(empty_btn)
        self._content_box.append(self._empty_state)

        # Apps list group
        self._apps_group = Adw.PreferencesGroup(
            title=_('Installed Applications'),
            description=_('Apps installed via Tarball Manager'),
        )
        self._content_box.append(self._apps_group)

        # Update all button (shown when updates available)
        self._update_all_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                        halign=Gtk.Align.CENTER, margin_top=8)
        self._update_all_btn = Gtk.Button(
            label=_('View Updates'),
            css_classes=['btn-primary'],
        )
        self._update_all_btn.connect('clicked', self._on_update_all)
        self._update_all_box.append(self._update_all_btn)
        self._content_box.append(self._update_all_box)
        self._update_all_box.set_visible(False)

    def refresh(self):
        """Reloads the installed apps list from the metadata store."""
        self._store.reload()  # Re-read from disk
        installs = self._store.list_installs()

        # Clear existing rows
        for row in self._rows.values():
            self._apps_group.remove(row)
        self._rows.clear()

        # Toggle empty state vs app list
        has_apps = len(installs) > 0
        self._empty_state.set_visible(not has_apps)
        self._apps_group.set_visible(has_apps)
        self._update_all_box.set_visible(False)

        if not has_apps:
            return

        for app_name, metadata in installs:
            row = self._create_app_row(app_name, metadata)
            self._apps_group.add(row)
            self._rows[app_name] = row

    def _create_app_row(self, app_name, metadata):
        """Creates a single app row for the list."""
        display_name = metadata.get('display_name', app_name)
        version = metadata.get('version', _('Unknown'))
        scope = metadata.get('scope', 'user')
        scope_label = _('System') if scope == 'system' else _('User')

        row = Adw.ActionRow(
            title=display_name,
            subtitle=f'{_("Version")} {version}  ·  {scope_label}',
            activatable=True,
        )

        # App icon (use installed icon or fallback)
        icon_name = metadata.get('icon_name', 'application-x-executable')
        icon = Gtk.Image(icon_name=icon_name, pixel_size=32)
        icon.add_css_class('app-row-icon')
        row.add_prefix(icon)

        # Status badge suffix
        badge = Gtk.Label(css_classes=['app-status-badge'])
        latest = metadata.get('latest_version')
        if latest and is_newer(latest, version):
            badge.set_label(f'⬆ {latest}')
            badge.add_css_class('badge-update')
        elif metadata.get('update_source'):
            badge.set_label('✓')
            badge.add_css_class('badge-ok')
        else:
            badge.set_label('')
        row.add_suffix(badge)

        # Arrow
        arrow = Gtk.Image(icon_name='go-next-symbolic')
        row.add_suffix(arrow)

        row.connect('activated', lambda _, n=app_name: self._on_app_clicked(n))
        return row

    def _on_check_all_updates(self, _btn):
        """Checks all apps for updates in a background thread."""
        self._refresh_btn.set_sensitive(False)
        self._toast_overlay.add_toast(
            Adw.Toast(title=_('Checking for updates…'), timeout=2)
        )

        def _worker():
            results = self._checker.check_all()
            GLib.idle_add(self._on_updates_checked, results)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

    def _on_updates_checked(self, results):
        """Callback when update check completes."""
        self._refresh_btn.set_sensitive(True)
        self.refresh()  # Rebuild rows with new cached versions

        updates_available = sum(1 for r in results.values() if r.get('has_update'))
        errors = sum(1 for r in results.values() if r.get('error'))

        if updates_available > 0:
            self._update_all_box.set_visible(True)
            self._toast_overlay.add_toast(
                Adw.Toast(title=_('%d update(s) available') % updates_available, timeout=3)
            )
        elif errors > 0:
            self._toast_overlay.add_toast(
                Adw.Toast(title=_('Some checks failed. Open app details for info.'), timeout=3)
            )
        else:
            self._toast_overlay.add_toast(
                Adw.Toast(title=_('All apps are up to date ✓'), timeout=3)
            )

    def _on_update_all(self, _btn):
        """Triggers the update flow for all apps with available updates."""
        for app_name, metadata in self._store.list_installs():
            latest = metadata.get('latest_version')
            version = metadata.get('version', '')
            if latest and is_newer(latest, version):
                self._on_app_clicked(app_name)
                return  # Navigate to first app that needs update
