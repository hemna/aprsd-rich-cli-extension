"""
APRS Chat for the terminal!

See https://textual.textualize.io/blog/2024/09/15/anatomy-of-a-textual-user-interface/
"""

# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "aprsd",
#     "textual",
# ]
# ///

import asyncio
import datetime
import logging
import queue
import signal
import sys
import time
import typing as t

import aprsd
import click
from aprsd import (
    cli_helper,
    conf,  # noqa: F401
    threads,
    utils,
)
from aprsd import client as aprsd_client
from aprsd.client import client_factory
from aprsd.packets import core
from aprsd.stats import collector
from aprsd.threads import aprsd as aprsd_threads
from aprsd.threads import keepalive, rx, service, tx
from haversine import Unit, haversine
from loguru import logger
from oslo_config import cfg
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, RenderResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, VerticalScroll
from textual.reactive import Reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Footer,
    Input,
    Label,
    TabbedContent,
    TabPane,
)

# Import the extension's configuration options
from aprsd_rich_cli_extension import (
    cmds,  # noqa
)

LOG = logging.getLogger("APRSD")
CONF = cfg.CONF
LOGU = logger
F = t.TypeVar("F", bound=t.Callable[..., t.Any])

MYCALLSIGN_COLOR = "yellow"


@click.version_option()
@click.pass_context
def cli(ctx):
    pass


def signal_handler(sig, frame):
    threads.APRSDThreadList().stop_all()
    if "subprocess" not in str(frame):
        LOG.info(
            "Ctrl+C, Sending all threads exit! Can take up to 10 seconds {}".format(
                datetime.datetime.now(),
            ),
        )
        time.sleep(5)
        # Last save to disk
        collector.Collector().collect()


def _get_scroll_id(callsign: str) -> str:
    """Get the scroll id for a callsign."""
    return f"{callsign}-scroll"


def _get_tab_id(callsign: str) -> str:
    """Get the tab id for a callsign."""
    return f"tab-{callsign}"


def _get_packet_id(packet: type[core.Packet]) -> str:
    """Get the packet id for a packet."""
    return f"_{packet.msgNo}"


class APRSDListenProcessThread(rx.APRSDProcessPacketThread):
    def __init__(
        self,
        packet_queue,
        processed_queue,
    ):
        super().__init__(packet_queue=packet_queue)
        # The processed queue are packets that need to be displayed
        # in the UI
        self.processed_queue = processed_queue

    def process_our_message_packet(self, packet: type[core.Packet]):
        """Process a packet and add it to the processed queue."""
        self.processed_queue.put(packet)


class APRSTXThread(aprsd_threads.APRSDThread):
    """Thread to pull messages from the queue and send them to the APRS server.

    We have to do this to allow the UI to update while the thread is sending messages.

    """

    def __init__(self, packet_queue):
        super().__init__("APRSTXThread")
        self.tx_queue = packet_queue

    def loop(self):
        """Process a packet and add it to the processed queue."""
        while not self.thread_stop:
            if not self.tx_queue.empty():
                packet = self.tx_queue.get()
                tx.send(packet)
            else:
                time.sleep(0.1)


