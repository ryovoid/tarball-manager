# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

"""Checks for newer versions via GitHub API or web page scraping."""

import json
import re
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta


_GITHUB_API = 'https://api.github.com/repos/{owner}/{repo}/releases/latest'
_CACHE_TTL = timedelta(hours=1)
_TIMEOUT = 15  # seconds

# Update source modes
SOURCE_GITHUB = 'github'
SOURCE_WEBPAGE = 'webpage'
SOURCE_MANUAL = 'manual'

# Version pattern: matches 1.2.3, 4.5, 1.0.242, etc.
_VERSION_RE = re.compile(r'(?<!\w)(\d+\.\d+(?:\.\d+)*)(?!\w)')


def parse_version(version_string):
    """Extracts numeric tuple from a version string.

    '4.5.12' → (4, 5, 12)
    'v2.1.0-beta' → (2, 1, 0)
    '153.0' → (153, 0)
    """
    nums = re.findall(r'\d+', version_string or '')
    return tuple(int(n) for n in nums) if nums else (0,)


def is_newer(latest, installed):
    """Returns True if latest version is newer than installed."""
    return parse_version(latest) > parse_version(installed)


def _clean_tag(tag):
    """Strips leading 'v' or 'release-' from a git tag."""
    tag = tag.strip()
    if tag.lower().startswith('v'):
        tag = tag[1:]
    if tag.lower().startswith('release-'):
        tag = tag[len('release-'):]
    return tag


