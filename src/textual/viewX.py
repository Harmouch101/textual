from __future__ import annotations

from typing import Callable, Iterable

import rich.repr
from rich.console import RenderableType
from rich.style import Style

from . import errors, events, messages
from ._arrangement import Arrangement
from .geometry import Size, Offset, Region
from .layout import Layout, NoWidget, WidgetPlacement
from .reactive import Reactive, watch
from .widget import Widget


@rich.repr.auto
class View(Widget):

    DEFAULT_STYLES = """
        layout: dock;
        docks: _default=top;
    """

    def __init__(self, name: str | None = None, id: str | None = None) -> None:
        self.mouse_over: Widget | None = None
        self._mouse_style: Style = Style()
        self._mouse_widget: Widget | None = None

        self._arrangement = Arrangement()

        self._cached_arrangement: tuple[Size, Offset, list[WidgetPlacement]] = (
            Size(),
            Offset(),
            [],
        )
        super().__init__(name=name, id=id)

    background: Reactive[str] = Reactive("")
    scroll_x: Reactive[int] = Reactive(0)
    scroll_y: Reactive[int] = Reactive(0)
    virtual_size = Reactive(Size(0, 0))

    async def watch_background(self, value: str) -> None:
        self._arrangement.background = value
        self.app.refresh()

    @property
    def layout(self) -> Layout | None:
        """Convenience property for accessing ``self.styles.layout``.

        Returns: The Layout associated with this view
        """

        return self.styles.layout

    @layout.setter
    def layout(self, new_value: Layout) -> None:
        """Convenience property setter for setting ``view.styles.layout``.
        Args:
            new_value:

        Returns:
            None
        """
        self.styles.layout = new_value

    @property
    def scroll(self) -> Offset:
        return Offset(self.scroll_x, self.scroll_y)

    def __rich_repr__(self) -> rich.repr.Result:
        yield "name", self.name

    @property
    def is_visual(self) -> bool:
        return False

    @property
    def is_root_view(self) -> bool:
        return bool(self._parent and self.parent is self.app)

    def is_mounted(self, widget: Widget) -> bool:
        return self.app.is_mounted(widget)

    def render(self) -> RenderableType:
        return self._arrangement

    def get_offset(self, widget: Widget) -> Offset:
        return self._arrangement.get_offset(widget)

    def get_arrangement(self, size: Size, scroll: Offset) -> Iterable[WidgetPlacement]:
        cached_size, cached_scroll, arrangement = self._cached_arrangement
        if cached_size == size and cached_scroll == scroll:
            return arrangement

        placements = [
            placement.apply_margin()
            for placement in self.layout.arrange(self, size, scroll)
        ]

        self._cached_arrangement = (size, scroll, placements)
        return placements

    async def handle_update(self, message: messages.Update) -> None:
        if self.is_root_view:
            message.stop()
            widget = message.widget
            assert isinstance(widget, Widget)

            display_update = self.layout.update_widget(self.console, widget)
            if display_update is not None:
                self.app.display(display_update)

    async def handle_layout(self, message: messages.Layout) -> None:
        await self.refresh_layout()
        if self.is_root_view:
            message.stop()
            self.app.refresh()

    def mount(self, *anon_widgets: Widget, **widgets: Widget) -> None:
        self.app.register(self, *anon_widgets, **widgets)
        self.refresh()

    async def refresh_layout(self) -> None:
        self._cached_arrangement = (Size(), Offset(), [])
        try:
            await self.layout.mount_all(self)
            if not self.is_root_view:
                await self.app.screen.refresh_layout()
                return

            if not self.size:
                return

            hidden, shown, resized = self.layout.reflow(self, Size(*self.console.size))
            assert self.layout.map is not None

            for widget in hidden:
                widget.post_message_no_wait(events.Hide(self))
            for widget in shown:
                widget.post_message_no_wait(events.Show(self))

            send_resize = shown
            send_resize.update(resized)

            for widget, region, unclipped_region in self.layout:
                widget._update_size(unclipped_region.size)
                if widget in send_resize:
                    widget.post_message_no_wait(
                        events.Resize(self, unclipped_region.size)
                    )
        except Exception:
            self.app.panic()

    async def on_resize(self, event: events.Resize) -> None:
        self._update_size(event.size)
        if self.is_root_view:
            await self.refresh_layout()
            self.app.refresh()
        event.stop()

    def get_widget_at(self, x: int, y: int) -> tuple[Widget, Region]:
        return self.layout.get_widget_at(x, y)

    def get_style_at(self, x: int, y: int) -> Style:
        return self.layout.get_style_at(x, y)

    def get_widget_region(self, widget: Widget) -> Region:
        return self.layout.get_widget_region(widget)

    async def on_mount(self, event: events.Mount) -> None:
        async def watch_background(value: str) -> None:
            self.background = value

        watch(self.app, "background", watch_background)

    async def on_idle(self, event: events.Idle) -> None:
        if self.layout is None:
            return
        if self.layout.check_update():
            self.layout.reset_update()
            await self.refresh_layout()

    async def _on_mouse_move(self, event: events.MouseMove) -> None:

        try:
            if self.app.mouse_captured:
                widget = self.app.mouse_captured
                region = self.get_widget_region(widget)
            else:
                widget, region = self.get_widget_at(event.x, event.y)
        except NoWidget:
            await self.app.set_mouse_over(None)
        else:
            await self.app.set_mouse_over(widget)
            await widget.forward_event(
                events.MouseMove(
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
            )

    async def forward_event(self, event: events.Event) -> None:
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
            except NoWidget:
                pass
            else:
                if isinstance(event, events.MouseDown) and widget.can_focus:
                    await self.app.set_focus(widget)
                event.style = self.get_style_at(event.screen_x, event.screen_y)
                await widget.forward_event(event.offset(-region.x, -region.y))

        elif isinstance(event, (events.MouseScrollDown, events.MouseScrollUp)):
            try:
                widget, _region = self.get_widget_at(event.x, event.y)
            except NoWidget:
                return
            scroll_widget = widget
            if scroll_widget is not None:
                await scroll_widget.forward_event(event)
        else:
            self.log("view.forwarded", event)
            await self.post_message(event)

    # async def action_toggle(self, name: str) -> None:
    #     widget = self[name]
    #     widget.visible = not widget.display
    #     await self.post_message(messages.Layout(self))