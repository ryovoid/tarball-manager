# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

import os
import subprocess


def format_display_name(app_name):
    """Converts 'zen-browser' → 'Zen Browser'."""
    return ' '.join(word.capitalize() for word in app_name.split('-'))


def generate_desktop_entry(name, exec_path, icon, categories='Utility;',
                           comment='', terminal=False):
    """Generates the content of a .desktop file."""
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
    return '\n'.join(lines) + '\n'


def write_desktop_entry(app_name, scope, **kwargs):
    """Writes a .desktop file to the correct directory.

    Returns the absolute path to the written file.
    """
    if scope == 'system':
        apps_dir = '/usr/share/applications'
    else:
        apps_dir = os.path.expanduser('~/.local/share/applications')

    os.makedirs(apps_dir, exist_ok=True)
    desktop_path = os.path.join(apps_dir, f'{app_name}.desktop')

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
