# Changelog

What changed in each release, newest first. The GitHub release notes carry the full detail and the upgrade steps.
Update the Mac helper before the server whenever its version changes.

## 0.13.0

- **Unsubscribing through a web page, after the owner confirms.** When a sender has no one-click or mailto unsubscribe,
  `mail_unsubscribe_from_list` can now open its unsubscribe page: the one in List-Unsubscribe, or the link in the body whose
  own text or the sentence just before it says unsubscribe (cs, en, de, fr, es, pl, sk). Off unless
  `ALLOW_UNSUBSCRIBE_LINKS=true` (always off when `READ_ONLY`). Two steps: the first call returns a preview (sender,
  website, link text) and a `confirm_token` bound to that message and URL; only a call with it opens the page. The server
  then clicks through up to three confirmation steps, each only on a single obvious form on the same site that asks for
  nothing typed. Every hop must be a public address and the connection goes to the address that was checked (no DNS
  rebinding), a redirect may not re-send a form to another site, the whole visit has a 45-second limit and pages are read
  up to 512 KB after decompression. The result says whether the page reports the address removed, with its text.

## 0.12.1

- **Agents are told which tool is for which job.** The server's instructions now open with a short WHICH TOOL map: one line
  per goal (find mail, write mail, file mail, people, calendar, reminders, notes, files, messages, health...) naming the tool
  for each step. It is built from the tools actually registered, so `TOOLS=essential`, a read-only server or an area that is
  off never points an agent at a tool it does not have. Tests check that every tool is in the map and that the instructions
  name only real tools. Three routing hints the map now covers were shortened in the rules.

## 0.12.0

- **Breaking: tool names.** Every tool is now `<area>_<verb>_<noun>`. The 36 names that had a verb alone are renamed below.
  `TOOLS` still accepts the old names (with a warning). Clients see the new names after reconnecting; update any prompt,
  allow list or deny list that names a tool.

  | Old | New | Old | New |
  |---|---|---|---|
  | `mail_search` | `mail_search_messages` | `reminders_list` | `reminders_list_reminders` |
  | `mail_send` | `mail_send_message` | `reminders_create` | `reminders_create_reminder` |
  | `mail_reply` | `mail_reply_to_message` | `reminders_update` | `reminders_update_reminder` |
  | `mail_forward` | `mail_forward_message` | `reminders_complete` | `reminders_complete_reminder` |
  | `mail_mark` | `mail_mark_messages` | `reminders_move` | `reminders_move_reminder` |
  | `mail_move` | `mail_move_messages` | `reminders_delete` | `reminders_delete_reminder` |
  | `mail_delete` | `mail_delete_messages` | `notes_list` | `notes_list_notes` |
  | `mail_unsubscribe` | `mail_unsubscribe_from_list` | `notes_read` | `notes_read_note` |
  | `calendar_rsvp` | `calendar_respond_to_event` | `notes_create` | `notes_create_note` |
  | `contacts_search` | `contacts_search_contacts` | `notes_update` | `notes_update_note` |
  | `contacts_get` | `contacts_get_contact` | `notes_append` | `notes_append_to_note` |
  | `contacts_create` | `contacts_create_contact` | `notes_move` | `notes_move_note` |
  | `contacts_update` | `contacts_update_contact` | `notes_delete` | `notes_delete_note` |
  | `contacts_delete` | `contacts_delete_contact` | `drive_list` | `drive_list_folder` |
  | `shortcuts_list` | `shortcuts_list_shortcuts` | `drive_search` | `drive_search_files` |
  | `shortcuts_run` | `shortcuts_run_shortcut` | `drive_read` | `drive_read_file` |
  | `drive_move` | `drive_move_item` | `drive_write` | `drive_write_file` |
  | `drive_trash` | `drive_trash_item` | `health_refresh` | `health_refresh_data` |

  Mac helper operation names are unchanged.
- **Apple Health** (`ENABLE_HEALTH`, off by default; Mac helper 0.7.0): daily figures, one day in detail, and freshness from
  exports the owner's iPhone writes with a Shortcut, kept in a private store on the Mac, plus a one-off import of the Health
  app's full export. Overlapping exports never count twice; a day without data is reported as missing, not as zero.
- **ChatGPT can sign in by default:** `OAUTH_ALLOWED_REDIRECT_HOSTS` now also lists `chatgpt.com` and `chat.openai.com`, and
  their origins are accepted, next to Claude and local clients such as Codex. Every sign-in still needs the owner password;
  a deployment that sets the variable itself keeps its own list.
- **Hidden text in mail:** Outlook's styling no longer sets off the warning; it appears only when the hidden part reads like
  instructions to an assistant. `mail_get_message(show_hidden=true)` shows what was hidden, marked as untrusted.

## 0.11.0

