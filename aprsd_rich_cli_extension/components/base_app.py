# this is the base class for all the TUI apps
# for APRSD.
# It provides the same functionality for all the apps.
# It provides a tabbed interface, a log, a status bar, and a help screen.

import asyncio
import logging
import queue
import sys
import typing as t

from aprsd import client as aprsd_client
from aprsd import (
    conf,  # noqa: F401
    threads,
)
from aprsd.client.client import APRSDClient
from aprsd.packets import core
from aprsd.threads import keepalive, rx, service
from aprsd.utils import trace
from loguru import logger
from oslo_config import cfg
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import (
    Footer,
    RichLog,
    TabbedContent,
    TabPane,
)

from aprsd_rich_cli_extension import log as log_extension
from aprsd_rich_cli_extension.components import header, help_screen
from aprsd_rich_cli_extension.components import utils as component_utils

LOG = logging.getLogger("APRSD")
CONF = cfg.CONF
LOGU = logger
F = t.TypeVar("F", bound=t.Callable[..., t.Any])


processed_queue = queue.Queue(maxsize=500)


class APRSDListenProcessThread(rx.APRSDFilterThread):
    """Thread that processes packets received from the APRS Client."""

    count = 0

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
            # packet_log.log(packet)
            pass

    def process_packet(self, packet: type[core.Packet]):
        self.processed_queue.put(packet)
        self.count += 1


class APRSDApp(App):
    """App to allow APRS chat in the terminal."""

    CSS_PATH = ["global.tcss"]

    BINDINGS = [
        Binding(
            "ctrl+d",
            "toggle_dark",
            "Toggle Dark",
            tooltip="Switch between light and dark themes",
        ),
        Binding(
            "f1",
            "show_help",
            "Show Help",
            tooltip="Show the help screen",
        ),
        Binding(
            "ctrl+l",
            "show_log",
            "Show Log",
            tooltip="Show the log screen",
        ),
    ]

    def __init__(self):
        super().__init__()
        # Create the queues to be used later by
        # the threads
        # the queue used to process packets that are received
        # from the APRS network
        self.processed_queue = processed_queue
        self.do_setup()

    def do_setup(self):
        self.check_setup()
        self.init_client()
        self.chat_binding_count = 1

        self._start_threads()

    @trace.trace
    def on_mount(self) -> None:
        """Called when the app is started and ready to go."""
        # Start checking for packets
        # self.packet_loop()
        # self.check_connection()
        # self._add_rich_log()

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

    def init_client(self, filter: str = None):
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

        if filter:
            LOG.debug(f"Filter messages on aprsis server by '{filter}'")
            self.aprs_client.set_filter(filter)
        else:
            LOG.debug("No filter set")

    def _start_threads(self):
        # thread to receive packets from the APRS network
        self.listen_thread = rx.APRSDRXThread(
            packet_queue=threads.packet_queue,
        )
        # thread to process packets received from listen_thread
        self.process_thread = APRSDListenProcessThread(
            packet_queue=threads.packet_queue,
            processed_queue=self.processed_queue,
        )
        service.ServiceThreads().register(self.listen_thread)
        service.ServiceThreads().register(self.process_thread)
        service.ServiceThreads().register(keepalive.KeepAliveThread())
        service.ServiceThreads().start()

    @trace.trace
    def _add_rich_log(self):
        """Add a rich log to the app."""
        tabbed_content = self.query_one(TabbedContent)
        tab_id = component_utils._get_tab_id("log")
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
        tabbed_content.active = component_utils._get_tab_id("log")

    def action_show_help(self):
        """Show the help screen."""
        self.notify("Showing help screen")
        self.push_screen(help_screen.HelpScreen())

    def action_show_tab(self, tab_id: str):
        """Show a tab."""
        self.query_one(TabbedContent).active = tab_id

    def compose(self) -> ComposeResult:
        yield header.AppHeader()
        yield TabbedContent(id="tabbed-content")
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
        self.notify("Log queue processing started")
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
    async def packet_loop(self) -> None:
        """Process packets in a loop."""
        self.packet_count = 0

        while True:
            try:
                # Non-blocking queue check
                packet = self.processed_queue.get_nowait()
                self.packet_count += 1

                # Child class will handle the packet
                await self.process_packet(packet)

            except queue.Empty:
                # No packets, wait a bit
                await asyncio.sleep(0.1)

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
