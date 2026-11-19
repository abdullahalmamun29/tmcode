"""Open buffer and multi-tab pane state model for TermCode."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


@dataclass
class OpenBuffer:
    """State of a single open editor tab."""

    path: Path | None = None
    content: str = ""
    saved_content: str = ""
    cursor: tuple[int, int] = (0, 0)

    @property
    def is_dirty(self) -> bool:
        return self.content != self.saved_content

    @property
    def title(self) -> str:
        return self.path.name if self.path is not None else "Untitled"

    def mark_saved(self, path: Path | None = None) -> None:
        if path is not None:
            self.path = path.resolve()
        self.saved_content = self.content


@dataclass
class EditorPaneState:
    """Manages the list of open tabs and active tab index for one editor pane."""

    tabs: list[OpenBuffer] = field(default_factory=list)
    active_index: int = -1

    @property
    def active_buffer(self) -> OpenBuffer | None:
        if 0 <= self.active_index < len(self.tabs):
            return self.tabs[self.active_index]
        return None

    def find_by_path(self, path: Path) -> OpenBuffer | None:
        resolved = path.resolve()
        for buf in self.tabs:
            if buf.path is not None and buf.path.resolve() == resolved:
                return buf
        return None

    def index_of_path(self, path: Path) -> int | None:
        resolved = path.resolve()
        for idx, buf in enumerate(self.tabs):
            if buf.path is not None and buf.path.resolve() == resolved:
                return idx
        return None

    def ensure_untitled(self) -> OpenBuffer:
        if not self.tabs:
            buf = OpenBuffer()
            self.tabs.append(buf)
            self.active_index = 0
            return buf
        assert self.active_buffer is not None
        return self.active_buffer

    def open_file(self, path: Path, content: str) -> OpenBuffer:
        resolved = path.resolve()
        existing_idx = self.index_of_path(resolved)
        if existing_idx is not None:
            self.active_index = existing_idx
            return self.tabs[existing_idx]

        # Replace a pristine empty untitled buffer if it's the only tab
        if (
            len(self.tabs) == 1
            and self.tabs[0].path is None
            and not self.tabs[0].content
            and not self.tabs[0].is_dirty
        ):
            buf = OpenBuffer(path=resolved, content=content, saved_content=content)
            self.tabs[0] = buf
            self.active_index = 0
            return buf

        buf = OpenBuffer(path=resolved, content=content, saved_content=content)
        self.tabs.append(buf)
        self.active_index = len(self.tabs) - 1
        return buf

    def select_tab(self, index: int) -> OpenBuffer | None:
        if 0 <= index < len(self.tabs):
            self.active_index = index
            return self.tabs[index]
        return None

    def next_tab(self) -> OpenBuffer | None:
        if not self.tabs:
            return None
        self.active_index = (self.active_index + 1) % len(self.tabs)
        return self.tabs[self.active_index]

    def close_tab(self, index: int) -> OpenBuffer | None:
        if not (0 <= index < len(self.tabs)):
            return None
        removed = self.tabs.pop(index)
        if not self.tabs:
            self.active_index = -1
        elif self.active_index >= len(self.tabs):
            self.active_index = len(self.tabs) - 1
        elif index < self.active_index:
            self.active_index -= 1
        return removed

    def handle_rename(self, old_path: Path, new_path: Path) -> None:
        old_res = old_path.resolve()
        new_res = new_path.resolve()
        for buf in self.tabs:
            if buf.path is None:
                continue
            buf_res = buf.path.resolve()
            if buf_res == old_res:
                buf.path = new_res
            elif _is_within(buf_res, old_res):
                buf.path = new_res / buf_res.relative_to(old_res)

    def handle_delete(self, deleted_path: Path) -> None:
        del_res = deleted_path.resolve()
        for buf in self.tabs:
            if buf.path is None:
                continue
            buf_res = buf.path.resolve()
            if buf_res == del_res or _is_within(buf_res, del_res):
                buf.path = None
                buf.saved_content = ""

    def merge_from(self, other: EditorPaneState) -> None:
        """Merge tabs from another pane without duplicating paths or dropping unsaved edits."""
        for buf in other.tabs:
            if buf.path is not None:
                existing = self.find_by_path(buf.path)
                if existing is not None:
                    if buf.is_dirty and not existing.is_dirty:
                        existing.content = buf.content
                    continue
            elif not buf.content and not buf.is_dirty and self.tabs:
                continue
            self.tabs.append(buf)
        if self.tabs and self.active_index < 0:
            self.active_index = 0
        other.tabs.clear()
        other.active_index = -1

    def has_dirty_tabs(self) -> bool:
        return any(buf.is_dirty for buf in self.tabs)

    def clear(self) -> None:
        self.tabs.clear()
        self.active_index = -1
