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

import datetime
import logging
import queue
import signal
import time
import typing as t

import click
from aprsd import cli_helper as aprsd_cli_helper
from aprsd import (
    conf,  # noqa: F401
    threads,
)
from aprsd.packets import core
from aprsd.stats import collector
from aprsd.utils import trace
from loguru import logger
from oslo_config import cfg
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Button, Footer, Input, Label, TabbedContent, TabPane

# Import the extension's configuration options
from aprsd_rich_cli_extension import (
    cli_helper,
    cmds,  # noqa
    conf,  # noqa
)
from aprsd_rich_cli_extension.components import (
    base_app,
    header,
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


class AppHeader(Horizontal):
    """The header of the app."""

    def __init__(self, filter: str):
        super().__init__()
        self.filter = filter

    def compose(self) -> ComposeResult:
        yield header.HeaderConnection(id="app-connection")
        yield header.HeaderFilter(id="app-filter", filter=self.filter)
        yield header.HeaderVersion(id="app-version")


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


class APRSListenerApp(base_app.APRSDApp):
    """App to display APRS packets in real-time."""

    CSS_PATH = base_app.APRSDApp.CSS_PATH + ["listen.tcss"]

    BINDINGS = base_app.APRSDApp.BINDINGS + [
        Binding(
            "ctrl+f",
            "change_filter",
            "Change Filter",
            tooltip="Set the aprs filter for incoming packets",
        ),
    ]

    filter = None
    pkt_hash = []

    def __init__(self, log_packets: bool = False, filter: str = None):
        self.filter = ",".join(filter)
        super().__init__()

    def do_setup(self):
        self.check_setup()
        self.init_client(filter=self.filter)
        self.chat_binding_count = 1

        self._start_threads()

    def action_change_filter(self):
        self.push_screen(APRSFilterInput(id="aprs-filter-dialog"))

    @work(exclusive=True)
    async def request_filter(self) -> None:
        # stop the client?
        await self.push_screen_wait(APRSFilterInput(id="aprs-filter-dialog"))

    def filter_changed(self, filter: str) -> None:
        LOG.debug(f"Filter_changed to '{filter}'")
        self.filter = filter

    @trace.trace
    def on_mount(self) -> None:
        """Start the APRS listener threads when app starts."""
        # super().on_mount()

        # now add the packet view
        self.query_one("#tabbed-content").add_pane(
            TabPane("Packets", VerticalScroll(id="packet-view"), id="packets-tab")
        )
        self._add_rich_log()
        self.packet_loop()
        self.check_connection()

    def compose(self) -> ComposeResult:
        yield AppHeader(filter=self.filter)
        yield TabbedContent(id="tabbed-content")
        yield Footer()

    @work(exclusive=False)
    async def packet_loop(self) -> None:
        """Check for new packets in a loop."""
        packet_view = self.query_one("#packet-view")
        filter_widget = self.query_one("#app-filter")
        self.packet_count = 0

        while True:
            try:
                # Non-blocking queue check
                packet = base_app.processed_queue.get(timeout=1)
                if packet:
                    self.packet_count += 1
                    filter_widget.sub_text = f"{self.packet_count}"
                    # LOG.error(f"packet_count = {self.packet_count} {hash(packet)}")
                    widget_id = components_utils._get_packet_id(packet)
                    if widget_id not in self.pkt_hash:
                        self.pkt_hash.append(widget_id)
                        found = False
                    else:
                        found = True

                    try:
                        if not found:
                            widget = packet_widget.APRSDPacketWidget(
                                packet, id=widget_id
                            )
                            if widget:
                                await packet_view.mount(widget)
                    except Exception as e:
                        LOG.error(f"Error mounting packet: {e}")
                        self.notify(f"Error adding packet: {packet}")

                    # Scroll to bottom
                    packet_view.scroll_end(animate=False)
                    if len(packet_view.children) > 10:
                        Widget.remove(packet_view.children[0])
                        self.pkt_hash.remove(self.pkt_hash[0])
            except queue.Empty:
                # No packets, wait a bit
                # await asyncio.sleep(0.05)
                pass
        LOG.error("check_packets: done")


@cmds.rich.command()
@aprsd_cli_helper.add_options(aprsd_cli_helper.common_options)
@click.option("--log-packets", is_flag=True, help="Log packets to the console.")
@click.argument(
    "filter",
    nargs=-1,
    required=True,
)
@click.pass_context
@cli_helper.process_standard_options
def listen(ctx, log_packets: bool, filter: str):
    """Listen to APRS packets in the terminal."""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    app = APRSListenerApp(log_packets=log_packets, filter=filter)
    app.run()
