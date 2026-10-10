"""Gamepad passthrough (frame.gamepad_passthrough): Steam Input's virtual pads into a game's Lepton container."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEPTON = "export LEPTON_ENV_FRAMEBRIDGE_CONFIG=x\n{lines}child=''\nsetsid /lepton start\n"
LINEAGE_RC = """# LineageOS core functionality
on init
    export TERMINFO /system/etc/terminfo

service bugreport /system/bin/dumpstate -d -p -z
    oneshot
    disabled
    keycodes 114 115 116
"""


def agent_module(monkeypatch, tmp_path):
    if sys.platform == "win32":
        monkeypatch.setitem(sys.modules, "fcntl", types.SimpleNamespace(flock=lambda *_: None, LOCK_EX=2))
    spec = importlib.util.spec_from_file_location("gamepad_agent", ROOT / "agent/frameport_agent.py")
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)
    monkeypatch.setattr(agent, "GAMEPAD_DIR", str(tmp_path / "gamepad"))
    monkeypatch.setattr(agent, "VIDEO_CODEC_DIR", str(tmp_path / "codec"))
    monkeypatch.setattr(agent, "ANCHORS", str(tmp_path / "apps"))
    return agent


def wrapper_module(tmp_path, agent):
    path = tmp_path / "gamepad/bin/podman"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(agent.GAMEPAD_PODMAN)
    (tmp_path / "gamepad" / "Vendor_28de_Product_11ff.kl").write_text(agent.GAMEPAD_KEYLAYOUT)
    spec = importlib.util.spec_from_loader("gamepad_wrapper", loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    exec(compile(agent.GAMEPAD_PODMAN, str(path), "exec"), module.__dict__)
    return module


def fake_input(tmp_path, devices):
    sys_input, dev_input = tmp_path / "sys", tmp_path / "dev"
    dev_input.mkdir(parents=True, exist_ok=True)
    for name, vendor, product in devices:
        ids = sys_input / name / "device" / "id"
        ids.mkdir(parents=True)
        (ids / "vendor").write_text(vendor + "\n")
        (ids / "product").write_text(product + "\n")
        (dev_input / name).write_text("")
    return sys_input, dev_input


def anchor(tmp_path, pkg, launcher, **dep):
    directory = tmp_path / "apps" / pkg
    directory.mkdir(parents=True)
    (directory / "launch.sh").write_text(launcher)
    (directory / "deployment.json").write_text(json.dumps({"package": pkg, "base": str(tmp_path / pkg), **dep}))
    return directory


def test_new_launchers_follow_the_recipe(monkeypatch, tmp_path):
    agent = agent_module(monkeypatch, tmp_path)
    for want in (True, False):
        directory = tmp_path / f"apps/game{want}"
        directory.mkdir(parents=True)
        agent.write_launcher(str(directory), str(tmp_path / "base"), "com.example.game", "Game", 123,
                             "/lepton/lepton", {}, hw_video=True, gamepad=want)
        text = (directory / "launch.sh").read_text()
        assert (agent.GAMEPAD_LINE in text) is want
        assert agent.set_gamepad_line(text, want) == text  # upgrade_launchers leaves it as it is
        if want:  # after the codec line: the gamepad wrapper is first on PATH and chains on to the codec wrapper
            assert text.index(agent.VIDEO_CODEC_LINE) < text.index(agent.GAMEPAD_LINE) < text.index("\nchild=''")
    assert (tmp_path / "gamepad/bin/podman").read_text() == agent.GAMEPAD_PODMAN
    assert (tmp_path / "gamepad/Vendor_28de_Product_11ff.kl").read_text() == agent.GAMEPAD_KEYLAYOUT


def test_upgrade_adds_and_removes_the_line(monkeypatch, tmp_path):
    agent = agent_module(monkeypatch, tmp_path)
    pad = anchor(tmp_path, "com.nakas.skate3", LEPTON.format(lines=""),
                 recipe={"patches": ["frame.sdl_clipboard", "frame.gamepad_passthrough"]})
    plain = anchor(tmp_path, "com.other.game", LEPTON.format(lines=agent.GAMEPAD_LINE + "\n"),
                   gamepad_passthrough=False)
    assert set(agent.upgrade_launchers()) == {"com.nakas.skate3", "com.other.game"}
    assert (pad / "launch.sh").read_text().count(agent.GAMEPAD_LINE) == 1
    assert agent.GAMEPAD_LINE not in (plain / "launch.sh").read_text()
    assert (tmp_path / "gamepad/bin/podman").exists()
    assert agent.upgrade_launchers() == []  # idempotent


def test_wrapper_mounts_pads_layout_and_keychord_free_init(monkeypatch, tmp_path):
    agent = agent_module(monkeypatch, tmp_path)
    wrapper = wrapper_module(tmp_path, agent)
    sys_input, dev_input = fake_input(tmp_path, [("event4", "28de", "0000"), ("event5", "28de", "11ff"),
                                                 ("event12", "28de", "11ff"), ("event0", "045e", "028e")])
    monkeypatch.setattr(wrapper, "SYS_INPUT", sys_input)
    monkeypatch.setattr(wrapper, "DEV_INPUT", dev_input)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    (tmp_path / "run").mkdir()
    root = tmp_path / "rootfs"
    (root / "system/etc/init").mkdir(parents=True)
    (root / "system/etc/init/lineage-system.rc").write_text(LINEAGE_RC)
    (root / "system/etc/init/other.rc").write_text("service x /bin/x\n")
    monkeypatch.setenv("SteamAppId", "3161551320")
    args = ["run", "--name", "lepton-steamlaunch-3161551320", "--rootfs", f"{root}:O", "/init"]
    mounts = wrapper.mounts(args)
    pairs = [mounts[i + 1] for i in range(0, len(mounts), 2)]
    assert all(mounts[i] == "--mount" for i in range(0, len(mounts), 2))
    assert pairs[0] == f"type=bind,source={dev_input / 'event5'},destination=/dev/input/event5,rw"
    assert pairs[1] == f"type=bind,source={dev_input / 'event12'},destination=/dev/input/event12,rw"
    assert pairs[2].endswith("destination=/system/usr/keylayout/Vendor_28de_Product_11ff.kl,ro")
    assert pairs[3].endswith("destination=/system/etc/init/lineage-system.rc,ro")
    assert len(pairs) == 4  # other.rc has no keychord: untouched
    fixed = Path(pairs[3].split("source=")[1].split(",")[0]).read_text()
    assert "keycodes" not in fixed and "service bugreport" in fixed and "    disabled\n" in fixed


def test_wrapper_leaves_other_containers_and_missing_pads_alone(monkeypatch, tmp_path):
    agent = agent_module(monkeypatch, tmp_path)
    wrapper = wrapper_module(tmp_path, agent)
    sys_input, dev_input = fake_input(tmp_path, [("event4", "28de", "0000")])
    monkeypatch.setattr(wrapper, "SYS_INPUT", sys_input)
    monkeypatch.setattr(wrapper, "DEV_INPUT", dev_input)
    root = tmp_path / "rootfs"
    (root / "system/etc/init").mkdir(parents=True)
    monkeypatch.setenv("SteamAppId", "1")
    run = ["run", "--name", "lepton-steamlaunch-1", "--rootfs", f"{root}:O", "/init"]
    assert wrapper.mounts(run) == []  # no Steam Input pad
    assert wrapper.mounts(["exec", "lepton-steamlaunch-1", "logcat"]) == []
    assert wrapper.mounts(["run", "--name", "something-else", "--rootfs", f"{root}:O"]) == []


def test_wrapper_skips_an_unknown_rootfs_layout(monkeypatch, tmp_path):
    agent = agent_module(monkeypatch, tmp_path)
    wrapper = wrapper_module(tmp_path, agent)
    sys_input, dev_input = fake_input(tmp_path, [("event5", "28de", "11ff")])
    monkeypatch.setattr(wrapper, "SYS_INPUT", sys_input)
    monkeypatch.setattr(wrapper, "DEV_INPUT", dev_input)
    monkeypatch.setenv("SteamAppId", "1")
    (tmp_path / "rootfs").mkdir()  # no init folder: keychords can't be checked, so no pads (boot could hang)
    assert wrapper.mounts(["run", "--name", "lepton-steamlaunch-1", "--rootfs", f"{tmp_path / 'rootfs'}:O"]) == []


@pytest.mark.skipif(sys.platform == "win32", reason="runs the launcher line in bash")
@pytest.mark.parametrize("env,wrapper,expected", [("", True, True), ("1", True, False), ("0", True, True),
                                                  ("", False, False)])
def test_launcher_line(monkeypatch, tmp_path, env, wrapper, expected):
    import subprocess

    agent = agent_module(monkeypatch, tmp_path)
    home = tmp_path / "home"
    if wrapper:
        podman = home / ".local/share/frameport/gamepad/bin/podman"
        podman.parent.mkdir(parents=True)
        podman.write_text("#!/bin/sh\n")
        podman.chmod(0o755)
    script = agent.GAMEPAD_LINE + '\necho "$PATH|${LEPTON_ENV_SDL_GAMECONTROLLER_ALLOW_STEAM_VIRTUAL_GAMEPAD:-}"\n'
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True,
                         env={"HOME": str(home), "PATH": "/usr/bin:/bin", "FRAMEPORT_NO_GAMEPAD": env}).stdout
    path, hint = out.strip().split("|")
    assert path.startswith(str(home / ".local/share/frameport/gamepad/bin")) is expected
    assert (hint == "1") is expected


def test_patch_is_suggested_for_flat_apps_only():
    from frameport.core.models import Analysis
    from frameport.patches.base import get, load_all

    load_all()
    patch = get("frame.gamepad_passthrough")
    assert patch.stage == "install" and patch.needs_vr is False
    flat = Analysis.__new__(Analysis)
    flat.extra = {"vr_kind": "none"}
    quest = Analysis.__new__(Analysis)
    quest.extra = {}
    assert patch.detect(flat).recommended
    assert patch.detect(quest) is None