class UpdateChecker:
    """Checks for available updates.

    Supports three modes:
    - github:  GitHub Releases API (owner/repo)
    - webpage: Scrapes a URL for version numbers
    - manual:  No auto-check (user drops tarball)

    Does NOT download anything — only checks versions.
    Results are cached per-app for 1 hour.
    """

    def __init__(self, store):
        self._store = store

    def check_one(self, app_name):
        """Checks a single app for updates.

        Returns:
            {
                'has_update': bool,
                'installed_version': str,
                'latest_version': str or None,
                'error': str or None,
            }
        """
        metadata = self._store.get_install(app_name)
        if not metadata:
            return {'has_update': False, 'error': 'App not installed'}

        installed = metadata.get('version', 'Unknown')
        source_type = metadata.get('update_source_type', SOURCE_GITHUB)
        source_value = metadata.get('update_source', '')

        # Manual mode = never auto-check
        if source_type == SOURCE_MANUAL or not source_value:
            return {
                'has_update': False,
                'installed_version': installed,
                'latest_version': None,
                'error': None,
            }

        # Check cache first
        last_checked = metadata.get('last_checked')
        cached_latest = metadata.get('latest_version')
        if last_checked and cached_latest:
            try:
                checked_at = datetime.fromisoformat(last_checked)
                if datetime.now() - checked_at < _CACHE_TTL:
                    return {
                        'has_update': is_newer(cached_latest, installed),
                        'installed_version': installed,
                        'latest_version': cached_latest,
                        'error': None,
                    }
            except (ValueError, TypeError):
                pass

        # Fetch latest version based on source type
        if source_type == SOURCE_GITHUB:
            result = self._fetch_from_github(source_value)
        elif source_type == SOURCE_WEBPAGE:
            result = self._fetch_from_webpage(source_value, installed)
        else:
            result = {'error': f'Unknown source type: {source_type}'}

        if result.get('error'):
            return {
                'has_update': False,
                'installed_version': installed,
                'latest_version': cached_latest,
                'error': result['error'],
            }

        latest = result['version']
        self._store.set_update_info(app_name, latest)

        return {
            'has_update': is_newer(latest, installed),
            'installed_version': installed,
            'latest_version': latest,
            'error': None,
        }

    def check_all(self, max_workers=4, timeout=30):
        """Checks all apps with a configured update source (parallel).

        Returns: dict of {app_name: check_result}
        """
        apps_to_check = [
            name for name, meta in self._store.list_installs()
            if meta.get('update_source') and
               meta.get('update_source_type', SOURCE_GITHUB) != SOURCE_MANUAL
        ]

        if not apps_to_check:
            return {}

        results = {}
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(self.check_one, name): name
                    for name in apps_to_check
                }
                try:
                    for future in as_completed(futures, timeout=timeout):
                        name = futures[future]
                        try:
                            results[name] = future.result()
                        except Exception as e:
                            results[name] = {
                                'has_update': False,
                                'error': str(e),
                            }
                except TimeoutError:
                    # Global timeout — cancel remaining checks, don't block
                    for f in futures:
                        f.cancel()
                    # Mark unchecked apps as timed out
                    for name in apps_to_check:
                        if name not in results:
                            results[name] = {
                                'has_update': False,
                                'error': 'Check timed out',
                            }
                pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass  # Never crash the caller

        return results

    # ── GitHub mode ──────────────────────────────────────

    @staticmethod
    def _fetch_from_github(github_repo):
        """Fetches latest release version from GitHub API.

        Args:
            github_repo: 'owner/repo' or full GitHub URL

        Returns:
            {'version': str} or {'error': str}
        """
        repo = github_repo.strip().rstrip('/')
        # Accept full URLs: https://github.com/owner/repo → owner/repo
        for prefix in ('https://github.com/', 'http://github.com/', 'github.com/'):
            if repo.lower().startswith(prefix.lower()):
                repo = repo[len(prefix):]
                break
        parts = repo.split('/')
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return {'error': f'Invalid format: "{github_repo}" (expected owner/repo)'}

        owner, repo = parts
        url = _GITHUB_API.format(owner=owner, repo=repo)

        try:
            req = urllib.request.Request(url, headers={
                'Accept': 'application/vnd.github+json',
                'User-Agent': 'TarballManager/0.1',
            })
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
                tag = data.get('tag_name', '')
                version = _clean_tag(tag)
                if not version:
                    return {'error': 'No version tag found in latest release'}
                return {'version': version}

        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {'error': f'Repository "{github_repo}" not found'}
            if e.code == 403:
                return {'error': 'GitHub rate limit hit. Try again later.'}
            return {'error': f'GitHub API error: {e.code}'}
        except urllib.error.URLError as e:
            return {'error': f'Network error: {e.reason}'}
        except Exception as e:
            return {'error': str(e)}

    # ── Web page scraping mode ───────────────────────────

    @staticmethod
    def _fetch_from_webpage(url, installed_version):
        """Scrapes a web page for version numbers.

        Finds all version-like patterns (X.Y.Z) on the page,
        and returns the highest one that matches the installed version's format.

        Falls back gracefully if the page can't be read or has
        no version numbers (login wall, JS-only, etc).

        Args:
            url: Full URL to scrape (e.g. https://kiro.dev/downloads)
            installed_version: Currently installed version string

        Returns:
            {'version': str} or {'error': str}
        """
        if not url.startswith(('http://', 'https://')):
            return {'error': f'Invalid URL: "{url}"'}

        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) TarballManager/0.1',
            })
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                html = resp.read().decode('utf-8', errors='replace')
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return {'error': 'Page requires login. Use Manual mode instead.'}
            return {'error': f'HTTP error: {e.code}'}
        except urllib.error.URLError as e:
            return {'error': f'Network error: {e.reason}'}
        except Exception as e:
            return {'error': str(e)}

        # Find all version-like strings on the page
        matches = _VERSION_RE.findall(html)
        if not matches:
            return {'error': 'No version numbers found on this page.'}

        unique = list(set(matches))

        # Smart filter: only keep versions with the same dot-depth as installed.
        # e.g. installed "1.0.242" (3 parts) → only match "X.Y.Z" patterns.
        # This prevents CSS/JS asset numbers like "185.2" from being picked.
        installed_depth = installed_version.count('.') + 1
        same_depth = [v for v in unique if v.count('.') + 1 == installed_depth]

        if not same_depth:
            return {
                'error': (
                    f'No version numbers matching format '
                    f'({"X.Y.Z" if installed_depth == 3 else "X.Y"}) '
                    f'found on this page. Try the downloads page URL.'
                )
            }

        same_depth.sort(key=parse_version, reverse=True)
        latest = same_depth[0]

        return {'version': latest}
