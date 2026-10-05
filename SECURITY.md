# Security policy

## Threat model

iCloud MCP is a **single-owner** server. One deployment serves one iCloud account and holds that account's app-specific password in its environment. It is reached from the internet over HTTPS by MCP clients such as Claude, and every client has to be approved by the owner with a separate owner password. An optional helper on the owner's Mac reaches Reminders, Notes and iCloud Drive over a private, certificate-pinned channel.

The contents it handles (email, calendar events, contacts, notes, files) can be written by anyone who can send the owner an email or an invitation. That makes it a prompt-injection target. The project's position is that **a model following injected text is expected, and the configured gates are what must hold.**

- **In scope:**
  - Bypassing OAuth or the owner password, or getting a client authorised without the owner's approval.
  - Getting mail out without approval while `SEND_REQUIRES_APPROVAL=true`.
  - Getting invitations out while `ALLOW_CALENDAR_INVITES=false`.
  - Getting any write through while `READ_ONLY=true`.
  - Deleting mail permanently while `ALLOW_PERMANENT_DELETE=false`.
  - Leaking the app-specific password, the owner password, OAuth tokens or the bridge token.
  - Anything that turns data into code, for example in the AppleScript or EventKit operations or `drive.py`.
  - Reading or writing outside iCloud Drive through the Drive tools, or deleting a note or file permanently through the Mac helper.
  - Reaching the Mac bridge from the public address or through the tunnel, or defeating its certificate pinning.
  - Server-side request forgery, path traversal or remote code execution in the server.
- **Out of scope:**
  - An agent doing something the owner's configuration allows because injected text asked it to. Report it anyway if you think a default is unsafe; that is a design discussion rather than a vulnerability.
  - Attacks that need the owner's app-specific password, owner password or `.env` file.
  - Exposing the server without HTTPS, or putting a login wall in front of it against the documented setup.
  - Weaknesses in iCloud itself, or in Claude or other MCP clients.
  - Denial of service by exhausting the owner's own iCloud quota or disk.

If you are not sure whether a finding is in scope, please report it. A report that is declined costs far less than one that was never made.

## Supported versions

The project is on a rolling release. Only the `main` branch receives security fixes. Please check that your finding reproduces on the latest commit before reporting.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security reports.**

Use one of these private channels:

