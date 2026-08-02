# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

import json
import os
import shutil
import subprocess
import sys
import threading

from .tarball_extractor import extract_tarball
from .binary_detector import detect_main_binary
from .icon_handler import find_best_icon, install_icon
from .desktop_entry_writer import write_desktop_entry, generate_desktop_entry, format_display_name
from .metadata_store import MetadataStore


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
            icon_info = find_best_icon(app_root)

            # Check for existing .desktop file in the tarball
            existing_desktop = self._find_existing_desktop(app_root)

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
        3. version / VERSION / VERSION.txt files
        4. *.desktop files (X-AppVersion=)

        Returns version string or None.
        """
        import re

        # 1. application.ini (Mozilla-based apps like Zen, Firefox, Thunderbird)
        ini_path = os.path.join(app_root, 'application.ini')
        if os.path.isfile(ini_path):
            try:
                with open(ini_path, 'r', errors='replace') as f:
                    for line in f:
                        key, _, value = line.strip().partition('=')
                        if key.strip().lower() == 'version' and value.strip():
                            return value.strip()
            except OSError:
                pass

        # 2. package.json (Electron apps like VS Code, Kiro, Obsidian)
        pkg_path = os.path.join(app_root, 'resources', 'app', 'package.json')
        if not os.path.isfile(pkg_path):
            pkg_path = os.path.join(app_root, 'package.json')
        if os.path.isfile(pkg_path):
            try:
                import json
                with open(pkg_path, 'r', errors='replace') as f:
                    data = json.load(f)
                    ver = data.get('version', '')
                    if ver:
                        return ver
            except (OSError, json.JSONDecodeError):
                pass

        # 3. version / VERSION / VERSION.txt files
        for name in ('version', 'VERSION', 'VERSION.txt', 'version.txt'):
            vpath = os.path.join(app_root, name)
            if os.path.isfile(vpath):
                try:
                    with open(vpath, 'r', errors='replace') as f:
                        ver = f.read().strip()
                        # Validate it looks like a version
                        if re.match(r'^\d+(\.\d+)+', ver):
                            return ver
                except OSError:
                    pass

        # 4. .desktop files with X-AppVersion
        for entry in os.listdir(app_root):
            if entry.endswith('.desktop'):
                try:
                    with open(os.path.join(app_root, entry), 'r', errors='replace') as f:
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
                            }
                    except OSError:
                        continue
        return None

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

        # Generate .desktop content
        desktop_content = generate_desktop_entry(
            name=display_name,
            exec_path=new_binary or '',
            icon=icon_name,
            categories=config.get('categories', 'Utility;'),
        )
        desktop_path = f'/usr/share/applications/{app_name}.desktop'

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
            desktop_path = write_desktop_entry(
                app_name, scope,
                name=display_name,
                exec_path=new_binary or '',
                icon=icon_result['icon_name'],
                categories=config.get('categories', 'Utility;'),
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
