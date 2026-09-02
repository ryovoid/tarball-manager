<p align="center">
  <img src="data/icons/hicolor/scalable/apps/io.github.ryovoid.TarballManager.svg" width="128" height="128" alt="Tarball Manager">
</p>

<h1 align="center">Tarball Manager</h1>

<p align="center">
  <strong>Install Linux app tarballs as proper desktop applications with one click.</strong>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#installation">Installation</a> •
  <a href="#building-from-source">Building</a> •
  <a href="#license">License</a>
</p>

---

Many Linux apps like **Zen Browser**, **Blender**, **Firefox**, and **Thunderbird** ship as bare tarballs — no package manager, no installer. You're expected to extract them, find the binary, set permissions, hunt for an icon, hand-write a `.desktop` file, and figure out where everything goes.

**Tarball Manager automates all of that.** Select a tarball, review what it found, tweak if needed, and hit install. Done.

## Features

- 🧙 **4-Step Wizard** — Select → Review → Configure → Install, with the next action always pinned in view
- 📚 **App Library** — Everything you installed in one list, with update badges, search, and one-click launch
- 🔍 **Smart Detection** — Automatically finds the main binary (ELF magic-byte detection), app icon, version, and architecture
- 📂 **Drag & Drop** — Drop a `.tar.gz`, `.tar.xz`, or `.tar.bz2` anywhere on the window, library included
- 🏠 **User or System-Wide** — Install just for yourself (`~/.local`) or for all users (`/opt`) with a native password prompt
- 🖼️ **Icon Handling** — Finds the best icon (SVG preferred) and installs it to the correct `hicolor` theme directory
- 🚀 **Desktop Integration** — Generates a proper `.desktop` launcher so the app shows up in your application menu
- 🔗 **PATH Symlink** — Optionally creates a command-line shortcut in your `$PATH`
- 🗑️ **Clean Uninstall** — Tracks every installed file and removes them all cleanly
- 🎨 **Native Look** — Follows your system accent colour, light/dark preference, and adapts down to narrow windows

## Installation

### Ubuntu / Debian

Download the `.deb` package from the [latest release](https://github.com/ryovoid/tarball-manager/releases/latest) and install it:

```bash
sudo dpkg -i tarball-manager_0.1.0_amd64.deb
sudo apt-get install -f   # installs any missing dependencies
```

To uninstall:

```bash
sudo dpkg -r tarball-manager
```

### Fedora

Download the `.rpm` package from the [latest release](https://github.com/ryovoid/tarball-manager/releases/latest) and install it:

```bash
sudo dnf install ./tarball-manager-0.1.0-1.fc44.noarch.rpm
```

To uninstall:

```bash
sudo dnf remove tarball-manager
```

### From Source

<details>
<summary>Build from source on any distribution</summary>

**Dependencies:**

| Distribution | Command |
|---|---|
| Fedora | `sudo dnf install python3 gtk4-devel libadwaita-devel meson ninja-build polkit` |
| Ubuntu / Debian | `sudo apt install python3 libgtk-4-dev libadwaita-1-dev meson ninja-build policykit-1` |
| Arch | `sudo pacman -S python gtk4 libadwaita meson ninja polkit` |

**Build & Install:**

```bash
git clone https://github.com/ryovoid/tarball-manager.git
cd tarball-manager
meson setup builddir --prefix=/usr/local
ninja -C builddir
sudo ninja -C builddir install
```

**Uninstall:**

```bash
sudo ninja -C builddir uninstall
```

</details>

## Usage

1. **Launch** — Open Tarball Manager from your app menu
2. **Select** — Drag & drop a tarball onto the window, or click *Browse Files*
3. **Review** — The app extracts and analyzes the tarball, showing detected name, version, executable, size, and architecture
4. **Configure** — Edit the app name, pick the right executable if multiple were found, choose a category, and select user or system-wide scope
5. **Install** — Hit install and you're done. Launch it straight from the finish screen, or find it in your app menu

### Supported Formats

| Format | Extension |
|--------|-----------|
| gzip   | `.tar.gz`, `.tgz` |
| xz     | `.tar.xz`, `.txz` |
| bzip2  | `.tar.bz2`, `.tbz2` |

## Contributing

Contributions are welcome! Feel free to open issues or submit pull requests.

```bash
# Development setup
git clone https://github.com/ryovoid/tarball-manager.git
cd tarball-manager
meson setup builddir --prefix=$HOME/.local
ninja -C builddir install
~/.local/bin/tarball_manager
```

## License

This project is licensed under the **MIT License** — see the [COPYING](COPYING) file for details.
