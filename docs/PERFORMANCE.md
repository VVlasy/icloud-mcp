# Performance

How fast the connector answers, how many network round trips each tool costs, and how much text it hands an agent. Every change
that claims to make something faster records its numbers here, measured the same way.

## How to measure

- `python dev/bench.py --local` runs agent-like scenarios against the local stack (`dev/start_local_stack.sh`: Dovecot, an SMTP
  sink and Radicale, all on 127.0.0.1). CI runs it on every pull request (job `bench` in `.github/workflows/tests.yml`) and puts
  the tables in the job summary and the `bench` artifact.
- `--latency-ms 40` adds 40 ms to every TCP connect and every request or command, which is roughly what iCloud costs from Europe.
  On localhost a round trip is almost free, so without it the timings hide exactly what the network charges for.
- `python dev/bench.py --live --env <server .env> [--scratch-calendar NAME]` measures a real account: read-only tools only, plus
  create/update/delete of one event inside the named scratch calendar. It never sends mail and never writes contacts.
- `python dev/tool_surface.py` measures the text an agent receives before doing anything: tool descriptions, parameter schemas,
  the whole `tools/list` result and the instructions.
- In the bench tables `bytes` is the result text the model reads (comparable with older runs); `wire bytes` is the whole JSON-RPC
  response body, which also counts a structuredContent copy where one is sent.

**Read the counts first.** Round trips (TCP connects, logins, IMAP commands, CalDAV and CardDAV requests) are what a real network
charges for, and they are exact. Local timings are noisy and small.

Known quirk of the local stack: Radicale answered each CalDAV request on its own TCP connection, so locally `tcp connects` equals
`caldav requests`. iCloud keeps connections alive, so there the connect count is lower; the request count is what carries over.

## Baseline (0.5.0, 24 September 2026)

Local stack in CI (GitHub-hosted Ubuntu runner, Python 3.12), 5 runs per scenario, fictional seed data: 60 inbox messages (a third
of them newsletters, six with a 300 KB PDF), 20 archived, 10 sent, five calendars with a month of events and a weekly series each,
200 contacts.

### local stack, 5 runs

| scenario | median s | p90 s | bytes | notice chars | tcp connects | imap logins | imap commands | caldav requests | carddav requests |
|---|---|---|---|---|---|---|---|---|---|
| mail_search_messages 20 + get_messages 10 | 0.076 | 0.107 | 35156 | 327 | 0 | 0 | 6 | 0 | 0 |
| mail_search_messages all_folders | 0.049 | 0.052 | 14111 | 124 | 0 | 0 | 14 | 0 | 0 |
| mail_get_attachment (300 KB pdf) | 0.041 | 0.042 | 423823 | 248 | 0 | 0 | 6 | 0 | 0 |
| calendar_list_calendars (cold) | 1.271 | 1.457 | 491 | 0 | 9 | 0 | 0 | 9 | 0 |
| calendar_list_calendars (warm) | 0.006 | 0.006 | 491 | 0 | 2 | 0 | 0 | 2 | 0 |
| calendar_list_events 7 days | 0.073 | 0.084 | 15758 | 160 | 7 | 0 | 0 | 7 | 0 |
| calendar_list_events 30 days | 0.160 | 0.161 | 26017 | 160 | 7 | 0 | 0 | 7 | 0 |
| calendar_find_free_time 14 days | 0.102 | 0.104 | 2395 | 160 | 7 | 0 | 0 | 7 | 0 |
| contacts_search_contacts (cold) | 0.118 | 0.118 | 8609 | 142 | 5 | 0 | 0 | 0 | 5 |
| contacts_search_contacts (warm) | 0.003 | 0.116 | 8609 | 142 | 0 | 0 | 0 | 0 | 0 |
| calendar create + update + delete | 0.063 | 0.069 | 1397 | 0 | 13 | 0 | 0 | 13 | 0 |
| mail_search_messages x5, 20 s apart | 0.008 | 0.035 | 0 | 0 | 1 | 1 | 18 | 0 | 0 |

### local stack, 5 runs, +40 ms per round trip

