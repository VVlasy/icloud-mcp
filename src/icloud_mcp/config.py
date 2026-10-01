"""Environment-driven configuration."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse


# Where OAuth may send a signed-in client back to: Claude (claude.ai, claude.com), ChatGPT (chatgpt.com, chat.openai.com)
# and local clients such as Codex CLI (127.0.0.1, localhost). Every sign-in still needs the owner password.
DEFAULT_REDIRECT_HOSTS = "claude.ai,claude.com,chatgpt.com,chat.openai.com,localhost,127.0.0.1"

def _str(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else v.strip()


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


OVERRIDES_FILE = "overrides.json"      # in DATA_DIR: credentials changed from the admin API (the menu bar app)
OVERRIDABLE = {"icloud_app_password": "ICLOUD_APP_PASSWORD", "owner_password": "MCP_OWNER_PASSWORD"}


def read_overrides(data_dir: str) -> dict[str, str]:
    """Credentials the owner changed through the admin API. They take precedence over the environment, so a changed passcode
    or app-specific password works the same whether the server runs under launchd, Docker or uvx. Unreadable = none."""
    import json
    try:
        with open(os.path.join(data_dir, OVERRIDES_FILE)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: v.strip() for k, v in data.items() if k in OVERRIDABLE and isinstance(v, str) and v.strip()} if isinstance(data, dict) else {}


def owner_password_problem(value: str, bridge_token: str = "") -> str | None:
    """Why a passcode cannot be used, or None. The same rules for the environment and the admin API."""
    if len(value) < 12:
        return "The owner passcode must be at least 12 characters long."
    if "change-me" in value.lower():
        return "The owner passcode still has the placeholder value."
    if bridge_token and value == bridge_token:
        return "The owner passcode must differ from BRIDGE_TOKEN."
    return None


def _norm_handle(value: str) -> str:
    """A Messages handle in the form chat.db uses: emails lower-case, phone numbers as +digits (spaces, dashes, dots and brackets
    dropped); anything else (a group chat id) as given."""
    v = value.strip()
    if "@" in v:
        return v.lower()
    digits = re.sub(r"[\s().-]", "", v)
    return digits if re.fullmatch(r"\+?\d{5,}", digits) else v


def _int(name: str, default: int) -> int:
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else int(v)


def keychain_password(account: str, service: str = "icloud-mcp") -> str:
    """The app-specific password stored in the macOS login Keychain (see `icloud-mcp --store-password`), or ''.
    Only consulted when ICLOUD_APP_PASSWORD is not set, so the password never has to sit in a file or a client config."""
    import subprocess
    import sys

    if sys.platform != "darwin" or not account or os.environ.get("ICLOUD_KEYCHAIN", "true").lower() in ("0", "false", "no", "off"):
        return ""
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", service, "-a", account, "-w"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _list(name: str, default: str = "") -> list[str]:
    return [p.strip() for p in _str(name, default).split(",") if p.strip()]


@dataclass(frozen=True)
class Settings:
    # --- iCloud credentials -------------------------------------------------
    username: str                 # Apple ID used to log in
    app_password: str             # app-specific password (xxxx-xxxx-xxxx-xxxx)
    email_address: str            # From address (defaults to username)
    display_name: str
    signature: str                # plain-text signature appended to outgoing mail
    # --- mail transport -------------------------------------------------------
    imap_host: str
    imap_port: int
    imap_security: str            # ssl | starttls | none
    imap_username: str
    smtp_host: str
    smtp_port: int
    smtp_security: str            # starttls | ssl | none
    smtp_username: str
    save_sent_copy: bool
    allow_permanent_delete: bool
    max_recipients: int
    send_allowlist: tuple[str, ...]
    max_body_chars: int
    max_attachment_bytes: int
    # --- calendar -------------------------------------------------------------
    caldav_url: str
    caldav_username: str
    caldav_require_tls: bool
    default_timezone: str
    default_calendar: str         # calendar new events go to when none is named ('' = auto)
    # --- feature switches -----------------------------------------------------
    enable_mail: bool
    enable_calendar: bool
    enable_contacts: bool
    enable_reminders: bool        # Reminders via the Mac helper (opt-in; needs BRIDGE_TOKEN)
    enable_notes: bool            # Notes via the Mac helper (opt-in; needs BRIDGE_TOKEN)
    enable_drive: bool            # iCloud Drive via the Mac helper (opt-in; needs BRIDGE_TOKEN)
    bridge_token: str             # shared secret of the Mac helper
    bridge_port: int              # private HTTPS port the Mac helper polls (never served on the public port)
    bridge_tls_names: tuple[str, ...]
    bridge_job_timeout: int       # seconds to wait for the Mac before a tool call fails
    carddav_url: str
    carddav_username: str
    read_only: bool
    allow_send: bool
    require_approval: bool        # outgoing mail is queued until the owner approves it on /outbox
    outbox_ttl: int               # seconds a queued message stays approvable
    outbox_max: int               # max messages waiting for approval
    allow_calendar_invites: bool  # calendar writes that make iCloud email other people (attendees)
    # --- MCP server / OAuth ---------------------------------------------------
    public_url: str
    owner_password: str
    data_dir: str
    host: str
    port: int
    stateless_http: bool          # no server-side MCP sessions: a restart never invalidates a client's connection
    tool_timeout: int             # seconds before a tool call is abandoned with an error instead of hanging
    allowed_redirect_hosts: tuple[str, ...]
    access_token_ttl: int
    refresh_token_ttl: int
    bridge_host: str = "127.0.0.1"  # address the bridge port binds to inside this process (0.0.0.0 only inside Docker, set by the image)
    local_mode: bool = False      # stdio for a desktop client on this computer: no OAuth, no public URL, no browser outbox
    tools: tuple[str, ...] = ()   # TOOLS: 'essential' and/or tool names to expose; empty = every tool of the enabled areas
    imap_pool_size: int = 3       # IMAP_POOL_SIZE: logged-in IMAP connections kept for reuse (0 = log in for every call)
    imap_idle_seconds: int = 600  # IMAP_IDLE_SECONDS: a pooled connection unused for longer is closed instead of reused
    caldav_pool_size: int = 4     # CALDAV_POOL_SIZE: CalDAV connections kept for reuse (calendars are read in parallel)
    caldav_keepalive_seconds: int = 600   # CALDAV_KEEPALIVE_SECONDS: keep pooled CalDAV connections warm this long after the last call (0 = off)
    enable_maps: bool = False     # ENABLE_MAPS: Apple Maps travel times and place search via the Mac helper (needs BRIDGE_TOKEN)
    enable_imessage: bool = False  # ENABLE_IMESSAGE: read and search the owner's own iMessage history via the Mac helper
    enable_health: bool = False    # ENABLE_HEALTH: daily Apple Health figures from the owner's iPhone exports, via the Mac helper
    imessage_hidden_chats: tuple[str, ...] = ()    # IMESSAGE_HIDDEN_CHATS: chat ids (handles) never listed, read or searched
    imessage_visible_chats: tuple[str, ...] = ()   # IMESSAGE_VISIBLE_CHATS: when set, only these chats
    imessage_max_age_days: int = 0                 # IMESSAGE_MAX_AGE_DAYS: 0 = the whole history (default); e.g. 365 limits it
    imessage_hide_short_codes: bool = True         # IMESSAGE_HIDE_SHORT_CODES: hide service senders (banks, one-time codes): not email, not a full number
    imessage_never_send: tuple[str, ...] = ()      # IMESSAGE_NEVER_SEND: handles or chat ids nothing is ever sent to (e.g. an assistant's own Apple ID)
    imessage_allow_send: bool = False              # IMESSAGE_ALLOW_SEND: register imessage_send_message at all
    imessage_send_requires_approval: bool = True   # IMESSAGE_SEND_REQUIRES_APPROVAL: every iMessage waits for the owner (independent of mail)
    imessage_send_allowlist: tuple[str, ...] = ()  # IMESSAGE_SEND_ALLOWLIST: who may receive; empty = nobody, "*" = anyone
    warmup_on_start: bool = True  # WARMUP_ON_START: log in to mail, calendar and contacts in the background right after start
    tool_workers: int = 8         # TOOL_WORKERS: threads that run tool calls, so parallel calls do not queue behind each other
    agent_notes_file: str = ""    # AGENT_NOTES_FILE: the owner's own rules for agents, appended to the instructions (never shipped)
    invite_allowlist: tuple[str, ...] = ()   # INVITE_ALLOWLIST: addresses/domains that may be invited when invites are on (empty = anyone)
    max_attendees: int = 10                 # MAX_ATTENDEES: most guests one event may carry through the connector
    allow_unsubscribe_links: bool = False          # ALLOW_UNSUBSCRIBE_LINKS: open unsubscribe web pages after the user confirms
    unsubscribe_links_confirm: bool = True         # UNSUBSCRIBE_LINKS_CONFIRM: false = open them on the first call, no preview
    contacts_allow_email_changes: bool = True  # CONTACTS_ALLOW_EMAIL_CHANGES: false = agents cannot add or replace emails/phones on cards
    mail_max_age_days: int = 0              # MAIL_MAX_AGE_DAYS: 0 = whole mailbox; N = searches never reach further back than N days
    safety_screen: str = ""                 # SAFETY_SCREEN: "" (built-in patterns) or "command:<path>" (the owner's own classifier)
    owner_addresses: tuple[str, ...] = ()   # OWNER_ADDRESSES: more addresses that are the owner's (aliases), e.g. on invitations
    shortcuts_allow: tuple[str, ...] = ()   # SHORTCUTS_ALLOW: exact Shortcut names the assistant may run (the Mac keeps its own list too)
    admin_port: int = 0           # ADMIN_PORT: loopback-only admin API for the menu bar app (0 = off); never the tunnelled port
    overrides_active: tuple[str, ...] = ()  # which OVERRIDABLE settings come from DATA_DIR/overrides.json (names only)

    @classmethod
    def from_env(cls) -> "Settings":
        username = _str("ICLOUD_USERNAME")
        read_only = _bool("READ_ONLY", False)
        overrides = read_overrides(_str("DATA_DIR", "./data"))
        return cls(
            username=username,
            app_password=(overrides.get("icloud_app_password") or _str("ICLOUD_APP_PASSWORD")
                          or keychain_password(username, _str("ICLOUD_KEYCHAIN_SERVICE", "icloud-mcp"))).replace(" ", ""),
            email_address=_str("ICLOUD_EMAIL_ADDRESS", username),
            display_name=_str("ICLOUD_DISPLAY_NAME"),
            signature=_str("EMAIL_SIGNATURE").replace("\\n", "\n"),
            imap_host=_str("IMAP_HOST", "imap.mail.me.com"),
            imap_port=_int("IMAP_PORT", 993),
            imap_security=_str("IMAP_SECURITY", "ssl").lower(),
            imap_username=_str("IMAP_USERNAME", username),
            smtp_host=_str("SMTP_HOST", "smtp.mail.me.com"),
            smtp_port=_int("SMTP_PORT", 587),
            smtp_security=_str("SMTP_SECURITY", "starttls").lower(),
            smtp_username=_str("SMTP_USERNAME", username),
            save_sent_copy=_bool("SAVE_SENT_COPY", True),
            allow_permanent_delete=_bool("ALLOW_PERMANENT_DELETE", False),
            allow_unsubscribe_links=_bool("ALLOW_UNSUBSCRIBE_LINKS", False) and not read_only,
            unsubscribe_links_confirm=_bool("UNSUBSCRIBE_LINKS_CONFIRM", True),
            max_recipients=_int("MAX_RECIPIENTS", 25),
            send_allowlist=tuple(x.lower() for x in _list("SEND_ALLOWLIST")),
            max_body_chars=_int("MAX_BODY_CHARS", 30000),
            max_attachment_bytes=_int("MAX_ATTACHMENT_BYTES", 5 * 1024 * 1024),
            caldav_url=_str("CALDAV_URL", "https://caldav.icloud.com"),
            caldav_username=_str("CALDAV_USERNAME", username),
            caldav_require_tls=_bool("CALDAV_REQUIRE_TLS", True),
            default_timezone=_str("DEFAULT_TIMEZONE", "UTC"),
            default_calendar=_str("DEFAULT_CALENDAR"),
            enable_mail=_bool("ENABLE_MAIL", True),
            enable_calendar=_bool("ENABLE_CALENDAR", True),
            enable_contacts=_bool("ENABLE_CONTACTS", True),
            enable_reminders=_bool("ENABLE_REMINDERS", False),
            enable_notes=_bool("ENABLE_NOTES", False),
            enable_drive=_bool("ENABLE_DRIVE", False),
            bridge_token=_str("BRIDGE_TOKEN"),
            bridge_port=_int("BRIDGE_PORT", 8001),
            bridge_tls_names=tuple(_list("BRIDGE_TLS_NAMES")),
            bridge_job_timeout=_int("BRIDGE_JOB_TIMEOUT_SECONDS", 60),
            carddav_url=_str("CARDDAV_URL", "https://contacts.icloud.com"),
            carddav_username=_str("CARDDAV_USERNAME", username),
            read_only=read_only,
            allow_send=_bool("ALLOW_SEND", True) and not read_only,   # READ_ONLY means no sending and no drafts either
            require_approval=_bool("SEND_REQUIRES_APPROVAL", True),
            outbox_ttl=_int("OUTBOX_TTL_SECONDS", 24 * 3600),
            outbox_max=_int("OUTBOX_MAX", 20),
            allow_calendar_invites=_bool("ALLOW_CALENDAR_INVITES", False),
            public_url=_str("MCP_PUBLIC_URL").rstrip("/"),
            owner_password=overrides.get("owner_password") or _str("MCP_OWNER_PASSWORD"),
            data_dir=_str("DATA_DIR", "./data"),
            host=_str("MCP_HOST", "127.0.0.1"),
            port=_int("MCP_PORT", 8000),
            stateless_http=_bool("MCP_STATELESS", True),
            tool_timeout=_int("TOOL_TIMEOUT_SECONDS", 60),
            allowed_redirect_hosts=tuple(
                h.lower() for h in _list("OAUTH_ALLOWED_REDIRECT_HOSTS", DEFAULT_REDIRECT_HOSTS)
            ),
            access_token_ttl=_int("ACCESS_TOKEN_TTL", 3600),
            refresh_token_ttl=_int("REFRESH_TOKEN_TTL", 60 * 60 * 24 * 30),
            bridge_host=_str("BRIDGE_HOST", "127.0.0.1"),
            tools=tuple(_list("TOOLS")),
            imap_pool_size=max(0, min(_int("IMAP_POOL_SIZE", 3), 8)),
            imap_idle_seconds=max(30, _int("IMAP_IDLE_SECONDS", 600)),
            caldav_pool_size=max(1, min(_int("CALDAV_POOL_SIZE", 4), 8)),
            caldav_keepalive_seconds=max(0, _int("CALDAV_KEEPALIVE_SECONDS", 600)),
            enable_maps=_bool("ENABLE_MAPS", False),
            enable_imessage=_bool("ENABLE_IMESSAGE", False),
            enable_health=_bool("ENABLE_HEALTH", False),
            imessage_hidden_chats=tuple(_norm_handle(x) for x in _list("IMESSAGE_HIDDEN_CHATS") if x.strip()),
            imessage_visible_chats=tuple(_norm_handle(x) for x in _list("IMESSAGE_VISIBLE_CHATS") if x.strip()),
            imessage_max_age_days=max(0, _int("IMESSAGE_MAX_AGE_DAYS", 0)),
            imessage_hide_short_codes=_bool("IMESSAGE_HIDE_SHORT_CODES", True),
            imessage_never_send=tuple(_norm_handle(x) for x in _list("IMESSAGE_NEVER_SEND") if x.strip()),
            imessage_allow_send=_bool("IMESSAGE_ALLOW_SEND", False),
            imessage_send_requires_approval=_bool("IMESSAGE_SEND_REQUIRES_APPROVAL", True),
            imessage_send_allowlist=tuple(x.strip() if x.strip() == "*" else _norm_handle(x) for x in _list("IMESSAGE_SEND_ALLOWLIST") if x.strip()),
            warmup_on_start=_bool("WARMUP_ON_START", True),
            tool_workers=max(2, min(_int("TOOL_WORKERS", 8), 32)),
            agent_notes_file=_str("AGENT_NOTES_FILE"),
            invite_allowlist=tuple(x.lower() for x in _list("INVITE_ALLOWLIST")),
            max_attendees=max(1, _int("MAX_ATTENDEES", 10)),
            contacts_allow_email_changes=_bool("CONTACTS_ALLOW_EMAIL_CHANGES", True),
            mail_max_age_days=max(0, _int("MAIL_MAX_AGE_DAYS", 0)),
            safety_screen=_str("SAFETY_SCREEN"),
            owner_addresses=tuple(a.strip().lower() for a in _list("OWNER_ADDRESSES") if a.strip()),
            shortcuts_allow=tuple(n.strip() for n in _str("SHORTCUTS_ALLOW").split(";" if ";" in _str("SHORTCUTS_ALLOW") else ",") if n.strip()),
            admin_port=max(0, _int("ADMIN_PORT", 0)),
            overrides_active=tuple(OVERRIDABLE[k] for k in sorted(overrides)),
        )

    # ------------------------------------------------------------------
    @property
    def own_addresses(self) -> set[str]:
        """Every address that is the owner: the account address, the Apple ID, the mail logins and OWNER_ADDRESSES."""
        return {a.strip().lower() for a in (self.email_address, self.username, self.imap_username, self.smtp_username,
                                            *self.owner_addresses) if a and "@" in a}

    @property
    def bridge_enabled(self) -> bool:
        return (self.enable_reminders or self.enable_notes or self.enable_drive or self.enable_maps or self.enable_imessage
                or self.enable_health or bool(self.shortcuts_allow))

    @property
    def public_host(self) -> str:
        return urlparse(self.public_url).netloc

    def validate_for_mail_calendar(self) -> None:
        missing = [n for n, v in (("ICLOUD_USERNAME", self.username), ("ICLOUD_APP_PASSWORD", self.app_password)) if not v]
        if missing:
            raise SystemExit(f"Missing required environment variable(s): {', '.join(missing)}")

    def _refuse_placeholders(self, checks: list[tuple[str, bool]]) -> None:
        stale = [name for name, is_placeholder in checks if is_placeholder]
        if stale:
            raise SystemExit(f"{', '.join(stale)} still has the placeholder value from .env.example. Set your own value.")

    def _validate_bridge_and_areas(self) -> None:
        if self.bridge_enabled:
            if len(self.bridge_token) < 32 or "change-me" in self.bridge_token.lower():
                raise SystemExit("ENABLE_REMINDERS / ENABLE_NOTES / ENABLE_DRIVE / ENABLE_MAPS / ENABLE_IMESSAGE / ENABLE_HEALTH / SHORTCUTS_ALLOW need BRIDGE_TOKEN: a random secret of at least 32 characters "
                                 "(for example `python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"`).")
            if self.owner_password and self.bridge_token == self.owner_password:
                raise SystemExit("BRIDGE_TOKEN must differ from MCP_OWNER_PASSWORD.")
        if not (self.enable_mail or self.enable_calendar or self.enable_contacts):
            raise SystemExit("ENABLE_MAIL, ENABLE_CALENDAR and ENABLE_CONTACTS are all false; nothing to serve.")

    def validate_for_local(self) -> None:
        """Local (stdio) mode: the desktop client on this computer starts the server itself, so there is no public URL or owner password."""
        self.validate_for_mail_calendar()
        self._refuse_placeholders([
            ("ICLOUD_APP_PASSWORD", "xxxx-xxxx" in self.app_password.lower()),
            ("ICLOUD_USERNAME", self.username.lower() == "you@icloud.com"),
        ])
        self._validate_bridge_and_areas()

    def validate_for_server(self) -> None:
        self.validate_for_mail_calendar()
        # Refuse to run with the untouched values from .env.example: a public mailbox server must not start with a guessable password.
        placeholders = [
            ("MCP_OWNER_PASSWORD", "change-me" in self.owner_password.lower()),
            ("ICLOUD_APP_PASSWORD", "xxxx-xxxx" in self.app_password.lower()),
            ("ICLOUD_USERNAME", self.username.lower() == "you@icloud.com"),
            ("MCP_PUBLIC_URL", (urlparse(self.public_url).hostname or "") == "icloud-mcp.example.com"),
        ]
        self._refuse_placeholders(placeholders)
        if not self.public_url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise SystemExit("MCP_PUBLIC_URL must be the public https:// URL of this server (no trailing path).")
        if len(self.owner_password) < 12:
            raise SystemExit("MCP_OWNER_PASSWORD must be set and at least 12 characters long.")
        if self.admin_port and self.admin_port in (self.port, self.bridge_port):
            raise SystemExit("ADMIN_PORT must differ from MCP_PORT and BRIDGE_PORT: the admin API is never served on another port.")
        self._validate_bridge_and_areas()
