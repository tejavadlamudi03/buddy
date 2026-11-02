import io
import json
import time
import urllib.error

import pytest

from buddy import ai, config, models

BIG_CARD = {"card": "RTX", "video_gb": 8.6, "memory_gb": 32.0, "free_gb": 200.0}
NO_CARD = {"card": "", "video_gb": 0.0, "memory_gb": 16.0, "free_gb": 200.0}


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(ai, "ensure_running", lambda: None)
    monkeypatch.setattr(ai, "installed_models", lambda: {"gemma4:e4b-it-q4_K_M": 6_580_000_000, "tiny:1b": 1_000_000_000})
    monkeypatch.setattr(ai, "can_see", lambda name: name != "tiny:1b")
    monkeypatch.setattr(models, "hardware", lambda: BIG_CARD)
    monkeypatch.setattr(models, "download", models.Download())


def test_names():
    for good in ["gemma4:e2b", "qwen2.5vl:7b", "someone/model:v1.2", "llama3.2"]:
        assert models.check_name(f" {good} ") == good
    for bad in ["", "../../etc", "a b", "a;rm -rf", "http://evil/model", "-flag", "a:b:c"]:
        with pytest.raises(ValueError):
            models.check_name(bad)


def test_models_on_other_peoples_servers_are_refused():
    for name in ["gpt-oss:120b-cloud", "kimi:cloud", "cloudy:1b"]:
        with pytest.raises(ValueError, match="servers"):
            models.check_name(name)


def test_every_listed_model_is_a_valid_local_name():
    for model in models.CATALOG:
        assert models.check_name(model["name"]) == model["name"]


def test_how_well_a_model_fits():
    assert models.fit(6.6, BIG_CARD)["level"] == "good"
    assert models.fit(7.8, BIG_CARD)["level"] == "slow"
    assert models.fit(40, BIG_CARD)["level"] == "big"
    assert models.fit(4.6, NO_CARD)["level"] == "slow"
    assert "no graphics card" in models.fit(4.6, NO_CARD)["text"]
    assert models.fit(15.5, NO_CARD)["level"] == "big"


def test_the_recommendation_suits_the_laptop(settings, monkeypatch):
    assert models.status()["recommended"] == "gemma4:e4b-it-q4_K_M"
    monkeypatch.setattr(models, "hardware", lambda: NO_CARD)
    assert models.status()["recommended"] == "gemma4:e2b"
    monkeypatch.setattr(models, "hardware", lambda: {**NO_CARD, "memory_gb": 4.0})
    assert models.status()["recommended"] == ""


def test_status_splits_installed_from_available(settings):
    status = models.status()
    assert {model["name"] for model in status["installed"]} == {"gemma4:e4b-it-q4_K_M", "tiny:1b"}
    assert "gemma4:e4b-it-q4_K_M" not in {model["name"] for model in status["available"]}
    assert "gemma4:e2b" in {model["name"] for model in status["available"]}
    assert status["installed"][0]["fit"]["level"] == "good"


def test_status_survives_the_ai_not_being_there(settings, monkeypatch):
    def missing():
        raise ai.NotReady("The local AI isn't installed yet.")
    monkeypatch.setattr(ai, "ensure_running", missing)
    status = models.status()
    assert "isn't installed" in status["problem"] and status["installed"] == []


def test_choosing_a_model_saves_it(settings):
    assert models.use("tiny:1b") == {"current": "tiny:1b", "sees": False}
    assert config.load()["ai_model"] == "tiny:1b"


def test_a_model_that_isnt_here_cant_be_chosen(settings):
    before = config.load()["ai_model"]
    with pytest.raises(ValueError, match="isn't downloaded"):
        models.use("gemma4:e2b")
    assert config.load()["ai_model"] == before


def wait_for(download):
    deadline = time.monotonic() + 3
    while download.active and time.monotonic() < deadline:
        time.sleep(0.005)
    return download.progress


