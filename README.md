<div align="center">

<!-- mcp-name: io.github.epinethrone/icloud-mcp -->

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/epinethrone/icloud-mcp/main/assets/readme/hero-dark.svg">
  <img src="https://raw.githubusercontent.com/epinethrone/icloud-mcp/main/assets/readme/hero-light.svg" alt="iCloud MCP. Your iCloud, in your AI. Mail, Calendar, Contacts, Reminders, Notes, iCloud Drive, Maps, Messages and Health, for Claude, Codex, ChatGPT or any MCP client." width="100%">
</picture>

<br>

[![PyPI](https://img.shields.io/pypi/v/icloud-mcp-server?style=flat-square&label=PyPI&color=0071e3)](https://pypi.org/project/icloud-mcp-server/)
[![Tests](https://img.shields.io/github/actions/workflow/status/epinethrone/icloud-mcp/tests.yml?style=flat-square&label=tests)](https://github.com/epinethrone/icloud-mcp/actions/workflows/tests.yml)
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-listed-6e56cf?style=flat-square)](https://registry.modelcontextprotocol.io/v0/servers?search=io.github.epinethrone/icloud-mcp)
[![License: MIT](https://img.shields.io/badge/license-MIT-86868b?style=flat-square)](https://github.com/epinethrone/icloud-mcp/blob/main/LICENSE)
[![icloud-mcp MCP server on Glama](https://glama.ai/mcp/servers/epinethrone/icloud-mcp/badges/score.svg)](https://glama.ai/mcp/servers/epinethrone/icloud-mcp)

**[Run it on this computer ›](#run-it-locally)** &nbsp;&nbsp; **[Host it for every device ›](#quick-start)** &nbsp;&nbsp; **[Claude Desktop in one click ›](#claude-desktop-in-one-click)**

</div>

<br>

<p align="center">
Ask your AI about your week, your inbox or the file you saved last spring, and it just knows.<br>
iCloud MCP connects Claude, Codex, ChatGPT or any other MCP client to the Apple account you already live in.<br>
It runs on your own machine, keeps your password there, and asks before anything leaves.
</p>

<br>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/epinethrone/icloud-mcp/main/assets/readme/apps-dark.svg">
  <img src="https://raw.githubusercontent.com/epinethrone/icloud-mcp/main/assets/readme/apps-light.svg" alt="Ten apps, one connector: Mail (25 tools), Calendar (12), Contacts (11), Reminders (10), Notes (9), iCloud Drive (10), Maps (2), Messages (4), Health (4) and Shortcuts (2), plus a health check." width="100%">
</picture>

<br><br>

<div align="center">

## Just ask.

*"Find the email from my landlord about the heating and draft a polite reply."*

*"When am I free for an hour next week? Book lunch with Anna then."*

*"Move Thursday's dentist appointment to Friday, same time. Only that one."*

*"Remind me to renew my passport on the first of next month."*

*"Find my tax return PDF in iCloud Drive and tell me what I paid last year."*

*"How long does it take me to cycle to the station, if I need to be there at nine?"*

*"Catch me up on my messages from this weekend."*

*"How did I sleep this week, compared with last month?"*

<br>

## Private by design.

**Your password stays home.**<br>
Apple has no sign-in for these services other than an app-specific password.<br>
It lives only on your machine or in your Mac's Keychain. Your AI never sees it.

**Nothing leaves without you.**<br>
Mail your AI writes waits for your approval, or lands in your Drafts.<br>
Invitations to other people are off. Deletes go to the Trash.

**Built for mail from strangers.**<br>
Everything your AI reads is marked as someone else's words, not instructions.<br>
Hidden characters are stripped, and phishing tricks are called out.

**Tested where it counts.**<br>
Over 600 offline tests on every change, including fuzzing of every parser that reads other people's data, integration tests against real mail and calendar servers,<br>
and hands-on runs against a live iCloud account for the quirks only Apple's servers have.

<br>

## New in 0.12.

</div>

<table>
<tr>
<td width="33%" valign="top"><b>Apple Health</b><br>Sleep with its stages, steps, activity and heart rate per day, from your iPhone, plus your whole history from the Health app's export.</td>
<td width="33%" valign="top"><b>Any MCP client</b><br>Claude, Codex, ChatGPT, Cursor and others. ChatGPT can sign in to a hosted server without extra settings.</td>
<td width="33%" valign="top"><b>Clearer tool names</b><br>Every tool is <code>area_verb_noun</code>, such as <code>mail_send_message</code>, so any agent picks the right one. Old names still work in <code>TOOLS</code>.</td>
</tr>
<tr>
<td width="33%" valign="top"><b>Fewer false alarms</b><br>Mail from Outlook no longer warns about hidden text. The warning stays for hidden text that addresses an AI, and you can see what was hidden.</td>
<td width="33%" valign="top"><b>Apple Maps</b><br>Travel time by bike, car, foot or transit for a departure or arrival time, and place search. Calendar travel time can use it.</td>
<td width="33%" valign="top"><b>Messages</b><br>Read and search your iMessage and SMS history. Sending is off by default, and then waits for your approval and an allowlist.</td>
</tr>
<tr>
<td width="33%" valign="top"><b>A menu bar app</b><br>See health at a glance, pause the server, restart it, change credentials and sign out any connected app.</td>
<td width="33%" valign="top"><b>Pause</b><br>One switch and every tool answers that the server is paused, without disconnecting anyone.</td>
<td width="33%" valign="top"><b>Safer</b><br>Invitation allowlists, screened subjects and names, your own classifier, and fuzzing of every parser that reads other people's data.</td>
</tr>
</table>

<p align="center"><sub>In 0.9 to 0.11: Maps, Messages, the menu bar app and pause, drafts and folders, calendars and contact groups, and richer Reminders. In 0.6 to 0.8: about three times faster calendars, lighter mail, an instant first call, your own rules file, replies owed, clash checks and a hardened recurrence guard. Since 0.4: bulk clean-up with undo, safe unsubscribe, exact bookings, note editing, search inside Drive files, birthdays and Shortcuts. In 0.3: the one-click Claude Desktop extension, local mode, Keychain storage, free time, RSVP and scam warnings.</sub></p>

<br>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/epinethrone/icloud-mcp/main/assets/readme/how-dark.svg">
  <img src="https://raw.githubusercontent.com/epinethrone/icloud-mcp/main/assets/readme/how-light.svg" alt="How it works: your AI (Claude, Codex, ChatGPT or any MCP client) connects to icloud-mcp over OAuth and MCP, or starts it on your computer. icloud-mcp talks to iCloud Mail, Calendar and Contacts over IMAP, SMTP, CalDAV and CardDAV. An optional Mac helper connects out to the server over pinned TLS and handles Reminders, Notes, iCloud Drive, Maps, Messages and Health on your Mac. An optional menu bar app controls the server through a private admin port on the same Mac." width="100%">
</picture>

<p align="center">Mail, Calendar and Contacts use Apple's standard protocols.<br>Reminders, Notes, iCloud Drive, Maps, Messages and Health go through an optional helper on your Mac.<br>It connects out, so your Mac never opens a port.</p>

<br>

<div align="center">

## Choose how you run it.

</div>

| | **Any client on this computer** | **Claude Desktop, one click** | **Your own server** |
|---|---|---|---|
| **Setup** | One command or a few lines of config | Double-click an extension | Docker and an HTTPS address |
| **Works in** | Claude Code, Codex, Claude Desktop, Cursor, VS Code and any other MCP client on this computer | Claude Desktop on this computer | Claude and ChatGPT on the web, desktop and phone, Codex, and any client that supports remote MCP servers |
| **Outgoing mail** | Saved to Drafts for you to send | Saved to Drafts for you to send | Waits for your approval in a browser |
| **Guide** | [Set up ›](#manual-setup-any-mcp-client) | [Install ›](#claude-desktop-in-one-click) | [Quick start ›](#quick-start) |

Tested with Claude (web, desktop, phone, Claude Code and the Desktop extension) and with Codex CLI running the server locally. Any client that speaks MCP works the same way: locally it starts the server itself, and hosted it signs in with OAuth.

<br>

## Run it locally

The server runs on your own computer. Your client starts it when it needs it and talks to it directly, so there is no public address, tunnel, Docker or OAuth. Apps on the web and on your phone can't reach it; for that, [host it](#quick-start).

### Claude Desktop in one click

1. Download **`icloud-mcp-<version>.mcpb`** from the [latest release](https://github.com/epinethrone/icloud-mcp/releases/latest).
2. Double-click it, or drag it onto Claude Desktop → Settings → Extensions.
3. Fill in your Apple Account email, an [app-specific password](https://account.apple.com) (Claude Desktop keeps it in your system keychain), your name and your time zone, such as `Europe/Amsterdam`.

It starts approval-first: mail is saved to Drafts for you to send, and invitations to other people are off. This covers Mail, Calendar and Contacts. Reminders, Notes and iCloud Drive need the [Mac helper](#reminders-notes-icloud-drive-maps-messages-and-health-through-your-mac) and the manual setup below.

### Manual setup (any MCP client)

**1. Put your settings in a file only you can read.**

```bash
mkdir -p ~/.icloud-mcp && chmod 700 ~/.icloud-mcp
cat > ~/.icloud-mcp/icloud.env <<'END'
ICLOUD_USERNAME=you@icloud.com
ICLOUD_DISPLAY_NAME=Your Name
DEFAULT_TIMEZONE=Europe/Berlin
END
chmod 600 ~/.icloud-mcp/icloud.env
```

**2. Keep the password in your Keychain** (on a Mac). It asks for the app-specific password and never shows it on the command line:

```bash
uvx icloud-mcp-server --store-password
```

Not on a Mac? Add `ICLOUD_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx` to the file instead. Any setting from [Configuration](#configuration) can go in it; `MCP_PUBLIC_URL` and `MCP_OWNER_PASSWORD` are not needed.

**3. Add it to your client** (needs [uv](https://docs.astral.sh/uv/)).

Claude Code:

```bash
claude mcp add icloud -- uvx icloud-mcp-server --local --env-file ~/.icloud-mcp/icloud.env
```

Codex:

```bash
codex mcp add icloud -- uvx icloud-mcp-server --local --env-file ~/.icloud-mcp/icloud.env
```

Claude Desktop: Settings → Developer → Edit Config, add this to `claude_desktop_config.json`, and restart Claude. Cursor (`~/.cursor/mcp.json`), VS Code, Windsurf, Gemini CLI and most other clients take the same `command` and `args` in their own MCP settings:

```json
{
  "mcpServers": {
    "icloud": {
      "command": "uvx",
      "args": ["icloud-mcp-server", "--local", "--env-file", "/Users/YOU/.icloud-mcp/icloud.env"]
    }
  }
}
```

If your client can't find `uvx`, use its full path (`which uvx`). Without uv, `pip install icloud-mcp-server` and use `icloud-mcp` as the command.

> [!TIP]
> **Fewer tools, better choices.** Clients pick the right tool more reliably from a short list. Add `TOOLS=essential` for a core of 19: search, read and reply to mail; list events, find free time and create or update events; find contacts; the main Reminders, Notes and Drive tools; and the health check. Add exact names to it as needed, for example `TOOLS=essential,mail_move_messages`, or pick whole areas: `TOOLS=mail`, `TOOLS=mail,calendar` (also `contacts`, `reminders`, `notes`, `drive`, `maps`, `imessage`, `health`; the health check always stays).

**How sending works locally.** There is no approval page, so with the default `SEND_REQUIRES_APPROVAL=true` every message your AI sends is saved to your **Drafts** folder instead, and the result says so. You review it in Mail and press Send yourself. Set `SEND_REQUIRES_APPROVAL=false` to let it send directly; your client's own approval prompt is then the only check.

**Reminders, Notes and iCloud Drive locally.** Add `ENABLE_REMINDERS=true` (and/or `ENABLE_NOTES`, `ENABLE_DRIVE`) and a `BRIDGE_TOKEN` to the env file, start your client once, then [install the Mac helper](#reminders-notes-icloud-drive-maps-messages-and-health-through-your-mac) with server `https://127.0.0.1:8001` and the fingerprint from `~/.icloud-mcp/bridge_fingerprint.txt`. In local mode the bridge only listens on `127.0.0.1`. If two clients start the server at once, only the first gets the Mac tools; Mail, Calendar and Contacts work in both.

## Quick start

Host it once, and your AI reaches your iCloud from the web, desktop apps and your phone: Claude, ChatGPT, Codex, or any client that supports remote MCP servers.

**You need**

- Docker with the compose plugin (or Python 3.11+).
- An Apple Account with two-factor authentication and an **app-specific password** (account.apple.com → Sign-In and Security → App-Specific Passwords).
- A public **HTTPS** address. Web and phone apps connect from their provider's cloud, so a LAN or VPN address won't work. A [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) is the simplest: outbound only, no port forwarding.
- A client that can add a remote MCP server with OAuth: Claude (custom connectors), ChatGPT (developer mode) or Codex, for example.

> [!WARNING]
> Don't put Cloudflare Access or any other login wall in front of the server. Your client's servers can't pass an interactive login, and the server has its own OAuth.

**1. Configure**

```bash
git clone https://github.com/epinethrone/icloud-mcp.git && cd icloud-mcp
cp .env.example .env     # fill in ICLOUD_USERNAME, ICLOUD_APP_PASSWORD, ICLOUD_DISPLAY_NAME,
                         # MCP_PUBLIC_URL (your https address, no trailing slash), MCP_OWNER_PASSWORD
chmod 600 .env
```

`MCP_OWNER_PASSWORD` is a new random password (12+ characters) that **you** type when approving a client and on the outbox page. It is not your Apple password. `./configure.sh` asks for the secrets with silent prompts if you prefer.

**2. Check your credentials against the real iCloud before exposing anything**

```bash
docker build -t icloud-mcp:local .
docker run --rm --env-file .env icloud-mcp:local python -m icloud_mcp.selftest                              # logs in to IMAP, SMTP, CalDAV, CardDAV; sends nothing
docker run --rm --env-file .env icloud-mcp:local python -m icloud_mcp.selftest --probe-sent you@example.com   # sends ONE test mail
```

If a login fails, the login name is the usual cause: set `IMAP_USERNAME`, `SMTP_USERNAME`, `CALDAV_USERNAME` or `CARDDAV_USERNAME` separately.

**3. Run it**

```bash
docker compose up -d --build     # listens on 127.0.0.1:8000 only
```

For the bundled Cloudflare Tunnel: create a tunnel, point its public hostname (the host in `MCP_PUBLIC_URL`) at `http://icloud-mcp:8000`, put the token in `.tunnel.env` as `TUNNEL_TOKEN=...`, and start with `docker compose --profile tunnel up -d --build`. Any TLS front (Caddy, nginx) works too, as long as it keeps the `Host` header. `MCP_PUBLIC_URL` must match the public address exactly.

**4. Connect your client**

Add `https://<your-host>/mcp` as a remote MCP server. Your server then shows an approval page: enter the owner password.

- **Claude:** Settings → Connectors → Add custom connector.
- **ChatGPT:** Settings → Apps & Connectors → Advanced → turn on developer mode, then create a connector with OAuth (needs a plan with developer mode).
- **Codex:** `codex mcp add icloud --url https://<your-host>/mcp`, then `codex mcp login icloud`.
- **Others:** any client that supports remote (streamable HTTP) MCP servers with OAuth. A web client whose sign-in returns to another host needs that host in `OAUTH_ALLOWED_REDIRECT_HOSTS`.

Reconnect whenever you change tools or settings, because clients cache tool definitions. The menu bar app signs out one connected client or all of them; without it, delete `oauth_state.json` in the data volume and restart to revoke every client.

**5. Check it works**

In a new chat, ask *"Check my iCloud connection."* Your AI runs `icloud_check_health`, which signs in to each service and reports how long each took. Then try *"What's on my calendar this week?"* and ask it to email you. With the default settings nothing is sent: the message waits at `https://<your-host>/outbox` until you approve it. If anything fails, see [Troubleshooting](#troubleshooting).

### Approving outgoing mail

With the default `SEND_REQUIRES_APPROVAL=true`, `mail_send_message`, `mail_reply_to_message` and `mail_forward_message` return `queued_for_owner_approval` and nothing leaves. Open `https://<your-host>/outbox` (bookmark it, and only type the password there, never on a link an agent gives you), enter the owner password, review the exact recipients and text, then approve or discard. Queued messages expire after `OUTBOX_TTL_SECONDS` (24 hours by default) and are released at most once.

## Security

A connector that can read your mail and act for you is a prompt-injection target: a hostile email or invitation can contain text that tries to steer the agent. The server labels all such content as untrusted and tells agents to treat it as data, but **that is a request to a language model, not a guarantee**. What actually protects you is configuration:

| Risk | Default | Setting |
|---|---|---|
| Agent sends mail on injected instructions | Sending only **queues** the message for your approval at `/outbox` (locally: saves it to Drafts) | `SEND_REQUIRES_APPROVAL=true` |
| Agent emails invitations to strangers | Attendee changes are **blocked** | `ALLOW_CALENDAR_INVITES=false` |
| Agent invites the wrong people once invites are on | Any address, at most 10 guests | `INVITE_ALLOWLIST`, `MAX_ATTENDEES` |
| Agent puts a stranger's address on a real contact so a later reply goes there | Allowed, but the address is marked as agent-added in results and on the approval page | `CONTACTS_ALLOW_EMAIL_CHANGES=false` to block it |
| Agent reads years of archive for a task about today | Whole mailbox | `MAIL_MAX_AGE_DAYS` |
| Hostile text is not spotted | Built-in patterns (English and Dutch) plus removal of text hidden in HTML mail (warned about when it reads like instructions; `show_hidden` shows it) | `SAFETY_SCREEN=command:<path>` adds your own classifier |
| Agent mails arbitrary addresses | Any address, at most 25 per message | `SEND_ALLOWLIST`, `MAX_RECIPIENTS` |
| Agent destroys mail | Delete moves mail to Trash; deleting from Trash is off | `ALLOW_PERMANENT_DELETE=false` |
| Agent destroys notes or files | Notes go to Recently Deleted, Drive files to the Trash. Reminders have no trash, so a deleted reminder is gone (it is one line, easily recreated); moving one between lists never deletes it | always |
| Agent texts people on injected instructions | iMessage sending is **off**; when on, every message waits for your approval, only people on an allowlist (empty = nobody) can receive one, and a never-send list blocks handles outright | `IMESSAGE_ALLOW_SEND=false`, `IMESSAGE_SEND_ALLOWLIST`, `IMESSAGE_NEVER_SEND` |
| Agent reads your codes from Messages | Messages is **off**; when on, bank and one-time-code senders are hidden, and you can hide or allow chats | `ENABLE_IMESSAGE=false`, `IMESSAGE_HIDE_SHORT_CODES=true`, `IMESSAGE_HIDDEN_CHATS` |
| Agent changes anything at all | Everything writable | `READ_ONLY=true` for a read-only connector |

Most clients can also ask you before a tool runs (in Claude, "ask before use" per tool): use it for the send, reply, forward and delete tools. Anyone who obtains the app-specific password has **full access to mail, calendar and contacts** (Apple offers no narrower scope), so protect the server and its `.env` accordingly.

<details>
<summary><b>The full security model</b></summary>

- iCloud credentials exist only in the server environment or the macOS Keychain. Clients hold short-lived bearer tokens for *this* server.
- Clients may register dynamically, but nothing is authorised without the owner password. Redirect hosts are restricted. Tokens are stored as SHA-256 hashes (file mode 600). The approval and outbox pages lock after 10 wrong passwords in 15 minutes (server-wide; existing tokens keep working).
- Every refused sign-in renewal is logged with its reason (already rotated, expired, wrong client, not recognised), never with token values, and unauthenticated callers cannot flood the log: `docker compose logs icloud-mcp | grep 'oauth:'`.
- Text from mail, events, notes and files is stripped of invisible steering characters (Unicode tag characters, zero-width spaces, direction overrides; the marks Kurdish, Persian and Arabic text need are kept), and results carry `safety_warnings` when the text addresses an AI, asks for passwords or codes, or says bank details changed (English and Dutch).
- Every tool error is scrubbed before it reaches the agent: passwords and tokens masked, URLs cut to their host (iCloud DAV paths carry the account id), invisible characters removed. Health-check and unexpected errors, which may quote a server response, also drop account addresses and long numbers.
- The MCP endpoint validates `Host` and `Origin`. Tool results carry an untrusted-content notice. HTTP-client request logging is disabled so account identifiers do not reach the logs.
- The Mac bridge runs on its own private TLS port with a self-signed certificate the helper pins by fingerprint, plus a bearer token. It is never served on the public address or through the tunnel. The server sends only an operation name and validated arguments from a fixed list, never script text.
- The admin API for the menu bar app is off unless `ADMIN_PORT` is set. It listens on 127.0.0.1 only, on its own port (never the public one or the tunnel), and every request needs the token in `DATA_DIR/admin-token` (mode 600); browser requests and non-loopback `Host` headers are refused.
- Single-owner by design: one deployment serves one iCloud account. It is not multi-tenant, and storing other people's app-specific passwords is deliberately out of scope.

</details>

## Tools

**92 tools.** 50 for Mail, Calendar, Contacts, the clock and the health check, 40 more with the optional Mac helper, and 2 for Shortcuts you allowlist. Open a section for the details.

Every name is `area_verb_noun` (`mail_send_message`, `calendar_create_event`). Eleven tools were renamed in 0.7.0 and 36 in 0.12.0 (a verb with no noun, such as `mail_send`) to follow that pattern; `TOOLS` still accepts the old names and logs the new one.

<details>
<summary><b>Mail</b> &nbsp;·&nbsp; 25 tools</summary>

| Kind | Tools |
|---|---|
| Read | `mail_list_folders`, `mail_search_messages`, `mail_list_changes`, `mail_find_correspondent`, `mail_get_message`, `mail_get_messages` (up to 25 in one call), `mail_get_thread`, `mail_get_attachment`, `mail_extract_bookings` |
| Read | `mail_list_senders` (who fills a folder, busiest first, with bulk and unsubscribe info), `mail_list_awaiting_reply` (mail you sent that has had no answer) |
| Write | `mail_send_message`, `mail_reply_to_message` (including reply-all), `mail_forward_message`, `mail_mark_messages`, `mail_move_messages`, `mail_delete_messages` (to Trash), `mail_send_draft` (a saved draft, as it is), `mail_update_draft`, `mail_create_folder`, `mail_update_folder` (rename), `mail_delete_folder` (its mail goes to Trash first), `mail_run_bulk_action`, `mail_undo_bulk_action`, `mail_unsubscribe_from_list` |

- Replies keep the `Re:` subject, `In-Reply-To` and `References`, the right recipients and the quoted original in plain text and HTML. Sent mail is copied to Sent and the original is flagged Answered (forwards get `$Forwarded`). `draft=true` saves to Drafts instead of sending.
- `mail_get_messages` reads a batch (a day's unread mail, a whole thread) in one IMAP round trip, about 7 times faster than one at a time.
- **Search every folder at once.** `mail_search_messages` with `all_folders=true` looks in Archive, Sent, Junk and your own folders too, newest first, because mail rules and replies file messages away from the inbox.
- **Newsletters are told apart from people.** Search results mark `bulk` mail (a List-Unsubscribe or List-Id header, bulk precedence, automated or no-reply senders) and say how it can be unsubscribed from; `mail_list_senders` groups a folder by sender.
- **Clean up in bulk, safely.** `mail_run_bulk_action` (move, archive, trash, mark read) always previews first: the count, a sample and a confirm token that stands for exactly those messages. Running needs that token, so mail that arrived since is never touched. Every run is logged by Message-ID and `mail_undo_bulk_action` reverses it for 30 days. It needs at least one filter and never deletes permanently.
- **Unsubscribe without following links.** `mail_unsubscribe_from_list` uses only the List-Unsubscribe header: the standard one-click request (RFC 8058, HTTPS to public addresses only) or an unsubscribe email through the normal send path, so approval rules apply. Links in the body are never followed, unsubscribe web pages are only handed to you, and mail in Junk is refused.
- **Bookings come out exact.** `mail_extract_bookings` reads the schema.org booking data airlines, hotels, rail and ticket shops embed (flights, stays, trains, buses, rental cars, restaurants, events) and `.ics` invitations, and returns each with a ready `calendar_create_event` block. Nothing is guessed from the wording; a message without that data says so.
- **Only what changed.** `mail_list_changes` returns a token; passed back next time, it lists just the new messages and those whose read, flagged or answered state changed, using IMAP CONDSTORE instead of re-reading the folder. If iCloud renumbered the folder, it says to start over rather than guess.
- **Who is waiting on whom.** `mail_list_awaiting_reply` lists mail you sent to a person that has had no reply and no later message from them, in any folder, longest waiting first; `mail_search_messages` takes `people_only` (no newsletters), `unanswered_only` and `since_hours`.
- **Layout checked, never rewritten.** Send and draft results carry `layout_warnings` when a plain-text body has HTML tags, Windows line endings or one long paragraph.
- **Stale ids are refused.** Every message comes with its folder's `uidvalidity`; tools that act on a uid accept it back and refuse if iCloud has renumbered the folder since, instead of touching a different message.
- Reading a message does not mark it read. Bcc recipients receive the mail, but the header is stripped on the wire.
- Recipients accept `a@b.com`, `Name <a@b.com>` or `mailto:a@b.com`. Anything else is rejected with a clear error and never silently dropped.

</details>

<details>
<summary><b>Calendar</b> &nbsp;·&nbsp; 12 tools</summary>

`calendar_list_calendars`, `calendar_list_events`, `calendar_find_free_time`, `calendar_get_event`, `calendar_create_event`, `calendar_update_event`, `calendar_move_event`, `calendar_delete_event`, `calendar_respond_to_event`, `calendar_create_calendar`, `calendar_update_calendar` (rename), `calendar_delete_calendar`

- Multiple calendars, recurring events expanded when listing, all-day events, alerts, links, notes and attendees. Editing or deleting a recurring event changes the whole series, or just one date when you pass `occurrence_start` (the rest of the series is left alone).
- **Finding free time is one call.** `calendar_find_free_time` returns openings of a given length within your hours and chosen weekdays. Travel time counts as busy; events marked free, cancelled events and invitations you declined do not; all-day events are listed separately instead of guessed about.
- **Know whether an invitation went out.** After inviting people, the result reports what iCloud recorded for each guest (sent, delivered, or refused, for example a mistyped address), so an agent never claims someone was invited when they were not.
- **Manage calendars.** Create, rename and delete calendars. The default calendar is never deleted, and one that holds events is only deleted after a preview with a confirm token; iCloud.com can restore a deleted calendar for about 30 days. (iCloud ignores a color set this way, so there is no color option.)
- **Move between calendars.** `calendar_move_event` moves an event (a whole series, if it repeats) to another calendar with a WebDAV MOVE, so nothing is recreated and guests get no new invitation. Servers without MOVE get a copy first and the original deleted only after.
- **Clashes and duplicates are reported.** `calendar_create_event` returns the events a new one overlaps (`conflicts`, travel time counted on both sides, free, cancelled and declined events ignored) and a `possible_duplicate` with the same title and time; `on_conflict` / `on_duplicate` = `refuse` creates nothing instead.
- **Invitations waiting for you.** `calendar_list_events(needs_reply=true)` lists invitations you have not answered (to any of your addresses: add aliases to `OWNER_ADDRESSES`); `starting_within_minutes` looks from now.
- **Answer invitations.** `calendar_respond_to_event` accepts, declines or marks tentative, for the whole series or one date; iCloud emails the organizer itself.
- **Safe to retry.** `calendar_create_event` and `contacts_create_contact` take an optional `request_id`: if a call times out and is retried with the same one, the first attempt is found instead of creating a duplicate.
- **Apple travel time and map locations.** Events can carry Apple's travel time (by bike, on foot, by car or public transport) and a structured destination, which is what makes Apple draw the map card.
- **Adding a guest leaves everyone else alone.** `add_attendees` and `remove_attendees` change one person; a full guest list merges instead of replacing, so existing guests keep their RSVP and aren't sent the invitation again. Invitations are emailed by iCloud itself and are off unless you allow them.

</details>

<details>
<summary><b>Contacts</b> &nbsp;·&nbsp; 11 tools</summary>

`contacts_search_contacts`, `contacts_get_contact`, `contacts_list_birthdays`, `contacts_create_contact`, `contacts_update_contact`, `contacts_delete_contact`, `contacts_list_groups`, `contacts_get_group`, `contacts_create_group`, `contacts_update_group` (rename, add or remove members), `contacts_delete_group` (the group only, never its members)

- Contacts are fetched whole, cached and searched locally by name, nickname, company, email or phone, ignoring accents. A contact with no email comes back with `has_email: false`, so an agent asks instead of guessing.
- **Misspelled names are handled.** `contacts_search_contacts` suggests similar-sounding names when nothing matches exactly, and `mail_find_correspondent` finds people you've emailed by approximate name, address or company, reading only message headers. Approximate matches are labelled, and agents must ask you to confirm before sending, inviting or editing on one.
- **Postal addresses** are read and written as street, city, region, postcode and country, with home, work or your own labels ("Holiday house"), stored the way Apple's Contacts app expects.
- **Birthdays coming up.** `contacts_list_birthdays` lists them soonest first, with the age turned when the year is known (Apple's "year unknown" 1604 is understood, and 29 February falls on the 28th in other years).
- Updates keep every field outside the changed ones and use ETags to refuse stale overwrites. `add_emails` and `add_phones` add to a card without touching its existing addresses or their labels. Contact photos and notes are never returned.

</details>

<details>
<summary><b>Reminders</b> &nbsp;·&nbsp; 10 tools, with the Mac helper</summary>

`reminders_list_lists`, `reminders_list_reminders`, `reminders_create_reminder`, `reminders_update_reminder`, `reminders_complete_reminder`, `reminders_move_reminder` (the same reminder to another list, nothing deleted), `reminders_delete_reminder` (Reminders has no Recently Deleted, so this is final), `reminders_create_list`, `reminders_update_list` (rename), `reminders_delete_list` (with everything in it, only after a preview and its token)

- Runs through Apple's EventKit: every read is live and takes about 20 to 40 ms, however long your lists are. Active reminders by default; `completed="only"` lists what was done in a window, with when.
- Repeating reminders (`repeat`, daily or coarser: `FREQ=WEEKLY;BYDAY=MO`, `FREQ=MONTHLY;BYMONTHDAY=1;COUNT=12`) and extra alerts (`alerts_minutes_before`, `alerts_at`). Changing alerts keeps the alert at the due time itself.
- List names can repeat across accounts, so tools accept a `list_id` and refuse an ambiguous name. Due dates are validated as real dates (a bare date means 09:00 local time).

</details>

<details>
<summary><b>Notes</b> &nbsp;·&nbsp; 9 tools, with the Mac helper</summary>

`notes_list_folders`, `notes_list_notes`, `notes_read_note`, `notes_create_note`, `notes_append_to_note`, `notes_update_note`, `notes_create_folder`, `notes_move_note`, `notes_delete_note`

- Read, create, **edit** and **organise**: add to a note (`notes_append_to_note`, keeps headings, lists and styling; notes with tables are refused) or rewrite it (`notes_update_note`, keeps the title), create folders and subfolders, and move notes between them.
- **Edits are guarded.** `notes_read_note` returns a `content_hash`; append and update need it with the current title, so a note that changed since it was read is never overwritten. Locked notes, notes with attachments and notes in Recently Deleted are refused, and the old version is saved to `~/Library/Application Support/icloud-mac-helper/note-backups/` before anything is written.
- Move and delete act on one note at a time and need its **current title** as well as its id, so a stale or wrong id changes nothing.
- Delete moves a note to **Recently Deleted**, where you can recover it for about 30 days. It refuses locked notes, and notes already in Recently Deleted, because removing them from there would be permanent. Nothing is ever moved into Recently Deleted.

</details>

<details>
<summary><b>iCloud Drive</b> &nbsp;·&nbsp; 10 tools, with the Mac helper</summary>

`drive_list_folder`, `drive_search_files`, `drive_search_content`, `drive_get_info`, `drive_read_file`, `drive_get_file`, `drive_write_file`, `drive_create_folder`, `drive_move_item`, `drive_trash_item`

- Works on your **whole iCloud Drive** as your Mac keeps it in sync, so every change syncs to your other devices by itself.
- `drive_read_file` returns text from plain text files, **PDFs** and **Word, RTF, ODT and HTML** documents. Files offloaded by "Optimise Mac Storage" are downloaded first. If that takes too long, the answer says the file is still downloading, instead of timing out.
- **Search inside files.** `drive_search_content` finds words in the text of plain text, PDF, Word, RTF, ODT and HTML files (case and accents ignored) and returns an excerpt for each match. Each file is read once and its text kept in a private cache on the Mac (`drive-text-cache.sqlite`, mode 600), so later searches are fast and still work after macOS offloads the file. Files that are only in iCloud are skipped unless `download=true`, and the answer always says how many were left out. (Spotlight was tried first and dropped: its index of iCloud Drive was measurably incomplete.)
- `drive_write_file` creates plain text files. Replacing a file needs `overwrite`, and the old version goes to the Trash. `drive_move_item` never overwrites.
- `drive_get_file` hands over the file itself (base64, up to `MAX_ATTACHMENT_BYTES`), so an agent can attach it or send it on, the way `mail_get_attachment` does for mail.
- **Nothing is ever deleted permanently.** `drive_trash_item` moves items to the Trash, where you can recover them.
- Paths are relative to the Drive and can't leave it, not through `..` and not through a symbolic link (links that lead outside are not even listed). The Drive's trash folder is off limits.

</details>

<details>
<summary><b>Apple Maps</b> &nbsp;·&nbsp; 2 tools, with the Mac helper</summary>

`maps_get_travel_time`, `maps_search_places`

- Travel time and distance between two places for walking, cycling, driving or public transport, for a departure or arrival time (public transport gives a time, not a route). Places are addresses, names or `lat,lon`; the destination is looked up near the origin, and the result names both resolved places so a wrong match is visible.
- With Maps on, calendar travel time uses a measured value when the owner has one and otherwise an Apple Maps estimate, always labelled as one; never an invented number. Repeat questions within 10 minutes are answered from a cache.
- Runs through MapKit on the Mac (`bin/maps-cli`, built by the installer). It needs no permission and never uses the Mac's own location. Turn it on with `ENABLE_MAPS=true`.

</details>

<details>
<summary><b>Messages</b> &nbsp;·&nbsp; 4 tools, with the Mac helper</summary>

`imessage_list_chats`, `imessage_read_chat`, `imessage_search_messages`, `imessage_send_message` (off by default)

- Your own iMessage and SMS history on your Mac, read-only: conversations with who is in them (matched to your contacts), messages with reactions, delivery and read state and attachments by name, and search across the whole history (case and accents ignored).
- Only your own Messages database is read (the Mac user the helper runs as); another user on the Mac is never touched. The helper's Python needs Full Disk Access, the same grant iCloud Drive uses.
- Messages are other people's words: results carry the untrusted-data notice and safety warnings. `IMESSAGE_HIDDEN_CHATS` hides chats completely, service senders such as banks and one-time codes are hidden by default, `IMESSAGE_MAX_AGE_DAYS` limits how far back, and `IMESSAGE_NEVER_SEND` labels your own assistant's thread. Turn it on with `ENABLE_IMESSAGE=true`.
- Sending is off unless `IMESSAGE_ALLOW_SEND=true`, only reaches people on `IMESSAGE_SEND_ALLOWLIST` (empty means nobody), never reaches `IMESSAGE_NEVER_SEND` or the handles in the Mac's own `imessage-never-send.txt`, and by default waits for your approval on `/outbox` even when your mail sends directly. iMessage only, never SMS; each send is confirmed from what Messages records. The first send asks once for permission to control Messages: `--selftest-imessage-send you@example.com` on the Mac triggers that while you are there.

</details>

<details>
<summary><b>Apple Health</b> &nbsp;·&nbsp; 4 tools, with the Mac helper</summary>

`health_get_summary`, `health_get_day`, `health_get_status`, `health_refresh_data`

- Daily figures from Apple Health: steps, active energy and distance (with how many hours had data), resting and walking heart rate, HRV, breathing rate, heart rate range, and every sleep with its stages, dated by the day it ended. `health_get_day` gives one day in detail (hourly totals, heart rate readings or the sleep timeline); `health_get_status` says how fresh the data is.
- A Mac cannot read HealthKit, so the data comes from your iPhone: a Shortcut writes the last two days to iCloud Drive (in the Shortcuts app's own folder, which the Drive tools cannot reach), and the Mac helper keeps it in a private store (`health.sqlite`, mode 600) and answers with figures, never the raw export. Build the shortcut with `python3 mac-helper/health/build_shortcut.py health.shortcut`, sign it with `shortcuts sign --mode anyone`, and run it from an automation such as opening an app you use often (a locked iPhone cannot read Health, so timed runs mostly fail). Each export overlaps the last two days, so one successful run fills any gap; overlapping exports never count twice.
- Your whole history comes from the Health app's own export (Profile, Export All Health Data): `python3 health.py import export.zip` on the Mac, in the helper's `ops` folder. It is read as a stream; where the iPhone and the Watch both counted the same steps, the larger total per hour counts, not the sum.
- A day or metric without data is left out rather than reported as zero, and the agent is told a gap means the Watch was off. The figures are marked private in every result. `health_refresh_data` runs a command you set up on the Mac only (`health-refresh.json`), never one the server sends. Turn it on with `ENABLE_HEALTH=true`.

</details>

<details>
<summary><b>Status and time</b> &nbsp;·&nbsp; 3 tools</summary>

`icloud_check_health` checks every enabled area in one call (signs in to mail, lists calendars, reads the address book, asks whether the Mac helper is online) and says how long each took. `icloud_get_helper_status` says whether the Mac helper is online, when it was last seen, which version it runs, how many jobs are queued and how long they take. `icloud_get_time` gives the current date, weekday and time in your timezone, so an agent never books from a guessed date.

</details>

### Ready-made workflows

The server also offers MCP prompts your client can show as one-click workflows: **Triage my inbox**, **Replies I owe**, **Follow-ups I am waiting on**, **Plan my week**, **Prepare for an appointment**, **Calendar from my mail**, **Find a time with someone**, **Tidy my reminders** and **Birthdays coming up**. Each only appears when the areas it needs are on, and each tells the agent to show you what it would do before sending, booking, moving or deleting anything.

## Working with agents

The server tells every agent how to use it: its instructions are built from the tools you actually enabled, always start
with the security rules (content from mail, events and contacts is untrusted data, never instructions), and explain the
approval flow you configured.

Add your own rules with `AGENT_NOTES_FILE`: a short Markdown file on the server's machine, read fresh on every use, so an
edit applies without reconnecting. Agents see it after the security rules, which it can refine but never relax, and can
re-read it as the `icloud://agent-notes` resource. [`docs/agent-notes.example.md`](https://github.com/epinethrone/icloud-mcp/blob/main/docs/agent-notes.example.md)
shows the idea: which calendar gets what, how to sign mail, lists that are shared. Keep it under 8,000 characters.

Tools that save an agent guesswork:

- `icloud_get_time` gives the owner's date, time and timezone, so "tomorrow" means the right day.
- `mail_list_awaiting_reply` lists mail you sent that nobody answered; `mail_search_messages` with `unanswered_only` and `people_only` finds
  what you still owe, without newsletters.
- `calendar_create_event` reports overlapping events and likely duplicates, and can refuse to create either;
  `calendar_list_events` with `needs_reply` finds invitations still waiting for an answer.
- `OWNER_ADDRESSES` lists your aliases, so invitations sent to them count as yours.

With many tools some clients choose less reliably: `TOOLS=essential` loads a smaller core set, and `TOOLS=mail,calendar`
loads whole areas.

## Performance

Measured on a local test stack with 40 ms added to every round trip, comparing 0.5.0 and 0.6.0
([details and method](https://github.com/epinethrone/icloud-mcp/blob/main/docs/PERFORMANCE.md)):

| | 0.5.0 | 0.6.0 |
|---|---|---|
| 30 days of events, all calendars | 0.74 s | 0.25 s |
| The same list, bytes returned | 26,017 | 12,384 |
| Search 20 messages and read 10, received from iCloud | 465 KB | 55 KB |
| A 20-hit mail search, bytes returned | 8,946 | 6,447 |
| Schema text every client loads | 51,951 chars | 37,956 chars |

Connections stay signed in for 10 minutes after the last call (`IMAP_IDLE_SECONDS`, `CALDAV_KEEPALIVE_SECONDS`), and
`WARMUP_ON_START` signs in as the server starts. `dev/bench.py` repeats the measurements against your own account.

## Control it from the menu bar

[`menubar/`](https://github.com/epinethrone/icloud-mcp/blob/main/menubar/README.md) holds **iCloud MCP Control**, a native macOS app for a server that runs on your Mac. Its menu is a standard macOS menu, like Time Machine's: it shows whether the server is up, how many tools and connected apps it has and any problem in plain words, and lets you pause the server (tools answer that it is paused, nobody is disconnected), restart or stop it, the Mac helper and a tunnel. Its Settings change the owner passcode, replace the iCloud app-specific password (tested with iCloud before it is saved) and list every connected app with when it was last used, so you can sign out one, a group or all of them. Turn on its admin API with `ADMIN_PORT`: it listens on 127.0.0.1 only, on its own port, and every request needs the token the server writes to `admin-token` in its data folder.

## Reminders, Notes, iCloud Drive, Maps, Messages and Health through your Mac

Apple only exposes Reminders, Notes, iCloud Drive, Apple Maps, your Messages history and Health data on its own devices, so a small helper ([`mac-helper/`](https://github.com/epinethrone/icloud-mcp/blob/main/mac-helper/README.md)) runs on your Mac and does the work when the server asks.

- **Nothing listens on your Mac.** The helper connects *out* to a private HTTPS port of the server (never the public address, never the tunnel) and long-polls for jobs.
- **No code is ever sent.** The server sends an operation name and validated arguments from a fixed list. Reminders run a small EventKit program the installer builds on your Mac, and Maps a small MapKit program. Notes run static scripts. Messages are read from your own Messages database, read-only. iCloud Drive and Health each run one fixed Python script under Apple's own Python. In every case the arguments arrive as one JSON value, never as code.
- **Pinned and authenticated.** TLS with a self-signed certificate the helper pins by fingerprint, plus a bearer token.
- **Honest when it's off.** It works while your Mac is on and reachable (home network or VPN). When it isn't, the tools say so.

Enable it in `.env` with any of `ENABLE_REMINDERS=true`, `ENABLE_NOTES=true`, `ENABLE_DRIVE=true`, `ENABLE_MAPS=true`, `ENABLE_IMESSAGE=true` and `ENABLE_HEALTH=true`, a `BRIDGE_TOKEN` of at least 32 random characters, and `BRIDGE_BIND` set to the address the Mac reaches the server on. The server logs the certificate fingerprint for the installer, and also writes it to `bridge_fingerprint.txt` in its data folder. Then follow the [Mac helper guide](https://github.com/epinethrone/icloud-mcp/blob/main/mac-helper/README.md).

**Shortcuts, allowlisted twice.** To let the assistant run some of your Shortcuts (`shortcuts_list_shortcuts`, `shortcuts_run_shortcut`), list their exact names in `SHORTCUTS_ALLOW` on the server **and**, one per line, in `~/Library/Application Support/icloud-mac-helper/shortcuts-allow.txt` on the Mac. A name must be on both lists, so a compromised server can never run a shortcut you did not allow at the Mac itself. Apple's `shortcuts` command runs it, with optional text input, and its text output comes back. The tools do not exist without an allowlist or on a read-only server.

> [!IMPORTANT]
> **iCloud Drive and Messages need Full Disk Access** for the helper's Python. On macOS 27 the grant only takes effect when the helper runs as the Command Line Tools `Python.app` executable, which is what the installer sets up. Details in the [Mac helper guide](https://github.com/epinethrone/icloud-mcp/blob/main/mac-helper/README.md#icloud-drive).

## Configuration

Everything is an environment variable. [`.env.example`](https://github.com/epinethrone/icloud-mcp/blob/main/.env.example) has a comment for each one.

<details>
<summary><b>All settings</b></summary>

| Variable | Default | Meaning |
|---|---|---|
| `ICLOUD_USERNAME` | required | The Apple Account you sign in with |
| `ICLOUD_APP_PASSWORD` | required (or Keychain) | App-specific password |
| `ICLOUD_KEYCHAIN`, `ICLOUD_KEYCHAIN_SERVICE` | true, icloud-mcp | macOS: read the app-specific password from the login Keychain when `ICLOUD_APP_PASSWORD` is not set (store it with `--store-password`) |
| `ICLOUD_EMAIL_ADDRESS` | username | From address (your iCloud address or alias) |
| `ICLOUD_DISPLAY_NAME`, `EMAIL_SIGNATURE` | empty | Sender name; plain-text signature added to sent mail (`\n` = new line) |
| `IMAP_HOST/PORT/SECURITY/USERNAME` | `imap.mail.me.com`, 993, ssl, username | IMAP |
| `SMTP_HOST/PORT/SECURITY/USERNAME` | `smtp.mail.me.com`, 587, starttls, username | SMTP |
| `CALDAV_URL`, `CALDAV_USERNAME`, `CALDAV_REQUIRE_TLS` | `https://caldav.icloud.com`, username, true | CalDAV |
| `CARDDAV_URL`, `CARDDAV_USERNAME` | `https://contacts.icloud.com`, username | CardDAV |
| `DEFAULT_TIMEZONE`, `DEFAULT_CALENDAR` | `UTC`, auto | Timezone for times without an offset (an IANA name such as `Europe/Amsterdam`); calendar for new events (else "Calendar" or "Home", else the first) |
| `ENABLE_MAIL`, `ENABLE_CALENDAR`, `ENABLE_CONTACTS` | true | Switch whole areas off |
| `ENABLE_REMINDERS`, `ENABLE_NOTES`, `ENABLE_DRIVE`, `ENABLE_MAPS` | false | Areas that go through the Mac helper (need `BRIDGE_TOKEN`); Maps is Apple Maps travel times and place search |
| `ENABLE_IMESSAGE` | false | Read and search your own iMessage and SMS history through the Mac helper |
| `ENABLE_HEALTH` | false | Daily Apple Health figures from your iPhone's exports, through the Mac helper |
| `IMESSAGE_HIDDEN_CHATS`, `IMESSAGE_VISIBLE_CHATS` | empty | Chats never shown to the agent; or, when set, the only ones shown |
| `IMESSAGE_MAX_AGE_DAYS` | 0 | 0 = the whole history; a number limits every read to that many days |
| `IMESSAGE_HIDE_SHORT_CODES` | true | Hides service senders (banks, delivery, one-time codes): any sender that is not an email or a full phone number |
| `IMESSAGE_NEVER_SEND` | empty | Handles nothing is ever sent to (your own assistant's Apple ID, say); its chat is labelled as the assistant's |
| `IMESSAGE_ALLOW_SEND`, `IMESSAGE_SEND_REQUIRES_APPROVAL` | false, true | Allow `imessage_send_message`; every iMessage then waits for your approval on `/outbox`, whatever the mail setting |
| `IMESSAGE_SEND_ALLOWLIST` | empty | Who may receive an iMessage (handles or chat ids); empty means nobody, `*` anyone |
| `TOOLS` | all | `essential`, areas (`mail`, `calendar`, `contacts`, `reminders`, `notes`, `drive`, `maps`, `imessage`, `health`) and/or tool names to expose; everything else is not registered at all. An unknown name stops the server and lists the real ones |
| `BRIDGE_TOKEN`, `BRIDGE_BIND` | empty, 127.0.0.1 | Mac helper secret (32+ characters) and the address its private port is published on |
| `BRIDGE_HOST` | 127.0.0.1 (0.0.0.0 in the Docker image) | Address the bridge binds to inside the process. Loopback unless the helper's Mac reaches this process over the network |
| `SHORTCUTS_ALLOW` | empty | Exact names of Shortcuts the assistant may run through the Mac helper, separated by commas (or by `;` when a name contains a comma); the Mac must list them too (see below) |
| `BRIDGE_JOB_TIMEOUT_SECONDS` | 60 | How long a tool call waits for the Mac (at most `TOOL_TIMEOUT_SECONDS` minus 5, so its own message arrives) |
| `READ_ONLY` | false | No sending (not even drafts), moving, deleting, or calendar, contact, reminder, note or file changes |
| `ALLOW_SEND` | true | false = agents can only save drafts |
| `SEND_REQUIRES_APPROVAL` | true | Queue outgoing mail for browser approval (locally: save it to Drafts) |
| `OUTBOX_TTL_SECONDS`, `OUTBOX_MAX` | 86400, 20 | Queue lifetime and size |
| `ALLOW_CALENDAR_INVITES` | false | Allow attendees (iCloud then emails invitations, updates and cancellations) |
| `SEND_ALLOWLIST` | empty | Only these addresses or domains may receive mail (`@example.org,friend@example.com`) |
| `MAX_RECIPIENTS` | 25 | Per message |
| `ALLOW_PERMANENT_DELETE` | false | Allow deleting mail from Trash |
| `ALLOW_UNSUBSCRIBE_LINKS` | false | Let `mail_unsubscribe_from_list` open a sender's unsubscribe web page (header or the unsubscribe link in the body) after the owner confirms a preview; a form asking for an email gets only the address that sender mailed (a Hide My Email alias as itself) |
| `SAVE_SENT_COPY` | true | Copy sent mail to Sent (iCloud doesn't do it itself) |
| `MAX_BODY_CHARS`, `MAX_ATTACHMENT_BYTES` | 30000, 5 MiB | Result size caps |
| `MCP_PUBLIC_URL`, `MCP_OWNER_PASSWORD` | required when hosted | Public https address; owner password (12+ characters) |
| `MCP_HOST`, `MCP_PORT`, `MCP_EXTRA_ALLOWED_HOSTS` | 127.0.0.1 (0.0.0.0 in the Docker image), 8000, empty | Bind address and extra allowed `Host` headers |
| `MCP_STATELESS` | true | No server-side MCP sessions, so a restart never breaks a connected client ("Missing session ID") |
| `TOOL_TIMEOUT_SECONDS` | 60 | A tool call running longer is abandoned with an error that names the slow step |
| `IMAP_POOL_SIZE`, `IMAP_IDLE_SECONDS` | 3, 600 | Logged-in mail connections kept for reuse (0 = log in on every call), and how long they are kept warm after the last call |
| `CALDAV_POOL_SIZE`, `CALDAV_KEEPALIVE_SECONDS` | 4, 600 | Calendar connections kept for reuse, and how long they are kept warm after the last call (0 = no keep-alive) |
| `WARMUP_ON_START` | true | Sign in to mail, calendar and contacts in the background right after start, so the first call is fast |
| `TOOL_WORKERS` | 8 | Tool calls that can run at the same time |
| `OWNER_ADDRESSES` | (none) | More addresses that are yours (aliases), so invitations to them count as yours |
| `AGENT_NOTES_FILE` | (none) | Your own rules for agents, added to the instructions and served as `icloud://agent-notes` (see `docs/agent-notes.example.md`) |
| `DATA_DIR` | `./data` (`/data` in Docker) | OAuth state and the outbox |
| `ADMIN_PORT` | off | Loopback-only admin API for the menu bar app (its token is `admin-token` in `DATA_DIR`) |
| `OAUTH_ALLOWED_REDIRECT_HOSTS` | `claude.ai,claude.com,chatgpt.com,chat.openai.com,localhost,127.0.0.1` | Where sign-in may return a client: Claude, ChatGPT, and local clients such as Codex. Every sign-in still needs the owner password |
| `ACCESS_TOKEN_TTL`, `REFRESH_TOKEN_TTL` | 3600, 30 days | Token lifetimes (refresh tokens rotate) |
| `LOG_LEVEL` | INFO | |

</details>

## Troubleshooting

Start by asking your AI to *"check my iCloud connection"*: `icloud_check_health` tests every enabled area and names the one that fails.

<details>
<summary><b>Claude Desktop extension</b></summary>

| Symptom | Fix |
|---|---|
| "Failed to initialize cache", "Operation not permitted" or "Unable to connect to extension server" | Security software is probably blocking the `uv` and Python the extension downloads (antivirus application control, such as F-Secure's, does this for unsigned programs). Allow `uv` and its Python in that software, or quit it once to confirm, or use the [manual setup](#manual-setup-any-mcp-client) with a Python you already trust. |
| Times are off by an hour or more | The time zone field needs one IANA name, such as `Europe/Amsterdam`, and nothing else. Change it under Settings → Extensions → iCloud. |
| Reminders, Notes or Drive are missing | The extension covers Mail, Calendar and Contacts. The Mac areas need the [manual setup](#manual-setup-any-mcp-client) and the Mac helper. |

</details>

<details>
<summary><b>Server and connection</b></summary>

| Symptom | Fix |
|---|---|
| `selftest` fails to log in | The login name is the usual cause. Some accounts sign in to one service with a different name: set `IMAP_USERNAME`, `SMTP_USERNAME`, `CALDAV_USERNAME` or `CARDDAV_USERNAME` separately. Use an app-specific password, never your Apple Account password. |
| Your client can't reach the server, or the approval page never appears | `MCP_PUBLIC_URL` must match the public address exactly, with no trailing slash, and your TLS front must keep the `Host` header. Make sure no login wall such as Cloudflare Access sits in front of the server. |
| The approval or outbox page won't accept your password | After 10 wrong passwords in 15 minutes the pages lock for everyone. Wait, then use the owner password from `.env`, not your Apple password. |
| New tools or changed settings don't show up | Clients cache tool definitions. Reconnect the server in your client's settings (in Codex, start a new session) and start a new chat. |
| A web client is refused at sign-in ("redirect host not allowed") | Its sign-in returns to a host the server doesn't know. Claude and ChatGPT are allowed by default; add another client's host to `OAUTH_ALLOWED_REDIRECT_HOSTS`. |
| "Missing session ID" after the server restarted | Leave `MCP_STATELESS=true` (the default). If you turned it off, reconnect the connector after every restart. |
| A tool call times out | iCloud can be slow; a call is abandoned after `TOOL_TIMEOUT_SECONDS` (60 by default) with an error naming the slow step. Retry, and check the server logs if it keeps happening. |

</details>

<details>
<summary><b>Mail, calendar and contacts</b></summary>

| Symptom | Fix |
|---|---|
| Your AI says it sent an email but nothing arrived | That is the approval step working. Hosted: open `https://<your-host>/outbox`, review the message and approve it. Locally: it is in your Drafts. |
| Adding a guest to an event is refused | Invitations are off by default. Set `ALLOW_CALENDAR_INVITES=true` if you want your AI to invite people. |
| Mail to a certain address is refused | Check `SEND_ALLOWLIST` and `MAX_RECIPIENTS`. |
| Sent mail doesn't appear in Sent | iCloud doesn't file sent mail by itself. Keep `SAVE_SENT_COPY=true` (the default). |

</details>

<details>
<summary><b>Mac helper: Reminders, Notes, iCloud Drive and Health</b></summary>

| Symptom | Fix |
|---|---|
| Tools say the Mac helper is offline | The Mac must be on, awake, logged in and able to reach the bridge address (home network or VPN). Ask *"Is the Mac helper online?"*, and check `~/Library/Logs/icloud-mac-helper/helper.log` on the Mac. |
| The helper log shows "No route to host" | macOS blocks third-party Python from the local network. Use Apple's Python (the installer picks it), or allow it under Privacy & Security > Local Network. |
| Reminders are refused | Allow Full Access to Reminders for "iCloud Mac Helper (Reminders)" under Privacy & Security > Reminders, then run the helper's self-test. |
| Notes are refused ("not allowed to control") | Allow the helper's Python to control Notes under Privacy & Security > Automation. |
| iCloud Drive says the helper has no access | Give Full Disk Access to the Command Line Tools `Python.app`, and make sure the helper runs as that executable (re-run the installer). See the [Mac helper guide](https://github.com/epinethrone/icloud-mcp/blob/main/mac-helper/README.md#icloud-drive). |
| Reading a Drive file says it is still downloading | The file was only in iCloud ("Optimise Mac Storage"). Its download has started; ask again in a minute. |
| Health figures are missing or hours old | Your iPhone only exports while it is unlocked: check `health_get_status`, then open the app your automation watches. A day without data means the Watch was off; one export fills the last two days. |

</details>

<details>
<summary><b>iCloud quirks this project works around</b></summary>

These only show up against Apple's real servers, never against local test servers:

- **CalDAV** rejects UID-filtered queries (`412`), so events are fetched by resource name with a scan fallback. An attendee who is the account owner is rewritten to an internal path with the address in the `EMAIL` parameter.
- **IMAP** has no `MOVE`. Moving and deleting use COPY, flag `\Deleted`, then `UID EXPUNGE` of exactly those messages (never a plain `EXPUNGE`). iCloud doesn't file sent mail by itself.
- **CardDAV** discovery ends on a different host than it starts on (follow the returned links), returns the whole address book in one request, and stores about half of all emails in grouped `itemN.EMAIL` properties with labels in `itemN.X-ABLabel`.
- **Travel time** is a number Apple stores, not a live estimate. It is never recomputed, so an origin or travel mode without a duration is refused instead of silently doing nothing.

</details>

<details>
<summary><b>Limits</b></summary>

- Reminders, Notes, iCloud Drive, Maps, Messages and Health need the Mac helper and a Mac that is on; Health also needs your iPhone to export. Contact photos and notes are deliberately not exposed to agents, and deleting a contact is permanent.
- One identity: aliases can't be used as the From address. Attachments that aren't text come back as base64 and are size-capped.
- Mail calls reuse up to two logged-in connections, which saves the login (about a second) on each call after the first; calendar and contacts calls take about 1.5 to 5 seconds against iCloud.
- Claude doesn't show custom icons for custom connectors yet ([open request](https://github.com/anthropics/claude-ai-mcp/issues/152)). The server serves and advertises the project logo anyway, so clients that do show icons, and your browser tab on the approval and outbox pages, display it.

</details>

## Contributing

Issues and pull requests are welcome. Every pull request gets the offline tests on Python 3.11 to 3.13 and an automated security-minded review.

- **Security problems:** please don't open a public issue. Follow the [security policy](https://github.com/epinethrone/icloud-mcp/blob/main/SECURITY.md) instead.
- **Before a pull request:** make sure `pytest tests --ignore=tests/integration` passes, and add tests for new behaviour.
- **Anything that talks to iCloud:** run `selftest`, and try it by hand against a real account. Local test servers accept things iCloud doesn't.
- **New tools:** keep the safety defaults intact. Anything that sends, invites or deletes must stay behind the existing settings, and anything read from iCloud must be treated as data, never as instructions.

<details>
<summary><b>Development</b></summary>

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
pytest tests --ignore=tests/integration      # offline tests, no network
sudo apt install dovecot-imapd && dev/start_local_stack.sh
pytest tests                                 # adds integration tests against local Dovecot, an SMTP sink and Radicale
python packaging/mcpb/build.py               # builds the Claude Desktop extension into dist/
python dev/readme_art.py                     # redraws the README artwork in assets/readme/
```

`dev/e2e_http.py` drives a running server over HTTP (OAuth plus tool calls). Local test servers accept things iCloud doesn't (see the quirks above), so treat `selftest` and a manual run against a real account as part of testing any change.

</details>

## Acknowledgements

Built on the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), [IMAPClient](https://github.com/mjs/imapclient), [caldav](https://github.com/python-caldav/caldav), [icalendar](https://github.com/collective/icalendar), [html2text](https://github.com/Alir3z4/html2text), [python-dateutil](https://github.com/dateutil/dateutil), [Uvicorn](https://github.com/encode/uvicorn) and [HTTPX](https://github.com/encode/httpx). The Notes scripts are adapted from [MrGo2/icloud-mcp](https://github.com/MrGo2/icloud-mcp) (MIT); see [THIRD_PARTY_NOTICES.md](https://github.com/epinethrone/icloud-mcp/blob/main/THIRD_PARTY_NOTICES.md).

<br>

<div align="center">
<sub>Not affiliated with Apple. iCloud is a trademark of Apple Inc.<br>
<a href="https://github.com/epinethrone/icloud-mcp/blob/main/LICENSE">MIT License</a> · Made for people who want an assistant for their Apple life without handing their Apple Account to anyone.</sub>
</div>
