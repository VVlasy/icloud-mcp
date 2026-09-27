"""Connectivity self-test.

    python -m icloud_mcp.selftest                       # IMAP + SMTP login + CalDAV, sends nothing
    python -m icloud_mcp.selftest --probe-sent me@x.com # also sends ONE test email to find out whether
                                                        # iCloud files sent mail in Sent by itself

Reads the same environment variables as the server (ICLOUD_USERNAME, ICLOUD_APP_PASSWORD, ...).
"""
from __future__ import annotations

import argparse
import sys
import time

from .cal import CalendarService
from .config import Settings
from .contacts import ContactsService
from .mail import MailService, build_message, parse_addrs

OK, BAD = "[ ok ]", "[FAIL]"


def _step(label: str, fn) -> bool:
    try:
        detail = fn()
        print(f"{OK} {label}" + (f": {detail}" if detail else ""))
        return True
    except Exception as e:  # noqa: BLE001
        print(f"{BAD} {label}: {type(e).__name__}: {e}")
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe-sent", metavar="ADDRESS", help="send one test email to ADDRESS and check the Sent folder for duplicates")
    args = ap.parse_args()

    s = Settings.from_env()
    s.validate_for_mail_calendar()
    mail, cal = MailService(s), CalendarService(s)
    results: list[bool] = []

    def imap_check() -> str:
        with mail.imap() as c:
            names = [f[2] for f in c.list_folders()]
            inbox = c.select_folder("INBOX", readonly=True)
            special = {a: mail.resolve_folder(c, a) for a in ("sent", "drafts", "trash")}
            return f"{len(names)} folders, INBOX has {inbox[b'EXISTS']} messages, special folders: {special}"

    def smtp_check() -> str:
        with mail._smtp_open():                   # the server's own connect path and timeouts: TLS, EHLO, LOGIN, then QUIT
            pass
        return f"logged in to {s.smtp_host}:{s.smtp_port} as {s.smtp_username} (nothing sent)"

    def cal_check() -> str:
        cals = cal.list_calendars()
        return f"{len(cals)} event calendar(s): {', '.join(c['name'] for c in cals)}"

    if s.enable_mail:
        results.append(_step("IMAP login + folders", imap_check))
        results.append(_step("SMTP login", smtp_check))
    if s.enable_calendar:
        results.append(_step("CalDAV login + calendars", cal_check))

    if s.enable_contacts:
        def contacts_check() -> str:
            r = ContactsService(s).search("", limit=1)
            return f"{r['total_matches']} contact(s) readable"

        results.append(_step("CardDAV login + contacts", contacts_check))

    if args.probe_sent and s.enable_mail:
        def probe() -> str:
            to = parse_addrs(args.probe_sent)
            if not to:
                raise ValueError("invalid address")
            msg = build_message(sender=mail.sender, to=to, subject="icloud-mcp Sent-folder probe", text="Test message from icloud-mcp selftest. You can delete it.")
            mid = str(msg["Message-ID"])
            mail._smtp_send(msg, [a for _, a in to])  # deliberately NOT appending to Sent
            with mail.imap() as c:
                sent = mail.resolve_folder(c, "sent")
                for _ in range(6):
                    time.sleep(5)
                    c.select_folder(sent, readonly=True)
                    if c.search(["HEADER", "Message-ID", mid]):
                        return f"iCloud DID file the message in '{sent}' by itself -> set SAVE_SENT_COPY=false to avoid duplicates"
            return f"iCloud did NOT file the message in '{sent}' -> keep SAVE_SENT_COPY=true (default)"

        results.append(_step("Sent-folder probe", probe))

    print()
    print("All checks passed." if all(results) else "Some checks failed; fix them before starting the server.")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
