Name:           tarball-manager
Version:        0.1.4
Release:        1%{?dist}
Summary:        Install Linux app tarballs as desktop applications

License:        MIT
URL:            https://github.com/ryovoid/tarball-manager
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  meson >= 1.0.0
BuildRequires:  ninja-build
BuildRequires:  gcc
BuildRequires:  gettext
BuildRequires:  glib2-devel
BuildRequires:  python3-devel
BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib

Requires:       python3
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       polkit
Requires:       hicolor-icon-theme
Requires:       shared-mime-info
Requires:       desktop-file-utils

# The application ships in %%{_datadir}, not in the Python sitelib, so the
# automatic byte-compiler has nothing to do here.
%global __brp_python_bytecompile %{nil}

%description
Tarball Manager lets you install, manage, and update
applications distributed as .tar.gz/.tar.xz archives.
It detects executables, creates desktop entries, and
handles both user and system-wide installations.

%prep
%autosetup -n %{name}-%{version}

%build
%meson
%meson_build

%install
%meson_install

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/io.github.ryovoid.TarballManager.desktop
appstream-util validate-relax --nonet \
    %{buildroot}%{_metainfodir}/io.github.ryovoid.TarballManager.metainfo.xml

%files
%license COPYING
%doc README.md
%{_bindir}/tarball_manager
%{_datadir}/applications/io.github.ryovoid.TarballManager.desktop
%{_datadir}/dbus-1/services/io.github.ryovoid.TarballManager.service
%{_datadir}/glib-2.0/schemas/io.github.ryovoid.TarballManager.gschema.xml
%{_datadir}/icons/hicolor/scalable/apps/io.github.ryovoid.TarballManager.svg
%{_datadir}/icons/hicolor/symbolic/apps/io.github.ryovoid.TarballManager-symbolic.svg
%{_metainfodir}/io.github.ryovoid.TarballManager.metainfo.xml
%{_datadir}/polkit-1/actions/io.github.ryovoid.TarballManager.policy
%{_datadir}/tarball_manager/

%changelog
* Wed Sep 02 2026 ryovoid <rp6502293@gmail.com> - 0.1.4-1
- Electron applications that nest their bundle a directory deeper, such as
  Postman, now report their version instead of Unknown
- Applications that ship package.json packed inside app.asar have their
  version read out of the archive

* Wed Sep 02 2026 ryovoid <rp6502293@gmail.com> - 0.1.3-1
- Sprite sheets and other bundler artwork no longer outrank the real
  application icon
- Icons kept in an assets directory are found again

* Sun Aug 30 2026 ryovoid <rp6502293@gmail.com> - 0.1.2-2
- Icon scanning no longer aborts the whole analysis when the tarball
  contains a broken symlink pointing at an icon file

* Sun Aug 30 2026 ryovoid <rp6502293@gmail.com> - 0.1.2-1
- Icon detection skips SVGs that only wrap an embedded bitmap, which
  installed as a dark square instead of the app icon
- Icon candidates are ranked by location, filename and real pixel size
- Generated desktop entries set StartupWMClass and are named after the
  app id the running window reports, fixing the Wayland fallback icon
  in the taskbar

* Sun Aug 02 2026 ryovoid <rp6502293@gmail.com> - 0.1.1-1
- System-scope updates and large tarball extraction fixes
- Custom application logo support

* Tue Jul 28 2026 ryovoid <rp6502293@gmail.com> - 0.1.0-1
- Initial release
