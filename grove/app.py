"""
Grove — Sovereign Messaging TUI
Human-to-human over u2u. Launch: python3 -m grove
"""

import asyncio
import os
import socket
from datetime import datetime
from pathlib import Path

from rich.markup import escape as _escape_markup

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, RichLog, Rule, Static
from textual import on, work

import grove_db as db
from u2u import dispatcher
from u2u.consent import ConsentGate
from u2u.contacts import Contact, ContactStore
from u2u.identity import Identity
from u2u.listener import U2UListener
from u2u.packets import PacketType
from u2u.sender import send_packet

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_IDENTITY_PATH = Path.home() / ".willow" / "grove_identity.json"
_CONTACTS_PATH = Path.home() / ".willow" / "grove_contacts.json"
try:
    _PORT = int(os.getenv("GROVE_PORT", "8550"))
    if not (1 <= _PORT <= 65535):
        raise ValueError
except ValueError:
    _PORT = 8550
_NAME          = os.getenv("GROVE_NAME", os.getenv("USER", "me"))


def _resolve_host() -> str:
    if h := os.getenv("GROVE_HOST"):
        return h
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        pass
    try:
        return socket.gethostname()
    except OSError:
        return "localhost"


_HOST    = _resolve_host()
_MY_ADDR = f"{_NAME}@{_HOST}:{_PORT}"

_SENDER_COLORS = ["cyan", "magenta", "yellow", "bright_green", "bright_blue", "bright_red", "bright_cyan"]
_MY_COLOR      = "green"


def _sender_color(addr: str) -> str:
    return _MY_COLOR if addr == _MY_ADDR else _SENDER_COLORS[hash(addr) % len(_SENDER_COLORS)]


def _display_name(addr: str, contacts: ContactStore) -> str:
    if addr == _MY_ADDR:
        return _NAME
    c = contacts.get(addr)
    return (c.name or addr.split("@")[0]) if c else addr.split("@")[0]


def _fmt_time(ts) -> str:
    if isinstance(ts, datetime):
        return ts.strftime("%H:%M")
    return ""


# ---------------------------------------------------------------------------
# TUI messages
# ---------------------------------------------------------------------------

class NoteReceived(Message):
    def __init__(self, from_addr: str, body: str):
        super().__init__()
        self.from_addr = from_addr
        self.body = body


class KnockReceived(Message):
    def __init__(self, from_addr: str, public_key: str):
        super().__init__()
        self.from_addr = from_addr
        self.public_key = public_key


class UnknownNoteAttempt(Message):
    """Someone tried to message us but isn't in our contacts."""
    def __init__(self, from_addr: str):
        super().__init__()
        self.from_addr = from_addr


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class ContactItem(ListItem):
    DEFAULT_CSS = """
    ContactItem { padding: 0 1; height: 2; }
    ContactItem .name { text-style: bold; }
    ContactItem .addr { color: $text-muted; }
    ContactItem:hover  { background: $boost; }
    ContactItem.--highlight { background: $accent 20%; }
    """

    def __init__(self, contact: Contact):
        super().__init__()
        self.contact = contact

    def compose(self) -> ComposeResult:
        name = self.contact.name or self.contact.addr.split("@")[0]
        suffix = " [dim](history)[/dim]" if not self.contact.public_key_hex else ""
        yield Label(name + suffix, classes="name", markup=True)
        yield Label(self.contact.addr, classes="addr")


class ChannelItem(ListItem):
    DEFAULT_CSS = """
    ChannelItem { padding: 0 1; height: 1; }
    ChannelItem .ch-name { color: $accent; }
    ChannelItem:hover  { background: $boost; }
    ChannelItem.--highlight { background: $accent 20%; }
    """

    def __init__(self, channel: dict):
        super().__init__()
        self.channel = channel

    def compose(self) -> ComposeResult:
        yield Label(f"# {self.channel['name']}", classes="ch-name", markup=False)


class PendingItem(ListItem):
    DEFAULT_CSS = """
    PendingItem        { padding: 0 1; height: 1; color: $warning; }
    PendingItem:hover  { background: $boost; }
    """

    def __init__(self, from_addr: str, public_key: str):
        super().__init__(Label(f"⚡ {from_addr.split('@')[0]}"))
        self.from_addr  = from_addr
        self.public_key = public_key


class SectionLabel(Static):
    DEFAULT_CSS = """
    SectionLabel {
        color: $text-muted;
        text-style: bold;
        padding: 1 1 0 1;
    }
    """


