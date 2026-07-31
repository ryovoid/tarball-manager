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
from .metadata_store import MetadataStore
from .tarball_extractor import derive_app_name, derive_version, derive_architecture, get_tarball_size
from .desktop_entry_writer import format_display_name
from .dashboard_page import DashboardPage
from .detail_page import DetailPage

_ = gettext.gettext

STEPS = [_('Select Tarball'), _('Review'), _('Configure'), _('Install')]
CATEGORIES = [
    'Utility', 'Development', 'Graphics', 'Game',
    'AudioVideo', 'Network', 'Office', 'Science', 'Education', 'System',
]


class TarballManagerWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'TarballManagerWindow'

    def __init__(self, **kwargs):
        super().__init__(**kwargs, default_width=700, default_height=650)
        self.set_title('Tarball Manager')

        self._service = InstallService()
        self._store = self._service.store
        self._analysis = None
        self._current_step = 0
        self._update_mode = None  # app_name when updating
        self._original_icon = None
        self._custom_icon_path = None

        self._load_css()
        self._build_ui()

    # ── CSS ──────────────────────────────────────────────

    def _load_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_resource('/io/github/ryovoid/TarballManager/style.css')
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    # ── Main layout ─────────────────────────────────────

    def _build_ui(self):
        # Navigation view is the root
        self._nav_view = Adw.NavigationView()
        self._nav_view.connect('popped', self._on_page_popped)
        self.set_content(self._nav_view)

        # Dashboard = home page
        self._dashboard = DashboardPage(
            store=self._store,
            on_install_clicked=self._show_install_wizard,
            on_app_clicked=self._show_app_detail,
        )
        dash_nav = Adw.NavigationPage(title=_('Tarball Manager'), tag='dashboard')
        dash_nav.set_child(self._dashboard.widget)
        self._nav_view.add(dash_nav)
        self._dashboard.refresh()

    # ── Navigation helpers ──────────────────────────────

    def _show_install_wizard(self, update_app_name=None):
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

    def _show_app_detail(self, app_name):
        """Pushes the app detail page onto the navigation stack."""
        detail = DetailPage(
            app_name=app_name,
            store=self._store,
            service=self._service,
            on_uninstalled=self._on_app_uninstalled,
            on_update_requested=self._on_update_requested,
        )
        display = self._store.get_install(app_name) or {}
        title = display.get('display_name', app_name)
        detail_nav = Adw.NavigationPage(title=title, tag='detail')

        # Wrap in toolbarview with headerbar for back button
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)
        toolbar.set_content(detail.widget)
        detail_nav.set_child(toolbar)

        self._nav_view.push(detail_nav)

    def _on_app_uninstalled(self, app_name):
        """Called after successful uninstall — pop back to dashboard."""
        self._nav_view.pop_to_tag('dashboard')
        self._dashboard.refresh()

    def _on_update_requested(self, app_name):
        """Called when user wants to update — open wizard in update mode."""
        self._nav_view.pop_to_tag('dashboard')
        self._show_install_wizard(update_app_name=app_name)

    # ── Wizard content builder ──────────────────────────

    def _build_wizard_content(self):
        """Builds the install wizard UI. Returns the root widget."""
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_centering_policy(Adw.CenteringPolicy.STRICT)
        toolbar.add_top_bar(header)

        self._toast_overlay = Adw.ToastOverlay()
        toolbar.set_content(self._toast_overlay)

        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self._toast_overlay.set_child(scroll)

        clamp = Adw.Clamp(maximum_size=600, margin_start=24, margin_end=24,
                          margin_top=20, margin_bottom=24)
        scroll.set_child(clamp)

        self._root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        clamp.set_child(self._root_box)

        # Title
        if self._update_mode:
            title_text = _('Update Application')
            sub_text = _('Provide the new tarball to update')
        else:
            title_text = _('Install App')
            sub_text = _('Upload a tarball to install a new application')

        title = Gtk.Label(label=title_text, halign=Gtk.Align.START,
                          css_classes=['page-title'])
        subtitle = Gtk.Label(label=sub_text,
                             halign=Gtk.Align.START, css_classes=['page-subtitle'])
        self._root_box.append(title)
        self._root_box.append(subtitle)

        # Step indicator
        self._step_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                 halign=Gtk.Align.CENTER, spacing=6,
                                 margin_top=8, margin_bottom=8,
                                 css_classes=['step-indicator'])
        self._root_box.append(self._step_box)
        self._step_badges = []
        self._step_labels = []
        self._step_connectors = []
        self._build_step_indicator()

        # Stack for pages
        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
                                transition_duration=300)
        self._root_box.append(self._stack)

        self._build_upload_page()
        self._build_analyzing_page()
        self._build_review_page()
        self._build_configure_page()
        self._build_install_page()

        # Button row
        self._btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                halign=Gtk.Align.END, spacing=8, margin_top=12)
        self._root_box.append(self._btn_box)

        self._back_btn = Gtk.Button(label=_('Back'), css_classes=['btn-secondary'])
        self._back_btn.connect('clicked', self._on_back)
        self._btn_box.append(self._back_btn)

        self._next_btn = Gtk.Button(label=_('Continue'), css_classes=['btn-primary'])
        self._next_btn.connect('clicked', self._on_next)
        self._btn_box.append(self._next_btn)

        self._update_step_ui()
        return toolbar

    # ── Step indicator ──────────────────────────────────

    def _build_step_indicator(self):
        for i, name in enumerate(STEPS):
            if i > 0:
                conn = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL,
                                     hexpand=False, css_classes=['step-connector'])
                conn.set_size_request(36, -1)
                self._step_box.append(conn)
                self._step_connectors.append(conn)

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            badge = Gtk.Label(label=str(i + 1), css_classes=['step-badge', 'inactive'],
                              width_request=28, height_request=28, halign=Gtk.Align.CENTER,
                              valign=Gtk.Align.CENTER)
            label = Gtk.Label(label=name, css_classes=['step-label', 'inactive'])
            box.append(badge)
            box.append(label)
            self._step_box.append(box)
            self._step_badges.append(badge)
            self._step_labels.append(label)

    def _update_step_ui(self):
        for i in range(len(STEPS)):
            badge = self._step_badges[i]
            label = self._step_labels[i]
            for cls in ('active', 'completed', 'inactive'):
                badge.remove_css_class(cls)
                label.remove_css_class(cls)

            if i < self._current_step:
                badge.add_css_class('completed')
                label.add_css_class('completed')
                badge.set_label('✓')
            elif i == self._current_step:
                badge.add_css_class('active')
                label.add_css_class('active')
                badge.set_label(str(i + 1))
            else:
                badge.add_css_class('inactive')
                label.add_css_class('inactive')
                badge.set_label(str(i + 1))

        for i, conn in enumerate(self._step_connectors):
            conn.remove_css_class('completed')
            if i < self._current_step:
                conn.add_css_class('completed')

        # Button visibility
        self._back_btn.set_visible(self._current_step > 0 and self._current_step < 3)
        page = self._stack.get_visible_child_name()
        self._next_btn.set_visible(page not in ('analyzing', 'install'))

        labels = {0: _('Continue'), 1: _('Continue'), 2: _('Install')}
        self._next_btn.set_label(labels.get(self._current_step, 'Continue'))
        self._next_btn.set_sensitive(self._current_step != 0 or self._analysis is not None)

        if self._current_step == 2:
            for cls in self._next_btn.get_css_classes():
                self._next_btn.remove_css_class(cls)
            self._next_btn.add_css_class('btn-primary')

    # ── Page: Upload ────────────────────────────────────

    def _build_upload_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        drop_zone = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                            halign=Gtk.Align.FILL, valign=Gtk.Align.CENTER,
                            css_classes=['drop-zone'])
        drop_zone.set_size_request(-1, 220)

        icon = Gtk.Image(icon_name='go-up-symbolic', pixel_size=48,
                         css_classes=['drop-zone-icon'], halign=Gtk.Align.CENTER)
        title = Gtk.Label(label=_('Drag & drop a tarball here'),
                          css_classes=['drop-zone-title'], halign=Gtk.Align.CENTER)
        sub = Gtk.Label(label=_('Supports .tar.gz, .tar.xz, .tar.bz2'),
                        css_classes=['drop-zone-subtitle'], halign=Gtk.Align.CENTER)

        browse = Gtk.Button(label=_('or browse files'), css_classes=['browse-link'],
                            halign=Gtk.Align.CENTER)
        browse.connect('clicked', self._on_browse)

        drop_zone.append(icon)
        drop_zone.append(title)
        drop_zone.append(sub)
        drop_zone.append(browse)

        # Drag and drop
        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect('drop', self._on_drop)
        drop_target.connect('enter', lambda *_: (drop_zone.add_css_class('drag-hover'), Gdk.DragAction.COPY)[-1])
        drop_target.connect('leave', lambda *_: drop_zone.remove_css_class('drag-hover'))
        drop_zone.add_controller(drop_target)

        self._upload_zone = drop_zone
        page.append(drop_zone)
        self._stack.add_named(page, 'upload')

    def _on_browse(self, _btn):
        dialog = Gtk.FileDialog(title=_('Select a Tarball'))
        f = Gtk.FileFilter()
        f.set_name(_('Tarball archives'))
        for p in ('*.tar.gz', '*.tar.xz', '*.tar.bz2', '*.tgz', '*.txz', '*.tbz2'):
            f.add_pattern(p)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dialog.set_filters(filters)
        dialog.set_default_filter(f)
        dialog.open(self, None, self._on_file_chosen)

    def _on_file_chosen(self, dialog, result):
        try:
            f = dialog.open_finish(result)
            if f:
                self._start_analysis(f.get_path())
        except GLib.Error:
            pass

    def _on_drop(self, _target, value, _x, _y):
        if isinstance(value, Gio.File):
            path = value.get_path()
            if path:
                self._start_analysis(path)
                return True
        return False

    # ── Page: Analyzing ─────────────────────────────────

    def _build_analyzing_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       halign=Gtk.Align.FILL, valign=Gtk.Align.CENTER,
                       css_classes=['analyzing-card'])
        card.set_size_request(-1, 220)

        self._analyze_progress = Gtk.ProgressBar(halign=Gtk.Align.FILL, margin_start=16,
                                                  margin_end=16)
        self._analyze_title = Gtk.Label(label=_('Analyzing tarball…'),
                                         css_classes=['analyzing-title'],
                                         halign=Gtk.Align.CENTER)
        self._analyze_sub = Gtk.Label(label=_('This may take a moment'),
                                       css_classes=['analyzing-subtitle'],
                                       halign=Gtk.Align.CENTER)

        card.append(self._analyze_progress)
        card.append(self._analyze_title)
        card.append(self._analyze_sub)
        page.append(card)
        self._stack.add_named(page, 'analyzing')

    def _start_analysis(self, tarball_path):
        self._stack.set_visible_child_name('analyzing')
        self._btn_box.set_visible(False)
        self._analyze_progress.pulse()

        self._pulse_id = GLib.timeout_add(80, self._pulse_analyzing)

        def worker():
            try:
                analysis = self._service.analyze(tarball_path)
                GLib.idle_add(self._on_analysis_done, analysis, None)
            except Exception as e:
                GLib.idle_add(self._on_analysis_done, None, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _pulse_analyzing(self):
        self._analyze_progress.pulse()
        return True

    def _on_analysis_done(self, analysis, error):
        if hasattr(self, '_pulse_id'):
            GLib.source_remove(self._pulse_id)

        if error:
            self._toast_overlay.add_toast(Adw.Toast(title=_('Analysis failed: %s') % error, timeout=5))
            self._stack.set_visible_child_name('upload')
            self._btn_box.set_visible(True)
            self._current_step = 0
            self._update_step_ui()
            return

        self._analysis = analysis
        # Deep-copy so overwriting analysis['icon'] doesn't corrupt backup
        detected = analysis.get('icon')
        self._original_icon = dict(detected) if detected else None
        self._populate_review()
        self._populate_configure()
        self._current_step = 1
        self._stack.set_visible_child_name('review')
        self._btn_box.set_visible(True)
        self._update_step_ui()

    # ── Page: Review ────────────────────────────────────

    def _build_review_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._review_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                     css_classes=['content-card'])
        heading = Gtk.Label(label=_('Detected Application'), halign=Gtk.Align.START,
                            css_classes=['card-heading'])
        self._review_card.append(heading)

        self._review_rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._review_card.append(self._review_rows_box)

        page.append(self._review_card)
        self._stack.add_named(page, 'review')

    def _make_detail_row(self, label_text, value_text):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, css_classes=['detail-row'],
                      hexpand=True)
        lbl = Gtk.Label(label=label_text, halign=Gtk.Align.START, hexpand=True,
                        css_classes=['detail-label'])
        val = Gtk.Label(label=value_text, halign=Gtk.Align.END,
                        css_classes=['detail-value'], selectable=True)
        row.append(lbl)
        row.append(val)
        return row

    def _populate_review(self):
        # Clear old rows
        child = self._review_rows_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self._review_rows_box.remove(child)
            child = next_child

        a = self._analysis
        details = [
            (_('Name'), a['display_name']),
            (_('Version'), a['version']),
            (_('Executable'), a['binary']['name'] or _('Not found')),
            (_('Size'), a['extracted_size']),
            (_('Architecture'), a['architecture']),
            (_('Tarball'), os.path.basename(a.get('tarball_path', ''))),
        ]
        for label, value in details:
            self._review_rows_box.append(self._make_detail_row(label, value))

    # ── Page: Configure ─────────────────────────────────

    def _build_configure_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        heading = Gtk.Label(label=_('Configure Installation'), halign=Gtk.Align.START,
                            css_classes=['card-heading'], margin_bottom=4)
        page.append(heading)

        # App identity group
        identity_group = Adw.PreferencesGroup(title=_('Application Details'),
                                               description=_('Edit the detected values if needed'))

        self._cfg_name = Adw.EntryRow(title=_('Application Name'))
        self._cfg_name.set_editable(True)
        identity_group.add(self._cfg_name)

        self._cfg_display = Adw.EntryRow(title=_('Display Name'))
        self._cfg_display.set_editable(True)
        identity_group.add(self._cfg_display)

        # Executable dropdown
        self._cfg_exec_model = Gtk.StringList()
        self._cfg_exec = Adw.ComboRow(title=_('Executable'),
                                       subtitle=_('Select the main binary'),
                                       model=self._cfg_exec_model)
        identity_group.add(self._cfg_exec)

        page.append(identity_group)

        # App icon group
        icon_group = Adw.PreferencesGroup(
            title=_('App Icon'),
            description=_('The icon shown in your application menu'),
        )

        self._cfg_icon_row = Adw.ActionRow(
            title=_('Icon'),
            subtitle=_('No icon detected'),
        )
        self._cfg_icon_preview = Gtk.Image(pixel_size=32)
        self._cfg_icon_row.add_prefix(self._cfg_icon_preview)

        # Browse button
        icon_browse_btn = Gtk.Button(
            icon_name='document-open-symbolic',
            tooltip_text=_('Choose a custom icon'),
            valign=Gtk.Align.CENTER,
        )
        icon_browse_btn.connect('clicked', self._on_icon_browse)
        self._cfg_icon_row.add_suffix(icon_browse_btn)

        # Remove custom icon button (hidden by default)
        self._cfg_icon_remove_btn = Gtk.Button(
            icon_name='edit-clear-symbolic',
            tooltip_text=_('Remove custom icon'),
            valign=Gtk.Align.CENTER,
            visible=False,
        )
        self._cfg_icon_remove_btn.connect('clicked', self._on_icon_remove)
        self._cfg_icon_row.add_suffix(self._cfg_icon_remove_btn)

        icon_group.add(self._cfg_icon_row)
        page.append(icon_group)

        # Installation settings group
        install_group = Adw.PreferencesGroup(title=_('Installation Settings'))

        # Category dropdown
        cat_model = Gtk.StringList()
        for c in CATEGORIES:
            cat_model.append(c)
        self._cfg_category = Adw.ComboRow(title=_('Category'),
                                           subtitle=_('Freedesktop application category'),
                                           model=cat_model)
        install_group.add(self._cfg_category)

        # Scope
        self._cfg_scope = Adw.ComboRow(title=_('Install Scope'),
                                        subtitle=_('Where the app will be installed'),
                                        model=Gtk.StringList.new([_('Current User'), _('System-Wide')]))
        install_group.add(self._cfg_scope)

        # Symlink toggle
        self._cfg_symlink = Adw.SwitchRow(title=_('Add to PATH'),
                                           subtitle=_('Create a command-line shortcut'))
        self._cfg_symlink.set_active(True)
        install_group.add(self._cfg_symlink)

        page.append(install_group)
        self._stack.add_named(page, 'configure')

    def _populate_configure(self):
        a = self._analysis
        self._cfg_name.set_text(a['app_name'])
        self._cfg_display.set_text(a['display_name'])

        self._cfg_exec_model.splice(0, self._cfg_exec_model.get_n_items(), [])
        for b in a['binary']['all_binaries']:
            self._cfg_exec_model.append(b['name'])
        if a['binary']['all_binaries']:
            # Select the best match (first one)
            self._cfg_exec.set_selected(0)

        # Update icon preview
        self._custom_icon_path = None
        self._refresh_icon_preview()

    def _refresh_icon_preview(self):
        """Updates the icon row to show the current icon status."""
        icon_info = self._analysis.get('icon') if self._analysis else None

        if self._custom_icon_path:
            # Custom icon selected by user
            self._cfg_icon_preview.set_from_file(self._custom_icon_path)
            self._cfg_icon_row.set_subtitle(
                _('Custom: %s') % os.path.basename(self._custom_icon_path)
            )
            self._cfg_icon_remove_btn.set_visible(True)
        elif icon_info:
            # Detected from tarball
            self._cfg_icon_preview.set_from_file(icon_info['path'])
            self._cfg_icon_row.set_subtitle(_('Detected from tarball'))
            self._cfg_icon_remove_btn.set_visible(False)
        else:
            # No icon at all
            self._cfg_icon_preview.set_from_icon_name('application-x-executable')
            self._cfg_icon_row.set_subtitle(_('No icon — click 📂 to add one'))
            self._cfg_icon_remove_btn.set_visible(False)

    def _on_icon_browse(self, _btn):
        """Opens a file chooser for the user to pick a custom icon."""
        dialog = Gtk.FileDialog(title=_('Select an Icon'))
        f = Gtk.FileFilter()
        f.set_name(_('Icon images (PNG, SVG, XPM)'))
        for p in ('*.png', '*.svg', '*.xpm'):
            f.add_pattern(p)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dialog.set_filters(filters)
        dialog.set_default_filter(f)
        dialog.open(self, None, self._on_icon_chosen)

    def _on_icon_chosen(self, dialog, result):
        """Callback when user picks an icon file."""
        try:
            f = dialog.open_finish(result)
            if f:
                path = f.get_path()
                ext = os.path.splitext(path)[1].lower()

                # Validate extension — hicolor only supports these
                if ext not in ('.png', '.svg', '.xpm'):
                    self._toast_overlay.add_toast(
                        Adw.Toast(title=_('Unsupported format. Use PNG, SVG, or XPM.'), timeout=4)
                    )
                    return

                self._custom_icon_path = path

                # Build an icon info dict matching find_best_icon() format
                if ext == '.svg':
                    size_dir = 'scalable'
                else:
                    size_dir = '128x128'

                # Override the analysis icon with the custom one
                self._analysis['icon'] = {
                    'path': path,
                    'ext': ext,
                    'size_dir': size_dir,
                }
                self._refresh_icon_preview()
        except GLib.Error:
            pass  # User cancelled

    def _on_icon_remove(self, _btn):
        """Removes the custom icon, reverting to detected or none."""
        self._custom_icon_path = None
        # Restore original detected icon (re-run detection)
        # Since we overwrote analysis['icon'], set to None if no original
        self._analysis['icon'] = self._original_icon
        self._refresh_icon_preview()

    # ── Page: Install ───────────────────────────────────

    def _build_install_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._install_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                                      halign=Gtk.Align.FILL, valign=Gtk.Align.CENTER,
                                      css_classes=['content-card'])
        self._install_card.set_size_request(-1, 250)

        self._install_progress = Gtk.ProgressBar(halign=Gtk.Align.FILL,
                                                  margin_start=16, margin_end=16)
        self._install_title = Gtk.Label(label=_('Installing…'),
                                         css_classes=['analyzing-title'],
                                         halign=Gtk.Align.CENTER)
        self._install_sub = Gtk.Label(label='',
                                       css_classes=['analyzing-subtitle'],
                                       halign=Gtk.Align.CENTER)

        self._install_card.append(self._install_progress)
        self._install_card.append(self._install_title)
        self._install_card.append(self._install_sub)

        # Success/failure area (hidden initially)
        self._result_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                                    halign=Gtk.Align.CENTER, visible=False)
        self._result_icon = Gtk.Image(pixel_size=64, halign=Gtk.Align.CENTER)
        self._result_title = Gtk.Label(css_classes=['success-title'], halign=Gtk.Align.CENTER)
        self._result_sub = Gtk.Label(css_classes=['success-subtitle'], halign=Gtk.Align.CENTER,
                                      wrap=True, max_width_chars=50)

        self._result_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                        halign=Gtk.Align.CENTER, spacing=8, margin_top=8)
        self._done_btn = Gtk.Button(label=_('Back to Dashboard'), css_classes=['btn-secondary'])
        self._done_btn.connect('clicked', self._on_install_another)
        self._result_btn_box.append(self._done_btn)

        self._result_box.append(self._result_icon)
        self._result_box.append(self._result_title)
        self._result_box.append(self._result_sub)
        self._result_box.append(self._result_btn_box)

        self._install_card.append(self._result_box)
        page.append(self._install_card)
        self._stack.add_named(page, 'install')

    def _start_install(self):
        self._current_step = 3
        self._update_step_ui()
        self._stack.set_visible_child_name('install')
        self._btn_box.set_visible(False)

        self._install_progress.set_visible(True)
        self._install_title.set_visible(True)
        self._install_sub.set_visible(True)
        self._result_box.set_visible(False)
        self._install_progress.set_fraction(0)

        # Gather config
        selected_idx = self._cfg_exec.get_selected()
        all_bins = self._analysis['binary']['all_binaries']
        binary_path = all_bins[selected_idx]['path'] if selected_idx < len(all_bins) else None

        config = {
            'app_name': self._cfg_name.get_text().strip() or self._analysis['app_name'],
            'display_name': self._cfg_display.get_text().strip() or self._analysis['display_name'],
            'binary_path': binary_path,
            'categories': CATEGORIES[self._cfg_category.get_selected()] + ';',
            'scope': 'system' if self._cfg_scope.get_selected() == 1 else 'user',
            'create_symlink': self._cfg_symlink.get_active(),
        }

        steps_map = {'installing': 0.1, 'icon': 0.3, 'desktop': 0.5,
                      'symlink': 0.65, 'refresh': 0.8, 'metadata': 0.9, 'done': 1.0}

        def on_progress(step, msg):
            frac = steps_map.get(step, 0)
            GLib.idle_add(self._update_install_progress, frac, msg)

        def worker():
            result = self._service.install(self._analysis, config, on_progress)
            GLib.idle_add(self._on_install_done, result)

        threading.Thread(target=worker, daemon=True).start()

    def _update_install_progress(self, fraction, message):
        self._install_progress.set_fraction(fraction)
        self._install_sub.set_label(message)

    def _on_install_done(self, result):
        self._install_progress.set_visible(False)
        self._install_title.set_visible(False)
        self._install_sub.set_visible(False)
        self._result_box.set_visible(True)

        if result.get('success'):
            self._result_icon.set_from_icon_name('emblem-ok-symbolic')
            self._result_icon.set_css_classes(['success-icon'])
            self._result_title.set_label(_('Installation Complete!'))
            name = self._cfg_display.get_text().strip() or self._analysis['display_name']
            self._result_sub.set_label(_('"%s" is ready. Find it in your app launcher.') % name)
        else:
            self._result_icon.set_from_icon_name('dialog-error-symbolic')
            self._result_icon.set_css_classes(['error-icon'])
            self._result_title.set_label(_('Installation Failed'))
            self._result_sub.set_label(result.get('error', _('Unknown error')))

    def _on_install_another(self, _btn):
        """Go back to dashboard after install."""
        self._cleanup_analysis()
        self._current_step = 0
        self._update_mode = None
        self._nav_view.pop_to_tag('dashboard')
        self._dashboard.refresh()

    def _cleanup_analysis(self):
        """Safely removes temp dir and clears the analysis reference."""
        if self._analysis:
            self._service.cleanup_analysis(self._analysis)
            self._analysis = None

    def _on_page_popped(self, _nav_view, page):
        """Called when any page is popped from the navigation stack.

        If the wizard page is popped (user pressed back), clean up
        any temp files from analysis to prevent /tmp/ leaks.
        """
        if page.get_tag() == 'wizard':
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
        if self._current_step == 0:
            return  # Need file first
        elif self._current_step == 1:
            self._current_step = 2
            self._stack.set_visible_child_name('configure')
            self._update_step_ui()
        elif self._current_step == 2:
            self._start_install()