class MyPacketDisplay(Widget):
    """Display an APRS packet."""

    DEFAULT_CSS = """
    MyPacketDisplay {
        color: $text;
        width: 100%;
        height: 6;
        margin: 1;
        padding: 0 0 0 0;
    }
    """

    packet: type[core.Packet]
    acked: Reactive[bool] = Reactive(False)

    def __init__(self, packet: type[core.Packet], id: str):
        super().__init__(id=id)
        self.packet = packet
        self.packet.prepare()

    @property
    def from_color(self):
        if self.packet.from_call == CONF.callsign:
            # The packet was sent by us. (TX)
            return f"{MYCALLSIGN_COLOR}"
        else:
            # The packet was sent by someone else. (RX)
            return f"b {utils.hex_from_name(self.packet.from_call)}"

    @property
    def to_color(self):
        if self.packet.from_call == CONF.callsign:
            # The packet was sent by us. (TX)
            return f"b {utils.hex_from_name(self.packet.to_call)}"
        else:
            # The packet was sent by someone else. (RX)
            return f"{MYCALLSIGN_COLOR}"

    def _distance_msg(self):
        # is there distance information?
        if (
            isinstance(self.packet, core.GPSPacket)
            and CONF.latitude
            and CONF.longitude
            and self.packet.latitude
            and self.packet.longitude
        ):
            DEGREES_COLOR = "[b bright_black]"
            DEGREES_COLOR_END = "[/b bright_black]"
            DISTANCE_COLOR = "[b bright_yellow]"
            DISTANCE_COLOR_END = "[/b bright_yellow]"
            my_coords = (float(CONF.latitude), float(CONF.longitude))
            packet_coords = (float(self.packet.latitude), float(self.packet.longitude))
            try:
                bearing = utils.calculate_initial_compass_bearing(
                    my_coords, packet_coords
                )
            except Exception as e:
                LOG.error(f"Failed to calculate bearing: {e}")
                bearing = 0

            return (
                f" : {DEGREES_COLOR}{utils.degrees_to_cardinal(bearing, full_string=True)}{DEGREES_COLOR_END} "
                f"{DISTANCE_COLOR}@ {haversine(my_coords, packet_coords, unit=Unit.MILES):.2f}miles{DISTANCE_COLOR_END}"
            )

    def _build_title(self):
        """build the title of the packet."""
        title = []
        from_color = self.from_color
        to_color = self.to_color

        FROM = f"[{from_color}]{self.packet.from_call}[/{from_color}]"
        TO = f"[{to_color}]{self.packet.to_call}[/{to_color}]"
        via_color = "b #1AA730"
        ARROW = f"[{via_color}]\u2192[/{via_color}]"
        title.append(f"{FROM} {ARROW}")
        if self.packet.from_call == CONF.callsign:
            title.append(f"{TO}")
        else:
            title.append(f"{ARROW}".join(self.packet.path))
            title.append(f"{ARROW} {TO}")

        title.append(f":msgNo {self.packet.msgNo}")

        distance_msg = self._distance_msg()
        if distance_msg:
            title.append(distance_msg)

        self.border_title = " ".join(title)

    def _build_subtitle(self):
        date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.border_subtitle = date_str
        self.styles.border_subtitle_color = "rgb(98,98,98)"

    def compose(self) -> ComposeResult:
        self._build_title()
        self._build_subtitle()

        msg_text = Text("", style="bright_white")
        msg_text.append(str(self.packet.human_info))

        raw_header = Text("Raw:", style="grey39")
        raw_text = Text(f"\n{self.packet.raw}", style="grey27")
        msg_text.append("\n\n")
        msg_text.append(raw_header)
        msg_text.append(raw_text)

        yield Label(msg_text)

        if self.packet.from_call == CONF.callsign:
            self.styles.border_title_align = "right"
            self.styles.border = ("solid", "red")
            self.styles.border_subtitle_align = "right"
        else:
            self.styles.border = ("solid", "green")
            self.styles.border_title_align = "left"
            self.styles.border_subtitle_align = "left"


class HeaderConnection(Horizontal):
    """Display the title / subtitle in the header."""

    text: Reactive[str] = Reactive("")
    """The main title text."""

    sub_text = Reactive("")
    """The sub-title text."""

    def render(self) -> RenderResult:
        """Render the title and sub-title.

        Returns:
            The value to render.
        """
        text = Text(self.text, no_wrap=True, overflow="ellipsis")
        if self.sub_text:
            text.append(" — ")
            text.append(self.sub_text, MYCALLSIGN_COLOR)
        return text


class HeaderVersion(Horizontal):
    """Display the version in the header."""

    text: Reactive[str] = Reactive("")
    """The main title text."""

    def render(self) -> RenderResult:
        return Text(f"APRSD : {aprsd.__version__}", no_wrap=True, overflow="ellipsis")


class AppHeader(Horizontal):
    """The header of the app."""

    def __init__(self):
        super().__init__()

    def compose(self) -> ComposeResult:
        yield HeaderConnection(id="app-connection")
        yield HeaderVersion(id="app-version")