class Sidebar(Vertical):
    DEFAULT_CSS = """
    Sidebar {
        width: 28;
        background: $panel;
        border-right: solid $primary-darken-3;
    }
    Sidebar #channel-list  { height: auto; max-height: 8; }
    Sidebar #contact-list  { height: auto; max-height: 10; }
    Sidebar #pending-list  { height: auto; max-height: 6; }
    Sidebar #knock-input   {
        margin: 1 1 0 1;
        border: tall $primary-darken-2;
    }
    Sidebar #knock-label   {
        color: $text-muted;
        padding: 1 1 0 1;
        text-style: bold;
    }
    """

    def compose(self) -> ComposeResult:
        yield SectionLabel("CHANNELS")
        yield ListView(id="channel-list")
        yield Rule(line_style="heavy")
        yield SectionLabel("CONVERSATIONS")
        yield ListView(id="contact-list")
        yield Rule(line_style="heavy")
        yield SectionLabel("PENDING")
        yield ListView(id="pending-list")
        yield Rule(line_style="heavy")
        yield Label("+ knock", id="knock-label")
        yield Input(placeholder="addr@host:port", id="knock-input")


class ConvHeader(Static):
    DEFAULT_CSS = """
    ConvHeader {
        height: 3;
        padding: 0 2;
        background: $panel;
        border-bottom: solid $primary-darken-3;
        color: $text;
    }
    ConvHeader .ch-name { text-style: bold; color: $accent; }
    ConvHeader .ch-addr { color: $text-muted; }
    """

    def __init__(self):
        super().__init__()
        self._contact: Contact | None = None

    def set_contact(self, contact: Contact) -> None:
        self._contact = contact
        name = contact.name or contact.addr.split("@")[0]
        self.update(
            f"[bold $accent]{name}[/bold $accent]\n"
            f"[dim]{contact.addr}[/dim]"
        )

    def clear(self) -> None:
        self._contact = None
        self.update("[dim]Select a contact[/dim]")


class ConvPane(Vertical):
    DEFAULT_CSS = """
    ConvPane {
        width: 1fr;
        background: $background;
    }
    ConvPane #msg-log   {
        height: 1fr;
        padding: 1 2;
    }
    ConvPane #msg-input {
        margin: 0 2 1 2;
        border: tall $primary-darken-2;
    }
    ConvPane #msg-input:focus {
        border: tall $accent;
    }
    """

    def compose(self) -> ComposeResult:
        yield ConvHeader()
        yield RichLog(id="msg-log", highlight=False, markup=True, wrap=True)
        yield Input(placeholder="Message…", id="msg-input")


# ---------------------------------------------------------------------------
# Identity modal
# ---------------------------------------------------------------------------

