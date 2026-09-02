pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()
moduledir = pkgdatadir / 'tarball_manager'
gnome = import('gnome')

gnome.compile_resources('tarball_manager',
  'tarball_manager.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
)

python = import('python')

conf = configuration_data()
conf.set('PYTHON', python.find_installation('python3').full_path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', get_option('prefix') / get_option('localedir'))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'tarball_manager.in',
  output: 'tarball_manager',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x'
)

tarball_manager_sources = [
  '__init__.py',
  'main.py',
  'window.py',
  'tarball_extractor.py',
  'binary_detector.py',
  'icon_handler.py',
  'desktop_entry_writer.py',
  'metadata_store.py',
  'install_service.py',
  'update_checker.py',
  'dashboard_page.py',
  'detail_page.py',
  'widgets.py',
]

install_data(tarball_manager_sources, install_dir: moduledir)

# Install the privileged helper script to pkgdatadir (not moduledir)
# so pkexec can find it at a stable path
install_data('install_helper.py',
  install_dir: pkgdatadir,
  install_mode: 'rwxr-xr-x'
)