| scenario | median s | p90 s | bytes | notice chars | tcp connects | imap commands | caldav requests | carddav requests |
|---|---|---|---|---|---|---|---|---|
| mail_search_messages 20 + get_messages 10 | 0.319 | 0.520 | 35156 | 327 | 0 | 6 | 0 | 0 |
| mail_search_messages all_folders | 0.622 | 0.623 | 14111 | 124 | 0 | 14 | 0 | 0 |
| mail_get_attachment (300 KB pdf) | 0.288 | 0.289 | 423823 | 248 | 0 | 6 | 0 | 0 |
| calendar_list_calendars (cold) | 1.867 | 2.237 | 491 | 0 | 9 | 0 | 9 | 0 |
| calendar_list_calendars (warm) | 0.170 | 1.538 | 491 | 0 | 2 | 0 | 2 | 0 |
| calendar_list_events 7 days | 0.652 | 0.661 | 15758 | 160 | 7 | 0 | 7 | 0 |
| calendar_list_events 30 days | 0.738 | 0.741 | 26017 | 160 | 7 | 0 | 7 | 0 |
| calendar_find_free_time 14 days | 0.681 | 0.683 | 2395 | 160 | 7 | 0 | 7 | 0 |
| contacts_search_contacts (cold) | 0.525 | 0.532 | 8609 | 142 | 5 | 0 | 0 | 5 |
| contacts_search_contacts (warm) | 0.003 | 0.524 | 8609 | 142 | 0 | 0 | 0 | 0 |
| calendar create + update + delete | 1.127 | 2.568 | 1397 | 0 | 13 | 0 | 13 | 0 |

### Tool surface (every area on)

| tool | description chars | schema chars |
|---|---|---|
| calendar_create_event | 407 | 3953 |
| calendar_update_event | 331 | 3410 |
| mail_reply_to_message | 689 | 2515 |
| contacts_update_contact | 241 | 2961 |
| contacts_create_contact | 224 | 2617 |
| mail_run_bulk_action | 550 | 1907 |
| mail_send_message | 688 | 1684 |
| mail_search_messages | 379 | 1969 |
| mail_forward_message | 398 | 1805 |
| calendar_find_free_time | 468 | 1726 |
| contacts_search_contacts | 738 | 638 |
| mail_get_messages | 407 | 912 |
| mail_extract_bookings | 594 | 705 |
| reminders_create_reminder | 186 | 1087 |
| drive_search_content | 464 | 803 |

**65 tools**: 18,006 description chars, 51,951 schema chars; instructions 4,331 chars.

### Live reference (22 September 2026, before this work)

Measured by hand against iCloud from the Netherlands: `calendar_list_calendars` 0.64 s, `calendar_list_events` a week 2.6 s and a
month 3.1 s, `calendar_get_event` 1.3 s, create 1.2 s, update 1.8 s, delete 1.8 s; `contacts_search_contacts` 1.7 s cold and 2 ms warm; an
IMAP login about 1 s.

## Phase 1: warm connections

CalDAV pool with keep-alive, IMAP keep-alive, a 60 s folder-list cache, SMTP and CardDAV connection reuse, warm-up at start, a
dedicated pool of tool workers. Same CI setup as the baseline. `background` columns are keep-alive pings, which run off the call
path and cost the caller nothing.

### local stack, 5 runs