class IdentityModal(ModalScreen):
    DEFAULT_CSS = """
    IdentityModal {
        align: center middle;
    }
    IdentityModal > Vertical {
        width: 70;
        height: auto;
        background: $panel;
        border: double $accent;
        padding: 1 2;
    }
    IdentityModal .im-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    IdentityModal .im-label {
        color: $text-muted;
        text-style: bold;
    }
    IdentityModal .im-value {
        margin-bottom: 1;
    }
    IdentityModal .im-hint {
        color: $text-muted;
        text-style: italic;
        margin-top: 1;
    }
    """

    def __init__(self, addr: str, pubkey: str):
        super().__init__()
        self._addr   = addr
        self._pubkey = pubkey

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("MY IDENTITY", classes="im-title")
            yield Label("Address", classes="im-label")
            yield Label(self._addr, classes="im-value")
            yield Label("Public Key", classes="im-label")
            yield Label(self._pubkey, classes="im-value")
            yield Label("Share these with a peer so they can knock you.  Esc to close", classes="im-hint")

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class GroveApp(App):
    TITLE    = "Grove"
    CSS_PATH = None
    BINDINGS = [
        Binding("ctrl+c", "quit",         "Quit",        show=True),
        Binding("ctrl+k", "focus_knock",  "Knock",       show=True),
        Binding("ctrl+n", "focus_msg",    "Message",     show=True),
        Binding("f1",     "show_identity","Identity",    show=True),
        Binding("escape", "blur",         "Blur",        show=False),
    ]
    DEFAULT_CSS = """
    Screen  { layout: horizontal; background: $background; }
    Header  { background: $panel; color: $accent; text-style: bold; }
    Footer  { background: $panel; }
    Rule    { margin: 0; color: $primary-darken-3; }
    """

    def __init__(self):
        super().__init__()
        self.identity       = Identity.load_or_generate(_IDENTITY_PATH)
        self.contacts       = ContactStore(_CONTACTS_PATH)
        self.active_contact: Contact | None = None
        self._conn          = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            yield Sidebar()
            yield ConvPane()
        yield Footer()

    def on_mount(self) -> None:
        self._conn = db.get_connection()
        db.init_schema(self._conn)
        self.sub_title = f"{_MY_ADDR}"
        self.query_one(ConvHeader).clear()
        self._refresh_channels()
        self._refresh_contacts()
        self._start_listener()

    def on_unmount(self) -> None:
        if self._conn:
            db.release_connection(self._conn)

    # ── U2U listener ─────────────────────────────────────────────────────────

    @work(name="u2u-listener")
    async def _start_listener(self) -> None:
        gate = ConsentGate(self.contacts)

        def _on_note(packet: dict) -> None:
            h         = packet["header"]
            pl        = packet.get("payload", {})
            from_addr = h.get("from", "unknown")
            if h.get("_denied"):
                self.post_message(UnknownNoteAttempt(from_addr))
                return
            body = pl.get("body", "")
            ch = _dm_channel(self._conn, from_addr)
            db.send_message(self._conn, channel_id=ch["id"], sender=from_addr, content=body)
            self.post_message(NoteReceived(from_addr, body))

        def _on_knock(packet: dict) -> None:
            h  = packet["header"]
            pl = packet.get("payload", {})
            self.post_message(KnockReceived(
                from_addr=h.get("from", "unknown"),
                public_key=pl.get("public_key", ""),
            ))

        dispatcher.register(PacketType.NOTE,  _on_note)
        dispatcher.register(PacketType.KNOCK, _on_knock)

        listener = U2UListener(host="0.0.0.0", port=_PORT,
                               identity=self.identity, consent=gate)
        try:
            async with listener.serve():
                await asyncio.Event().wait()
        except (OSError, ValueError) as e:
            self.notify(
                f"Could not bind port {_PORT} — set GROVE_PORT to use a different port.\n{e}",
                title="Listener failed",
                severity="error",
                timeout=0,
            )

    # ── Incoming handlers ────────────────────────────────────────────────────

    def on_note_received(self, event: NoteReceived) -> None:
        if self.active_contact and event.from_addr == self.active_contact.addr:
            self._append_msg(event.from_addr, event.body)
        else:
            name    = _display_name(event.from_addr, self.contacts)
            preview = event.body[:120] + "…" if len(event.body) > 120 else event.body
            self.notify(preview, title=name)

    def on_unknown_note_attempt(self, event: UnknownNoteAttempt) -> None:
        short = event.from_addr.split("@")[0]
        self.notify(
            f"{event.from_addr} tried to message you but isn't in your contacts. Ask them to knock.",
            title=f"⚠ Blocked — {short}",
            severity="warning",
            timeout=8,
        )

    def on_knock_received(self, event: KnockReceived) -> None:
        existing = self.contacts.get(event.from_addr)
        if existing:
            key_updated = bool(event.public_key and event.public_key != existing.public_key_hex)
            if key_updated:
                self.contacts.add(event.from_addr, event.public_key, existing.name)
                self._refresh_contacts()
            short = existing.name or event.from_addr.split("@")[0]
            msg = f"{short} re-knocked — key updated" if key_updated else f"{short} re-knocked"
            self.notify(msg, title="Re-knock", severity="information")
            return
        pending = self.query_one("#pending-list", ListView)
        for item in pending.query(PendingItem):
            if item.from_addr == event.from_addr:
                item.public_key = event.public_key  # update key in place
                return
        pending.append(PendingItem(event.from_addr, event.public_key))
        self.notify("Enter on Pending to approve", title=f"⚡ KNOCK — {event.from_addr.split('@')[0]}", severity="warning")

    # ── Contact / pending selection ──────────────────────────────────────────

    @on(ListView.Selected, "#channel-list")
    def _channel_selected(self, event: ListView.Selected) -> None:
        if not isinstance(event.item, ChannelItem):
            return
        self.active_contact = None
        ch = event.item.channel
        self.query_one(ConvHeader).update(f"[bold $accent]# {ch['name']}[/bold $accent]")
        log = self.query_one("#msg-log", RichLog)
        log.clear()
        msgs = db.get_history(self._conn, ch["id"], limit=100)
        for m in reversed(msgs):
            self._write_msg(log, m["sender"], m["content"], m.get("created_at"))

    @on(ListView.Selected, "#contact-list")
    def _contact_selected(self, event: ListView.Selected) -> None:
        if not isinstance(event.item, ContactItem):
            return
        self.active_contact = event.item.contact
        self.query_one(ConvHeader).set_contact(event.item.contact)
        self._load_history()
        self.query_one("#msg-input", Input).focus()

    @on(ListView.Selected, "#pending-list")
    def _pending_selected(self, event: ListView.Selected) -> None:
        if not isinstance(event.item, PendingItem):
            return
        addr, pubkey = event.item.from_addr, event.item.public_key
        if pubkey:
            self.contacts.add(addr, pubkey)
            self._refresh_contacts()
            event.item.remove()
            self.notify(f"{addr} added", title="Approved ✓", severity="information")
        else:
            self.notify("No public key — ask them to re-knock", severity="warning")

    # ── Send message ─────────────────────────────────────────────────────────

    @on(Input.Submitted, "#msg-input")
    async def _send_message(self, event: Input.Submitted) -> None:
        body = event.value.strip()
        if not body or not self.active_contact:
            return
        event.input.value = ""
        if not self.active_contact.public_key_hex:
            self.notify(
                "This contact hasn't knocked — they may not receive your message.",
                title="⚠ Unverified contact",
                severity="warning",
            )
        ch = _dm_channel(self._conn, self.active_contact.addr)
        db.send_message(self._conn, channel_id=ch["id"], sender=_MY_ADDR, content=body)
        ok = await send_packet(
            PacketType.NOTE, _MY_ADDR, self.active_contact.addr,
            {"subject": "", "body": body}, self.identity,
        )
        suffix = "" if ok else " [dim red](not delivered)[/dim red]"
        self._append_msg(_MY_ADDR, body + suffix)

    # ── Knock ────────────────────────────────────────────────────────────────

    @on(Input.Submitted, "#knock-input")
    async def _send_knock(self, event: Input.Submitted) -> None:
        addr = event.value.strip()
        if not addr:
            return
        event.input.value = ""
        if addr == _MY_ADDR:
            self.notify("That's you.", title="Knock", severity="warning")
            return
        ok = await send_packet(
            PacketType.KNOCK, _MY_ADDR, addr,
            {"public_key": self.identity.public_key_hex}, self.identity,
        )
        self.notify(
            f"KNOCK → {addr}" if ok else f"Could not reach {addr}",
            title="Knock" if ok else "Knock failed",
            severity="information" if ok else "error",
        )

    # ── Actions ──────────────────────────────────────────────────────────────

    def action_focus_knock(self) -> None:
        self.query_one("#knock-input", Input).focus()

    def action_focus_msg(self) -> None:
        self.query_one("#msg-input", Input).focus()

    def action_blur(self) -> None:
        self.screen.set_focus(None)

    def action_show_identity(self) -> None:
        if not isinstance(self.screen, IdentityModal):
            self.push_screen(IdentityModal(_MY_ADDR, self.identity.public_key_hex))



    # ── Helpers ──────────────────────────────────────────────────────────────

    def _refresh_channels(self) -> None:
        lst = self.query_one("#channel-list", ListView)
        lst.clear()
        for ch in db.list_channels(self._conn, include_archived=False):
            if not ch["name"].startswith("dm:"):
                lst.append(ChannelItem(ch))

    def _refresh_contacts(self) -> None:
        lst = self.query_one("#contact-list", ListView)
        lst.clear()
        seen: set[str] = set()
        for c in self.contacts.all():
            if not c.blocked:
                lst.append(ContactItem(c))
                seen.add(c.addr)
        # Surface anyone we've chatted with but haven't formally approved
        if self._conn:
            for ch in db.list_channels(self._conn, include_archived=False):
                if ch["name"].startswith("dm:"):
                    peer = ch["name"][3:]
                    if peer != _MY_ADDR and peer not in seen:
                        lst.append(ContactItem(Contact(addr=peer, public_key_hex="")))
                        seen.add(peer)
        # Restore visual selection and refresh the active_contact reference
        if self.active_contact:
            active_addr = self.active_contact.addr
            for i, item in enumerate(lst.query(ContactItem)):
                if item.contact.addr == active_addr:
                    lst.index = i
                    self.active_contact = item.contact
                    break

    def _load_history(self) -> None:
        if not self.active_contact:
            return
        log = self.query_one("#msg-log", RichLog)
        log.clear()
        ch   = _dm_channel(self._conn, self.active_contact.addr)
        msgs = db.get_history(self._conn, ch["id"], limit=100)
        for m in reversed(msgs):
            self._write_msg(log, m["sender"], m["content"], m.get("created_at"))

    def _append_msg(self, sender: str, body: str) -> None:
        log = self.query_one("#msg-log", RichLog)
        self._write_msg(log, sender, body)

    def _write_msg(self, log: RichLog, sender: str, body: str, ts=None) -> None:
        color    = _sender_color(sender)
        name     = _display_name(sender, self.contacts)
        name_col = (name[:11] + "…") if len(name) > 12 else f"{name:<12}"
        time_str = _fmt_time(ts) if ts else datetime.now().strftime("%H:%M")
        log.write(f"[dim]{time_str}[/dim]  [{color} bold]{name_col}[/{color} bold]  {_escape_markup(body)}")


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

def _dm_channel(conn, contact_addr: str) -> dict:
    name = f"dm:{contact_addr}"
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO grove.channels (name, channel_type, description)
        VALUES (%s, 'direct', %s)
        ON CONFLICT (name) DO NOTHING
    """, (name, f"DM with {contact_addr}"))
    conn.commit()
    cur.execute("SELECT * FROM grove.channels WHERE name = %s", (name,))
    row = cur.fetchone()
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))
