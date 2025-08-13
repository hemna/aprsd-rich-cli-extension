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
from aprsd import cli_helper as aprsd_cli_helper
from aprsd import client as aprsd_client
from aprsd import (
    conf,  # noqa: F401
    threads,
)
from aprsd.client.client import APRSDClient
from aprsd.packets import core
from aprsd.stats import collector
from aprsd.threads import aprsd as aprsd_threads
from aprsd.threads import keepalive, rx, service, tx
from loguru import logger
from oslo_config import cfg
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, RenderResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.reactive import Reactive
from textual.widget import Widget
from textual.widgets import (
    Footer,
    Input,
    RichLog,
    TabbedContent,
    TabPane,
)

# Import the extension's configuration options
from aprsd_rich_cli_extension import (
    cli_helper,
    cmds,  # noqa
)
from aprsd_rich_cli_extension import log as log_extension
from aprsd_rich_cli_extension.components import (
    add_chat_screen,
    help_screen,
    packet_widget,
)
from aprsd_rich_cli_extension.components import utils as components_utils

LOG = logging.getLogger("APRSD")
CONF = cfg.CONF
LOGU = logger
F = t.TypeVar("F", bound=t.Callable[..., t.Any])


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

    def process_ack_packet(self, packet):
        """We got an ack for a message, no need to resend it."""
        super().process_ack_packet(packet)
        self.processed_queue.put(packet)

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


class MyBeaconSendThread(aprsd_threads.APRSDThread):
    """Thread that sends a GPS beacon packet periodically.

    Settings are in the [DEFAULT] section of the config file.
    """

    _loop_cnt: int = 1

    def __init__(self, notify_queue):
        super().__init__("BeaconSendThread")
        self._loop_cnt = 1
        self.notify_queue = notify_queue
        # Make sure Latitude and Longitude are set.
        if not CONF.latitude or not CONF.longitude:
            LOG.error(
                "Latitude and Longitude are not set in the config file."
                "Beacon will not be sent and thread is STOPPED.",
            )
            self.stop()
        LOG.info(
            "Beacon thread is running and will send "
            f"beacons every {CONF.beacon_interval} seconds.",
        )

    def loop(self):
        # Only dump out the stats every N seconds
        if self._loop_cnt % CONF.beacon_interval == 0:
            pkt = core.BeaconPacket(
                from_call=CONF.callsign,
                to_call="APRS",
                latitude=float(CONF.latitude),
                longitude=float(CONF.longitude),
                comment="APRSD GPS Beacon",
                symbol=CONF.beacon_symbol,
            )
            try:
                # Only send it once
                pkt.retry_count = 1
                tx.send(pkt, direct=True)
                self.notify_queue.put("Beacon sent")
            except Exception as e:
                LOG.error(f"Failed to send beacon: {e}")
                APRSDClient().reset()
                time.sleep(5)

        self._loop_cnt += 1
        time.sleep(1)
        return True


# All of the Textual Widgets needed for the UI


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
            text.append(self.sub_text, packet_widget.MYCALLSIGN_COLOR)
        return text


class HeaderVersion(Horizontal):
    """Display the version in the header."""

    text: Reactive[str] = Reactive("")
    """The main title text."""

    def render(self) -> RenderResult:
        return Text(f"APRSD : {aprsd.__version__}", no_wrap=True, overflow="ellipsis")


class HeaderEarth(Horizontal):
    """The earth icon in the header."""

    def render(self) -> RenderResult:
        # 🌍
        # 🌏
        return Text("🌎 ")


class AppHeader(Horizontal):
    """The header of the app."""

    DEFAULT_CSS = """
    SpinnerWidget {
        content-align: left middle;
    }
    """

    def __init__(self):
        super().__init__()

    def compose(self) -> ComposeResult:
        yield HeaderConnection(id="app-connection")
        earth = HeaderEarth(id="app-earth")
        earth.display = False
        yield earth
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