class Stream(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def lines(*news):
    return Stream(b"".join(json.dumps(item).encode() + b"\n" for item in news))


def test_pull_adds_up_the_layers(monkeypatch):
    monkeypatch.setattr(ai, "ensure_running", lambda: None)
    monkeypatch.setattr(ai.urllib.request, "urlopen", lambda *args, **kwargs: lines(
        {"status": "pulling manifest"},
        {"status": "pulling a", "digest": "a", "total": 100, "completed": 40},
        {"status": "pulling b", "digest": "b", "total": 50},
        {"status": "pulling a", "digest": "a", "total": 100, "completed": 100},
        {"status": "success"}))
    seen = []
    assert ai.pull("m", lambda done, total: seen.append((done, total)), lambda: False) is True
    assert seen == [(40, 100), (40, 150), (100, 150)]


def test_pull_reports_an_error_in_the_stream(monkeypatch):
    monkeypatch.setattr(ai, "ensure_running", lambda: None)
    monkeypatch.setattr(ai.urllib.request, "urlopen", lambda *args, **kwargs: lines({"error": "file does not exist"}))
    with pytest.raises(RuntimeError, match="does not exist"):
        ai.pull("m", lambda done, total: None, lambda: False)


def test_pull_reports_a_refusal(monkeypatch):
    def refuse(*args, **kwargs):
        raise urllib.error.HTTPError("u", 500, "no", {}, io.BytesIO(b'{"error": "pull model manifest: file does not exist"}'))
    monkeypatch.setattr(ai, "ensure_running", lambda: None)
    monkeypatch.setattr(ai.urllib.request, "urlopen", refuse)
    with pytest.raises(RuntimeError, match="file does not exist"):
        ai.pull("m", lambda done, total: None, lambda: False)


def test_cancelling_stops_a_pull(monkeypatch):
    monkeypatch.setattr(ai, "ensure_running", lambda: None)
    monkeypatch.setattr(ai.urllib.request, "urlopen", lambda *args, **kwargs: lines({"digest": "a", "total": 5}))
    assert ai.pull("m", lambda done, total: None, lambda: True) is False


def test_download_finishes(settings, monkeypatch):
    def pull(name, progress, cancelled):
        progress(5, 10)
        return True
    monkeypatch.setattr(ai, "pull", pull)
    models.download.start("gemma4:e2b")
    progress = wait_for(models.download)
    assert progress["state"] == "finished" and progress["total"] == 10


def test_download_failing_says_why(settings, monkeypatch):
    def pull(name, progress, cancelled):
        raise RuntimeError("no such model")
    monkeypatch.setattr(ai, "pull", pull)
    models.download.start("nothing:here")
    progress = wait_for(models.download)
    assert progress["state"] == "failed" and "no such model" in progress["error"]


def test_only_one_download_at_a_time(settings, monkeypatch):
    release = []
    def pull(name, progress, cancelled):
        while not release:
            time.sleep(0.005)
        return True
    monkeypatch.setattr(ai, "pull", pull)
    models.download.start("gemma4:e2b")
    with pytest.raises(RuntimeError, match="Still downloading"):
        models.download.start("qwen3:4b")
    release.append(1)
    wait_for(models.download)


def test_a_cloud_model_cant_even_start_downloading(settings):
    with pytest.raises(ValueError):
        models.download.start("gpt-oss:cloud")
    assert models.download.progress is None


def test_a_full_disk_stops_a_download(settings, monkeypatch):
    def pull(name, progress, cancelled):
        progress(0, 500 * models.GB)
        return True
    monkeypatch.setattr(ai, "pull", pull)
    class Disk:
        free = 10 * models.GB
    monkeypatch.setattr(models.shutil, "disk_usage", lambda path: Disk)
    models.download.start("huge:1b")
    progress = wait_for(models.download)
    assert progress["state"] == "failed" and "disk" in progress["error"]
