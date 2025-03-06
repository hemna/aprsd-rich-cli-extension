from pathlib import Path

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Markdown,
    Static,
)


class VerticalSuppressClicks(Vertical):
    def on_click(self, message: events.Click) -> None:
        message.stop()


class SplashScreen(ModalScreen[str]):
    header_text = """
        Welcome to APRSD Rich!
    """.split()

    def compose(self) -> ComposeResult:
        markdown_path = Path(__file__).parent / "splash_screen.md"
        with open(markdown_path, "r") as f:
            markdown = f.read()

        with VerticalSuppressClicks(id="splash_outer"):
            yield Static(" ".join(self.header_text), id="splash_header")
            with VerticalScroll(id="splash_inner"):
                yield Markdown(markdown=markdown)
            yield Static(
                "Scroll with arrows. Press any other key to continue.",
                id="splash_footer",
            )

    def on_mount(self) -> None:
        container = self.query_one("#splash_outer")
        container.border_title = "APRSD Rich"
        self.body = self.query_one("#splash_inner")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        if event.key == "up":
            self.body.scroll_up()
        elif event.key == "down":
            self.body.scroll_down()
        elif event.key == "left":
            self.body.scroll_left()
        elif event.key == "right":
            self.body.scroll_right()
        elif event.key == "pageup":
            self.body.scroll_page_up()
        elif event.key == "pagedown":
            self.body.scroll_page_down()
        else:
            # self.app.pop_screen()
            self.dismiss(True)

    def on_click(self) -> None:
        # self.app.pop_screen()
        self.dismiss(True)
