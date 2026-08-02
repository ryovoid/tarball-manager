#!/usr/bin/env python3
# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT
#
# This helper script runs with elevated privileges via pkexec.
# It reads a JSON operation from stdin and performs privileged filesystem ops.

import json
import os
import shutil
import subprocess
import sys


# Allowed source prefixes for app_root (temp extraction directories)
ALLOWED_SOURCE_PREFIXES = ('/tmp/', '/var/tmp/')

# Allowed target prefixes for filesystem operations
ALLOWED_INSTALL_PREFIXES = ('/opt/',)
ALLOWED_FILE_PREFIXES = ('/usr/share/', '/usr/local/bin/')


def _validate_source_path(path):
    """Ensures the source path is inside a temp directory."""
    real = os.path.realpath(path)
    if not any(real.startswith(p) for p in ALLOWED_SOURCE_PREFIXES):
        raise ValueError(f'Source path must be inside /tmp/: {path}')


def _validate_install_path(path):
    """Ensures the install path is inside an allowed prefix."""
    real = os.path.realpath(path)
    if not any(real.startswith(p) for p in ALLOWED_INSTALL_PREFIXES):
        raise ValueError(f'Install path must be inside /opt/: {path}')


def _validate_file_path(path):
    """Ensures a file operation path is inside allowed system directories.

    Uses abspath (not realpath) because we're validating where we intend
    to WRITE, not where an existing symlink at that location points to.
    realpath would follow existing symlinks and reject valid paths like
    /usr/local/bin/zen → /opt/zen/zen during updates.
    """
    normalized = os.path.abspath(path)
    if not any(normalized.startswith(p) for p in ALLOWED_FILE_PREFIXES):
        raise ValueError(f'File path not in allowed directories: {path}')


def do_install(data):
    """Copies extracted app to /opt, installs icon, .desktop, symlink."""
    app_root = data['app_root']
    install_dir = data['install_dir']
    binary_rel = data.get('binary_rel')
    icon_src = data.get('icon_src')
    icon_dest = data.get('icon_dest')
    desktop_path = data['desktop_path']
    desktop_content = data['desktop_content']
    symlink_path = data.get('symlink_path')
    symlink_target = data.get('symlink_target')

    # Validate paths before any filesystem operations
    _validate_source_path(app_root)
    _validate_install_path(install_dir)

    # 1. Copy app files to /opt/<app>/
    if os.path.exists(install_dir):
        shutil.rmtree(install_dir)
    shutil.copytree(app_root, install_dir, symlinks=True)

    # 2. Ensure binary is executable
    if binary_rel:
        binary_path = os.path.join(install_dir, binary_rel)
        if os.path.exists(binary_path):
            os.chmod(binary_path, os.stat(binary_path).st_mode | 0o755)

    # 3. Install icon
    if icon_src and icon_dest:
        _validate_file_path(icon_dest)
        if not os.path.isfile(icon_src):
            # icon_src could be from temp dir (tarball) or user's filesystem (custom)
            # Just skip if the file doesn't exist — don't fail the whole install
            pass
        else:
            os.makedirs(os.path.dirname(icon_dest), exist_ok=True)
            shutil.copy2(icon_src, icon_dest)

    # 4. Write .desktop file
    _validate_file_path(desktop_path)
    os.makedirs(os.path.dirname(desktop_path), exist_ok=True)
    with open(desktop_path, 'w') as f:
        f.write(desktop_content)
    os.chmod(desktop_path, 0o644)

    # 5. Create symlink
    if symlink_path and symlink_target:
        _validate_file_path(symlink_path)
        os.makedirs(os.path.dirname(symlink_path), exist_ok=True)
        if os.path.lexists(symlink_path):
            os.remove(symlink_path)
        os.symlink(symlink_target, symlink_path)

    # 6. Refresh databases
    for cmd in (
        ['update-desktop-database', '/usr/share/applications'],
        ['gtk-update-icon-cache', '-f', '-t', '/usr/share/icons/hicolor'],
    ):
        try:
            subprocess.run(cmd, capture_output=True, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return {'success': True}


def do_uninstall(data):
    """Removes system-wide installed app files."""
    for path in data.get('remove_dirs', []):
        if os.path.isdir(path) and path.startswith('/opt/'):
            shutil.rmtree(path, ignore_errors=True)

    for path in data.get('remove_files', []):
        if os.path.lexists(path) and (
            path.startswith('/usr/share/') or
            path.startswith('/usr/local/bin/')
        ):
            os.remove(path)

    for cmd in (
        ['update-desktop-database', '/usr/share/applications'],
        ['gtk-update-icon-cache', '-f', '-t', '/usr/share/icons/hicolor'],
    ):
        try:
            subprocess.run(cmd, capture_output=True, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return {'success': True}


def do_update(data):
    """Updates a system-wide installed app with backup/rollback.

    Steps:
    1. Backup existing install to install_dir.backup
    2. Install new version (same as do_install)
    3. On failure, rollback from backup
    """
    install_dir = data['install_dir']
    backup_dir = install_dir.rstrip('/') + '.backup'

    _validate_install_path(install_dir)

    # 1. Create backup
    if os.path.exists(install_dir):
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir, ignore_errors=True)
        shutil.move(install_dir, backup_dir)

    try:
        # 2. Install new version (reuse do_install logic)
        result = do_install(data)

        if result.get('success'):
            # 3a. Success — remove backup
            if os.path.exists(backup_dir):
                shutil.rmtree(backup_dir, ignore_errors=True)
            return result
        else:
            # 3b. Install reported failure — rollback
            raise RuntimeError(result.get('error', 'Install failed'))

    except Exception as e:
        # Rollback: restore from backup
        if os.path.exists(backup_dir):
            if os.path.exists(install_dir):
                shutil.rmtree(install_dir, ignore_errors=True)
            shutil.move(backup_dir, install_dir)
        return {'success': False, 'error': f'Update failed (rolled back): {e}'}


def main():
    try:
        raw = sys.stdin.read()
        request = json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        print(json.dumps({'success': False, 'error': f'Invalid input: {e}'}))
        sys.exit(1)

    action = request.get('action')
    try:
        if action == 'install':
            result = do_install(request)
        elif action == 'uninstall':
            result = do_uninstall(request)
        elif action == 'update':
            result = do_update(request)
        else:
            result = {'success': False, 'error': f'Unknown action: {action}'}
    except Exception as e:
        result = {'success': False, 'error': str(e)}

    print(json.dumps(result))
    sys.exit(0 if result.get('success') else 1)


if __name__ == '__main__':
    main()
