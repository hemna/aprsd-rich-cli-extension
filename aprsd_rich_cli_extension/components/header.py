import logging

import aprsd
from rich.text import Text
from textual.app import ComposeResult, RenderResult
from textual.containers import Horizontal
from textual.reactive import Reactive

from aprsd_rich_cli_extension.components import packet_widget

LOG = logging.getLogger("APRSD")


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


class HeaderFilter(Horizontal):
    """Display the filter in the header."""

    text: Reactive[str] = Reactive("")
    """The main title text."""

    sub_text = Reactive("")
    """The sub-title text."""

    def __init__(self, id: str, filter: str = None):
        super().__init__(id=id)
        LOG.error(f"HeaderFilter: {filter}")
        self.filter = filter
        if self.filter:
            self.text = f"Filter: {self.filter}"
        else:
            self.text = "Filter: None"

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


class AppHeader(Horizontal):
    """The header of the app."""

    DEFAULT_CSS = """
    SpinnerWidget {
        content-align: left middle;
    }
    """

    def __init__(self, filter: str = None):
        super().__init__()
        self.filter = filter

    def compose(self) -> ComposeResult:
        yield HeaderConnection(id="app-connection")
        earth = HeaderEarth(id="app-earth")
        earth.display = False
        yield earth
        yield HeaderFilter(id="app-filter")
        yield HeaderVersion(id="app-version")
