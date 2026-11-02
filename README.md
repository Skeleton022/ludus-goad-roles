# ludus-goad-roles

Custom Ansible roles for GOAD environments deployed with Ludus 2.

This repo is based on the two original roles exported from a working Ludus range.
The changes are intentionally small and documented in [CHANGELOG.md](CHANGELOG.md).

## Roles

| Role | Target | What it does |
|---|---|---|
| `goad_hungarian_locale` | Windows and Debian Linux | Budapest timezone, Hungarian keyboard. On Windows, language/region changes are **disabled by default**. |
| `goad_kali_dns` | Kali Linux | Derives VLAN DNS IPs from the machine address, sets search domains and keyboard layout. |

### English Windows UI/log preservation

The default behavior of `goad_hungarian_locale` does **not** call `win_region`,
`Set-Culture`, `Set-WinSystemLocale` or `Set-WinUserLanguageList`.
Windows UI language, user language preferences, culture, and system locale are left untouched.
It sets the Windows timezone to `Central Europe Standard Time`, the primary keyboard
registry preload entry to `0000040e` for the Ansible user and the sign-in screen,
and ensures W32Time is enabled. **It does not set every domain user's keyboard profile.**

To explicitly opt into the **original** regional behavior (not recommended when
English Windows UI and messages are a requirement), set:

```yaml
role_vars:
  goad_windows_apply_locale_changes: true
```

When opted in, the role sets Hungarian user language preferences, culture, system
locale, and region, using GeoID `109` for Hungary (corrected from the original `94`, Germany).
Only use this after verifying the result on a disposable Windows test VM.

### Kali DNS warning

The role assumes that the Kali IPv4 address is in the same `/24` as the GOAD
DCs at `.10` and `.11`, with router fallback `.254`. Check those assumptions
against your Ludus range. The fallback path writes `/etc/resolv.conf` using
a systemd oneshot service. If `/etc/resolv.conf` is managed by a different
resolver, review the system configuration before deploying.

## Repository structure

```text
source.yml
ansible/roles/goad_hungarian_locale/
ansible/roles/goad_kali_dns/
```

## Install on a NEW Ludus instance

```bash
ludus source add https://github.com/Skeleton022/ludus-goad-roles \
  --id goad-roles \
  --source-roles goad_hungarian_locale,goad_kali_dns \
  --global
ludus ansible role list
```

> Note: An admin's global role is ignored when that deploying user already
> has a user-local copy with the same name. For an existing Ludus instance,
> inspect `ludus ansible role list` before replacing anything. A `--force`
> install can replace existing installed roles; make a backup first.

Your existing range config can continue to reference the role names unchanged.

## Dry-run verification on a Windows lab VM

In PowerShell before and after applying the role:

```powershell
Get-UICulture
Get-Culture
Get-WinSystemLocale
Get-WinUserLanguageList
Get-TimeZone
Get-ItemProperty 'HKCU:\Keyboard Layout\Preload'
Get-ItemProperty 'Registry::HKEY_USERS\.DEFAULT\Keyboard Layout\Preload'
```

Expected: time zone and keyboard change, language/culture/locale values remain
unchanged. Verify the actual UI and Event Viewer in the test VM as well.
Windows event message rendering depends on installed language resources and context;
role code alone cannot guarantee every log message's language.

No Windows/Kali end-to-end deployment was run in the packaging environment.
