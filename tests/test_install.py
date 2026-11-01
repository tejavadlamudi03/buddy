import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts/install.sh"


@pytest.fixture
def laptop(tmp_path):
    """A fake laptop: Ubuntu 24.04 on Xorg with an 8 GB NVIDIA card, unless a test changes that."""
    (tmp_path / "bin").mkdir()
    release = tmp_path / "os-release"
    release.write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
    nvidia = tmp_path / "bin/nvidia-smi"
    nvidia.write_text("#!/bin/sh\necho 8188\n")
    nvidia.chmod(0o755)
    return {"PATH": f"{tmp_path}/bin:{os.environ['PATH']}", "HOME": os.environ["HOME"], "BUDDY_OS_RELEASE": str(release),
            "BUDDY_SESSION_TYPE": "x11", "BUDDY_MEMORY_GB": "32", "BUDDY_FREE_GB": "100", "BUDDY_XORG_SESSION": str(tmp_path / "xorg.desktop"), "_dir": tmp_path}


def check(laptop, *options):
    env = {key: value for key, value in laptop.items() if not key.startswith("_")}
    return subprocess.run(["bash", str(SCRIPT), "--check", *options], capture_output=True, text=True, env=env)


def test_wayland_with_xorg_available_says_how_to_switch(laptop):
    laptop["BUDDY_SESSION_TYPE"] = "wayland"
    (laptop["_dir"] / "xorg.desktop").touch()
    done = check(laptop)
    assert done.returncode == 1 and "Ubuntu on Xorg" in done.stdout and "Nothing has been installed" in done.stdout


def test_wayland_with_no_xorg_says_so(laptop):
    laptop["BUDDY_SESSION_TYPE"] = "wayland"
    done = check(laptop)
    assert done.returncode == 1 and "no Xorg session" in done.stdout


def test_not_ubuntu_is_refused(laptop):
    (laptop["_dir"] / "os-release").write_text('ID=fedora\nVERSION_ID="40"\n')
    done = check(laptop)
    assert done.returncode == 1 and "isn't Ubuntu" in done.stdout


def test_another_ubuntu_is_only_a_warning(laptop):
    (laptop["_dir"] / "os-release").write_text('ID=ubuntu\nVERSION_ID="26.04"\n')
    assert "hasn't been tested" in check(laptop).stdout


def set_video(laptop, megabytes):
    (laptop["_dir"] / "bin/nvidia-smi").write_text(f"#!/bin/sh\necho {megabytes}\n")


def test_an_8_gb_card_gets_the_full_setup(laptop):
    done = check(laptop)
    assert done.returncode == 0 and "8188 MB" in done.stdout and "Setup: full" in done.stdout


def test_a_bigger_card_also_gets_the_full_setup(laptop):
    set_video(laptop, 24576)
    assert "Setup: full" in check(laptop).stdout


def test_a_small_card_gets_the_smaller_model_instead_of_being_refused(laptop):
    set_video(laptop, 4096)
    done = check(laptop)
    assert done.returncode == 0 and "Setup: light" in done.stdout and "8 GB of video memory" in done.stdout


def test_no_nvidia_card_still_gets_the_ai_on_the_processor(laptop):
    (laptop["_dir"] / "bin/nvidia-smi").write_text("#!/bin/sh\nexit 1\n")
    done = check(laptop)
    assert done.returncode == 0 and "Setup: light" in done.stdout and "processor" in done.stdout


def test_little_memory_gets_built_in_commands(laptop):
    laptop["BUDDY_MEMORY_GB"] = "8"
    done = check(laptop)
    assert done.returncode == 0 and "Setup: basic" in done.stdout


def test_little_disk_chooses_something_that_fits(laptop):
    laptop["BUDDY_FREE_GB"] = "13"
    assert "Setup: light" in check(laptop).stdout
    laptop["BUDDY_FREE_GB"] = "8"
    assert "Setup: basic" in check(laptop).stdout


def test_too_small_for_anything_is_explained(laptop):
    laptop["BUDDY_MEMORY_GB"] = "4"
    done = check(laptop)
    assert done.returncode == 1 and "at least 8 GB" in done.stdout


def test_the_owner_can_choose(laptop):
    assert "Setup: basic" in check(laptop, "--no-ai").stdout
    done = check(laptop, "--profile=light")
    assert "Setup: light" in done.stdout and "you asked for it" in done.stdout


def test_a_mistyped_option_changes_nothing(laptop):
    env = {key: value for key, value in laptop.items() if not key.startswith("_")}
    assert subprocess.run(["bash", str(SCRIPT), "--instal"], capture_output=True, env=env).returncode == 2


def test_check_only_never_reaches_installing(laptop):
    assert "System packages" not in check(laptop).stdout


def test_every_profile_can_be_applied(tmp_path, monkeypatch):
    from buddy import config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    assert config.apply_profile("full")["ai_context"] == 12288
    light = config.apply_profile("light")
    assert light["ai_model"] == "gemma4:e2b" and light["agent_enabled"]
    assert config.apply_profile("basic")["agent_enabled"] is False
    assert config.load()["profile"] == "basic"


def test_the_page_still_has_everything_its_code_uses():
    """Buddy finds its own window by this title, and the script looks these up by id."""
    import re
    page = (Path(__file__).parent.parent / "buddy/static/index.html").read_text()
    assert "<title>Buddy Voice Control</title>" in page
    defined = set(re.findall(r'\bid="([\w-]+)"', page))
    used = set(re.findall(r"\$\('([\w-]+)'\)", page))
    assert used <= defined, used - defined
