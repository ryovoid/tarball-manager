# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

"""Dashboard page — the library of everything installed via Tarball Manager."""

import gettext
import threading

import gi
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gdk, Gio, GLib, GObject

from .update_checker import UpdateChecker, is_newer
from . import widgets

_ = gettext.gettext

# Below this many apps a search field is more clutter than help.
SEARCH_THRESHOLD = 4


class DashboardPage:
    """Builds and manages the installed-apps library.

    Displayed as the home/root page in the navigation view.
    """

    def __init__(self, store, on_install_clicked, on_app_clicked, on_tarball_dropped=None):
        self._store = store
        self._checker = UpdateChecker(store)
        self._on_install_clicked = on_install_clicked
        self._on_app_clicked = on_app_clicked
        self._on_tarball_dropped = on_tarball_dropped
        self._rows = {}       # app_name → Adw.ActionRow
        self._row_terms = {}  # app_name → lowercased searchable text
        self._app_count = 0
        self._build()

    @property
    def widget(self):
        return self._view

    # ── Layout ──────────────────────────────────────────

    def _build(self):
        self._view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=_('Tarball Manager')))

        install_btn = Gtk.Button(
            child=widgets.button_content(_('Install App'), 'list-add-symbolic'),
            tooltip_text=_('Install an app from a tarball'),
            css_classes=['suggested-action'],
        )
        install_btn.connect('clicked', lambda _b: self._on_install_clicked())
        header.pack_start(install_btn)

        menu = Gio.Menu()
        menu.append(_('Keyboard Shortcuts'), 'win.show-help-overlay')
        menu.append(_('About Tarball Manager'), 'app.about')
        menu_btn = Gtk.MenuButton(icon_name='open-menu-symbolic',
                                  tooltip_text=_('Main menu'),
                                  menu_model=menu, primary=True)
        header.pack_end(menu_btn)

        self._refresh_btn = Gtk.Button(icon_name='view-refresh-symbolic',
                                       tooltip_text=_('Check all apps for updates'))
        self._refresh_btn.connect('clicked', self._on_check_all_updates)
        header.pack_end(self._refresh_btn)

        self._search_btn = Gtk.ToggleButton(icon_name='system-search-symbolic',
                                            tooltip_text=_('Search installed apps'))
        header.pack_end(self._search_btn)

        self._view.add_top_bar(header)

        # Update banner, shown only when a check turns something up
        self._banner = Adw.Banner(button_label=_('Review'), revealed=False)
        self._banner.connect('button-clicked', self._on_banner_clicked)
        self._view.add_top_bar(self._banner)

        # Search bar
        self._search_entry = Gtk.SearchEntry(placeholder_text=_('Search apps'),
                                             hexpand=True)
        self._search_entry.connect('search-changed', self._on_search_changed)
        self._search_bar = Gtk.SearchBar(child=Adw.Clamp(maximum_size=560,
                                                         child=self._search_entry))
        self._search_btn.bind_property(
            'active', self._search_bar, 'search-mode-enabled',
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )
        self._search_bar.connect('notify::search-mode-enabled',
                                 self._on_search_mode_changed)
        self._view.add_top_bar(self._search_bar)

        self._toast_overlay = Adw.ToastOverlay()
        self._view.set_content(self._toast_overlay)

        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    vexpand=True)
        self._toast_overlay.set_child(scroll)

        clamp = Adw.Clamp(maximum_size=620, margin_start=18, margin_end=18,
                          margin_top=18, margin_bottom=24)
        scroll.set_child(clamp)

        self._content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                                    vexpand=True)
        clamp.set_child(self._content_box)

        # Library list
        self._apps_group = Adw.PreferencesGroup(title=_('Your Library'))
        self._count_label = Gtk.Label(css_classes=['tm-pill'], valign=Gtk.Align.CENTER)
        self._apps_group.set_header_suffix(self._count_label)
        self._content_box.append(self._apps_group)

        # Pushes the drop hint to the bottom edge, out of the list's way
        self._spacer = Gtk.Box(vexpand=True)
        self._content_box.append(self._spacer)

        self._drop_hint = self._build_drop_hint()
        self._content_box.append(self._drop_hint)

        # "No results" for an active search
        self._no_results = Adw.StatusPage(
            icon_name='system-search-symbolic',
            title=_('No Matches'),
            description=_('No installed app matches your search'),
            vexpand=True,
            visible=False,
            css_classes=['compact'],
        )
        self._content_box.append(self._no_results)

        # Empty library
        self._empty_state = self._build_empty_state()
        self._content_box.append(self._empty_state)

        self._install_drop_target()

    def _build_drop_hint(self):
        """The space under the list is not dead air — it takes a tarball."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
                      css_classes=['tm-dropzone', 'subtle'])
        box.append(Gtk.Image(icon_name='folder-download-symbolic', pixel_size=18,
                             css_classes=['dim-label'], valign=Gtk.Align.CENTER))
        box.append(Gtk.Label(label=_('Drop a tarball here to install another app'),
                             css_classes=['tm-caption'], valign=Gtk.Align.CENTER))
        return box

    def _build_empty_state(self):
        status = Adw.StatusPage(
            icon_name='package-x-generic-symbolic',
            title=_('Nothing Installed Yet'),
            description=_('Drop a tarball anywhere on this window, or pick one '
                          'from your files, and it becomes a real desktop app.'),
            vexpand=True,
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      halign=Gtk.Align.CENTER)

        button = Gtk.Button(
            child=widgets.button_content(_('Install from Tarball'), 'list-add-symbolic'),
            css_classes=['suggested-action', 'pill'],
            halign=Gtk.Align.CENTER,
        )
        button.connect('clicked', lambda _b: self._on_install_clicked())
        box.append(button)

        formats = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                          halign=Gtk.Align.CENTER)
        for fmt in ('.tar.gz', '.tar.xz', '.tar.bz2'):
            formats.append(widgets.pill(fmt, 'outline'))
        box.append(formats)

        status.set_child(box)
        return status

    def _install_drop_target(self):
        """Lets a tarball be dropped anywhere on the dashboard."""
        if self._on_tarball_dropped is None:
            return
        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect('drop', self._on_drop)
        self._view.add_controller(drop_target)

    def _on_drop(self, _target, value, _x, _y):
        if isinstance(value, Gio.File) and value.get_path():
            self._on_tarball_dropped(value.get_path())
            return True
        return False

    # ── Content ─────────────────────────────────────────

    def refresh(self):
        """Reloads the installed apps list from the metadata store."""
        self._store.reload()
        installs = sorted(
            self._store.list_installs(),
            key=lambda item: (item[1].get('display_name') or item[0]).lower(),
        )

        for row in self._rows.values():
            self._apps_group.remove(row)
        self._rows.clear()
        self._row_terms.clear()

        has_apps = bool(installs)
        self._empty_state.set_visible(not has_apps)
        self._apps_group.set_visible(has_apps)
        self._drop_hint.set_visible(has_apps)
        self._spacer.set_visible(has_apps)
        self._no_results.set_visible(False)

        pending = 0
        for app_name, metadata in installs:
            row = self._create_app_row(app_name, metadata)
            self._apps_group.add(row)
            self._rows[app_name] = row
            self._row_terms[app_name] = ' '.join([
                app_name, metadata.get('display_name', ''),
                metadata.get('version', ''),
            ]).lower()
            if self._has_update(metadata):
                pending += 1

        self._app_count = len(installs)
        self._set_count_label(self._app_count)
        # Type-to-search only once the library is big enough to need it,
        # so the field never appears without the button that opens it
        searchable = self._app_count >= SEARCH_THRESHOLD
        self._search_btn.set_visible(searchable)
        self._search_bar.set_key_capture_widget(self._view if searchable else None)
        if not searchable:
            self._search_bar.set_search_mode(False)

        self._show_update_banner(pending)
        if self._search_entry.get_text():
            self._apply_filter(self._search_entry.get_text())

    def _set_count_label(self, shown):
        total = self._app_count
        self._count_label.set_label(
            gettext.ngettext('%d app', '%d apps', total) % total
            if shown == total else
            _('%(shown)d of %(total)d') % {'shown': shown, 'total': total}
        )

    @staticmethod
    def _has_update(metadata):
        latest = metadata.get('latest_version')
        return bool(latest and is_newer(latest, metadata.get('version', '')))

    def _create_app_row(self, app_name, metadata):
        """Creates a single app row for the library list."""
        display_name = metadata.get('display_name', app_name)
        version = metadata.get('version') or _('Unknown version')
        scope = metadata.get('scope', 'user')

        row = Adw.ActionRow(
            title=display_name,
            subtitle=f'{version}  ·  {widgets.scope_label(scope)}',
            activatable=True,
            css_classes=['tm-app-row'],
        )

        tile, image = widgets.icon_tile(size=32)
        widgets.set_app_icon(image, metadata.get('icon_name'),
                             metadata.get('icon_path'))
        row.add_prefix(tile)

        if self._has_update(metadata):
            row.add_suffix(widgets.pill(
                _('Update to %s') % metadata['latest_version'], 'accent'))
        elif metadata.get('latest_version'):
            row.add_suffix(widgets.pill(_('Up to date'), 'success'))

        row.add_suffix(Gtk.Image(icon_name='go-next-symbolic',
                                 css_classes=['dim-label']))
        row.connect('activated', lambda _r, n=app_name: self._on_app_clicked(n))
        return row

    # ── Search ──────────────────────────────────────────

    def _on_search_changed(self, entry):
        self._apply_filter(entry.get_text())

    def _on_search_mode_changed(self, search_bar, _pspec):
        if not search_bar.get_search_mode():
            self._search_entry.set_text('')

    def _apply_filter(self, text):
        needle = text.strip().lower()
        matches = 0
        for app_name, row in self._rows.items():
            visible = needle in self._row_terms.get(app_name, '')
            row.set_visible(visible)
            matches += visible

        searching = bool(needle) and bool(self._rows)
        self._set_count_label(matches)
        self._no_results.set_visible(searching and matches == 0)
        self._apps_group.set_visible(bool(self._rows) and matches > 0)
        self._drop_hint.set_visible(bool(self._rows) and not searching)
        self._spacer.set_visible(self._drop_hint.get_visible())

    # ── Updates ─────────────────────────────────────────

    def _show_update_banner(self, pending):
        if pending:
            self._banner.set_title(
                gettext.ngettext('%d update available',
                                 '%d updates available', pending) % pending
            )
            self._banner.set_revealed(True)
        else:
            self._banner.set_revealed(False)

    def _on_banner_clicked(self, _banner):
        self._open_first_outdated()

    def trigger_update_check(self):
        """Public entry point for the Ctrl+R shortcut."""
        if self._refresh_btn.get_sensitive():
            self._on_check_all_updates(self._refresh_btn)

    def focus_search(self):
        """Public entry point for the Ctrl+F shortcut."""
        if not self._rows:
            return
        self._search_btn.set_visible(True)
        self._search_bar.set_search_mode(True)
        self._search_entry.grab_focus()

    def _on_check_all_updates(self, _btn):
        """Checks all apps for updates in a background thread."""
        self._refresh_btn.set_sensitive(False)
        spinner = Gtk.Spinner(spinning=True)
        self._refresh_btn.set_child(spinner)

        def _worker():
            results = self._checker.check_all()
            GLib.idle_add(self._on_updates_checked, results)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_updates_checked(self, results):
        """Callback when the update check completes."""
        self._refresh_btn.set_sensitive(True)
        self._refresh_btn.set_child(None)
        self._refresh_btn.set_icon_name('view-refresh-symbolic')
        self.refresh()

        updates = sum(1 for r in results.values() if r.get('has_update'))
        errors = sum(1 for r in results.values() if r.get('error'))

        if updates:
            message = gettext.ngettext('%d update available',
                                       '%d updates available', updates) % updates
        elif errors:
            message = _('Some checks failed — open an app for details')
        else:
            message = _('Everything is up to date')
        self._toast_overlay.add_toast(Adw.Toast(title=message, timeout=3))

    def _open_first_outdated(self):
        """Opens the first app that has an update waiting."""
        for app_name, metadata in self._store.list_installs():
            if self._has_update(metadata):
                self._on_app_clicked(app_name)
                return