class ChatInput(Horizontal):
    """The input for the chat."""

    DEFAULT_CSS = """
    ChatInput {
        dock: bottom;
        height: 3;
        width: 100%;
        margin-bottom: 1;
        background: $panel;
    }
    Input {
        align: left middle;
        width: 95%;
    }
    """

    @on(Input.Submitted)
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle the input submitted event."""
        LOG.info(f"Input submitted: {event.value}")
        msg_text = event.value
        self.app.action_send_message(msg_text)
        self.query_one("#message-input").value = ""

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Enter message", id="message-input")


class AddChatScreen(ModalScreen[str]):
    """The screen to add a new chat."""

    CSS = """
        AddChatScreen {
            align: center middle;
        }

        Grid {
            grid-size: 2 2;
            padding: 0 1;
            width: 40;
            height: 10;
            border: thick $background 80%;
            background: $surface;
        }

        #input_callsign {
            column-span: 2;
        }
    """

    def compose(self) -> ComposeResult:
        with Grid():
            yield Input(
                placeholder="Enter callsign", id="input_callsign", max_length=60
            )
            yield Button("Add", id="submit")
            yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event):
        if event.button.id == "submit":
            input_text = self.query_one("#input_callsign").value
            self.dismiss(input_text)


class APRSChatApp(App):
    """App to allow APRS chat in the terminal."""

    DEFAULT_CSS = """
    AppHeader {
        dock: top;
        width: 100%;
        background: $panel;
        color: $foreground;
        height: 1;
        padding-left: 1;
        margin-bottom: 1;
    }

    TabbedContent {
        width: 100%;
        height: 1fr;
    }

    VerticalScroll {
        width: 100%;
    }

    HeaderConnection {
        content-align: left middle;
    }

    HeaderFilter {
        content-align: center middle;
    }

    HeaderVersion {
        content-align: right middle;
    }
    """

    BINDINGS = [
        Binding(
            "ctrl+d",
            "toggle_dark",
            "Toggle Dark",
            tooltip="Switch between light and dark themes",
        ),
        Binding(
            "ctrl+n",
            "add_new_chat",
            "Add New Chat",
            tooltip="Add a chat with a new callsign",
        ),
    ]

    def __init__(self):
        super().__init__()
        self.check_setup()
        self.init_client()
        self.chat_binding_count = 0

        # packets to be sent to the UI
        self.processed_queue = queue.Queue()
        self.tx_queue = queue.Queue()
        self.listen_thread = rx.APRSDRXThread(
            packet_queue=threads.packet_queue,
        )
        self.process_thread = APRSDListenProcessThread(
            packet_queue=threads.packet_queue,
            processed_queue=self.processed_queue,
        )
        self.tx_thread = APRSTXThread(
            packet_queue=self.tx_queue,
        )

    def check_setup(self):
        # Initialize the client factory and create
        # The correct client object ready for use
        if not client_factory.is_client_enabled():
            LOG.error("No Clients are enabled in config.")
            sys.exit(-1)

        # Make sure we have 1 client transport enabled
        if not client_factory.is_client_enabled():
            LOG.error("No Clients are enabled in config.")
            sys.exit(-1)

        if not client_factory.is_client_configured():
            LOG.error("APRS client is not properly configured in config file.")
            sys.exit(-1)

    def init_client(self):
        # Creates the client object
        LOG.info("Creating client connection")
        self.aprs_client = client_factory.create()
        LOG.info(self.aprs_client)
        if not self.aprs_client.login_success:
            # We failed to login, will just quit!
            msg = f"Login Failure: {self.aprs_client.login_failure}"
            LOG.error(msg)
            print(msg)
            sys.exit(-1)

    def _start_threads(self):
        service.ServiceThreads().register(self.listen_thread)
        service.ServiceThreads().register(self.process_thread)
        service.ServiceThreads().register(self.tx_thread)
        service.ServiceThreads().register(keepalive.KeepAliveThread())
        service.ServiceThreads().start()

    def _get_active_callsign(self):
        """Get the active callsign from the active tab."""
        active_tab = self.query_one(TabbedContent).active
        return str(active_tab).replace("tab-", "")

    def _get_scroll_for_callsign(self, callsign: str):
        """Get the scroll view for a callsign."""
        try:
            scroll = self.query_one(f"#{_get_scroll_id(callsign)}")
            return scroll
        except Exception as e:
            LOG.error(f"Error getting scroll for callsign {callsign}: {e}")
            return None

    def _get_tab_for_callsign(self, callsign: str):
        """Get the tab for a callsign."""
        try:
            tab = self.query_one(f"#{_get_tab_id(callsign)}")
            return tab
        except Exception as e:
            LOG.error(f"Error getting tab for callsign {callsign}: {e}")
            return None

    def action_add_new_chat(self):
        """When the user asks to create a chat with a new callsign."""
        self.push_screen(AddChatScreen(), callback=self._on_add_chat)

    def action_send_message(self, msg_text: str):
        """Send a message to the APRS server."""
        # Get the active callsign
        active_callsign = self._get_active_callsign()
        # self.notify(f"Sending message '{msg_text}' to {active_callsign}")

        # Create the message packet
        msg = core.MessagePacket(
            from_call=CONF.callsign,
            to_call=active_callsign,
            message_text=msg_text,
        )
        msg.prepare(create_msg_number=True)
        self.processed_queue.put(msg)
        self.tx_queue.put(msg)

    def _on_add_chat(self, callsign: str) -> None:
        """Handle the result of the add chat screen."""
        callsign = callsign.strip().upper()
        # self.notify(f"Adding new chat with callsign: {callsign}")
        if callsign:
            LOG.info(f"Adding new chat with callsign: {callsign}")
            # get the tabbedcontent and add a new pane
            tabbed_content = self.query_one(TabbedContent)
            tabbed_content.add_pane(
                TabPane(
                    callsign,
                    VerticalScroll(id=_get_scroll_id(callsign)),
                    id=_get_tab_id(callsign),
                )
            )
            self.chat_binding_count += 1
            self.bind(
                f"ctrl-{self.chat_binding_count}",
                f"show_tab('{_get_tab_id(callsign)}')",
                description=f"{callsign}",
            )
            # set the new tab to be active
            tabbed_content.active = _get_tab_id(callsign)

        # set the focus on the input
        self.query_one("#message-input").focus()

    def compose(self) -> ComposeResult:
        yield AppHeader()
        yield TabbedContent()
        yield ChatInput()
        yield Footer()

    def on_mount(self) -> None:
        """Start the APRS listener threads when app starts."""
        self._start_threads()

        # Start checking for packets
        self.check_packets()
        self.check_connection()

    def on_unmount(self) -> None:
        """Stop threads when app exits."""
        threads.APRSDThreadList().stop_all()

    @work(exclusive=False)
    async def check_packets(self) -> None:
        """Check for new packets in a loop."""
        self.packet_count = 0

        while True:
            try:
                # Non-blocking queue check
                packet = self.processed_queue.get_nowait()
                self.packet_count += 1

                callsign = packet.from_call
                if packet.from_call == CONF.callsign:
                    # this is a message we sent.
                    callsign = packet.to_call
                else:
                    # make sure there is a tab existing for this callsign
                    if not self._get_tab_for_callsign(callsign):
                        self._on_add_chat(callsign)

                scroll_view = self._get_scroll_for_callsign(callsign)
                if scroll_view:
                    if isinstance(packet, core.MessagePacket):
                        await scroll_view.mount(
                            MyPacketDisplay(packet, id=_get_packet_id(packet))
                        )
                        # self.notify(f"Packet({packet.from_call}): '{packet.message_text}' {scroll_view}")
                        # Scroll to bottom
                        scroll_view.scroll_end(animate=False)
                        if len(scroll_view.children) > 10:
                            Widget.remove(scroll_view.children[0])

                        if self._get_active_callsign() != callsign:
                            self.notify(f"New message from {callsign}")
                            # Can we change the color of the tab to red?

                    elif isinstance(packet, core.AckPacket):
                        # remove the ack packet from the scroll view
                        self.notify(f"Ack packet: {packet.msgNo}")
                        try:
                            pkt_widget = self.query_one(f"#{_get_packet_id(packet)}")
                            if pkt_widget:
                                pkt_widget.acked = True
                        except Exception as e:
                            LOG.error(f"Error getting packet widget: {e}")
                else:
                    # put the packet back in the queue
                    # the scroll view is not found, so we need to wait a bit
                    # and try again
                    self.processed_queue.put(packet)
                    await asyncio.sleep(0.2)

            except queue.Empty:
                # No packets, wait a bit
                await asyncio.sleep(0.1)

    def _build_connection_string(self, stats) -> str:
        match stats["transport"]:
            case aprsd_client.TRANSPORT_APRSIS:
                transport_name = "APRS-IS"
                connection_string = f"{transport_name} : {stats['server_string']}"
            case aprsd_client.TRANSPORT_TCPKISS:
                transport_name = "TCP/KISS"
                connection_string = (
                    f"{transport_name} : {CONF.kiss_tcp.host}:{CONF.kiss_tcp.port}"
                )
            case aprsd_client.TRANSPORT_SERIALKISS:
                transport_name = "Serial/KISS"
                connection_string = f"{transport_name} : {CONF.kiss_serial.device}"
        return connection_string

    @work(exclusive=False)
    async def check_connection(self) -> None:
        """Check for connection to APRS server."""
        while True:
            if self.aprs_client:
                stats = self.aprs_client.stats()
            try:
                connection_widget = self.query_one("#app-connection")
                connection_string = self._build_connection_string(stats)
                sub_text = CONF.callsign
                if not self.listen_thread.is_alive():
                    connection_widget.text = "Connection Lost"
                    connection_widget.sub_text = ""
                else:
                    connection_widget.text = f"{connection_string}"
                    connection_widget.sub_text = f"{sub_text}"
            except Exception as e:
                LOG.error(f"check_connection: error: {e}")
                await asyncio.sleep(1)

            await asyncio.sleep(1)


@cmds.rich.command()
@cli_helper.add_options(cli_helper.common_options)
@click.pass_context
@cli_helper.process_standard_options
def chat(ctx):
    """APRS Chat in the terminal."""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    app = APRSChatApp()
    app.run()