| scenario | median s | p90 s | bytes | notice chars | tcp connects | imap logins | imap commands | caldav requests | carddav requests | background tcp connects | background caldav requests |
|---|---|---|---|---|---|---|---|---|---|---|---|
| mail_search_messages 20 + get_messages 10 | 0.083 | 0.115 | 35156 | 327 | 0 | 0 | 6 | 0 | 0 | 0 | 0 |
| mail_search_messages all_folders | 0.055 | 0.058 | 14111 | 124 | 0 | 0 | 13 | 0 | 0 | 0 | 0 |
| mail_get_attachment (300 KB pdf) | 0.043 | 0.044 | 423823 | 248 | 0 | 0 | 6 | 0 | 0 | 0 | 0 |
| calendar_list_calendars (cold) | 1.264 | 1.431 | 491 | 0 | 9 | 0 | 0 | 9 | 0 | 0 | 0 |
| calendar_list_calendars (warm) | 0.000 | 0.000 | 491 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| calendar_list_events 7 days | 0.071 | 0.074 | 15758 | 160 | 5 | 0 | 0 | 5 | 0 | 0 | 0 |
| calendar_list_events 30 days | 0.177 | 0.181 | 25976 | 160 | 5 | 0 | 0 | 5 | 0 | 0 | 0 |
| calendar_find_free_time 14 days | 0.110 | 0.113 | 2395 | 160 | 5 | 0 | 0 | 5 | 0 | 0 | 0 |
| contacts_search_contacts (cold) | 0.135 | 0.136 | 8609 | 142 | 5 | 0 | 0 | 0 | 5 | 0 | 0 |
| contacts_search_contacts (warm) | 0.003 | 0.132 | 8609 | 142 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| calendar create + update + delete | 0.044 | 0.050 | 1397 | 0 | 7 | 0 | 0 | 7 | 0 | 0 | 0 |
| mail_search_messages x5, 20 s apart (totals) | 0.007 | 0.035 | 0 | 0 | 1 | 1 | 18 | 0 | 0 | 0 | 0 |
| calendar_list_events x5, 20 s apart (totals) | 0.074 | 1.515 | 0 | 0 | 34 | 0 | 0 | 34 | 0 | 10 | 10 |

### local stack, 5 runs, +40 ms per round trip

| scenario | median s | p90 s | bytes | notice chars | tcp connects | imap commands | caldav requests | carddav requests |
|---|---|---|---|---|---|---|---|---|
| mail_search_messages 20 + get_messages 10 | 0.326 | 0.522 | 35156 | 327 | 0 | 6 | 0 | 0 |
| mail_search_messages all_folders | 0.583 | 0.623 | 14111 | 124 | 0 | 13 | 0 | 0 |
| mail_get_attachment (300 KB pdf) | 0.286 | 0.286 | 423823 | 248 | 0 | 6 | 0 | 0 |
| calendar_list_calendars (cold) | 1.673 | 2.181 | 491 | 0 | 9 | 0 | 9 | 0 |
| calendar_list_calendars (warm) | 0.000 | 0.000 | 491 | 0 | 0 | 0 | 0 | 0 |
| calendar_list_events 7 days | 0.483 | 0.495 | 15758 | 160 | 5 | 0 | 5 | 0 |
| calendar_list_events 30 days | 0.582 | 0.583 | 25976 | 160 | 5 | 0 | 5 | 0 |
| calendar_find_free_time 14 days | 0.515 | 0.515 | 2395 | 160 | 5 | 0 | 5 | 0 |
| contacts_search_contacts (cold) | 0.537 | 0.547 | 8609 | 142 | 5 | 0 | 0 | 5 |
| contacts_search_contacts (warm) | 0.003 | 0.537 | 8609 | 142 | 0 | 0 | 0 | 0 |
| calendar create + update + delete | 0.613 | 0.620 | 1397 | 0 | 7 | 0 | 7 | 0 |

What changed against the baseline, with 40 ms per round trip:

| scenario | baseline | phase 1 | change |
|---|---|---|---|
| `calendar_list_calendars` (warm) | 2 requests, 0.170 s | 0 requests, 0.000 s | calendar list kept per connection |
| `calendar_list_events` 7 days | 7 requests, 0.652 s | 5 requests, 0.483 s | -26 % |
| `calendar_find_free_time` 14 days | 7 requests, 0.681 s | 5 requests, 0.515 s | -24 % |
| create + update + delete | 13 requests, 1.127 s | 7 requests, 0.613 s | -46 % |
| `mail_search_messages all_folders` | 14 IMAP commands | 13 | folder LIST cached |
| calendar reads 20 s apart | a new connection after every pause (15 s idle limit: 9 requests each time) | none after the first; 2 background pings per pause | no cold starts while in use |

Mail searches 20 s apart logged in once in both versions: Dovecot does not drop idle sessions the way iCloud does, so the
IMAP keep-alive (a NOOP every 5 minutes) only shows its effect against a live account.

## Phase 2a: parallel calendars, leaner results

Calendars are read in parallel on spare pooled connections (never new ones), `limit` is applied before events are converted,
descriptions in event lists are cut at 2,000 characters, `fields="summary"` gives a compact list, all-folder mail search runs
folders in parallel, HTML is converted from a bounded slice, attachment sizes come without decoding, list results leave empty
fields out, and untrusted-content notices are one short line.

