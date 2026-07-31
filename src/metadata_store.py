# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

import fcntl
import json
import os
import tempfile
from datetime import datetime


def _get_store_path():
    return os.path.expanduser('~/.local/share/tarball-manager/installs.json')


class MetadataStore:
    """JSON-based store for tracking installed applications.

    Uses fcntl.flock() to prevent concurrent writes from corrupting the file.
    Writes use atomic temp-file + os.replace to avoid truncation races.
    """

    def __init__(self):
        self._path = _get_store_path()
        self._data = {}
        self._load()

    def _load(self):
        try:
            with open(self._path, 'r') as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    self._data = json.load(f)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}

    def reload(self):
        """Public method to re-read the store from disk."""
        self._load()

    def _save(self):
        """Atomic save: write to temp file, then replace original.

        This avoids the truncation-before-lock race condition that
        open('w') + flock() would have.
        """
        store_dir = os.path.dirname(self._path)
        os.makedirs(store_dir, exist_ok=True)

        # Write to a temp file in the same directory (same filesystem for rename)
        fd, tmp_path = tempfile.mkstemp(dir=store_dir, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    json.dump(self._data, f, indent=2)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
            # Atomic rename — this is the safe part
            os.replace(tmp_path, self._path)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def add_install(self, app_name, metadata):
        self._data[app_name] = {
            **metadata,
            'installed_at': datetime.now().isoformat(),
        }
        self._save()

    def remove_install(self, app_name):
        self._data.pop(app_name, None)
        self._save()

    def get_install(self, app_name):
        return self._data.get(app_name)

    def list_installs(self):
        return list(self._data.items())

    def is_installed(self, app_name):
        return app_name in self._data

    # ── Update-related methods ──────────────────────────

    def set_update_source(self, app_name, github_repo):
        """Sets the GitHub repo (owner/repo) used to check for updates."""
        if app_name in self._data:
            self._data[app_name]['update_source'] = github_repo
            self._save()

    def set_update_info(self, app_name, latest_version):
        """Caches the latest version found from GitHub."""
        if app_name in self._data:
            self._data[app_name]['latest_version'] = latest_version
            self._data[app_name]['last_checked'] = datetime.now().isoformat()
            self._save()

    def update_version(self, app_name, new_version):
        """Updates the installed version after a successful update."""
        if app_name in self._data:
            self._data[app_name]['version'] = new_version
            self._data[app_name]['updated_at'] = datetime.now().isoformat()
            # Clear cached update info since we just updated
            self._data[app_name].pop('latest_version', None)
            self._data[app_name].pop('last_checked', None)
            self._save()

    def update_metadata(self, app_name, fields):
        """Updates arbitrary metadata fields for an app."""
        if app_name in self._data:
            self._data[app_name].update(fields)
            self._save()