- **Injection hardening** (#71): an invitation allowlist and attendee cap (`INVITE_ALLOWLIST`, `MAX_ATTENDEES`); text hidden
  in HTML mail removed with a warning; subjects, display names and listings screened, not only bodies; an optional
  classifier of your own (`SAFETY_SCREEN=command:<path>`); agent-added contact addresses journaled and marked
  (`CONTACTS_ALLOW_EMAIL_CHANGES`); `MAIL_MAX_AGE_DAYS`; the agent cannot rewrite its own notes file in iCloud Drive.
- **iCloud MCP Control**, a menu bar app for the Mac (`menubar/`): status, health, pause, restart and stop, owner passcode,
  iCloud app-specific password, and the connected apps (which ones, when each was last used, sign out one or all).
- Admin API for it, off unless `ADMIN_PORT` is set: loopback only, on its own port, token in `DATA_DIR/admin-token`.
- Pause: while `DATA_DIR/paused` exists every tool except the diagnostics answers that the server is paused.
- A passcode or app-specific password changed through the admin API is stored in `DATA_DIR/overrides.json` and takes
  precedence over the environment.
- The approval page has a "Discard all" button per queue. It clears only the messages shown on the page, so one queued later
  stays. An exact repeat of a waiting message is queued once, and a full queue tells the agent not to retry.
- Safety warnings on a stranger's text in more places: unsubscribe results, the sample in a bulk-action preview, and the
  subjects shown before deleting a mail folder.
- A calendar event with an unreadable date (a malformed invitation) is left out of listings instead of failing them. The booking
  extractor keeps the other events of an attachment when one is broken.
- Fuzz tests for the parsers that read other people's data: contact cards, invitations and repeat rules, booking data,
  List-Unsubscribe headers and the Messages decoder.

## 0.10.0

- Apple Maps (`ENABLE_MAPS`, Mac helper 0.6.0): `maps_get_travel_time` (walking, cycling, driving, public transport; for a
  departure or arrival time) and `maps_search_places`. With Maps on, calendar travel time uses a measured value first, else a
  labelled Apple Maps estimate.
- iMessage (`ENABLE_IMESSAGE`): `imessage_list_chats`, `imessage_read_chat`, `imessage_search_messages` over your own Messages
  history (the whole history unless `IMESSAGE_MAX_AGE_DAYS` limits it; `IMESSAGE_HIDDEN_CHATS` hides chats; service senders
  such as banks and one-time codes are hidden unless `IMESSAGE_HIDE_SHORT_CODES=false`; handles match however they are
  written), and a "Catch up on my messages" prompt.
- iMessage sending (`imessage_send_message`), off by default: owner approval on `/outbox` by default (independent of mail),
  an allowlist that starts empty, and a never-send list on the server and on the Mac itself. iMessage only; each send confirmed.

## 0.9.0

- Saved drafts: `mail_send_draft` sends a draft exactly as saved (same checks and approval as `mail_send`; the draft then goes to
  Trash), `mail_update_draft` changes one without losing it.
- Mail folders: `mail_update_folder` renames, `mail_delete_folder` deletes; a folder's mail always goes to Trash first, after a
  confirm step. The mailbox's own folders are refused.
- Calendars: `calendar_create_calendar`, `calendar_update_calendar` (rename), `calendar_delete_calendar` (never the default
  calendar; one with events only after a preview and its token).
- Contact groups, in the format the Contacts app uses: list, read, create, rename, add and remove members, delete (only the
  group, never its members). A contact shows the groups it is in. Fixed: a group card was listed as if it were a person.
- Reminders (Mac helper 0.5.0): repeating reminders, extra alerts, listing completed reminders with when they were done, and
  creating, renaming and deleting lists (deleting a list with reminders needs a preview and its token: Reminders has no trash).
- A server call that needs a newer Mac helper says so and asks for the update, instead of failing with "unknown operation".

## 0.8.0

- **Calendar reads cannot be stalled by a stranger's invitation:** a series that would repeat more than 48 times a day
  (every second or minute, or an hourly or daily rule multiplied up with BYMINUTE / BYSECOND lists) is no longer expanded.
  The calendar library expanded such rules before our guard saw them, so one invitation could hang a read until the tool
  timed out. Only the series' dated exceptions come through; the result counts the skipped series in `series_not_expanded`
  and never repeats their text. New and updated events refuse such rules too.
- **Stale message numbers are always caught on destructive mail actions:** `uidvalidity` is now required on `mail_delete`,
  `mail_move` and `mail_mark`. Breaking: a call without it is refused, and the agent retries with the value every search,
  thread and changes result carries.
- **Forwarded state is back:** mail results say `forwarded` again (lost in 0.6.0 when results became leaner).
- The Mac's hostname is no longer shown by `icloud_get_helper_status`.
- Mac helper 0.4.2: note backups older than 30 days move to the Trash once a day (never deleted outright).
- The sign-in and outbox pages follow the system light or dark mode.
- A failing integration test now fails the build; `CHANGELOG.md` and `CONTRIBUTING.md` added.

## 0.7.0

- Every tool name follows `area_verb_noun`; eleven tools renamed (`mail_changes` became `mail_list_changes`, `icloud_now`
  became `icloud_get_time`, and so on). `TOOLS` still accepts the old names.
- `mail_send` and `mail_reply` explain attachments; the instructions say which areas need the Mac helper.
- Mac helper 0.4.1.

## 0.6.0

- Calendar about four times faster on real iCloud (warm connections, all calendars read at once); mail reads fetch text
  only; sign-in at start (`WARMUP_ON_START`).
- Instructions built from the enabled tools; your own rules with `AGENT_NOTES_FILE`; `icloud_get_time` (then `icloud_now`),
  replies owed, clash and duplicate checks, invitations still to answer.
- One safe retry on a dropped connection, never after a write; timeouts name the slow step. Mac helper 0.4.0.

## 0.5.0

- Security hardening from a full audit (read-only mode, IMAP search text, invitations on recurring events, no duplicate
  events on retry, contact card fields, scrubbed errors). `reminders_move`.

## 0.4.2

- Privacy release.

## 0.4.1

- `calendar_move_event`; a privacy check on every pull request.

## 0.4.0

- Reused mail connections, what-changed tokens, newsletter radar, safe bulk clean-up with undo, safe unsubscribe, exact
  bookings, editable notes, search inside Drive files, birthdays, Shortcuts.

## 0.3.0

- One-click Claude Desktop bundle, free time, RSVP, single dates of a series, invitation delivery reports, all-folder
  search, the health check.

## 0.2.0

- Local mode for Claude Desktop and Claude Code, postal addresses, reading many messages at once, PyPI.
