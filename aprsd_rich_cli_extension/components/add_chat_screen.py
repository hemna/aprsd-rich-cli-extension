from textual.app import ComposeResult
from textual.containers import Grid
from textual.screen import ModalScreen
from textual.widgets import Button, Input


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