### local stack, 5 runs, +40 ms per round trip

| scenario | median s | p90 s | bytes | notice chars | tcp connects | imap commands | imap kb in | caldav requests | carddav requests |
|---|---|---|---|---|---|---|---|---|---|
| mail_search_messages 20 | 0.136 | 0.179 | 10438 | 85 | 0 | 3 | 12 | 0 | 0 |
| mail_search_messages 20 + get_messages 10 | 0.308 | 0.310 | 30776 | 249 | 0 | 6 | 465 | 0 | 0 |
| mail_search_messages all_folders | 0.342 | 0.466 | 11257 | 85 | 0 | 19 | 31 | 0 | 0 |
| mail_get_attachment (300 KB pdf) | 0.277 | 0.277 | 420259 | 170 | 0 | 6 | 436 | 0 | 0 |
| calendar_list_calendars (cold) | 1.608 | 1.967 | 491 | 0 | 9 | 0 | 0 | 9 | 0 |
| calendar_list_calendars (warm) | 0.000 | 0.168 | 491 | 0 | 0 | 0 | 0 | 0 | 0 |
| calendar_list_events 7 days | 0.210 | 0.223 | 7633 | 85 | 5 | 0 | 0 | 5 | 0 |
| calendar_list_events 30 days | 0.245 | 0.264 | 12384 | 85 | 5 | 0 | 0 | 5 | 0 |
| calendar_list_events 30 days, fields=summary | 0.247 | 0.260 | 13208 | 85 | 5 | 0 | 0 | 5 | 0 |
| calendar_find_free_time 14 days | 0.223 | 0.231 | 2320 | 85 | 5 | 0 | 0 | 5 | 0 |
| contacts_search_contacts (cold) | 0.494 | 0.495 | 8539 | 72 | 5 | 0 | 0 | 0 | 5 |
| contacts_search_contacts (warm) | 0.002 | 0.003 | 8539 | 72 | 0 | 0 | 0 | 0 | 0 |
| calendar create + update + delete | 0.605 | 0.608 | 1397 | 0 | 7 | 0 | 0 | 7 | 0 |

Against the baseline (40 ms per round trip):

| scenario | baseline | phase 2a | change |
|---|---|---|---|
| `calendar_list_events` 7 days | 7 requests, 0.652 s | 5 requests, 0.210 s | -68 % time |
| `calendar_list_events` 30 days | 7 requests, 0.738 s, 26,017 bytes | 5 requests, 0.245 s, 12,384 bytes | 33 % of the time, -52 % bytes |
| `calendar_find_free_time` 14 days | 0.681 s | 0.223 s | -67 % |
| `mail_search_messages all_folders` | 0.622 s, 14,111 bytes | 0.342 s, 11,257 bytes | -45 % time, -20 % bytes |
| `mail_search_messages` 20 hits (same messages, computed offline) | 8,946 bytes | 6,827 bytes | -24 % |

The first attempt at parallel calendar reads opened new connections for them and made a week of events slower (1.6 s, 9
requests). A new CalDAV connection costs more than reading a few calendars one after another, so parallel reads now only borrow
idle pooled connections, and warm-up opens up to three spares in the background.

**Server-side recurrence expansion on iCloud** (plan 2.2), measured read-only on a real account with caldav 3.3: iCloud accepts
`server_expand` and its replies are 40 to 70 % smaller, but it turns all-day events into UTC date-times (a DATE start came back
as midnight UTC), which would break `all_day` and shift the day for anyone east or west of UTC. Expansion stays client-side.

## Phase 2b: partial mail fetches