class APRSChatApp(App):
    """App to allow APRS chat in the terminal."""

    CSS_PATH = ["global.tcss", "chat.tcss"]

    BINDINGS = [
        Binding(
            "ctrl+q",
            "quit",
            "Quit",
            tooltip="Quit the app",
        ),
        Binding(
            "ctrl+n",
            "add_new_chat",
            "Add New Chat",
            tooltip="Add a chat with a new callsign",
        ),
        Binding(
            "ctrl+d",
            "toggle_dark",
            "Toggle Dark",
            tooltip="Switch between light and dark themes",
        ),
        Binding(
            "ctrl+l",
            "show_log",
            "Show Log",
            tooltip="Show the log screen",
        ),
        Binding(
            "f1",
            "show_help",
            "Show Help",
            tooltip="Show the help screen",
        ),
    ]

    def __init__(self):
        super().__init__()
        # Create the queues to be used later by
        # the threads
        # the queue used to process packets that are received
        # from the APRS network
        self.processed_queue = queue.Queue()
        # the queue used to send packets to the APRS network
        self.tx_queue = queue.Queue()
        # the queue used to notify the user that a beacon has been sent
        self.beacon_notify_queue = queue.Queue()

        self.callsign_tabs = {}

    def on_mount(self) -> None:
        """Called when the app is started and ready to go."""
        # Show the initial splash screen.
        self.check_setup()
        self.init_client()
        self.chat_binding_count = 1

        # packets to be sent to the UI
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

        self._start_threads()

        # Start checking for packets
        self.process_packets()
        self.check_connection()
        if CONF.enable_beacon:
            self.check_beacon_notify()

        self._add_rich_log()

    def _add_rich_log(self):
        """Add a rich log to the app."""
        tabbed_content = self.query_one(TabbedContent)
        tab_id = _get_tab_id("log")
        tabbed_content.add_pane(
            TabPane(
                "Log",
                RichLog(id="log-scroll"),
                id=tab_id,
            )
        )
        tabbed_content.active = tab_id
        # now start the worker to process the log queue
        self.process_log_queue()

    def action_show_log(self):
        """Show the log screen."""
        tabbed_content = self.query_one(TabbedContent)
        tabbed_content.active = _get_tab_id("log")

    def check_setup(self):
        # Initialize the client factory and create
        # The correct client object ready for use
        if not APRSDClient().is_enabled:
            LOG.error("No Clients are enabled in config.")
            sys.exit(-1)

        # Make sure we have 1 client transport enabled
        if not APRSDClient().is_configured:
            LOG.error("APRS client is not properly configured in config file.")
            sys.exit(-1)

    def init_client(self):
        # Creates the client object
        LOG.info("Creating client connection")
        self.aprs_client = APRSDClient()
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
        if CONF.enable_beacon:
            LOG.info("Beacon Enabled.  Starting Beacon thread.")
            service.ServiceThreads().register(
                MyBeaconSendThread(self.beacon_notify_queue)
            )
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
        if callsign in self.callsign_tabs:
            return self.callsign_tabs[callsign]

        try:
            tab = self.query_one(f"#{_get_tab_id(callsign)}")
            return tab
        except Exception as e:
            LOG.error(f"Error getting tab for callsign {callsign}: {e}")
            return None

    def action_add_new_chat(self):
        """When the user asks to create a chat with a new callsign."""
        self.push_screen(add_chat_screen.AddChatScreen(), callback=self._on_add_chat)

    def action_show_help(self):
        """Show the help screen."""
        self.notify("Showing help screen")
        self.push_screen(help_screen.HelpScreen())

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

    def action_show_tab(self, tab_id: str):
        """Show a tab."""
        self.query_one(TabbedContent).active = tab_id

    def _on_add_chat(self, callsign: str) -> None:
        """Handle the result of the add chat screen."""
        callsign = callsign.strip().upper()
        self.notify(f"Adding new chat with callsign: {callsign}")
        # self.notify(f"Adding new chat with callsign: {callsign}")
        if callsign:
            LOG.info(f"Adding new chat with callsign: {callsign}")
            # get the tabbedcontent and add a new pane
            tabbed_content = self.query_one("#tabbed-content")
            tab_id = _get_tab_id(callsign)
            log_pane = self.query_one(f"#{_get_tab_id('log')}")
            tabbed_content.add_pane(
                TabPane(
                    callsign,
                    VerticalScroll(id=_get_scroll_id(callsign)),
                    id=tab_id,
                ),
                before=log_pane,
            )
            self.chat_binding_count += 1
            self.bind(
                # Bind the chat tab to an F key
                f"f{self.chat_binding_count}",
                f"show_tab('{tab_id}')",
                description=f"{callsign}",
            )
            # set the new tab to be active
            tabbed_content.active = tab_id
            self.callsign_tabs[callsign] = self.query_one(f"#{tab_id}")

        # set the focus on the input
        self.query_one("#message-input").focus()

    def compose(self) -> ComposeResult:
        yield AppHeader()
        yield TabbedContent(id="tabbed-content")
        yield ChatInput()
        yield Footer()

    # def on_key(self, event: events.Key) -> None:
    #    self.notify(f"Key pressed: {event}")
    # self.query_one(RichLog).write(event)

    def on_unmount(self) -> None:
        """Stop threads when app exits."""
        threads.APRSDThreadList().stop_all()

    @work(exclusive=False)
    async def process_log_queue(self) -> None:
        """Process the log queue."""
        while True:
            try:
                record = log_extension.textual_log_queue.get_nowait()
                rich_log = self.query_one("#log-scroll")
                rich_log.write(record.getMessage(), expand=True)
            except queue.Empty:
                await asyncio.sleep(0.1)
            except Exception:
                await asyncio.sleep(0.1)

        self.notify("Log queue processing stopped")

    @work(exclusive=False)
    async def process_packets(self) -> None:
        """Process packets in a loop."""
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

                if isinstance(packet, core.AckPacket):
                    try:
                        pkt_widget = self.query_one(
                            f"#{components_utils._get_packet_id(packet)}"
                        )
                        if pkt_widget:
                            pkt_widget.acked = True
                            pkt_widget.refresh(recompose=True)
                    except Exception as e:
                        LOG.error(f"Error getting packet widget: {e}")

                scroll_view = self._get_scroll_for_callsign(callsign)
                if scroll_view:
                    if isinstance(packet, core.MessagePacket):
                        await scroll_view.mount(
                            packet_widget.APRSDPacketWidget(
                                packet, id=components_utils._get_packet_id(packet)
                            )
                        )
                        # self.notify(f"Packet({packet.from_call}): '{packet.message_text}' {scroll_view}")
                        # Scroll to bottom
                        scroll_view.scroll_end(animate=False)
                        if len(scroll_view.children) > 10:
                            Widget.remove(scroll_view.children[0])

                        if self._get_active_callsign() != callsign:
                            self.notify(f"New message from {callsign}")
                            # Can we change the color of the tab to red?
                            tab_id = _get_tab_id(callsign)
                            tab = self.query_one(f"#{tab_id}")
                            if tab:
                                tab.styles.background = "red"
                                self.notify(f"Tab: {tab.classes}")
                                # tab.refresh(recompose=True)
                            ass = self.query(f"#{callsign}")
                            for a in ass:
                                self.notify(f"Child: {a}")
                else:
                    # put the packet back in the queue
                    # the scroll view is not found, so we need to wait a bit
                    # and try again
                    self.processed_queue.put(packet)
                    await asyncio.sleep(0.2)

            except queue.Empty:
                # No packets, wait a bit
                await asyncio.sleep(0.1)

    @work(exclusive=False)
    async def check_beacon_notify(self):
        while True:
            try:
                _ = self.beacon_notify_queue.get_nowait()
                self._update_earth()
            except queue.Empty:
                await asyncio.sleep(1)

    @work(exclusive=False)
    async def _update_earth(self):
        """Show the earth icon for 2 seconds when we send a beacon."""
        earth = self.query_one("#app-earth")
        earth.display = not earth.display
        await asyncio.sleep(2)
        earth.display = not earth.display

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
@aprsd_cli_helper.add_options(aprsd_cli_helper.common_options)
@click.pass_context
@cli_helper.process_standard_options
def chat(ctx):
    """APRS Chat in the terminal."""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    tui = APRSChatApp()
    tui.run()
