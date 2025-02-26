"""
Listen to aprs packets and show them.

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
from aprsd.packets import log as packet_log
from aprsd.stats import collector
from aprsd.threads import aprsd as aprsd_threads
from aprsd.threads import keepalive, rx
from aprsd.utils import singleton
from haversine import Unit, haversine
from loguru import logger
from oslo_config import cfg
from rich import box
from rich.panel import Panel
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult, RenderResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, VerticalScroll
from textual.reactive import Reactive
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Button, Footer, Input, Label

# Import the extension's configuration options
from aprsd_rich_cli_extension import (
    cmds,  # noqa
    conf,  # noqa
)

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


@singleton
class ServerThreads:
    """Registry for threads that the server command runs.

    This enables extensions to register a thread to run during
    the server command.

    """

    def __init__(self):
        self.threads: list[aprsd_threads.APRSDThread] = []

    def register(self, thread: aprsd_threads.APRSDThread):
        if not isinstance(thread, aprsd_threads.APRSDThread):
            raise TypeError(f"Thread {thread} is not an APRSDThread")
        self.threads.append(thread)

    def unregister(self, thread: aprsd_threads.APRSDThread):
        if not isinstance(thread, aprsd_threads.APRSDThread):
            raise TypeError(f"Thread {thread} is not an APRSDThread")
        self.threads.remove(thread)

    def start(self):
        """Start all threads in the list."""
        for thread in self.threads:
            thread.start()

    def join(self):
        """Join all the threads in the list"""
        for thread in self.threads:
            thread.join()


class APRSDListenProcessThread(rx.APRSDFilterThread):
    def __init__(
        self,
        packet_queue,
        processed_queue,
        log_packets=False,
    ):
        super().__init__("ListenProcThread", packet_queue)
        self.processed_queue = processed_queue
        self.log_packets = True

    def print_packet(self, packet):
        if self.log_packets:
            packet_log.log(packet)

    def process_packet(self, packet: type[core.Packet]):
        self.processed_queue.put(packet)


class PacketStats(Widget):
    def __init__(self):
        self.packet_count = 0
        self.packet_types = {}

    def compose(self) -> ComposeResult:
        yield Label(f"Packet Count: {self.packet_count}")
        yield Label(f"Packet Types: {self.packet_types}")

    def update(self, packet: type[core.Packet]):
        self.packet_count += 1
        self.packet_types[packet.__class__.__name__] = (
            self.packet_types.get(packet.__class__.__name__, 0) + 1
        )


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
            text.append(self.sub_text, "yellow")
        return text


class HeaderFilter(Horizontal):
    """Display the filter in the header."""

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
            text.append("Num Packets: ")
            text.append(self.sub_text, "yellow")
        return text


class HeaderVersion(Horizontal):
    """Display the version in the header."""

    text: Reactive[str] = Reactive("")
    """The main title text."""

    def render(self) -> RenderResult:
        return Text(f"APRSD : {aprsd.__version__}", no_wrap=True, overflow="ellipsis")


class AppHeader(Horizontal):
    """The header of the app."""

    def __init__(self, filter: str):
        super().__init__()
        f = list(filter)
        self.filter = " ".join(f)

    def compose(self) -> ComposeResult:
        yield HeaderConnection(id="app-connection")
        yield HeaderFilter(id="app-filter")
        yield HeaderVersion(id="app-version")


class MyPacketDisplay(Widget):
    """Display an APRS packet."""

    packet: type[core.Packet]

    def __init__(self, packet: type[core.Packet], packet_count: int):
        super().__init__()
        self.packet = packet
        self.packet_count = packet_count
        # self.border_title = f"{packet.from_call} -> {packet.to_call}"

    # def compose(self) -> ComposeResult:
    def render(self) -> RenderResult:
        # yield Markdown(f"```\n{str(self.packet.human_info)}\n```")
        header = []
        FROM_COLOR = f"b {utils.hex_from_name(self.packet.from_call)}"
        FROM = f"[{FROM_COLOR}]{self.packet.from_call}[/{FROM_COLOR}]"
        TO_COLOR = f"b {utils.hex_from_name(self.packet.to_call)}"
        TO = f"[{TO_COLOR}]{self.packet.to_call}[/{TO_COLOR}]"
        via_color = "b #1AA730"
        ARROW = f"[{via_color}]\u2192[/{via_color}]"
        header.append(f"{FROM} {ARROW}")
        header.append(f"{ARROW}".join(self.packet.path))
        header.append(f"{ARROW} {TO}")

        # is there distance information?
        distance_msg = None
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

            distance_msg = (
                f" : {DEGREES_COLOR}{utils.degrees_to_cardinal(bearing, full_string=True)}{DEGREES_COLOR_END} "
                f"{DISTANCE_COLOR}@ {haversine(my_coords, packet_coords, unit=Unit.MILES):.2f}miles{DISTANCE_COLOR_END}"
            )

        date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = str(self.packet.human_info)
        if distance_msg:
            header.append(distance_msg)

        msg_text = Text("", style="bright_white")

        date_text = Text(date_str, style="grey39")
        msg_text.append(date_text)
        msg_text.append("\n\n")
        msg_text.append(message)

        class_name = self.packet.__class__.__name__.replace("Packet", "")
        class_name_color = f"{utils.hex_from_name(class_name)}"
        pkt_type_text = Text(
            f"{class_name} ({self.packet_count})", style=f"{class_name_color}"
        )

        raw_header = Text("Raw:", style="grey39")
        raw_text = Text(f"\n{self.packet.raw}", style="grey27")
        msg_text.append("\n\n")
        msg_text.append(raw_header)
        msg_text.append(raw_text)

        return Panel(
            msg_text,
            box=box.ROUNDED,
            padding=(0, 0),
            title=" ".join(header),
            title_align="left",
            # border_style="bright_blue",
            subtitle=pkt_type_text,
            subtitle_align="right",
        )


class APRSFilterInput(Screen):
    CSS = """
        APRSFilterInput {
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

        #input_filter {
            column-span: 2;
        }
    """

    def compose(self) -> ComposeResult:
        with Grid():
            yield Input(placeholder="Enter APRS filter", id="input_filter")
            yield Button("Submit", variant="primary", id="submit")
            yield Button("Cancel", variant="error", id="cancel")

    def on_button_pressed(self, event):
        if event.button.id == "submit":
            self.input_text = self.query_one("#input_filter").value
            self.app.filter_changed(self.input_text)
            self.app.pop_screen()


class APRSListenerApp(App):
    """App to display APRS packets in real-time."""

    CSS = """
    AppHeader {
        dock: top;
        width: 100%;
        background: $panel;
        color: $foreground;
        height: 1;
        padding-left: 1;
        margin-bottom: 1;
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

    MyPacketDisplay {
        color: $text;
        width: 100%;
        height: 8;
        margin: 1;
        padding: 0 0 0 0;
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
            "ctrl+f",
            "change_filter",
            "Change Filter",
            tooltip="Set the aprs filter for incoming packets",
        ),
    ]

    def __init__(self, log_packets: bool = False, filter: str = None):
        super().__init__()
        self.check_setup()
        self.init_client(filter)
        self.filter = ",".join(filter)
        self.ass = False

        # packets to be sent to the UI
        self.processed_queue = queue.Queue()
        self.listen_thread = rx.APRSDRXThread(
            packet_queue=threads.packet_queue,
        )
        self.process_thread = APRSDListenProcessThread(
            packet_queue=threads.packet_queue,
            processed_queue=self.processed_queue,
            log_packets=log_packets,
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

    def init_client(self, filter: str):
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

        LOG.debug(f"Filter messages on aprsis server by '{filter}'")
        self.aprs_client.set_filter(filter)

    def _start_threads(self):
        ServerThreads().register(self.listen_thread)
        ServerThreads().register(self.process_thread)
        ServerThreads().register(keepalive.KeepAliveThread())
        ServerThreads().start()

    def action_change_filter(self):
        self.push_screen(APRSFilterInput(id="aprs-filter-dialog"))

    @work(exclusive=True)
    async def request_filter(self) -> None:
        # stop the client?
        await self.push_screen_wait(APRSFilterInput(id="aprs-filter-dialog"))

    def filter_changed(self, filter: str) -> None:
        LOG.debug(f"Filter_changed to '{filter}'")
        self.filter = filter

    def compose(self) -> ComposeResult:
        yield AppHeader(filter=self.filter)
        yield VerticalScroll(id="packet-view")
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
        packet_view = self.query_one("#packet-view")
        filter_widget = self.query_one("#app-filter")
        self.packet_count = 0

        while not self.ass:
            try:
                # Non-blocking queue check
                packet = self.processed_queue.get_nowait()
                self.packet_count += 1
                filter_widget.sub_text = f"{self.packet_count}"
                await packet_view.mount(
                    MyPacketDisplay(packet, packet_count=self.packet_count)
                )
                # Scroll to bottom
                packet_view.scroll_end(animate=False)
                if len(packet_view.children) > 10:
                    Widget.remove(packet_view.children[0])
            except queue.Empty:
                # No packets, wait a bit
                await asyncio.sleep(0.1)
        LOG.error("check_packets: done")

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
                LOG.debug(
                    f"check_connection: current filter '{self.aprs_client.get_filter()}'"
                )
                current_filter = ",".join(self.aprs_client.get_filter())
                if current_filter != self.filter:
                    LOG.debug(
                        f"check_connection: current filter '{current_filter}' != self.filter '{self.filter}'"
                    )
                    self.aprs_client.set_filter(self.filter)
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

                filter_widget = self.query_one("#app-filter")
                filter_widget.text = f"Filter: {self.filter}"
                filter_widget.sub_text = f"{self.packet_count}"
            except Exception as e:
                LOG.error(f"check_connection: error: {e}")
                await asyncio.sleep(1)

            await asyncio.sleep(1)


@cmds.rich.command()
@cli_helper.add_options(cli_helper.common_options)
@click.option("--log-packets", is_flag=True, help="Log packets to the console.")
@click.argument(
    "filter",
    nargs=-1,
    required=True,
)
@click.pass_context
@cli_helper.process_standard_options
def listen(ctx, log_packets: bool, filter: str):
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    app = APRSListenerApp(log_packets=log_packets, filter=filter)
    app.run()
