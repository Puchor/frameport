"""Gamepad passthrough: controllers reach Android apps as a gamepad, not only as touch/pointer.

Lepton's Android gets just three input devices from the Wayland seat (touch, pointer, keyboard): Wayland has no gamepad
protocol and Lepton has no controller passthrough, so SDL/Android games that support controllers fall back to their
on-screen controls (e.g. Skate 3's Android port). Steam Input already presents every controller as a virtual Xbox 360
pad (evdev 28de:11ff, readable by the steamos user). This patch only selects the fix per game: the agent gives the
game's launcher the gamepad line (deployment.json `gamepad_passthrough`), which puts its Podman wrapper first on
Lepton's PATH; the wrapper bind-mounts those pads into this game's container only, adds a key layout for them, drops
init's keychords (with a real input device present they made Android's boot hang on Lepton's FIFOs, found by Armada's
Lepton, armada-os/armada PR #596) and the launcher sets SDL's hint to accept Steam's virtual pad. The APK is not
changed. Controllers must be on when the game starts (no hot-plug). One game: FRAMEPORT_NO_GAMEPAD=1 %command%.
"""
from __future__ import annotations

from ..base import Patch, Suggestion, register


class GamepadPassthrough(Patch):
    id = "frame.gamepad_passthrough"
    title = "Pass controllers to the app as a gamepad"
    description = ("Lepton only gives Android apps touch, pointer and keyboard input, so games that support "
                   "controllers show their on-screen controls instead (e.g. Skate 3's Android port). This passes "
                   "Steam Input's controllers into this game's container as an Xbox-style gamepad. The APK isn't "
                   "changed. Turn your controllers on before starting the game.")
    stage = "install"  # the agent reads it from the recipe at finalize: no APK change
    order = 49
    needs_vr = False

    def applies(self, a):
        return True

    def detect(self, a):
        if a.vr_kind == "none":
            return Suggestion(True, "A flat Android app: pass your controllers to it as a gamepad.")
        return None


register(GamepadPassthrough)