1. **Preferred:** a [GitHub private security advisory](https://github.com/epinethrone/icloud-mcp/security/advisories/new). It is private and threaded, and a CVE can be requested through it if appropriate.
2. **Fallback:** open an issue titled "Security report, please contact me" with no details, and the maintainer will set up a private channel.

Please include:

- A description of the vulnerability and its impact.
- The commit SHA you reproduced against, and the relevant settings (`SEND_REQUIRES_APPROVAL`, `READ_ONLY`, `ENABLE_*` and so on).
- Step-by-step reproduction. A short script, or the exact tool calls and arguments, is ideal.
- Any logs, with account names, addresses and tokens removed.
- Your assessment of severity and any suggested fix.

Never include a real app-specific password, owner password or token in a report.

## What to expect

- **Acknowledgement** within 5 business days.
- **Triage and a proposed timeline** within 14 days.
- **A fix and coordinated disclosure** usually within 90 days, sooner for severe issues.
- **Credit** in the advisory and release notes, unless you prefer to stay anonymous.

## Outbound channels and the gate on each

An agent that obeys every instruction it reads can only get data out through these paths. Each one is listed with what stops it.

| Channel | Gate |
|---|---|
| Mail (`mail_send_message`, `mail_reply_to_message`, `mail_forward_message`, `mail_unsubscribe_from_list` by mail) | Queued for the owner on `/outbox` (`SEND_REQUIRES_APPROVAL`), `SEND_ALLOWLIST`, `MAX_RECIPIENTS`; re-checked at release |
| iMessage (`imessage_send_message`) | Off by default; queued for the owner; `IMESSAGE_SEND_ALLOWLIST` (empty = nobody), `IMESSAGE_NEVER_SEND` |
| Calendar invitations, updates, cancellations and RSVPs | Off by default (`ALLOW_CALENDAR_INVITES`); when on, `INVITE_ALLOWLIST` and `MAX_ATTENDEES`. No approval page yet: an allowed guest receives the event's text |
| Attachment downloads (`attachment_urls`) | Off by default; a GET only to https prefixes on `ATTACHMENT_URL_ALLOWLIST` (every redirect too), on public addresses only. The agent chooses the query string, so an allowlisted site sees whatever it puts there: list only sites you would let read that. The file then goes out through the mail gate above |
| One-click unsubscribe (`mail_unsubscribe_from_list`) | A POST with a fixed body to the URL in the sender's own header, public HTTPS only; it tells the sender the mail was processed and reveals the server's address, nothing else |
| Shortcuts (`shortcuts_run_shortcut`) | Off by default; a double allowlist of names; the agent's `input` text reaches whatever the Shortcut does, so allow only Shortcuts that send nothing anywhere |
| Apple Maps (`maps_*`) | Query text goes to Apple only |
| Contact cards (`contacts_update_contact`) | Not an exit by itself, but a poisoned address routes a later reply; results and the approval page mark agent-added addresses; `CONTACTS_ALLOW_EMAIL_CHANGES=false` blocks address changes |
| Notes, Reminders lists and iCloud Drive folders shared with other people | Not detected: a write into a shared container reaches its members. Keep shared lists and folders out of agents' hands, or run read-only |

## Hardening notes for operators

By default the server:

- Queues outgoing mail for approval in a browser (`/outbox`), blocks calendar invitations, moves deleted mail to Trash, and caps recipients per message.
- Stores OAuth tokens only as SHA-256 hashes (file mode 600), rotates refresh tokens, caps open client registrations, and locks the approval and outbox pages after 10 wrong passwords in 15 minutes. The bridge port locks out an address after 20 wrong tokens, never the correct token.
- Masks passwords and tokens in every tool error and cuts URLs to their host; refuses IMAP search strings that contain control characters; refuses repeat rules finer than hourly and never expands one it finds in a stranger's invitation; binds to loopback unless told otherwise (the Docker image binds all interfaces inside the container).
- Validates `Host` and `Origin` on the MCP endpoint, labels every piece of iCloud content as untrusted, and keeps account identifiers out of HTTP client logs.
- Serves the admin API (for the menu bar app) only when `ADMIN_PORT` is set, on 127.0.0.1 and its own port, never on the
  public app. Every request needs the token in `DATA_DIR/admin-token` (mode 600); requests from a browser or with a
  non-loopback `Host` are refused. Credentials changed there are stored in `DATA_DIR/overrides.json` (mode 600).
- Serves the Mac bridge only on its own private port, pinned by certificate fingerprint and protected by a bearer token, never on the public address. The server sends the Mac only an operation name and validated arguments from a fixed list, never script text.
- Confines iCloud Drive access to the Drive folder, and never deletes notes or files permanently through the Mac helper: notes go to Recently Deleted and files to the Trash. Reminders have no trash: `reminders_delete_reminder` is final, which is accepted because a reminder is one line and easily recreated; `reminders_move_reminder` changes a reminder's list without deleting it.
- Keeps what the server caches for speed in memory only, never on disk, and loses it on restart: logged-in IMAP and
  calendar connections while the server was used within the last 10 minutes (`IMAP_IDLE_SECONDS`,
  `CALDAV_KEEPALIVE_SECONDS`); the mail folder list for 60 seconds; the structure of up to 5,000 recently searched messages
  (their parts, including attachment names and types, never bodies); the correspondent index for `mail_find_correspondent`
  for 10 minutes; calendar names for 10 minutes; the address book, re-checked against the server at most every 2 minutes.
- On the Mac, the helper keeps a Notes listing in memory for 30 seconds and drops it on any Notes change, and keeps the text
  it extracted from iCloud Drive files for `drive_search_content` on disk in a private cache
  (`~/Library/Application Support/icloud-mac-helper/drive-text-cache.sqlite`, mode 600), so searches stay fast and still
  work after macOS offloads a file.
- Reads `AGENT_NOTES_FILE` fresh on every use, caps it at 8,000 characters, strips invisible characters, and never logs its
  path or contents. The owner's rules come after the security rules in the instructions and can only refine them; a closing
  line repeats that the security rules win. If the file lives inside iCloud Drive, the Drive tools refuse to write, move or
  trash it (keep it outside the Drive anyway).
- Removes text a reader cannot see from HTML mail before converting it (hidden elements, zero-size fonts, comments) and says so
  in `safety_warnings`; screens subjects and display names in search results, contact cards, and note, reminder and file
  listings, not only message bodies; counts every warning by pattern id (never text) for `icloud_check_health`; and runs the
  owner's own classifier on every piece of third-party text when `SAFETY_SCREEN=command:<path>` is set. A hosted model is
  deliberately not offered here: mail must not leave the owner's server because of a safety feature.
- On the approval page, shows which message a reply answers and the warnings that message carried, marks recipients whose
  address an agent added to a contact, refuses to send such a reply until the owner ticks an explicit override, and says how
  many items an agent queued in the last hour.

- Reads iMessage only from the helper user's own Messages database, read-only, and never another macOS user's. Sending an iMessage
  is off by default (`IMESSAGE_ALLOW_SEND`); when on, it waits for the owner on `/outbox` by default whatever the mail setting
  (`IMESSAGE_SEND_REQUIRES_APPROVAL`), reaches only `IMESSAGE_SEND_ALLOWLIST` (empty means nobody), and never reaches a handle in
  `IMESSAGE_NEVER_SEND` or in the Mac's own `imessage-never-send.txt`. That last list exists for an owner who runs an assistant
  on its own Apple ID: a message to it would read as the owner's command. The lists are checked again when the owner approves, against the conversation's participants as they are then. Handles are
  compared in one form on both sides (emails ignore case; phone numbers match on their last nine digits, so `06...` and `+316...`
  are the same). Service senders (short codes: banks, one-time passcodes) are hidden from reading by default, so a message that
  tricks the agent cannot search them for codes.

To tighten a deployment further:

- Set `READ_ONLY=true` if agents only need to read, or `ALLOW_SEND=false` to allow drafts only.
- Use `SEND_ALLOWLIST` to restrict who can receive mail, and keep `ALLOW_CALENDAR_INVITES=false` unless you need it.
- Only enable the Mac areas you use (`ENABLE_REMINDERS`, `ENABLE_NOTES`, `ENABLE_DRIVE`), and keep the bridge port on your LAN or VPN.
- Have your client ask before the send, reply, forward and delete tools run (in Claude, "ask before use").
- Keep `.env` at mode 600. Revoke all clients by deleting `oauth_state.json` and restarting. Rotate the app-specific password at account.apple.com if the server or its backups may have been exposed.