`mail_get_messages` reads a message that carries more than 64 KB of non-text parts as a skeleton (every part's headers and the
text bodies, not the attachments' contents); `mail_get_attachment` fetches only the part asked for. Structures from a search
are remembered, so search-then-read costs no extra round trip.

| scenario | median s | p90 s | bytes | notice chars | tcp connects | imap commands | imap kb in | caldav requests | carddav requests |
|---|---|---|---|---|---|---|---|---|---|
| mail_search_messages 20 | 0.132 | 0.173 | 9938 | 85 | 0 | 3 | 12 | 0 | 0 |
| mail_search_messages 20 + get_messages 10 | 0.320 | 0.321 | 30276 | 249 | 0 | 7 | 55 | 0 | 0 |
| mail_search_messages all_folders | 0.337 | 0.442 | 11256 | 85 | 0 | 19 | 31 | 0 | 0 |
| mail_get_attachment (300 KB pdf) | 0.305 | 0.307 | 419759 | 170 | 0 | 7 | 424 | 0 | 0 |

| scenario | before | phase 2b |
|---|---|---|
| `mail_search_messages` 20 + `mail_get_messages` 10 (two with a 300 KB PDF) | 465 KB received from the IMAP server | 55 KB (12 %) |
| `mail_search_messages` 20 hits, result size (computed offline, same messages) | 8,946 bytes at 0.5.0 | 6,447 bytes (-28 %) |

Checked read-only against iCloud on three real messages with attachments: the skeleton gives the same text and the same
attachment list as the whole message, and every attachment fetched on its own is byte-for-byte identical. The largest message
was 1.3 MB whole and 26 KB as a skeleton.

## Phase 2c: contacts and the Mac helper

- **Contacts, warm search:** search fields are normalised once per card when it is loaded; a warm `contacts_search_contacts` went from
  3 ms to 1 ms in the CI bench.
- **Contacts after an edit elsewhere:** change detection (every card's ETag in one PROPFIND, then one addressbook-multiget of
  only the new or changed cards) was checked read-only against iCloud and rebuilds exactly what a full download gives. On a
  115-card address book it was *slower* than simply downloading again (0.77 s against 0.64 s: two requests instead of one small
  one), so it only runs for address books of 300 cards or more; smaller ones are downloaded again in one request, as before.
  The plan's "under 40 % of a full refetch" holds only for large address books.
- **Contacts writes** made through the connector update the cached address book in place, so the next search needs no download.
- **Mac helper:** one pinned HTTPS connection kept across polls instead of two TLS handshakes per job; Notes listings kept 30 s.
  The helper is not part of the CI bench (it needs a Mac); its connection reuse is covered by tests against the real bridge server.

## Phase 3: sturdiness

**Retry policy** (one place: `callctx.retry_once_if_safe`, used by mail and calendar; CardDAV keeps its one rediscovery of a
stale address-book URL). A call is retried once, and only when all of these hold:

1. the failure is the connection, not the request (a dead socket, a dropped TLS session, a timeout, an IMAP abort);
2. the connection came from a pool (a brand-new connection that fails is a real failure, reported as such);
3. nothing was written on this call yet: after a STORE, APPEND, EXPUNGE, PUT, DELETE or SMTP send the write may have landed,
   and repeating it could duplicate or double-delete.

The retry always opens a brand-new connection. Mail retries only its read calls (and `mail_get_message` until it marks the
message read). A send is never retried.

**Timeouts**: `TOOL_TIMEOUT_SECONDS` is 60 (was 90). Each service names the step in progress ("IMAP SELECT Archive", "CalDAV
search in 5 calendar(s)", "CardDAV address book", "waiting for the Mac helper (notes_list)"), and a timeout says which one was
slow. The Mac helper stops a job 3 seconds before the server stops waiting, so "the script did not finish" reaches the agent,
and the bridge tells "the Mac picked up the request but was slow" apart from "the Mac never picked it up".

**Partial reads**: list results carry `complete` and, when a source could not be read, `not_read` (a folder in an all-folder
search, a calendar the server refused). One broken calendar no longer fails a whole calendar read, and `calendar_find_free_time`
warns that its slots may not really be free when a calendar is missing.

## Phase 4: agents

**4a, instructions**: built from the tools actually registered, so a rule never names a tool the server does not offer (checked
for every combination of areas and modes); the security rules always come first; the owner's own rules from `AGENT_NOTES_FILE`
follow, capped at 8,000 characters and also served as the `icloud://agent-notes` resource. Five prompts cover the common
workflows. Instructions grew from 4,331 to 6,143 characters (cap 6,500).

**4b, tools that replace hand-written rules**: `icloud_get_time`, `mail_list_awaiting_reply`, conflict and duplicate checks on
`calendar_create_event`, `needs_reply` and `starting_within_minutes` on `calendar_list_events`, `add_attendees` /
`remove_attendees`, `people_only` / `unanswered_only` / `since_hours` on `mail_search_messages`, `add_emails` / `add_phones` on
`contacts_update_contact`. 67 tools; the parameter schemas reached 54,961 characters.

**4c, a leaner tool surface**: parameter schemas lose what carries no meaning for an agent (titles derived from parameter
names, `anyOf [X, null]` around optional parameters, `default: null`), and the longest shared descriptions were shortened
without dropping a warning (a test pins the safety sentences). An explicit `null` is still accepted: arguments are validated
by each tool's own model, not by the published schema.

| | 0.5.0 (65 tools) | after 4b (67 tools) | after 4c |
|---|---|---|---|
| schema chars | 51,951 | 54,961 | 37,956 (-27% on 0.5.0, -31% on 4b) |
| description chars | 18,006 | 18,740 | 18,740 |
| instructions chars | 4,331 | 6,143 | 6,143 |

### Tool surface after 4c (every area on)

| tool | description chars | schema chars |
|---|---|---|
| calendar_create_event | 457 | 2881 |
| calendar_update_event | 331 | 2377 |
| contacts_update_contact | 241 | 2347 |
| mail_reply_to_message | 689 | 1733 |
| contacts_create_contact | 224 | 1898 |
| mail_search_messages | 402 | 1716 |
| mail_send_message | 688 | 1204 |
| mail_run_bulk_action | 550 | 1285 |
| calendar_find_free_time | 468 | 1326 |
| mail_forward_message | 398 | 1258 |
| calendar_list_events | 518 | 1094 |
| contacts_search_contacts | 738 | 533 |
| drive_search_content | 464 | 631 |
| notes_update_note | 512 | 527 |
| mail_find_correspondent | 511 | 509 |

**67 tools**: 18,740 description chars, 37,956 schema chars; instructions 6,143 chars.

## Live check (0.6.0, 24 September 2026)

`dev/bench.py --live` against a real iCloud account from the Netherlands, 5 runs, scratch calendar for the write test. Compared
with the live reference measured by hand on 22 September:

| | 22 September | 0.6.0 |
|---|---|---|
| `calendar_list_events` 30 days | 3.1 s | 0.70 s |
| `calendar_list_events` 7 days | 2.6 s | 0.78 s |
| `calendar_list_calendars`, warm | 0.64 s | under 1 ms |
| create + update + delete one event | 4.8 s | 3.7 s |
| `contacts_search_contacts` warm / cold | 2 ms / 1.7 s | 1 ms / 1.8 s |
| `mail_search_messages` 20 | | 1.1 s |

With 20 seconds between calls, 10 mail searches needed one login in total. The calendar keep-alive kept most calls warm,
but a few still reconnected (slowest 5.5 s).

## 0.7.0: names

Eleven tools renamed to a strict `area_verb_noun` pattern (tested against a fixed verb list), attachment sources described on
`mail_send_message` / `mail_reply_to_message`, and a line in the instructions saying what the Mac helper adds when its areas are off. Schema
37,900 characters (budget 38,000), instructions 6,153.

## 0.9.0 and 0.10.0: budget for new tools

About 21 tools arrive across these two releases (drafts and folders, calendars, contact groups, reminders, Maps, iMessage).
The parameter-schema budget in `tests/test_tool_surface.py` goes from 38,000 to 52,000 characters and the instructions cap in
`tests/test_instructions.py` from 6,500 to 8,000. Clients that load too much can still use `TOOLS=essential` or an area preset.
After the mail drafts and folders PR: 71 tools, 40,283 schema characters, instructions 6,393.

## Results sent once, compact

Each tool result is now serialized once, in the worker thread after cleaning, as one compact JSON text block: no indented copy
plus a structuredContent duplicate, no outputSchema on any tool, and no block per list item (`MCP_STRUCTURED_CONTENT=true` adds
structuredContent back for a client that needs it). Tool descriptions lose their docstring indentation, and the HTTP transport
answers each request with a plain JSON body instead of an event stream (`MCP_JSON_RESPONSE`, default true; the tool timeout is
then capped at 90 s, below Cloudflare's 100 s). With every area on (`dev/tool_surface.py`): description characters 23,662 to
21,682, the `tools/list` result 93,182 to 82,035 bytes. Bench figures follow.
