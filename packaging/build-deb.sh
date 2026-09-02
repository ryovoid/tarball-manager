#!/bin/bash
#
# Builds a .deb package for Debian/Ubuntu and derivatives (Mint, Kali, Pop!_OS…).
#
# The package is architecture-independent, so it can be built on any distro
# that has meson and ninja — dpkg-deb is used when available, otherwise the
# archive is assembled with ar and tar.
#
# Usage: packaging/build-deb.sh [revision]     (revision defaults to 1)
#
# MIT License
# Copyright (c) 2026 ryovoid
# SPDX-License-Identifier: MIT

set -euo pipefail

REVISION="${1:-1}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEB_DIR="$REPO_ROOT/packaging/deb"
OUT_DIR="$REPO_ROOT/dist"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

VERSION="$(sed -n "s/^ *version: *'\([^']*\)'.*/\1/p" "$REPO_ROOT/meson.build" | head -1)"
if [ -z "$VERSION" ]; then
    echo "error: could not read the version from meson.build" >&2
    exit 1
fi

PKG="tarball-manager"
ROOT="$WORK_DIR/root"

echo "Building $PKG $VERSION-$REVISION (all)"

# 1. Build and stage the application tree.
meson setup "$WORK_DIR/build" "$REPO_ROOT" --prefix=/usr --buildtype=release >/dev/null
DESTDIR="$ROOT" meson install -C "$WORK_DIR/build" >/dev/null

# 2. Documentation Debian policy expects.
DOC_DIR="$ROOT/usr/share/doc/$PKG"
install -d "$DOC_DIR"
install -m 644 "$DEB_DIR/copyright" "$DOC_DIR/copyright"
install -m 644 "$REPO_ROOT/README.md" "$DOC_DIR/README.md"
gzip -9nc "$DEB_DIR/changelog" > "$DOC_DIR/changelog.Debian.gz"
chmod 644 "$DOC_DIR/changelog.Debian.gz"

# 3. Control area.
install -d "$ROOT/DEBIAN"
INSTALLED_SIZE="$(du -ks "$ROOT" | cut -f1)"
sed -e "s/@VERSION@/$VERSION/" \
    -e "s/@REVISION@/$REVISION/" \
    -e "s/@INSTALLED_SIZE@/$INSTALLED_SIZE/" \
    "$DEB_DIR/control.in" > "$ROOT/DEBIAN/control"
install -m 755 "$DEB_DIR/postinst" "$ROOT/DEBIAN/postinst"
install -m 755 "$DEB_DIR/postrm" "$ROOT/DEBIAN/postrm"

# 4. Normalise permissions, then checksum every shipped file.
find "$ROOT/usr" -type d -exec chmod 755 {} +
find "$ROOT/usr" -type f -exec chmod 644 {} +
chmod 755 "$ROOT/usr/bin/tarball_manager" "$ROOT/usr/share/tarball_manager/install_helper.py"
(cd "$ROOT" && find usr -type f -print0 | sort -z | xargs -0 md5sum > DEBIAN/md5sums)
chmod 644 "$ROOT/DEBIAN/md5sums" "$ROOT/DEBIAN/control"

# 5. Assemble the archive.
install -d "$OUT_DIR"
DEB="$OUT_DIR/${PKG}_${VERSION}-${REVISION}_all.deb"
rm -f "$DEB"

if command -v dpkg-deb >/dev/null 2>&1; then
    dpkg-deb --root-owner-group --build "$ROOT" "$DEB" >/dev/null
else
    # A .deb is an ar archive of three members, in this exact order.
    TAR_OPTS=(--owner=root --group=root --numeric-owner --sort=name --mtime=@0)
    echo '2.0' > "$WORK_DIR/debian-binary"
    tar "${TAR_OPTS[@]}" -czf "$WORK_DIR/control.tar.gz" -C "$ROOT/DEBIAN" .
    tar "${TAR_OPTS[@]}" -cJf "$WORK_DIR/data.tar.xz" -C "$ROOT" usr
    (cd "$WORK_DIR" && ar rcD "$DEB" debian-binary control.tar.gz data.tar.xz)
fi

echo "Wrote $DEB"
