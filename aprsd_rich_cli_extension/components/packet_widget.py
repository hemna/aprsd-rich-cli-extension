import datetime
import logging

from aprsd import utils
from aprsd.packets import core
from haversine import Unit, haversine
from loguru import logger
from oslo_config import cfg
from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import Reactive
from textual.widget import Widget
from textual.widgets import Label

LOG = logging.getLogger("APRSD")
LOGU = logger
CONF = cfg.CONF
MYCALLSIGN_COLOR = "yellow"


class APRSDPacketWidget(Widget):
    """Display an APRS packet."""

    DEFAULT_CSS = """
    APRSDPacketWidget {
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

    @property
    def is_tx(self):
        """Did we TX this packet?"""
        return self.packet.from_call == CONF.callsign

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
            DEGREES_COLOR = "[b #62C2DD]"
            DEGREES_COLOR_END = "[/b #62C2DD]"
            DISTANCE_COLOR = "[orange]"
            DISTANCE_COLOR_END = "[/orange]"
            my_coords = (float(CONF.latitude), float(CONF.longitude))
            packet_coords = (float(self.packet.latitude), float(self.packet.longitude))
            try:
                bearing = utils.calculate_initial_compass_bearing(
                    my_coords, packet_coords
                )
            except Exception as e:
                LOG.error(f"Failed to calculate bearing: {e}")
                bearing = 0

            # cardinal = utils.degrees_to_cardinal(bearing, full_string=True)
            # cardinal_color = f"b {utils.hex_from_name(cardinal)}"

            return (
                f"{DEGREES_COLOR}{utils.degrees_to_cardinal(bearing, full_string=True)}{DEGREES_COLOR_END} "
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

        if self.packet.msgNo:
            title.append(f":{self.packet.msgNo}")

        distance_msg = self._distance_msg()
        if distance_msg:
            title.append(f" : {distance_msg}")

        if self.is_tx:
            if self.acked:
                title.append(" 👍")
            else:
                title.append(" 👎")
        self.border_title = " ".join(title)

    def _build_subtitle(self):
        date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pkt_type = self.packet.__class__.__name__
        pkt_type_color = f"b {utils.hex_from_name(pkt_type)}"
        self.border_subtitle = (
            f"{date_str} [{pkt_type_color}]{pkt_type}[/{pkt_type_color}]"
        )
        self.styles.border_subtitle_color = "rgb(150,150,150)"

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
