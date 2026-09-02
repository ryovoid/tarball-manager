# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

"""Shared UI building blocks.

The dashboard, the wizard and the detail page all show the same things —
an app icon, a version, a scope, a status — so they are built here once
and reused, which is what keeps the three screens looking like one app.
"""

import gettext
import os

import gi
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, Gdk

_ = gettext.gettext

FALLBACK_ICON = 'application-x-executable'


def pill(text, style=None):
    """A small capsule label — version, scope, status."""
    classes = ['tm-pill']
    if style:
        classes.append(style)
    return Gtk.Label(label=text, css_classes=classes,
                     valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)


def icon_tile(size=32, tile_size=None, large=False, accent=False):
    """An app icon sitting on a rounded tile.

    Returns (tile, image) so the caller can swap the image later.
    """
    image = Gtk.Image(pixel_size=size)
    classes = ['tm-icon-tile']
    if large:
        classes.append('large')
    if accent:
        classes.append('accent')

    tile = Adw.Bin(css_classes=classes, valign=Gtk.Align.CENTER,
                   halign=Gtk.Align.CENTER)
    tile.set_child(image)
    if tile_size:
        tile.set_size_request(tile_size, tile_size)
    return tile, image


def set_app_icon(image, icon_name=None, icon_path=None):
    """Points an icon image at the best available source.

    Installed apps are recorded by icon name (the theme resolves them), but
    a freshly analysed tarball only has a file on disk, and a name that is
    not in the theme yet resolves to a blank image — so paths win, and the
    generic executable icon is the last resort.
    """
    if icon_path and os.path.exists(icon_path):
        image.set_from_file(icon_path)
        return
    if icon_name:
        display = Gdk.Display.get_default()
        theme = Gtk.IconTheme.get_for_display(display) if display else None
        if theme is None or theme.has_icon(icon_name):
            image.set_from_icon_name(icon_name)
            return
    image.set_from_icon_name(FALLBACK_ICON)


def section_heading(text, margin_top=0):
    """A small uppercase label that titles a block of content."""
    return Gtk.Label(label=text, halign=Gtk.Align.START,
                     css_classes=['tm-section'], margin_top=margin_top)


def hero(icon_name=None, icon_path=None, name='', pills=(), subtitle=None):
    """The app identity block used at the top of the detail and review pages."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                  halign=Gtk.Align.FILL, css_classes=['tm-hero'], hexpand=True)

    tile, image = icon_tile(size=56, large=True)
    set_app_icon(image, icon_name, icon_path)
    box.append(tile)

    title = Gtk.Label(label=name, css_classes=['tm-hero-name'],
                      halign=Gtk.Align.CENTER, wrap=True, max_width_chars=28,
                      justify=Gtk.Justification.CENTER)
    box.append(title)

    if subtitle:
        box.append(Gtk.Label(label=subtitle, css_classes=['tm-subtitle'],
                             halign=Gtk.Align.CENTER, wrap=True,
                             max_width_chars=40,
                             justify=Gtk.Justification.CENTER))

    if pills:
        pill_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                           halign=Gtk.Align.CENTER)
        for text, style in pills:
            pill_box.append(pill(text, style))
        box.append(pill_box)

    return box


def detail_row(title, value, copyable=False):
    """A read-only key/value row.

    The `property` style class is libadwaita's own treatment for read-only
    facts: it quiets the label and gives the value the emphasis.
    """
    row = Adw.ActionRow(title=title, subtitle=str(value),
                        css_classes=['property'])
    row.set_subtitle_selectable(True)
    if copyable:
        button = Gtk.Button(icon_name='edit-copy-symbolic',
                            tooltip_text=_('Copy'),
                            valign=Gtk.Align.CENTER,
                            css_classes=['flat'])
        button.connect('clicked', lambda _b, v=str(value): _copy(v))
        row.add_suffix(button)
    return row


def _copy(text):
    display = Gdk.Display.get_default()
    if display:
        display.get_clipboard().set(text)


def button_content(label, icon_name):
    """Icon + label button content, so header buttons say what they do."""
    return Adw.ButtonContent(label=label, icon_name=icon_name)


def scope_label(scope):
    return _('System-wide') if scope == 'system' else _('Just for me')
