# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

import os
import struct

# ELF magic bytes
ELF_MAGIC = b'\x7fELF'

# ELF architecture codes (e_machine at offset 18, 2 bytes little-endian)
ELF_ARCH = {
    0x03: 'i386',
    0x28: 'ARM',
    0x3E: 'x86_64',
    0xB7: 'aarch64',
}

# Directories to skip when searching for the main binary
SKIP_DIRS = {
    'lib', 'lib64', 'lib32', 'share', 'include', 'man', 'doc',
    'locale', 'icons', 'themes', 'fonts', 'licenses',
    'dictionaries', 'resources', 'locales', 'plugins',
}


def is_elf_binary(filepath):
    """Checks if a file is an ELF binary by reading its magic bytes."""
    try:
        with open(filepath, 'rb') as f:
            magic = f.read(4)
            return magic == ELF_MAGIC
    except (OSError, PermissionError):
        return False


def get_elf_architecture(filepath):
    """Reads the ELF header to determine the binary's architecture."""
    try:
        with open(filepath, 'rb') as f:
            magic = f.read(4)
            if magic != ELF_MAGIC:
                return 'Unknown'
            f.seek(18)
            e_machine = struct.unpack('<H', f.read(2))[0]
            return ELF_ARCH.get(e_machine, f'Unknown (0x{e_machine:X})')
    except (OSError, struct.error):
        return 'Unknown'


def _is_in_skip_dir(filepath, root_path):
    """Checks if a file is inside a directory we typically skip."""
    relative = os.path.relpath(filepath, root_path)
    parts = relative.split(os.sep)
    return any(part in SKIP_DIRS for part in parts)


def scan_for_binaries(app_root_dir):
    """Recursively scans a directory for ELF binaries.

    Returns a list of dicts:
        [{'path': str, 'name': str, 'size': int, 'depth': int, 'in_skip_dir': bool, 'arch': str}]
    """
    results = []

    for dirpath, dirnames, filenames in os.walk(app_root_dir):
        depth = dirpath.replace(app_root_dir, '').count(os.sep)
        if depth > 3:
            dirnames.clear()
            continue

        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            if not os.path.isfile(filepath):
                continue
            if is_elf_binary(filepath):
                results.append({
                    'path': filepath,
                    'name': filename,
                    'size': os.path.getsize(filepath),
                    'depth': depth,
                    'in_skip_dir': _is_in_skip_dir(filepath, app_root_dir),
                    'arch': get_elf_architecture(filepath),
                })

    return results


def detect_main_binary(app_root_dir, app_name):
    """Detects the main executable binary using heuristics.

    Priority:
    1. Name matches app name (bidirectional)
    2. Not a known non-launcher binary (pingsender, updater, etc.)
    3. Not in a skip directory (lib/, share/, etc.)
    4. Shallowest depth
    5. Largest file size

    Returns:
        {
            'path': str or None,
            'name': str or None,
            'arch': str,
            'all_binaries': list of {'path', 'name'}
        }
    """
    binaries = scan_for_binaries(app_root_dir)

    # Also look for shell script launchers at root level
    for entry in os.listdir(app_root_dir):
        filepath = os.path.join(app_root_dir, entry)
        if not os.path.isfile(filepath) or not os.access(filepath, os.X_OK):
            continue
        if entry.endswith('.sh') or not os.path.splitext(entry)[1]:
            try:
                with open(filepath, 'rb') as f:
                    head = f.read(64)
                    if head.startswith((b'#!/bin/bash', b'#!/bin/sh', b'#!/usr/bin/env')):
                        binaries.append({
                            'path': filepath,
                            'name': entry,
                            'size': os.path.getsize(filepath),
                            'depth': 0,
                            'in_skip_dir': False,
                            'arch': 'script',
                        })
            except OSError:
                pass

    if not binaries:
        return {'path': None, 'name': None, 'arch': 'Unknown', 'all_binaries': []}

    # Known non-launcher binaries (helpers, not the main app)
    _NON_LAUNCHERS = {
        'pingsender', 'updater', 'crashreporter', 'glxtest',
        'vaapitest', 'vulkantest', 'minidump-analyzer',
        'plugin-container',
    }

    normalized_app = app_name.lower().replace('-', '').replace('_', '')

    def sort_key(b):
        bn = b['name'].lower().replace('-', '').replace('_', '')
        # Bidirectional match: app_name in binary OR binary in app_name
        name_match = 0 if (normalized_app in bn or bn in normalized_app) else 1
        # Penalize known helper binaries
        is_helper = 1 if b['name'].lower().rstrip('.sh') in _NON_LAUNCHERS else 0
        skip = 1 if b['in_skip_dir'] else 0
        return (name_match, is_helper, skip, b['depth'], -b['size'])

    binaries.sort(key=sort_key)
    best = binaries[0]

    return {
        'path': best['path'],
        'name': best['name'],
        'arch': best['arch'],
        'all_binaries': [{'path': b['path'], 'name': b['name']} for b in binaries],
    }
