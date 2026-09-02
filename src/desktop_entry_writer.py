# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

import os
import re
import subprocess


def format_display_name(app_name):
    """Converts 'zen-browser' → 'Zen Browser'."""
    return ' '.join(word.capitalize() for word in app_name.split('-'))


def sanitize_desktop_id(value):
    """Turns a WM class / app id into a safe .desktop basename.

    'Antigravity IDE' -> 'antigravity-ide'. Returns '' if nothing is left.
    """
    cleaned = re.sub(r'[^a-z0-9._-]+', '-', (value or '').strip().lower())
    return cleaned.strip('-.')


def generate_desktop_entry(name, exec_path, icon, categories='Utility;',
                           comment='', terminal=False, wm_class=''):
    """Generates the content of a .desktop file.

    wm_class becomes StartupWMClass, which desktop environments use to match
    a running window (its Wayland app_id) back to this entry — without it
    the taskbar shows a generic fallback icon.
    """
    # Quote exec path if it contains spaces (freedesktop spec)
    if ' ' in exec_path:
        quoted_exec = f'"{exec_path}" %f'
    else:
        quoted_exec = f'{exec_path} %f'

    lines = [
        '[Desktop Entry]',
        'Type=Application',
        f'Name={name}',
    ]
    if comment:
        lines.append(f'Comment={comment}')
    lines.extend([
        f'Exec={quoted_exec}',
        f'Icon={icon}',
        f'Terminal={"true" if terminal else "false"}',
        f'Categories={categories}',
        'StartupNotify=true',
    ])
    if wm_class:
        lines.append(f'StartupWMClass={wm_class}')
    return '\n'.join(lines) + '\n'


def write_desktop_entry(app_name, scope, desktop_id=None, **kwargs):
    """Writes a .desktop file to the correct directory.

    desktop_id overrides the basename — pass the app's WM class / app_id so
    the filename matches what the running window reports, which is the other
    way desktop environments resolve a window to its entry.
    Returns the absolute path to the written file.
    """
    if scope == 'system':
        apps_dir = '/usr/share/applications'
    else:
        apps_dir = os.path.expanduser('~/.local/share/applications')

    os.makedirs(apps_dir, exist_ok=True)
    basename = sanitize_desktop_id(desktop_id) or app_name
    desktop_path = os.path.join(apps_dir, f'{basename}.desktop')

    content = generate_desktop_entry(**kwargs)
    with open(desktop_path, 'w') as f:
        f.write(content)

    os.chmod(desktop_path, 0o644)
    return desktop_path


def validate_desktop_entry(desktop_path):
    """Validates a .desktop file (if desktop-file-validate is available)."""
    try:
        result = subprocess.run(
            ['desktop-file-validate', desktop_path],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return {'valid': True, 'errors': []}
        errors = [l.strip() for l in (result.stdout + result.stderr).splitlines() if l.strip()]
        return {'valid': False, 'errors': errors}
    except FileNotFoundError:
        return {'valid': True, 'errors': []}
