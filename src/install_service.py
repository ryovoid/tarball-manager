# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

import glob
import json
import os
import shutil
import struct
import subprocess
import sys
import threading

from .tarball_extractor import extract_tarball
from .binary_detector import detect_main_binary
from .icon_handler import find_best_icon, install_icon
from .desktop_entry_writer import write_desktop_entry, generate_desktop_entry, \
    format_display_name, sanitize_desktop_id
from .metadata_store import MetadataStore


# Files an Electron bundle ships next to its root package.json.
ELECTRON_MARKERS = (
    'resources/app.asar', 'resources/app', 'chrome-sandbox',
    'chrome_100_percent.pak', 'libffmpeg.so', 'icudtl.dat',
)

# Extensions of launcher wrappers — stripped when deriving a WM class.
LAUNCHER_EXTENSIONS = ('.sh', '.bin', '.run', '.py', '.appimage')


def _metadata_candidates(app_root, relative):
    """Expands a relative metadata path at the tarball root and one level deeper.

    Some bundles (Postman, for one) nest the whole application under app/, so
    the familiar Electron/Mozilla paths sit a directory below the root.
    """
    paths = []
    for pattern in (relative, os.path.join('*', relative)):
        paths.extend(sorted(glob.glob(os.path.join(app_root, pattern))))
    return [p for p in paths if os.path.isfile(p)]


