# Changelog

## Reviewed release, 2026-11-02

Based on the original `goad-custom-roles.tar.gz` export. Changes are deliberately small:

- `goad_hungarian_locale/defaults/main.yml`: added `goad_windows_apply_locale_changes: false`; fixed Windows GeoID `94` (Germany) to `109` (Hungary) for optional legacy mode.
- `goad_hungarian_locale/tasks/windows.yml`: gated the original Windows region/language/culture/system-locale tasks behind the new opt-in flag. Preserved timezone, keyboard registry writes, Windows Time and optional reboot.
- `goad_hungarian_locale/tasks/linux.yml`: ensure `/etc/X11/Xsession.d` exists before writing Xsession configuration.
- `goad_kali_dns/tasks/main.yml`: changed `command -v nmcli` to use `ansible.builtin.shell` (because `command` is a shell builtin); added `daemon_reload: true` before enabling the custom systemd resolver service.
- Removed generated `.galaxy_install_info` files from the source repository (they are installation metadata, not role source code).
- Added Ludus `source.yml`, root README, `.gitignore` and this changelog.

Not changed: original DNS topology (.10, .11, optional .254), Kali NetworkManager
connection modification behavior, fallback resolver script, remaining keyboards/timezones,
role names, or role order in the Ludus range.

## Known limitations

- Kali NetworkManager branch modifies and reactivates its connection on each run,
  potentially disrupting Ansible connectivity. Test on a disposable Kali VM first.
- Existing user-local role copies override the global copies in Ludus.
- Only static YAML/Jinja syntax checks were performed; VM integration testing is pending.
