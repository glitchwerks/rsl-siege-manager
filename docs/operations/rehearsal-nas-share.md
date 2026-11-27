# Isolated rehearsal backup share

This share holds disposable encrypted backups from the local VMware rehearsal
VM. It must be separate from the production `share` SMB share and its account.
The proposed share and local account names are both `rsl_rehearsal`; choose
different names only if those names are already in use, and pass the chosen
names to the inspector.

## Prepare on the Windows NAS

Inspect the existing production share, backing folder, accounts, and ACLs first.
Preserve their settings. Create a separate backing folder outside the production
share folder. Create a non-administrator local account with a new password that
is not set to require a change at next logon. Do not reuse the production SMB
account or grant the new account access to the production share.

Create the `rsl_rehearsal` share on the new folder with SMB encryption required.
Grant the dedicated account Change access at the share and read/write access
through NTFS; retain NAS administrator access. Remove broad share grants such
as Everyone, Users, Authenticated Users, and Guests from both shares before
claiming isolation. Do not weaken the production share or reuse its directory.
The planned rclone prefix is `rsl_rehearsal/siege-backups`, where the first
component is the SMB share name.

From an elevated **Windows PowerShell** session on the NAS, run the reviewed
read-only inspector from a pinned checkout:

```powershell
& .\scripts\portable-rehearsal-nas-inspect.ps1 `
  -ShareName rsl_rehearsal `
  -AccountName rsl_rehearsal `
  -ProductionShareName share `
  -ConfirmReadOnly
```

The script prints one sanitized JSON report. A PASS checks share encryption,
backing-folder separation, local account status, direct rehearsal share and
NTFS rights, and absence of obvious broad or direct production share grants.
It cannot prove effective access through every nested or domain group. A live
SMB connection from the VM with the dedicated account, plus a production-share
denial check, remains required. Do not paste passwords, rclone configuration,
raw ACLs, or backup content into issues or chat.

The VM-to-NAS tailnet policy should allow only TCP 445 to the NAS. Configure a
separate age recipient and keep its private recovery key off the VM and NAS.
Before enabling a schedule, verify a disposable encrypted upload, remote
readback, and restore using the rehearsal account. If setup fails, disable only
the rehearsal share/account and revert only its new tailnet grant; preserve the
production share and backup service.
