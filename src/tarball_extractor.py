# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

import os
import re
import subprocess
import tempfile
import threading


def get_compression_flag(filename):
    """Returns the tar flag for the given archive extension."""
    if filename.endswith(('.tar.gz', '.tgz')):
        return '-z'
    if filename.endswith(('.tar.xz', '.txz')):
        return '-J'
    if filename.endswith(('.tar.bz2', '.tbz2')):
        return '-j'
    return '-a'


def derive_app_name(filename):
    """Derives a clean app name from a tarball filename.

    Examples:
        'zen.linux-x86_64.tar.xz' → 'zen'
        'zen-browser-2.1.0-x86_64.tar.xz' → 'zen-browser'
        'blender-4.5.12-linux-x64.tar.xz' → 'blender'
        'kiro-ide-1.0.242-stable-linux-x64.tar.gz' → 'kiro-ide'
        'thunderbird-153.0.tar.bz2' → 'thunderbird'
    """
    name = os.path.basename(filename)

    # Strip tarball extensions
    for ext in ('.tar.gz', '.tar.xz', '.tar.bz2', '.tgz', '.txz', '.tbz2'):
        if name.endswith(ext):
            name = name[:-len(ext)]
            break

    # Normalize dots to dashes (handles 'zen.linux-x86_64' → 'zen-linux-x86_64')
    name = name.replace('.', '-')

    # Strip platform/arch/build suffixes from the end, repeatedly
    _PLATFORM_RE = re.compile(
        r'[-_](x86_64|x86[-_]64|amd64|x64|arm64|aarch64|'
        r'linux|Linux|win32|darwin|'
        r'stable|release|beta|alpha|nightly|'
        r'universal|generic)$',
        re.IGNORECASE
    )
    prev = None
    while prev != name:
        prev = name
        name = _PLATFORM_RE.sub('', name)

    # Strip version numbers (e.g. -1.0.242, -v4.5.12)
    name = re.sub(r'[-_]v?\d+(?:[-_.]\d+)*$', '', name)

    # Clean up trailing separators
    name = name.lower().rstrip('-_')
    return name or 'unknown-app'


def derive_version(filename):
    """Tries to extract a version number from a tarball filename."""
    name = os.path.basename(filename)
    for ext in ('.tar.gz', '.tar.xz', '.tar.bz2', '.tgz', '.txz', '.tbz2'):
        if name.endswith(ext):
            name = name[:-len(ext)]
            break
    match = re.search(r'[-_]v?(\d+(?:\.\d+)+)', name)
    return match.group(1) if match else 'Unknown'


def derive_architecture(filename):
    """Tries to detect architecture from filename."""
    name = os.path.basename(filename).lower()
    if any(a in name for a in ('x86_64', 'amd64', 'x64')):
        return 'x86_64'
    if any(a in name for a in ('aarch64', 'arm64')):
        return 'aarch64'
    if 'armhf' in name or 'armv7' in name:
        return 'armhf'
    if 'i686' in name or 'i386' in name:
        return 'i686'
    return 'Unknown'


def get_tarball_size(tarball_path):
    """Returns a human-readable file size."""
    size = os.path.getsize(tarball_path)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024:
            return f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} TB'


def extract_tarball(tarball_path, dest_dir, on_progress=None):
    """Extracts a tarball to dest_dir.

    Returns the effective app root (handles single-dir vs flat layouts).
    Runs synchronously — call from a thread.
    """
    flag = get_compression_flag(tarball_path)
    os.makedirs(dest_dir, exist_ok=True)

    if on_progress:
        on_progress('Extracting tarball…')

    result = subprocess.run(
        ['tar', flag, '-xf', tarball_path, '-C', dest_dir],
        capture_output=True, text=True
    )

    if result.returncode != 0:
        raise RuntimeError(f'tar extraction failed: {result.stderr or "unknown error"}')

    if on_progress:
        on_progress('Analyzing layout…')

    # Check for single top-level directory
    entries = os.listdir(dest_dir)
    if len(entries) == 1:
        single = os.path.join(dest_dir, entries[0])
        if os.path.isdir(single):
            return single

    return dest_dir


def extract_tarball_async(tarball_path, dest_dir, on_progress=None, on_done=None):
    """Runs extraction in a background thread.

    on_done(app_root_dir, error) is called when finished.
    """
    def _worker():
        try:
            app_root = extract_tarball(tarball_path, dest_dir, on_progress)
            if on_done:
                on_done(app_root, None)
        except Exception as e:
            if on_done:
                on_done(None, str(e))

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread
