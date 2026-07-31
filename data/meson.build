desktop_file = i18n.merge_file(
        input: 'io.github.ryovoid.TarballManager.desktop.in',
       output: 'io.github.ryovoid.TarballManager.desktop',
         type: 'desktop',
       po_dir: '../po',
      install: true,
  install_dir: get_option('datadir') / 'applications'
)

desktop_utils = find_program('desktop-file-validate', required: false)
if desktop_utils.found()
  test('Validate desktop file', desktop_utils, args: [desktop_file])
endif

appstream_file = i18n.merge_file(
        input: 'io.github.ryovoid.TarballManager.metainfo.xml.in',
       output: 'io.github.ryovoid.TarballManager.metainfo.xml',
       po_dir: '../po',
      install: true,
  install_dir: get_option('datadir') / 'metainfo'
)

appstreamcli = find_program('appstreamcli', required: false, disabler: true)
test('Validate appstream file', appstreamcli,
     args: ['validate', '--no-net', '--explain', appstream_file])

install_data('io.github.ryovoid.TarballManager.gschema.xml',
  install_dir: get_option('datadir') / 'glib-2.0' / 'schemas'
)

compile_schemas = find_program('glib-compile-schemas', required: false, disabler: true)
test('Validate schema file',
     compile_schemas,
     args: ['--strict', '--dry-run', meson.current_source_dir()])

service_conf = configuration_data()
service_conf.set('bindir', get_option('prefix') / get_option('bindir'))
configure_file(
  input: 'io.github.ryovoid.TarballManager.service.in',
  output: 'io.github.ryovoid.TarballManager.service',
  configuration: service_conf,
  install_dir: get_option('datadir') / 'dbus-1' / 'services'
)

# PolicyKit policy for privilege escalation
pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()
policy_conf = configuration_data()
policy_conf.set('HELPER_PATH', pkgdatadir / 'install_helper.py')
configure_file(
  input: 'io.github.ryovoid.TarballManager.policy.in',
  output: 'io.github.ryovoid.TarballManager.policy',
  configuration: policy_conf,
  install_dir: get_option('datadir') / 'polkit-1' / 'actions'
)

subdir('icons')
