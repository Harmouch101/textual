from __future__ import annotations

from rich.console import RenderableType
import rich.repr
from rich.style import Style


from . import events, messages, errors

from .geometry import Offset, Region
from ._compositor import Compositor
from .widget import Widget
from .renderables.gradient import VerticalGradient


@rich.repr.auto
class Screen(Widget):
    """A widget for the root of the app."""

    DEFAULT_STYLES = """

    layout: dock
    docks: _default=top;

    """

    def __init__(self, name: str | None = None, id: str | None = None) -> None:
        super().__init__(name=name, id=id)
        self._compositor = Compositor()

    @property
    def is_visual(self) -> bool:
        return False

    @property
    def is_transparent(self) -> bool:
        return False

    def render(self) -> RenderableType:
        return VerticalGradient("red", "blue")
        return self._compositor

    def render_background(self) -> RenderableType:
        return VerticalGradient("#000000", "#00ff00")

    def get_offset(self, widget: Widget) -> Offset:
        """Get the absolute offset of a given Widget.

        Args:
            widget (Widget): A widget

        Returns:
            Offset: The widget's offset relative to the top left of the terminal.
        """
        return self._compositor.get_offset(widget)

    def get_widget_at(self, x: int, y: int) -> tuple[Widget, Region]:
        """Get the widget at a given coordinate.

        Args:
            x (int): X Coordinate.
            y (int): Y Coordinate.

        Returns:
            tuple[Widget, Region]: Widget and screen region.
        """
        return self._compositor.get_widget_at(x, y)

    def get_style_at(self, x: int, y: int) -> Style:
        """Get the style under a given coordinate.

        Args:
            x (int): X Coordinate.
            y (int): Y Coordinate.

        Returns:
            Style: Rich Style object
        """
        return self._compositor.get_style_at(x, y)

    def get_widget_region(self, widget: Widget) -> Region:
        """Get the screen region of a Widget.

        Args:
            widget (Widget): A Widget within the composition.

        Returns:
            Region: Region relative to screen.
        """
        return self._compositor.get_widget_region(widget)

    async def refresh_layout(self) -> None:

        # await self._compositor.mount_all(self)

        if not self.size:
            return

        try:
            hidden, shown, resized = self._compositor.reflow(self, self.size)

            for widget in hidden:
                widget.post_message_no_wait(events.Hide(self))
            for widget in shown:
                widget.post_message_no_wait(events.Show(self))

            send_resize = shown | resized

            for widget, region, unclipped_region, virtual_size in self._compositor:
                widget._update_size(unclipped_region.size)
                if widget in send_resize:
                    widget.post_message_no_wait(
                        events.Resize(self, unclipped_region.size, virtual_size)
                    )

        except Exception:
            self.app.panic()

        self.app.refresh()

    async def handle_update(self, message: messages.Update) -> None:
        message.stop()
        widget = message.widget
        assert isinstance(widget, Widget)

        display_update = self._compositor.update_widget(self.console, widget)
        if display_update is not None:
            self.app.display(display_update)

    async def handle_layout(self, message: messages.Layout) -> None:
        message.stop()
        await self.refresh_layout()

    async def on_resize(self, event: events.Resize) -> None:
        self._update_size(event.size)
        await self.refresh_layout()
        event.stop()

    async def on_idle(self, event: events.Idle) -> None:
        if self._compositor.check_update():
            self._compositor.reset_update()
            await self.refresh_layout()

    async def _on_mouse_move(self, event: events.MouseMove) -> None:

        try:
            if self.app.mouse_captured:
                widget = self.app.mouse_captured
                region = self.get_widget_region(widget)
            else:
                widget, region = self.get_widget_at(event.x, event.y)
        except errors.NoWidget:
            await self.app.set_mouse_over(None)
        else:
            await self.app.set_mouse_over(widget)
            mouse_event = events.MouseMove(
                self,
                event.x - region.x,
                event.y - region.y,
                event.delta_x,
                event.delta_y,
                event.button,
                event.shift,
                event.meta,
                event.ctrl,
                screen_x=event.screen_x,
                screen_y=event.screen_y,
                style=event.style,
            )
            mouse_event.set_forwarded()
            await widget.forward_event(mouse_event)

    async def forward_event(self, event: events.Event) -> None:
        if event.is_forwarded:
            return
        event.set_forwarded()
        if isinstance(event, (events.Enter, events.Leave)):
            await self.post_message(event)

        elif isinstance(event, events.MouseMove):
            event.style = self.get_style_at(event.screen_x, event.screen_y)
            await self._on_mouse_move(event)

        elif isinstance(event, events.MouseEvent):
            try:
                if self.app.mouse_captured:
                    widget = self.app.mouse_captured
                    region = self.get_widget_region(widget)
                else:
                    widget, region = self.get_widget_at(event.x, event.y)
            except errors.NoWidget:
                await self.app.set_focus(None)
            else:
                if isinstance(event, events.MouseDown) and widget.can_focus:
                    await self.app.set_focus(widget)
                event.style = self.get_style_at(event.screen_x, event.screen_y)
                await widget.forward_event(event.offset(-region.x, -region.y))

        elif isinstance(event, (events.MouseScrollDown, events.MouseScrollUp)):
            try:
                widget, _region = self.get_widget_at(event.x, event.y)
            except errors.NoWidget:
                return
            scroll_widget = widget
            if scroll_widget is not None:
                await scroll_widget.forward_event(event)
        else:
            self.log("view.forwarded", event)
            await self.post_message(event)