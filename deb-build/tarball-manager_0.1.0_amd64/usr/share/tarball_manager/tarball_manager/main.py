# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

import sys
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gio
from .window import TarballManagerWindow


class TarballManagerApplication(Adw.Application):
    """The main application singleton class."""

    def __init__(self, version='0.1.0'):
        super().__init__(application_id='io.github.ryovoid.TarballManager',
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._version = version
        self.create_action('quit', lambda *_: self.quit(), ['<control>q'])
        self.create_action('about', self.on_about_action)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = TarballManagerWindow(application=self)
        win.present()

    def on_about_action(self, *args):
        about = Adw.AboutDialog(
            application_name='Tarball Manager',
            application_icon='io.github.ryovoid.TarballManager',
            developer_name='ryovoid',
            version=self._version,
            developers=['ryovoid'],
            comments='Install Linux app tarballs as desktop applications',
            license_type=Gtk.License.MIT_X11,
            website='https://github.com/ryovoid/tarball-manager',
            copyright='© 2026 ryovoid',
        )
        about.present(self.props.active_window)

    def create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


def main(version):
    """The application's entry point."""
    app = TarballManagerApplication(version=version)
    return app.run(sys.argv)
