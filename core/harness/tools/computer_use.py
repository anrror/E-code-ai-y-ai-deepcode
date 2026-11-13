"""Desktop control on Windows: screen capture, pointer and keyboard input.

DeepSeek Harness's computer-use plugin has a native helper with an accessibility
tree; here the model gets what Windows exposes without extra services: a real
screenshot it can look at, and real pointer/keyboard input. Coordinates are
physical screen pixels.
"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

from core.agent_runtime.tools.base import Tool, ToolResult, tool_parameters

_SHOT_DIR = Path(os.path.expanduser("~")) / ".deepcode" / "computer-shots"

_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_WHEEL = 0x0800

_VK = {
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    "esc": 0x1B,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
}


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT(ctypes.Structure):
    class _UNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


def _user32() -> Any:
    user32 = ctypes.windll.user32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001, S110 - older Windows has no shcore
        pass
    return user32


def _send(*inputs: _INPUT) -> None:
    if not inputs:
        return
    array = (_INPUT * len(inputs))(*inputs)
    ctypes.windll.user32.SendInput(len(inputs), array, ctypes.sizeof(_INPUT))


def _mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> _INPUT:
    item = _INPUT()
    item.type = _INPUT_MOUSE
    item.mi = _MOUSEINPUT(dx, dy, data, flags, 0, None)
    return item


def _key_input(vk: int, up: bool = False) -> _INPUT:
    item = _INPUT()
    item.type = _INPUT_KEYBOARD
    item.ki = _KEYBDINPUT(vk, 0, _KEYEVENTF_KEYUP if up else 0, 0, None)
    return item


def _unicode_input(char: str, up: bool = False) -> _INPUT:
    item = _INPUT()
    item.type = _INPUT_KEYBOARD
    flags = _KEYEVENTF_UNICODE | (_KEYEVENTF_KEYUP if up else 0)
    item.ki = _KEYBDINPUT(0, ord(char), flags, 0, None)
    return item


def _type_text(text: str) -> None:
    for char in text:
        if char == "\n":
            _send(_key_input(_VK["enter"]), _key_input(_VK["enter"], up=True))
            continue
        _send(_unicode_input(char), _unicode_input(char, up=True))


def _press_chord(chord: str) -> str:
    parts = [part.strip().lower() for part in chord.split("+") if part.strip()]
    if not parts:
        return "Error: empty key"
    keys: list[int] = []
    for part in parts:
        if part in _VK:
            keys.append(_VK[part])
        elif len(part) == 1:
            scan = _user32().VkKeyScanW(ord(part))
            if scan == -1:
                return f"Error: unsupported key '{part}'"
            keys.append(scan & 0xFF)
        else:
            return f"Error: unsupported key '{part}'"
    _send(*[_key_input(vk) for vk in keys])
    _send(*[_key_input(vk, up=True) for vk in reversed(keys)])
    return f"pressed {chord}"


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "screenshot",
                    "size",
                    "move",
                    "click",
                    "double_click",
                    "right_click",
                    "scroll",
                    "type",
                    "key",
                ],
                "description": "Desktop action to perform.",
            },
            "x": {"type": "integer", "description": "Screen x in pixels."},
            "y": {"type": "integer", "description": "Screen y in pixels."},
            "text": {"type": "string", "description": "Text for action=type."},
            "key": {
                "type": "string",
                "description": "Key or chord for action=key, e.g. Enter, Control+S.",
            },
            "amount": {
                "type": "integer",
                "description": "Wheel notches for action=scroll (positive is up).",
            },
        },
        "required": ["action"],
    }
)
class ComputerUseTool(Tool):
    """See the screen and drive the mouse and keyboard on Windows."""

    @property
    def name(self) -> str:
        return "computer"

    @property
    def description(self) -> str:
        return (
            "Control the Windows desktop: take a screenshot, move the pointer, "
            "click, scroll, type text and press key chords. Coordinates are "
            "physical screen pixels; take a screenshot first to see the layout."
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        if os.name != "nt":
            return ToolResult(
                "Error: computer control is implemented for Windows only",
                is_error=True,
            )
        action = str(kwargs.get("action") or "").strip()
        try:
            text = self._run(action, kwargs)
        except Exception as exc:  # noqa: BLE001 - report, never crash the Turn
            return ToolResult(f"Error: {str(exc)[:300]}", is_error=True)
        return ToolResult(text)

    def _point(self, kwargs: dict[str, Any]) -> tuple[int, int]:
        x = kwargs.get("x")
        y = kwargs.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            raise TypeError("x and y are required for this action")
        return x, y

    def _run(self, action: str, kwargs: dict[str, Any]) -> str:
        user32 = _user32()
        if action == "screenshot":
            from PIL import ImageGrab

            _SHOT_DIR.mkdir(parents=True, exist_ok=True)
            path = _SHOT_DIR / f"screen-{time.strftime('%Y%m%d-%H%M%S')}.png"
            image = ImageGrab.grab(all_screens=True)
            image.save(path)
            return f"screenshot saved: {path} ({image.width}x{image.height})"
        if action == "size":
            width = user32.GetSystemMetrics(0)
            height = user32.GetSystemMetrics(1)
            return f"screen: {width}x{height}"
        if action == "move":
            x, y = self._point(kwargs)
            user32.SetCursorPos(x, y)
            return f"pointer moved to {x},{y}"
        if action in {"click", "double_click", "right_click"}:
            x, y = self._point(kwargs)
            user32.SetCursorPos(x, y)
            time.sleep(0.05)
            if action == "right_click":
                _send(_mouse_input(_MOUSEEVENTF_RIGHTDOWN))
                _send(_mouse_input(_MOUSEEVENTF_RIGHTUP))
            else:
                clicks = 2 if action == "double_click" else 1
                for _ in range(clicks):
                    _send(_mouse_input(_MOUSEEVENTF_LEFTDOWN))
                    _send(_mouse_input(_MOUSEEVENTF_LEFTUP))
                    time.sleep(0.05)
            return f"{action} at {x},{y}"
        if action == "scroll":
            amount = kwargs.get("amount")
            notches = amount if isinstance(amount, int) else 3
            _send(_mouse_input(_MOUSEEVENTF_WHEEL, data=120 * notches))
            return f"scrolled {notches} notch(es)"
        if action == "type":
            text = kwargs.get("text")
            if not isinstance(text, str) or not text:
                raise TypeError("text is required for action=type")
            _type_text(text)
            return f"typed {len(text)} characters"
        if action == "key":
            key = str(kwargs.get("key") or "").strip()
            if not key:
                raise TypeError("key is required for action=key")
            return _press_chord(key)
        raise ValueError(f"unknown action {action!r}")