def _clean_version(value):
    """Returns a non-empty version string, or None."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _version_from_package_json(path):
    """Reads the version field out of a package.json."""
    try:
        with open(path, 'r', errors='replace') as f:
            return _clean_version(json.load(f).get('version'))
    except (OSError, ValueError):
        return None


def _version_from_asar(path):
    """Reads package.json's version out of an Electron app.asar archive.

    An asar opens with two Chromium pickles: uint32 4, uint32 header size,
    uint32 payload size, uint32 JSON length, then the JSON directory listing.
    File contents follow it, at offsets relative to 8 + header size.
    """
    try:
        with open(path, 'rb') as f:
            _, header_size, _, json_len = struct.unpack('<4I', f.read(16))
            listing = json.loads(f.read(json_len).decode('utf-8', 'replace'))
            entry = listing.get('files', {}).get('package.json') or {}
            if 'offset' not in entry:
                return None
            f.seek(8 + header_size + int(entry['offset']))
            data = json.loads(f.read(int(entry['size'])).decode('utf-8', 'replace'))
            return _clean_version(data.get('version'))
    except (OSError, ValueError, struct.error, TypeError):
        return None


def _get_install_dir(scope, app_name):
    if scope == 'system':
        return f'/opt/{app_name}'
    return os.path.expanduser(f'~/.local/share/apps/{app_name}')


def _get_symlink_dir(scope):
    if scope == 'system':
        return '/usr/local/bin'
    return os.path.expanduser('~/.local/bin')


def _find_helper_script():
    """Finds the install_helper.py script.

    Searches in order (local-first so dev builds override system packages):
    1. User-local install (~/.local/share/tarball_manager/)
    2. Development: next to this file
    3. System-wide (/usr/local, then /usr)
    """
    # Development: next to this file (highest priority for dev builds)
    here = os.path.dirname(os.path.abspath(__file__))
    dev_path = os.path.join(here, 'install_helper.py')
    if os.path.exists(dev_path):
        return dev_path

    # User-local install
    local_path = os.path.join(
        os.path.expanduser('~/.local'), 'share', 'tarball_manager', 'install_helper.py'
    )
    if os.path.exists(local_path):
        return local_path

    # System-wide installs (RPM/DEB)
    for prefix in ('/usr/local', '/usr'):
        path = os.path.join(prefix, 'share', 'tarball_manager', 'install_helper.py')
        if os.path.exists(path):
            return path

    return None


def _run_pkexec(helper_path, request_data):
    """Runs the helper script via pkexec and returns the result.

    pkexec shows the native GNOME password dialog automatically.
    """
    try:
        proc = subprocess.run(
            ['pkexec', sys.executable, helper_path],
            input=json.dumps(request_data),
            capture_output=True, text=True, timeout=300,
        )

        if proc.returncode == 126:
            # User dismissed the password dialog
            return {'success': False, 'error': 'Authentication was cancelled'}
        if proc.returncode == 127:
            return {'success': False, 'error': 'pkexec is not available on this system'}

        try:
            result = json.loads(proc.stdout)
            return result
        except json.JSONDecodeError:
            stderr = proc.stderr.strip()
            return {'success': False, 'error': stderr or f'Helper exited with code {proc.returncode}'}

    except FileNotFoundError:
        return {'success': False, 'error': 'pkexec is not installed. Install polkit to enable system-wide installs.'}
    except subprocess.TimeoutExpired:
        return {'success': False, 'error': 'Installation timed out'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


class InstallService:
    """Orchestrates the full app installation pipeline."""

    def __init__(self):
        self.store = MetadataStore()

    @staticmethod
    def cleanup_analysis(analysis):
        """Cleans up the temp directory from an analysis.

        Call this when the user navigates away without installing,
        or after a failed install, to prevent /tmp/ leaks.
        """
        if analysis and analysis.get('temp_dir'):
            shutil.rmtree(analysis['temp_dir'], ignore_errors=True)

    def analyze(self, tarball_path, on_progress=None):
        """Analyzes a tarball without installing.

        Extracts to a temporary location, detects binary/icon/metadata.
        Returns an analysis dict with all detected info.
        Runs synchronously — call from a thread.
        """
        import tempfile
        # Use /var/tmp instead of /tmp because /tmp is often a tmpfs (RAM disk)
        # with limited space. Large tarballs (e.g. JetBrains IDEs at 1.5GB+)
        # need real disk-backed storage for extraction.
        temp_dir = tempfile.mkdtemp(prefix='tarball-manager-', dir='/var/tmp')

        try:
            if on_progress:
                on_progress('Extracting tarball…')

            app_root = extract_tarball(tarball_path, temp_dir, on_progress)

            if on_progress:
                on_progress('Detecting executables…')

            from .tarball_extractor import derive_app_name, derive_version, \
                derive_architecture, get_tarball_size

            app_name = derive_app_name(tarball_path)
            binary_result = detect_main_binary(app_root, app_name)
            icon_info = find_best_icon(app_root, app_name)

            # Check for existing .desktop file in the tarball
            existing_desktop = self._find_existing_desktop(app_root)

            # WM class / app_id — needed so the desktop environment can match
            # the running window to the .desktop entry (Wayland taskbar icon)
            wm_class = self._detect_wm_class(
                app_root, existing_desktop, binary_result.get('path')
            )

            # Version: try filename first, then look inside extracted files
            version = derive_version(tarball_path)
            if version == 'Unknown':
                version = self._detect_version_from_contents(app_root) or 'Unknown'

            # Calculate extracted size
            total_size = 0
            for dirpath, _, filenames in os.walk(app_root):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if os.path.isfile(fp):
                        total_size += os.path.getsize(fp)

            def _human_size(size):
                for unit in ('B', 'KB', 'MB', 'GB'):
                    if size < 1024:
                        return f'{size:.1f} {unit}'
                    size /= 1024
                return f'{size:.1f} TB'

            return {
                'app_name': app_name,
                'display_name': format_display_name(app_name),
                'version': version,
                'architecture': binary_result['arch'] if binary_result['path'] else derive_architecture(tarball_path),
                'tarball_size': get_tarball_size(tarball_path),
                'extracted_size': _human_size(total_size),
                'binary': binary_result,
                'icon': icon_info,
                'existing_desktop': existing_desktop,
                'wm_class': wm_class,
                'temp_dir': temp_dir,
                'app_root': app_root,
                'tarball_path': tarball_path,
            }
        except Exception:
            # Clean up temp dir on failure to prevent leaks
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    @staticmethod
    def _detect_version_from_contents(app_root):
        """Tries to detect the app version from files inside the tarball.

        Checks (in order):
        1. application.ini (Mozilla/Zen: Version=X.Y.Z)
        2. package.json (Electron apps: "version": "X.Y.Z")
        3. app.asar (Electron apps that ship package.json packed)
        4. version / VERSION / VERSION.txt files
        5. *.desktop files (X-AppVersion=)

        Every location is looked for at the tarball root and one directory
        deeper, since some bundles nest the application under app/.

        Returns version string or None.
        """
        import re

        # 1. application.ini (Mozilla-based apps like Zen, Firefox, Thunderbird)
        for ini_path in _metadata_candidates(app_root, 'application.ini'):
            try:
                with open(ini_path, 'r', errors='replace') as f:
                    for line in f:
                        key, _, value = line.strip().partition('=')
                        if key.strip().lower() == 'version' and value.strip():
                            return value.strip()
            except OSError:
                pass

        # 2. package.json (Electron apps like VS Code, Kiro, Obsidian, Postman)
        for relative in ('resources/app/package.json', 'package.json'):
            for pkg_path in _metadata_candidates(app_root, relative):
                version = _version_from_package_json(pkg_path)
                if version:
                    return version

        # 3. app.asar — Electron apps that keep package.json packed
        for asar_path in _metadata_candidates(app_root, 'resources/app.asar'):
            version = _version_from_asar(asar_path)
            if version:
                return version

        # 4. version / VERSION / VERSION.txt files
        for name in ('version', 'VERSION', 'VERSION.txt', 'version.txt'):
            for vpath in _metadata_candidates(app_root, name):
                try:
                    with open(vpath, 'r', errors='replace') as f:
                        ver = f.read().strip()
                        # Validate it looks like a version
                        if re.match(r'^\d+(\.\d+)+', ver):
                            return ver
                except OSError:
                    pass

        # 5. .desktop files with X-AppVersion
        for dpath in _metadata_candidates(app_root, '*.desktop'):
            try:
                with open(dpath, 'r', errors='replace') as f:
                    for line in f:
                        if line.strip().startswith('X-AppVersion='):
                            ver = line.strip().split('=', 1)[1].strip()
                            if ver:
                                return ver
            except OSError:
                pass

        return None

    @staticmethod
    def _find_existing_desktop(app_root):
        """Checks if the tarball contains a .desktop file.

        Returns the parsed content dict or None.
        """
        for dirpath, _, filenames in os.walk(app_root):
            depth = dirpath.replace(app_root, '').count(os.sep)
            if depth > 3:
                continue
            for filename in filenames:
                if filename.endswith('.desktop'):
                    filepath = os.path.join(dirpath, filename)
                    try:
                        data = {}
                        with open(filepath, 'r', errors='replace') as f:
                            for line in f:
                                line = line.strip()
                                if '=' in line and not line.startswith('#') and not line.startswith('['):
                                    key, _, value = line.partition('=')
                                    data[key.strip()] = value.strip()
                        if data.get('Type') == 'Application':
                            return {
                                'path': filepath,
                                'name': data.get('Name', ''),
                                'exec': data.get('Exec', ''),
                                'icon': data.get('Icon', ''),
                                'categories': data.get('Categories', ''),
                                'comment': data.get('Comment', ''),
                                'wm_class': data.get('StartupWMClass', ''),
                            }
                    except OSError:
                        continue
        return None

    @staticmethod
    def _detect_wm_class(app_root, existing_desktop=None, binary_path=None):
        """Detects the WM class (Wayland app_id) the app will report.

        Checks (in order):
        1. StartupWMClass from a .desktop file shipped in the tarball
        2. package.json "name" (Electron lowercases it and turns spaces into
           hyphens to build the app_id)
        3. The binary filename

        Returns the WM class string, or '' if nothing could be detected.
        """
        if existing_desktop and existing_desktop.get('wm_class'):
            return existing_desktop['wm_class'].strip()

        # Electron apps: resources/app/package.json, else a root package.json
        # (only trusted when the tarball actually looks like an Electron bundle
        # — a stray package.json would give a bogus app_id).
        pkg_path = os.path.join(app_root, 'resources', 'app', 'package.json')
        if not os.path.isfile(pkg_path):
            pkg_path = os.path.join(app_root, 'package.json')
            if not any(os.path.exists(os.path.join(app_root, marker))
                       for marker in ELECTRON_MARKERS):
                pkg_path = None
        if pkg_path and os.path.isfile(pkg_path):
            try:
                with open(pkg_path, 'r', errors='replace') as f:
                    name = json.load(f).get('name', '')
                if name:
                    return name.strip().lower().replace(' ', '-')
            except (OSError, json.JSONDecodeError, AttributeError):
                pass

        if binary_path:
            base = os.path.basename(binary_path)
            stem, ext = os.path.splitext(base)
            return stem if ext.lower() in LAUNCHER_EXTENSIONS else base

        return ''

    def install(self, analysis, config, on_progress=None):
        """Performs the actual installation using analyzed data + user config.

        For system scope: uses pkexec to elevate privileges (shows password dialog).
        For user scope: installs directly without elevation.
        """
        app_name = config['app_name']
        scope = config['scope']
        display_name = config['display_name']

        if scope == 'system':
            return self._install_system(analysis, config, on_progress)
        else:
            return self._install_user(analysis, config, on_progress)

    def _install_system(self, analysis, config, on_progress=None):
        """System-wide install via pkexec helper."""
        app_name = config['app_name']
        display_name = config['display_name']

        helper = _find_helper_script()
        if not helper:
            return {'success': False,
                    'error': 'Could not find the install helper script. '
                             'Please reinstall Tarball Manager.'}

        if on_progress:
            on_progress('auth', 'Requesting administrator privileges…')

        install_dir = _get_install_dir('system', app_name)

        # Compute binary relative path
        binary_rel = None
        if config.get('binary_path') and analysis['app_root']:
            binary_rel = os.path.relpath(config['binary_path'], analysis['app_root'])

        new_binary = os.path.join(install_dir, binary_rel) if binary_rel else None

        # Compute icon destination
        icon_src = None
        icon_dest = None
        icon_name = 'application-x-executable'
        if analysis.get('icon'):
            icon_src = analysis['icon']['path']
            ext = analysis['icon']['ext']
            size_dir = analysis['icon']['size_dir']
            icon_dest = f'/usr/share/icons/hicolor/{size_dir}/apps/{app_name}{ext}'
            icon_name = app_name

        # Generate .desktop content. StartupWMClass and a filename matching
        # the app_id let the desktop environment resolve the running window.
        wm_class = config.get('wm_class') or analysis.get('wm_class') or ''
        desktop_id = sanitize_desktop_id(wm_class) or app_name

        desktop_content = generate_desktop_entry(
            name=display_name,
            exec_path=new_binary or '',
            icon=icon_name,
            categories=config.get('categories', 'Utility;'),
            wm_class=wm_class,
        )
        desktop_path = f'/usr/share/applications/{desktop_id}.desktop'

        # Symlink
        symlink_path = None
        symlink_target = None
        if config.get('create_symlink', True) and new_binary:
            symlink_path = f'/usr/local/bin/{app_name}'
            symlink_target = new_binary

        request = {
            'action': 'install',
            'app_root': analysis['app_root'],
            'install_dir': install_dir,
            'binary_rel': binary_rel,
            'icon_src': icon_src,
            'icon_dest': icon_dest,
            'desktop_path': desktop_path,
            'desktop_content': desktop_content,
            'symlink_path': symlink_path,
            'symlink_target': symlink_target,
        }

        if on_progress:
            on_progress('installing', 'Installing with elevated privileges…')

        result = _run_pkexec(helper, request)

        if result.get('success'):
            if on_progress:
                on_progress('metadata', 'Recording installation…')

            self.store.add_install(app_name, {
                'scope': 'system',
                'install_dir': install_dir,
                'binary_path': new_binary,
                'icon_path': icon_dest,
                'icon_name': icon_name,
                'desktop_entry_path': desktop_path,
                'symlink_path': symlink_path,
                'tarball_name': os.path.basename(analysis['tarball_path']),
                'display_name': display_name,
                'version': analysis.get('version', 'Unknown'),
                'wm_class': wm_class,
            })

            if on_progress:
                on_progress('done', 'Installation complete!')

        # Always clean up temp dir (success or failure)
        self.cleanup_analysis(analysis)

        return result

    def _install_user(self, analysis, config, on_progress=None):
        """User-scope install (no privilege elevation needed)."""
        app_name = config['app_name']
        scope = 'user'
        display_name = config['display_name']

        try:
            if on_progress:
                on_progress('installing', 'Moving files to install directory…')

            install_dir = _get_install_dir(scope, app_name)
            if os.path.exists(install_dir):
                shutil.rmtree(install_dir)
            shutil.copytree(analysis['app_root'], install_dir, symlinks=True)

            # Recompute binary path
            if config.get('binary_path') and analysis['app_root']:
                rel = os.path.relpath(config['binary_path'], analysis['app_root'])
                new_binary = os.path.join(install_dir, rel)
            else:
                new_binary = config.get('binary_path')

            if new_binary and os.path.exists(new_binary):
                os.chmod(new_binary, os.stat(new_binary).st_mode | 0o755)

            if on_progress:
                on_progress('icon', 'Installing application icon…')
            icon_result = install_icon(analysis.get('icon'), app_name, scope)

            if on_progress:
                on_progress('desktop', 'Creating desktop launcher…')
            wm_class = config.get('wm_class') or analysis.get('wm_class') or ''
            desktop_path = write_desktop_entry(
                app_name, scope,
                desktop_id=wm_class,
                name=display_name,
                exec_path=new_binary or '',
                icon=icon_result['icon_name'],
                categories=config.get('categories', 'Utility;'),
                wm_class=wm_class,
            )

            symlink_path = None
            if config.get('create_symlink', True) and new_binary:
                if on_progress:
                    on_progress('symlink', 'Setting up command-line access…')
                symlink_dir = _get_symlink_dir(scope)
                os.makedirs(symlink_dir, exist_ok=True)
                symlink_path = os.path.join(symlink_dir, app_name)
                if os.path.lexists(symlink_path):
                    os.remove(symlink_path)
                try:
                    os.symlink(new_binary, symlink_path)
                except OSError:
                    symlink_path = None

            if on_progress:
                on_progress('refresh', 'Refreshing desktop databases…')
            self._post_install_refresh(scope)

            if on_progress:
                on_progress('metadata', 'Recording installation…')

            self.store.add_install(app_name, {
                'scope': scope,
                'install_dir': install_dir,
                'binary_path': new_binary,
                'icon_path': icon_result.get('icon_path'),
                'icon_name': icon_result['icon_name'],
                'desktop_entry_path': desktop_path,
                'symlink_path': symlink_path,
                'tarball_name': os.path.basename(analysis['tarball_path']),
                'display_name': display_name,
                'version': analysis.get('version', 'Unknown'),
                'wm_class': wm_class,
            })

            if on_progress:
                on_progress('done', 'Installation complete!')

            return {'success': True}

        except Exception as e:
            return {'success': False, 'error': str(e)}
        finally:
            # Always clean up temp dir (success or failure)
            self.cleanup_analysis(analysis)

    def uninstall(self, app_name):
        """Removes an installed app. Uses pkexec for system-scope apps."""
        metadata = self.store.get_install(app_name)
        if not metadata:
            return {'success': False, 'error': f'"{app_name}" is not installed'}

        scope = metadata.get('scope', 'user')

        if scope == 'system':
            return self._uninstall_system(app_name, metadata)
        else:
            return self._uninstall_user(app_name, metadata)

    def _uninstall_system(self, app_name, metadata):
        """System uninstall via pkexec."""
        helper = _find_helper_script()
        if not helper:
            return {'success': False, 'error': 'Install helper not found'}

        remove_dirs = []
        remove_files = []

        if metadata.get('install_dir'):
            remove_dirs.append(metadata['install_dir'])
        for key in ('desktop_entry_path', 'icon_path', 'symlink_path'):
            path = metadata.get(key)
            if path:
                remove_files.append(path)

        request = {
            'action': 'uninstall',
            'remove_dirs': remove_dirs,
            'remove_files': remove_files,
        }

        result = _run_pkexec(helper, request)
        if result.get('success'):
            self.store.remove_install(app_name)
        return result

    def _uninstall_user(self, app_name, metadata):
        """User-scope uninstall."""
        try:
            if metadata.get('install_dir') and os.path.exists(metadata['install_dir']):
                shutil.rmtree(metadata['install_dir'])
            for key in ('desktop_entry_path', 'icon_path', 'symlink_path'):
                path = metadata.get(key)
                if path and os.path.lexists(path):
                    os.remove(path)

            self._post_install_refresh('user')
            self.store.remove_install(app_name)
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _post_install_refresh(self, scope):
        if scope == 'system':
            apps_dir = '/usr/share/applications'
            icons_dir = '/usr/share/icons/hicolor'
        else:
            apps_dir = os.path.expanduser('~/.local/share/applications')
            icons_dir = os.path.expanduser('~/.local/share/icons/hicolor')

        for cmd in (
            ['update-desktop-database', apps_dir],
            ['gtk-update-icon-cache', '-f', '-t', icons_dir],
        ):
            try:
                subprocess.run(cmd, capture_output=True, timeout=10)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
