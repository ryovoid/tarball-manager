# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

import gettext
import os
import threading

import gi
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gio, GLib, Gdk

from .install_service import InstallService
from .dashboard_page import DashboardPage
from .detail_page import DetailPage
from . import widgets

_ = gettext.gettext

APP_ID = 'io.github.ryovoid.TarballManager'

STEPS = [_('Select Tarball'), _('Review'), _('Configure'), _('Install')]
CATEGORIES = [
    'Utility', 'Development', 'Graphics', 'Game',
    'AudioVideo', 'Network', 'Office', 'Science', 'Education', 'System',
]

# Progress weights for the install steps the service reports.
INSTALL_PROGRESS = {
    'installing': 0.10, 'icon': 0.30, 'desktop': 0.50,
    'symlink': 0.65, 'refresh': 0.80, 'metadata': 0.90, 'done': 1.0,
}

TARBALL_PATTERNS = ('*.tar.gz', '*.tar.xz', '*.tar.bz2', '*.tgz', '*.txz', '*.tbz2')


class TarballManagerWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'TarballManagerWindow'

    def __init__(self, **kwargs):
        super().__init__(**kwargs, default_width=820, default_height=700)
        self.set_size_request(360, 480)
        self.set_title(_('Tarball Manager'))

        self._service = InstallService()
        self._store = self._service.store
        self._analysis = None
        self._current_step = 0
        self._update_mode = None  # app_name when updating
        self._original_icon = None
        self._custom_icon_path = None
        self._pulse_id = None

        self._load_css()
        self._load_shortcuts_window()
        self._restore_window_state()
        self._build_ui()
        self._add_actions()

    # ── CSS ──────────────────────────────────────────────

    def _load_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_resource('/io/github/ryovoid/TarballManager/style.css')
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _restore_window_state(self):
        """Reopens at the size the window was last closed at.

        The schema is only present in an installed build, so a source
        checkout simply keeps the default size instead of crashing.
        """
        source = Gio.SettingsSchemaSource.get_default()
        if source is None or source.lookup(APP_ID, True) is None:
            self._settings = None
            return

        self._settings = Gio.Settings.new(APP_ID)
        self.set_default_size(self._settings.get_int('window-width'),
                              self._settings.get_int('window-height'))
        if self._settings.get_boolean('window-maximized'):
            self.maximize()
        self.connect('close-request', self._save_window_state)

    def _save_window_state(self, *_args):
        width, height = self.get_default_size()
        self._settings.set_int('window-width', width)
        self._settings.set_int('window-height', height)
        self._settings.set_boolean('window-maximized', self.is_maximized())
        return False

    def _load_shortcuts_window(self):
        """Wires up the Ctrl+? shortcuts window shipped in the resource bundle."""
        try:
            builder = Gtk.Builder.new_from_resource(
                '/io/github/ryovoid/TarballManager/gtk/help-overlay.ui')
            self.set_help_overlay(builder.get_object('help_overlay'))
        except GLib.Error:
            pass

    # ── Actions ─────────────────────────────────────────

    def _add_actions(self):
        """Window actions, so the keyboard shortcuts window is not a fiction."""
        app = self.get_application()
        specs = [
            ('install-app', lambda *_a: self._show_install_wizard(), ['<control>n']),
            ('check-updates', lambda *_a: self._dashboard.trigger_update_check(),
             ['<control>r']),
            ('search', lambda *_a: self._dashboard.focus_search(), ['<control>f']),
        ]
        for name, callback, accels in specs:
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            self.add_action(action)
            if app:
                app.set_accels_for_action(f'win.{name}', accels)

    # ── Main layout ─────────────────────────────────────

    def _build_ui(self):
        self._nav_view = Adw.NavigationView()
        self._nav_view.connect('popped', self._on_page_popped)
        self.set_content(self._nav_view)

        self._dashboard = DashboardPage(
            store=self._store,
            on_install_clicked=self._show_install_wizard,
            on_app_clicked=self._show_app_detail,
            on_tarball_dropped=self._on_tarball_dropped,
        )
        dash_nav = Adw.NavigationPage(title=_('Tarball Manager'), tag='dashboard')
        dash_nav.set_child(self._dashboard.widget)
        self._nav_view.add(dash_nav)
        self._dashboard.refresh()

    # ── Navigation helpers ──────────────────────────────

    def _show_install_wizard(self, update_app_name=None, tarball_path=None):
        """Pushes the install wizard page onto the navigation stack."""
        self._analysis = None
        self._current_step = 0
        self._update_mode = update_app_name

        wizard_box = self._build_wizard_content()
        wizard_nav = Adw.NavigationPage(
            title=_('Update %s') % update_app_name if update_app_name else _('Install App'),
            tag='wizard',
        )
        wizard_nav.set_child(wizard_box)
        self._nav_view.push(wizard_nav)

        if tarball_path:
            self._start_analysis(tarball_path)

    def _on_tarball_dropped(self, path):
        """A tarball dropped on the dashboard goes straight into the wizard."""
        self._show_install_wizard(tarball_path=path)

    def _show_app_detail(self, app_name):
        """Pushes the app detail page onto the navigation stack."""
        detail = DetailPage(
            app_name=app_name,
            store=self._store,
            service=self._service,
            on_uninstalled=self._on_app_uninstalled,
            on_update_requested=self._on_update_requested,
        )
        metadata = self._store.get_install(app_name) or {}
        detail_nav = Adw.NavigationPage(
            title=metadata.get('display_name', app_name))
        detail_nav.set_child(detail.widget)
        self._nav_view.push(detail_nav)

    def _on_app_uninstalled(self, app_name):
        """Called after successful uninstall — pop back to the library."""
        self._nav_view.pop_to_tag('dashboard')
        self._dashboard.refresh()

    def _on_update_requested(self, app_name):
        """Called when the user wants to update — wizard in update mode."""
        self._nav_view.pop_to_tag('dashboard')
        self._show_install_wizard(update_app_name=app_name)

    # ── Wizard shell ────────────────────────────────────

    def _build_wizard_content(self):
        """Builds the install wizard UI. Returns the root widget."""
        toolbar = Adw.ToolbarView()

        self._wizard_title = Adw.WindowTitle(
            title=_('Update Application') if self._update_mode else _('Install App'),
            subtitle='',
        )
        header = Adw.HeaderBar(title_widget=self._wizard_title)
        toolbar.add_top_bar(header)

        self._toast_overlay = Adw.ToastOverlay()
        toolbar.set_content(self._toast_overlay)

        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    vexpand=True)
        self._toast_overlay.set_child(scroll)

        clamp = Adw.Clamp(maximum_size=620, margin_start=18, margin_end=18,
                          margin_top=18, margin_bottom=18)
        scroll.set_child(clamp)

        self._root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                                 vexpand=True)
        clamp.set_child(self._root_box)

        self._root_box.append(self._build_step_indicator())

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                                transition_duration=200, vexpand=True)
        self._root_box.append(self._stack)

        self._build_upload_page()
        self._build_analyzing_page()
        self._build_review_page()
        self._build_configure_page()
        self._build_install_page()

        toolbar.add_bottom_bar(self._build_action_bar())

        # A tarball can be dropped anywhere in the wizard, not just on the zone
        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect('drop', self._on_drop)
        drop_target.connect('enter', self._on_drag_enter)
        drop_target.connect('leave', self._on_drag_leave)
        scroll.add_controller(drop_target)

        self._update_step_ui()
        return toolbar

    def _build_action_bar(self):
        """Back/Continue live in a bottom bar so they never scroll out of reach."""
        self._btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self._back_btn = Gtk.Button(label=_('Back'))
        self._back_btn.connect('clicked', self._on_back)
        self._btn_box.append(self._back_btn)

        self._btn_box.append(Gtk.Box(hexpand=True))

        self._next_btn = Gtk.Button(label=_('Continue'),
                                    css_classes=['suggested-action', 'pill'])
        self._next_btn.connect('clicked', self._on_next)
        self._btn_box.append(self._next_btn)

        self._action_bar = Adw.Bin(css_classes=['tm-action-bar'])
        self._action_bar.set_child(Adw.Clamp(maximum_size=620,
                                             child=self._btn_box))
        return self._action_bar

    # ── Step indicator ──────────────────────────────────

    def _build_step_indicator(self):
        self._step_badges = []
        self._step_labels = []
        self._step_tracks = []

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                      halign=Gtk.Align.CENTER, css_classes=['tm-steps'])

        for i, name in enumerate(STEPS):
            if i > 0:
                track = Adw.Bin(css_classes=['tm-step-track'],
                                valign=Gtk.Align.CENTER, hexpand=True)
                track.set_size_request(28, 2)
                box.append(track)
                self._step_tracks.append(track)

            step = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
            badge = Gtk.Label(label=str(i + 1), css_classes=['tm-step-badge'],
                              width_request=24, height_request=24,
                              valign=Gtk.Align.CENTER)
            label = Gtk.Label(label=name, css_classes=['tm-step-label'],
                              valign=Gtk.Align.CENTER)
            step.append(badge)
            step.append(label)
            box.append(step)
            self._step_badges.append(badge)
            self._step_labels.append(label)

        # On a narrow window the labels drop out and the badges stay
        bin_ = Adw.BreakpointBin(width_request=180, height_request=48)
        bin_.set_child(box)
        breakpoint_ = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse('max-width: 460px'))
        for label in self._step_labels:
            breakpoint_.add_setter(label, 'visible', False)
        bin_.add_breakpoint(breakpoint_)
        return bin_

    def _update_step_ui(self):
        for i in range(len(STEPS)):
            badge, label = self._step_badges[i], self._step_labels[i]
            for cls in ('active', 'done'):
                badge.remove_css_class(cls)
                label.remove_css_class(cls)

            if i < self._current_step:
                badge.add_css_class('done')
                label.add_css_class('done')
                badge.set_label('✓')
            else:
                badge.set_label(str(i + 1))
                if i == self._current_step:
                    badge.add_css_class('active')
                    label.add_css_class('active')

        for i, track in enumerate(self._step_tracks):
            track.remove_css_class('done')
            if i < self._current_step:
                track.add_css_class('done')

        self._wizard_title.set_subtitle(
            _('Step %(current)d of %(total)d · %(name)s') % {
                'current': self._current_step + 1,
                'total': len(STEPS),
                'name': STEPS[self._current_step],
            }
        )

        page = self._stack.get_visible_child_name()
        self._back_btn.set_visible(0 < self._current_step < 3)
        self._next_btn.set_visible(page not in ('analyzing', 'install'))
        self._action_bar.set_visible(page in ('review', 'configure'))

        self._next_btn.set_label(
            _('Install') if self._current_step == 2 else _('Continue'))
        self._next_btn.set_sensitive(
            self._current_step != 0 or self._analysis is not None)

    # ── Page: Select ────────────────────────────────────

    def _build_upload_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)

        zone = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       valign=Gtk.Align.FILL, vexpand=True,
                       css_classes=['tm-dropzone'])
        zone.append(Gtk.Box(vexpand=True))

        tile, image = widgets.icon_tile(size=32, large=True, accent=True)
        image.set_from_icon_name('folder-download-symbolic')
        zone.append(tile)

        zone.append(Gtk.Label(
            label=_('Drop a tarball here'),
            css_classes=['tm-dropzone-title'], halign=Gtk.Align.CENTER,
            margin_top=6))
        zone.append(Gtk.Label(
            label=_('It becomes a real desktop app — launcher, icon and all'),
            css_classes=['tm-subtitle'], halign=Gtk.Align.CENTER,
            wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=40))

        browse = Gtk.Button(
            child=widgets.button_content(_('Browse Files'), 'document-open-symbolic'),
            css_classes=['suggested-action', 'pill'],
            halign=Gtk.Align.CENTER, margin_top=8)
        browse.connect('clicked', self._on_browse)
        zone.append(browse)

        formats = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                          halign=Gtk.Align.CENTER, margin_top=10)
        for fmt in ('.tar.gz', '.tar.xz', '.tar.bz2'):
            formats.append(widgets.pill(fmt, 'outline'))
        zone.append(formats)
        zone.append(Gtk.Box(vexpand=True))

        self._upload_zone = zone
        page.append(zone)
        self._stack.add_named(page, 'upload')

    def _on_drag_enter(self, *_args):
        self._upload_zone.add_css_class('drag-over')
        return Gdk.DragAction.COPY

    def _on_drag_leave(self, *_args):
        self._upload_zone.remove_css_class('drag-over')

    def _on_browse(self, _btn):
        dialog = Gtk.FileDialog(title=_('Select a Tarball'))
        file_filter = Gtk.FileFilter()
        file_filter.set_name(_('Tarball archives'))
        for pattern in TARBALL_PATTERNS:
            file_filter.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(file_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(file_filter)
        dialog.open(self, None, self._on_file_chosen)

    def _on_file_chosen(self, dialog, result):
        try:
            chosen = dialog.open_finish(result)
            if chosen:
                self._start_analysis(chosen.get_path())
        except GLib.Error:
            pass

    def _on_drop(self, _target, value, _x, _y):
        self._on_drag_leave()
        if isinstance(value, Gio.File) and value.get_path():
            self._start_analysis(value.get_path())
            return True
        return False

    # ── Page: Analyzing ─────────────────────────────────

    def _build_analyzing_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                       valign=Gtk.Align.CENTER, vexpand=True,
                       css_classes=['tm-card'])

        tile, image = widgets.icon_tile(size=32, large=True, accent=True)
        image.set_from_icon_name('package-x-generic-symbolic')
        tile.set_halign(Gtk.Align.CENTER)
        card.append(tile)

        self._analyze_title = Gtk.Label(label=_('Reading the tarball'),
                                        css_classes=['tm-progress-title'],
                                        halign=Gtk.Align.CENTER)
        self._analyze_sub = Gtk.Label(label=_('This may take a moment'),
                                      css_classes=['tm-subtitle'],
                                      halign=Gtk.Align.CENTER, wrap=True,
                                      max_width_chars=44,
                                      justify=Gtk.Justification.CENTER)
        self._analyze_progress = Gtk.ProgressBar(margin_top=18,
                                                 margin_start=24, margin_end=24)

        card.append(self._analyze_title)
        card.append(self._analyze_sub)
        card.append(self._analyze_progress)
        page.append(card)
        self._stack.add_named(page, 'analyzing')

    def _start_analysis(self, tarball_path):
        if not tarball_path:
            return
        self._stack.set_visible_child_name('analyzing')
        self._analyze_title.set_label(
            _('Reading %s') % os.path.basename(tarball_path))
        self._analyze_sub.set_label(_('Extracting tarball…'))
        self._analyze_progress.pulse()
        self._update_step_ui()

        self._pulse_id = GLib.timeout_add(80, self._pulse_analyzing)

        def on_progress(message):
            GLib.idle_add(self._analyze_sub.set_label, message)

        def worker():
            try:
                analysis = self._service.analyze(tarball_path, on_progress)
                GLib.idle_add(self._on_analysis_done, analysis, None)
            except Exception as error:
                GLib.idle_add(self._on_analysis_done, None, str(error))

        threading.Thread(target=worker, daemon=True).start()

    def _pulse_analyzing(self):
        self._analyze_progress.pulse()
        return True

    def _on_analysis_done(self, analysis, error):
        if self._pulse_id:
            GLib.source_remove(self._pulse_id)
            self._pulse_id = None

        if error:
            self._toast_overlay.add_toast(
                Adw.Toast(title=_('Could not read that tarball: %s') % error,
                          timeout=5))
            self._current_step = 0
            self._stack.set_visible_child_name('upload')
            self._update_step_ui()
            return

        self._analysis = analysis
        detected = analysis.get('icon')
        self._original_icon = dict(detected) if detected else None
        self._populate_review()
        self._populate_configure()
        self._current_step = 1
        self._stack.set_visible_child_name('review')
        self._update_step_ui()

    # ── Page: Review ────────────────────────────────────

    def _build_review_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self._review_hero_slot = Adw.Bin()
        page.append(self._review_hero_slot)

        self._review_warning = Adw.Banner(
            title=_('No executable found — you can still pick one manually'),
            revealed=False)
        page.append(self._review_warning)

        self._review_group = Adw.PreferencesGroup()
        page.append(self._review_group)

        self._review_rows = []
        self._stack.add_named(page, 'review')

    def _populate_review(self):
        analysis = self._analysis

        icon_info = analysis.get('icon') or {}
        self._review_hero_slot.set_child(widgets.hero(
            icon_path=icon_info.get('path'),
            name=analysis['display_name'],
            pills=[
                (analysis['version'], 'accent'),
                (analysis['architecture'], None),
                (analysis['extracted_size'], None),
            ],
        ))

        for row in self._review_rows:
            self._review_group.remove(row)
        self._review_rows.clear()

        binary_name = analysis['binary']['name']
        details = [
            (_('Executable'), binary_name or _('Not found')),
            (_('Tarball'), os.path.basename(analysis.get('tarball_path', ''))),
            (_('Download size'), analysis['tarball_size']),
        ]
        if not icon_info:
            details.append((_('Icon'), _('None found — add one in the next step')))

        for title, value in details:
            row = widgets.detail_row(title, value)
            self._review_group.add(row)
            self._review_rows.append(row)

        self._review_warning.set_revealed(not binary_name)

    # ── Page: Configure ─────────────────────────────────

    def _build_configure_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        identity = Adw.PreferencesGroup(
            title=_('Application Details'),
            description=_('Edit the detected values if needed'))

        self._cfg_name = Adw.EntryRow(title=_('Application Name'))
        identity.add(self._cfg_name)

        self._cfg_display = Adw.EntryRow(title=_('Display Name'))
        identity.add(self._cfg_display)

        self._cfg_exec_model = Gtk.StringList()
        self._cfg_exec = Adw.ComboRow(title=_('Executable'),
                                      subtitle=_('The binary your launcher runs'),
                                      model=self._cfg_exec_model)
        identity.add(self._cfg_exec)
        page.append(identity)

        icon_group = Adw.PreferencesGroup(
            title=_('App Icon'),
            description=_('Shown in your application menu and dock'))

        self._cfg_icon_row = Adw.ActionRow(title=_('Icon'),
                                           subtitle=_('No icon detected'))
        icon_tile, self._cfg_icon_preview = widgets.icon_tile(size=32)
        self._cfg_icon_row.add_prefix(icon_tile)

        icon_browse_btn = Gtk.Button(icon_name='document-open-symbolic',
                                     tooltip_text=_('Choose a custom icon'),
                                     valign=Gtk.Align.CENTER,
                                     css_classes=['flat'])
        icon_browse_btn.connect('clicked', self._on_icon_browse)
        self._cfg_icon_row.add_suffix(icon_browse_btn)

        self._cfg_icon_remove_btn = Gtk.Button(icon_name='edit-clear-symbolic',
                                               tooltip_text=_('Remove custom icon'),
                                               valign=Gtk.Align.CENTER,
                                               visible=False,
                                               css_classes=['flat'])
        self._cfg_icon_remove_btn.connect('clicked', self._on_icon_remove)
        self._cfg_icon_row.add_suffix(self._cfg_icon_remove_btn)

        icon_group.add(self._cfg_icon_row)
        page.append(icon_group)

        install_group = Adw.PreferencesGroup(title=_('Installation Settings'))

        cat_model = Gtk.StringList()
        for category in CATEGORIES:
            cat_model.append(category)
        self._cfg_category = Adw.ComboRow(
            title=_('Category'),
            subtitle=_('Where it lands in your application menu'),
            model=cat_model)
        install_group.add(self._cfg_category)

        self._cfg_scope = Adw.ComboRow(
            title=_('Install For'),
            subtitle=_('System-wide installs ask for your password'),
            model=Gtk.StringList.new([_('Just me'), _('Everyone on this computer')]))
        install_group.add(self._cfg_scope)

        self._cfg_symlink = Adw.SwitchRow(
            title=_('Add to PATH'),
            subtitle=_('Launch it from a terminal by name'))
        self._cfg_symlink.set_active(True)
        install_group.add(self._cfg_symlink)

        page.append(install_group)
        self._stack.add_named(page, 'configure')

    def _populate_configure(self):
        analysis = self._analysis
        self._cfg_name.set_text(analysis['app_name'])
        self._cfg_display.set_text(analysis['display_name'])

        self._cfg_exec_model.splice(0, self._cfg_exec_model.get_n_items(), [])
        for binary in analysis['binary']['all_binaries']:
            self._cfg_exec_model.append(binary['name'])
        if analysis['binary']['all_binaries']:
            self._cfg_exec.set_selected(0)

        self._custom_icon_path = None
        self._refresh_icon_preview()

    def _refresh_icon_preview(self):
        """Updates the icon row to show the current icon status."""
        icon_info = self._analysis.get('icon') if self._analysis else None

        if self._custom_icon_path:
            self._cfg_icon_preview.set_from_file(self._custom_icon_path)
            self._cfg_icon_row.set_subtitle(
                _('Custom: %s') % os.path.basename(self._custom_icon_path))
            self._cfg_icon_remove_btn.set_visible(True)
        elif icon_info:
            self._cfg_icon_preview.set_from_file(icon_info['path'])
            self._cfg_icon_row.set_subtitle(_('Detected from tarball'))
            self._cfg_icon_remove_btn.set_visible(False)
        else:
            self._cfg_icon_preview.set_from_icon_name(widgets.FALLBACK_ICON)
            self._cfg_icon_row.set_subtitle(_('None found — choose one'))
            self._cfg_icon_remove_btn.set_visible(False)

    def _on_icon_browse(self, _btn):
        """Opens a file chooser for the user to pick a custom icon."""
        dialog = Gtk.FileDialog(title=_('Select an Icon'))
        file_filter = Gtk.FileFilter()
        file_filter.set_name(_('Icon images (PNG, SVG, XPM)'))
        for pattern in ('*.png', '*.svg', '*.xpm'):
            file_filter.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(file_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(file_filter)
        dialog.open(self, None, self._on_icon_chosen)

    def _on_icon_chosen(self, dialog, result):
        """Callback when the user picks an icon file."""
        try:
            chosen = dialog.open_finish(result)
            if not chosen:
                return
            path = chosen.get_path()
            ext = os.path.splitext(path)[1].lower()

            # hicolor only takes these three
            if ext not in ('.png', '.svg', '.xpm'):
                self._toast_overlay.add_toast(Adw.Toast(
                    title=_('Unsupported format. Use PNG, SVG, or XPM.'), timeout=4))
                return

            self._custom_icon_path = path
            self._analysis['icon'] = {
                'path': path,
                'ext': ext,
                'size_dir': 'scalable' if ext == '.svg' else '128x128',
            }
            self._refresh_icon_preview()
        except GLib.Error:
            pass  # User cancelled

    def _on_icon_remove(self, _btn):
        """Removes the custom icon, reverting to the detected one or none."""
        self._custom_icon_path = None
        self._analysis['icon'] = self._original_icon
        self._refresh_icon_preview()

    # ── Page: Install ───────────────────────────────────

    def _build_install_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)

        self._install_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                                     valign=Gtk.Align.CENTER, vexpand=True,
                                     css_classes=['tm-card'])

        tile, image = widgets.icon_tile(size=32, large=True, accent=True)
        image.set_from_icon_name('system-software-install-symbolic')
        tile.set_halign(Gtk.Align.CENTER)
        self._install_tile = tile

        self._install_title = Gtk.Label(label=_('Installing…'),
                                        css_classes=['tm-progress-title'],
                                        halign=Gtk.Align.CENTER)
        self._install_sub = Gtk.Label(label='', css_classes=['tm-subtitle'],
                                      halign=Gtk.Align.CENTER, wrap=True,
                                      max_width_chars=44,
                                      justify=Gtk.Justification.CENTER)
        self._install_progress = Gtk.ProgressBar(margin_top=18, margin_start=24,
                                                 margin_end=24)

        self._install_card.append(tile)
        self._install_card.append(self._install_title)
        self._install_card.append(self._install_sub)
        self._install_card.append(self._install_progress)

        # Result state — replaces the progress card once the install finishes
        self._result_page = Adw.StatusPage(vexpand=True, css_classes=['compact'])
        self._result_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                       spacing=10, halign=Gtk.Align.CENTER)
        self._launch_btn = Gtk.Button(
            child=widgets.button_content(_('Launch'), 'media-playback-start-symbolic'),
            css_classes=['suggested-action', 'pill'])
        self._launch_btn.connect('clicked', self._on_launch_installed)
        self._done_btn = Gtk.Button(label=_('Back to Library'),
                                    css_classes=['pill'])
        self._done_btn.connect('clicked', self._on_install_another)
        self._result_buttons.append(self._launch_btn)
        self._result_buttons.append(self._done_btn)
        self._result_page.set_child(self._result_buttons)
        self._result_page.set_visible(False)

        page.append(self._install_card)
        page.append(self._result_page)
        self._stack.add_named(page, 'install')

    def _start_install(self):
        self._current_step = 3
        self._stack.set_visible_child_name('install')
        self._update_step_ui()

        self._install_card.set_visible(True)
        self._result_page.set_visible(False)
        self._install_progress.set_fraction(0)

        display_name = self._cfg_display.get_text().strip() or self._analysis['display_name']
        self._install_title.set_label(_('Installing %s') % display_name)
        self._install_sub.set_label(_('Getting started…'))

        selected = self._cfg_exec.get_selected()
        binaries = self._analysis['binary']['all_binaries']
        binary_path = binaries[selected]['path'] if selected < len(binaries) else None

        config = {
            'app_name': self._cfg_name.get_text().strip() or self._analysis['app_name'],
            'display_name': display_name,
            'binary_path': binary_path,
            'categories': CATEGORIES[self._cfg_category.get_selected()] + ';',
            'scope': 'system' if self._cfg_scope.get_selected() == 1 else 'user',
            'create_symlink': self._cfg_symlink.get_active(),
        }
        self._installed_app_name = config['app_name']

        def on_progress(step, message):
            GLib.idle_add(self._update_install_progress,
                          INSTALL_PROGRESS.get(step, 0), message)

        def worker():
            result = self._service.install(self._analysis, config, on_progress)
            GLib.idle_add(self._on_install_done, result)

        threading.Thread(target=worker, daemon=True).start()

    def _update_install_progress(self, fraction, message):
        self._install_progress.set_fraction(fraction)
        self._install_sub.set_label(message)

    def _on_install_done(self, result):
        self._install_card.set_visible(False)
        self._result_page.set_visible(True)

        display_name = self._cfg_display.get_text().strip() or self._analysis['display_name']

        self._result_page.set_css_classes(['compact'])

        if result.get('success'):
            can_launch = bool(self._launch_target())
            self._result_page.add_css_class('tm-status-success')
            self._result_page.set_icon_name('emblem-ok-symbolic')
            self._result_page.set_title(_('%s is installed') % display_name)
            self._result_page.set_description(
                _('Look for it in your application menu, or start it right now.')
                if can_launch else
                _('Look for it in your application menu.'))
            self._launch_btn.set_visible(can_launch)
        else:
            self._result_page.add_css_class('tm-status-error')
            self._result_page.set_icon_name('dialog-error-symbolic')
            self._result_page.set_title(_('Installation Failed'))
            self._result_page.set_description(result.get('error', _('Unknown error')))
            self._launch_btn.set_visible(False)

    def _launch_target(self):
        """Returns a launchable Gio.AppInfo for the app just installed."""
        metadata = self._store.get_install(getattr(self, '_installed_app_name', '')) or {}
        desktop_path = metadata.get('desktop_entry_path')
        if desktop_path and os.path.exists(desktop_path):
            return Gio.DesktopAppInfo.new_from_filename(desktop_path)
        return None

    def _on_launch_installed(self, _btn):
        app_info = self._launch_target()
        if app_info:
            try:
                app_info.launch(None, None)
            except GLib.Error as error:
                self._toast_overlay.add_toast(
                    Adw.Toast(title=_('Could not launch: %s') % error.message,
                              timeout=4))
                return
        self._on_install_another(None)

    def _on_install_another(self, _btn):
        """Go back to the library after an install."""
        self._cleanup_analysis()
        self._current_step = 0
        self._update_mode = None
        self._nav_view.pop_to_tag('dashboard')
        self._dashboard.refresh()

    def _cleanup_analysis(self):
        """Safely removes the temp dir and clears the analysis reference."""
        if self._analysis:
            self._service.cleanup_analysis(self._analysis)
            self._analysis = None

    def _on_page_popped(self, _nav_view, page):
        """Cleans up wizard temp files when the wizard page is popped."""
        if page.get_tag() == 'wizard':
            if self._pulse_id:
                GLib.source_remove(self._pulse_id)
                self._pulse_id = None
            self._cleanup_analysis()
            self._dashboard.refresh()

    # ── Navigation ──────────────────────────────────────

    def _on_back(self, _btn):
        if self._current_step > 0:
            self._current_step -= 1
            pages = ['upload', 'review', 'configure', 'install']
            self._stack.set_visible_child_name(pages[self._current_step])
            self._update_step_ui()

    def _on_next(self, _btn):
        if self._current_step == 1:
            self._current_step = 2
            self._stack.set_visible_child_name('configure')
            self._update_step_ui()
        elif self._current_step == 2:
            self._start_install()
