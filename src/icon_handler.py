# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

import os
import re
import shutil
import struct

ICON_EXTENSIONS = ('.svg', '.png', '.xpm')

KNOWN_SIZES = [16, 22, 24, 32, 48, 64, 96, 128, 256, 512]

# Directories that usually hold the real application icon.
GOOD_PATH_HINTS = (
    'resources/linux', 'share/icons', 'share/pixmaps', 'hicolor',
    '/icons/', '/icon/', '/branding/',
)

# Directories that usually hold web assets, theme previews or bundled
# third-party art rather than the application icon.
BAD_PATH_HINTS = (
    'out/media', '/assets/', '/extensions/', 'node_modules', '/locales/',
    '/www/', '/test/', '/tests/', '/doc/', '/docs/', '/sample', '/theme',
)

GOOD_NAME_HINTS = ('icon', 'logo', 'app')

# SVGs are read in full for the fake-icon check; real icons are never huge.
MAX_SVG_SCAN_BYTES = 2 * 1024 * 1024

_RASTER_DATA_URI_RE = re.compile(r'data:image/(?:png|jpe?g|webp|gif);base64,', re.I)
_PATH_TAG_RE = re.compile(r'<path\b([^>]*)>', re.I)
_RECT_TAG_RE = re.compile(r'<rect\b([^>]*)>', re.I)
_SHAPE_TAG_RE = re.compile(r'<(?:circle|ellipse|polygon|polyline|text)\b', re.I)
_FILL_COLOR_RE = re.compile(r'fill\s*=\s*"#([0-9a-fA-F]{3,6})"')
_PATH_DATA_RE = re.compile(r'\bd\s*=\s*"([^"]*)"')


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
                    try:
                        size = os.path.getsize(filepath)
                    except OSError:
                        break  # broken symlink or unreadable file
                    results.append({
                        'path': filepath,
                        'name': filename,
                        'ext': ext,
                        'size': size,
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


def _png_dimensions(path):
    """Reads (width, height) from a PNG header. Returns (0, 0) on failure."""
    try:
        with open(path, 'rb') as f:
            header = f.read(24)
        if header[:8] == b'\x89PNG\r\n\x1a\n' and header[12:16] == b'IHDR':
            return struct.unpack('>II', header[16:24])
    except (OSError, struct.error):
        pass
    return (0, 0)


def _icon_pixel_size(icon):
    """Pixel size of an icon: from its path, else from the PNG header."""
    guessed = _guess_icon_size(icon['path'])
    if guessed:
        return guessed
    if icon['ext'] == '.png':
        width, height = _png_dimensions(icon['path'])
        return min(width, height)
    return 0


def _hex_luminance(value):
    """Relative luminance (0.0–1.0) of a hex colour without the leading '#'."""
    if len(value) == 3:
        value = ''.join(c * 2 for c in value)
    if len(value) != 6:
        return 1.0
    r, g, b = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _is_rectangular_path(path_data):
    """True if an SVG path is a plain rectangle (a backdrop, not artwork)."""
    if set(re.findall(r'[A-Za-z]', path_data)) - set('MmHhVvLlZz'):
        return False
    return len(re.findall(r'-?\d*\.?\d+', path_data)) <= 12


def is_fake_vector_icon(path):
    """True when an .svg is really a raster image in a vector wrapper.

    Many Electron/Chromium apps ship SVGs that are dark-background wrappers
    around a base64-encoded PNG — web assets, not app icons. Used as a
    desktop icon they render as a dark square, so they must never beat a
    real PNG.
    """
    try:
        if os.path.getsize(path) > MAX_SVG_SCAN_BYTES:
            return False
        with open(path, 'r', errors='replace') as f:
            content = f.read()
    except OSError:
        return False

    # No embedded bitmap → a genuine vector icon.
    if not _RASTER_DATA_URI_RE.search(content):
        return False

    # Any real vector geometry, or only rectangles around the bitmap?
    has_vector_art = bool(_SHAPE_TAG_RE.search(content))
    if not has_vector_art:
        for attrs in _PATH_TAG_RE.findall(content):
            data = _PATH_DATA_RE.search(attrs)
            if data and not _is_rectangular_path(data.group(1)):
                has_vector_art = True
                break
    if not has_vector_art:
        return True

    # Vector art exists, but a dark full-canvas backdrop still renders as a
    # dark square in the application menu.
    for attrs in _PATH_TAG_RE.findall(content):
        fill = _FILL_COLOR_RE.search(attrs)
        data = _PATH_DATA_RE.search(attrs)
        if fill and data and _is_rectangular_path(data.group(1)):
            if _hex_luminance(fill.group(1)) < 0.25:
                return True
    for attrs in _RECT_TAG_RE.findall(content):
        fill = _FILL_COLOR_RE.search(attrs)
        if fill and _hex_luminance(fill.group(1)) < 0.25:
            return True

    return False


def _score_icon(icon, app_name=None):
    """Scores an icon candidate — higher is a better application icon."""
    path_lower = icon['path'].replace(os.sep, '/').lower()
    stem = os.path.splitext(icon['name'])[0].lower()

    score = {'.svg': 30, '.png': 20, '.xpm': 0}.get(icon['ext'], 0)

    if any(hint in path_lower for hint in GOOD_PATH_HINTS):
        score += 40
    if any(hint in path_lower for hint in BAD_PATH_HINTS):
        score -= 60

    if app_name:
        base = app_name.lower()
        if stem == base:
            score += 30
        elif base in stem or stem in base:
            score += 15
    if any(hint in stem for hint in GOOD_NAME_HINTS):
        score += 10

    if icon['ext'] == '.svg':
        score += 20  # resolution independent
    else:
        pixels = _icon_pixel_size(icon)
        if pixels >= 256:
            score += 20
        elif pixels >= 128:
            score += 12
        elif pixels >= 48:
            score += 5
        elif pixels > 0:
            score -= 10  # too small to look good in the application menu

    return score


def _size_dir_for(pixels):
    """Maps a pixel size to a standard hicolor size directory."""
    if pixels <= 0:
        return '128x128'
    for size in KNOWN_SIZES:
        if pixels <= size:
            return f'{size}x{size}'
    largest = KNOWN_SIZES[-1]
    return f'{largest}x{largest}'


def find_best_icon(app_root_dir, app_name=None):
    """Finds the best icon in the app directory.

    Candidates are scored on location, filename, format and pixel size;
    SVGs that are only raster wrappers (see is_fake_vector_icon) are
    skipped in favour of a real PNG.
    Returns {'path': str, 'ext': str, 'size_dir': str} or None.
    """
    icons = scan_for_icons(app_root_dir)
    if not icons:
        return None

    icons.sort(key=lambda icon: (-_score_icon(icon, app_name),
                                 -_icon_pixel_size(icon),
                                 -icon['size']))

    # Fake SVGs are only detected by reading the file, so check lazily —
    # the first candidate that is not a raster wrapper wins.
    best = icons[0]
    for icon in icons:
        if icon['ext'] == '.svg' and is_fake_vector_icon(icon['path']):
            continue
        best = icon
        break

    if best['ext'] == '.svg':
        size_dir = 'scalable'
    else:
        size_dir = _size_dir_for(_icon_pixel_size(best))

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
