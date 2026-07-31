# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

import os
import re
import shutil

ICON_EXTENSIONS = ('.svg', '.png', '.xpm')

KNOWN_SIZES = [16, 22, 24, 32, 48, 64, 96, 128, 256, 512]


def scan_for_icons(app_root_dir):
    """Recursively scans a directory for icon files.

    Returns a list of dicts:
        [{'path': str, 'name': str, 'ext': str, 'size': int}]
    """
    results = []
    for dirpath, _, filenames in os.walk(app_root_dir):
        depth = dirpath.replace(app_root_dir, '').count(os.sep)
        if depth > 5:
            continue
        for filename in filenames:
            name_lower = filename.lower()
            for ext in ICON_EXTENSIONS:
                if name_lower.endswith(ext):
                    filepath = os.path.join(dirpath, filename)
                    results.append({
                        'path': filepath,
                        'name': filename,
                        'ext': ext,
                        'size': os.path.getsize(filepath),
                    })
                    break
    return results


def _guess_icon_size(path):
    """Tries to determine icon pixel size from its filename or path."""
    match = re.search(r'(\d+)x\1', path)
    if match:
        return int(match.group(1))
    match = re.search(r'/(\d+)/', path)
    if match:
        num = int(match.group(1))
        if num in KNOWN_SIZES:
            return num
    return 0


def find_best_icon(app_root_dir):
    """Finds the best icon in the app directory.

    Priority: SVG > largest PNG > any PNG > XPM.
    Returns {'path': str, 'ext': str, 'size_dir': str} or None.
    """
    icons = scan_for_icons(app_root_dir)
    if not icons:
        return None

    def sort_key(icon):
        ext_priority = {'.svg': 0, '.png': 1, '.xpm': 2}
        guessed = _guess_icon_size(icon['path'])
        return (ext_priority.get(icon['ext'], 3), -guessed, -icon['size'])

    icons.sort(key=sort_key)
    best = icons[0]

    if best['ext'] == '.svg':
        size_dir = 'scalable'
    else:
        guessed = _guess_icon_size(best['path'])
        size_dir = f'{guessed}x{guessed}' if guessed > 0 else '128x128'

    return {
        'path': best['path'],
        'ext': best['ext'],
        'size_dir': size_dir,
    }


def install_icon(icon_info, app_name, scope):
    """Copies the icon to the correct hicolor directory.

    Returns {'icon_name': str, 'icon_path': str or None}.
    """
    if icon_info is None:
        return {'icon_name': 'application-x-executable', 'icon_path': None}

    if scope == 'system':
        base = f'/usr/share/icons/hicolor/{icon_info["size_dir"]}/apps'
    else:
        base = os.path.expanduser(
            f'~/.local/share/icons/hicolor/{icon_info["size_dir"]}/apps'
        )

    os.makedirs(base, exist_ok=True)
    target = os.path.join(base, f'{app_name}{icon_info["ext"]}')
    shutil.copy2(icon_info['path'], target)

    return {'icon_name': app_name, 'icon_path': target}
