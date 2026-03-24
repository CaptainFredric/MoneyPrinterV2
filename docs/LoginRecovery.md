# Login Recovery

The X session itself can still be invalidated upstream, so this project cannot guarantee permanent authentication forever. What it can do now is keep fresh Firefox session backups and restore the last known-good login state in one command.

## Commands

- `bash scripts/phone_post.sh session-all`
  - Confirms both profiles are authenticated.
  - Automatically creates or refreshes a session backup when an account is ready.

- `bash scripts/phone_post.sh session-backups all`
  - Lists the saved Firefox session restore points.

- `bash scripts/phone_post.sh session-restore EyeCatcher`
  - Stops automation and restores the latest saved Firefox session snapshot for one account.

- `bash scripts/phone_post.sh login-auto EyeCatcher`
  - Stops automation.
  - Restores the latest saved session if one exists.
  - Opens the exact Firefox profile on `x.com/home`.
  - Runs a readiness check and refreshes the backup if the session is valid.

## Recommended Flow

1. Log in once in the normal Firefox profile window.
2. Run `bash scripts/phone_post.sh session-all`.
3. Confirm each account shows `Ready   : YES` and `Backup  : created` or `Backup  : skipped:up-to-date`.
4. If a session later breaks, try `bash scripts/phone_post.sh login-auto <account>` before doing a full manual relogin.

## Notes

- Backups are stored in `.mp/profile_backups/`.
- Restore keeps a safety copy of the current profile beside the original profile directory.
- Restore will refuse to overwrite a Firefox profile that is currently open.
- Account/browser links are stored in `.mp/twitter.json` via `browser_binary`, so one account can stay on Firefox Developer Edition while another stays on normal Firefox.