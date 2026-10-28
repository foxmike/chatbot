"""AVTR worker helpers (portrait handling, loader capture, EOF shutdown) with fake cv2/AVTR modules.

The real worker runs in AVTR's own environment on the GPU; these tests cover its pure logic only.
"""
import importlib.util
import io
import queue
import sys
import threading
import types
from pathlib import Path

from PIL import Image
import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "chatbot/avatar/worker_scripts/avatar_worker.py"


class FakeCV2(types.ModuleType):
    IMREAD_UNCHANGED = -1
    COLOR_GRAY2BGR = 8
    INTER_AREA = 3
    INTER_CUBIC = 2

    def __init__(self):
        super().__init__("cv2")
        self.files, self.written = {}, {}

    def imread(self, path, flag=None):
        return self.files.get(str(path))

    def imwrite(self, path, image):
        self.written[str(path)] = image
        return True

    def resize(self, image, size, interpolation=None):
        return np.asarray(Image.fromarray(image).resize(size, resample=Image.Resampling.NEAREST))

    def cvtColor(self, image, code):
        return np.repeat(image[:, :, None], 3, axis=2)


@pytest.fixture
def worker(monkeypatch):
    cv2 = FakeCV2()
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    spec = importlib.util.spec_from_file_location("avatar_worker_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, cv2


def test_prepare_portrait_accepts_color_gray_and_rgba_images(worker):
    module, cv2 = worker
    cv2.files.update({"/p/color.jpg": np.zeros((200, 100, 3), np.uint8),
                      "/p/gray.png": np.zeros((200, 100), np.uint8),
                      "/p/alpha.png": np.zeros((200, 100, 4), np.uint8)})
    for source in cv2.files:
        out = module.prepare_portrait(source, "/tmp/d", "portrait-1")
        assert out == "/tmp/d/portrait-1.png" and cv2.written[out].shape[2] in (3, 4)


@pytest.mark.parametrize("image, message", [
    (None, "Cannot read"),
    (np.zeros((10, 500, 3), np.uint8), "too small"),
    (np.zeros((200, 200, 2), np.uint8), "Unsupported"),
])
def test_prepare_portrait_rejects_unusable_images(worker, image, message):
    module, cv2 = worker
    if image is not None:
        cv2.files["/p/bad.png"] = image
    with pytest.raises(ValueError, match=message):
        module.prepare_portrait("/p/bad.png", "/tmp/d", "portrait-2")


def install_fake_avtr(monkeypatch, construct=True):
    package = types.ModuleType("avtr1_renderer")
    loader_module = types.ModuleType("avtr1_renderer.avatar_loader")

    class AvatarLoader:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
    loader_module.AvatarLoader = AvatarLoader
    package.avatar_loader = loader_module
    monkeypatch.setitem(sys.modules, "avtr1_renderer", package)
    monkeypatch.setitem(sys.modules, "avtr1_renderer.avatar_loader", loader_module)

    def from_artifacts(**kwargs):   # imports the loader inside, like AVTR's Pipeline.from_artifacts
        if construct:
            from avtr1_renderer.avatar_loader import AvatarLoader as Loader
            Loader(out_h=720)
        return ("pipeline", {}), kwargs
    return loader_module, AvatarLoader, from_artifacts


def test_capture_avatar_loader_returns_the_pipelines_own_loader_and_restores_the_class(worker, monkeypatch):
    module, _ = worker
    loader_module, original, from_artifacts = install_fake_avtr(monkeypatch)
    result, loader = module.capture_avatar_loader(from_artifacts, avatar_ids=[], portraits_dir="/tmp/x")
    assert result[0][0] == "pipeline" and result[1] == {"avatar_ids": [], "portraits_dir": "/tmp/x"}
    assert isinstance(loader, original) and loader.kwargs == {"out_h": 720}
    assert loader_module.AvatarLoader is original


def test_capture_fails_clearly_when_no_loader_was_built_and_still_restores(worker, monkeypatch):
    module, _ = worker
    loader_module, original, from_artifacts = install_fake_avtr(monkeypatch, construct=False)
    with pytest.raises(RuntimeError, match="cannot switch portraits"):
        module.capture_avatar_loader(from_artifacts)
    assert loader_module.AvatarLoader is original


def test_reader_forwards_commands_wakes_on_portrait_and_stops_on_parent_exit(worker, monkeypatch):
    module, _ = worker
    lines = "\n".join([
        '{"type":"portrait","portrait_id":"a","path":"/x.png"}',
        "garbage",
        '{"type":"interrupt"}',
        '{"type":"speak","id":"1:1","audio":""}',
    ]) + "\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(lines))
    commands, wake, interrupt = queue.Queue(maxsize=128), threading.Event(), threading.Event()
    module.reader(commands, wake, interrupt)       # returns at EOF
    kinds = [commands.get_nowait()["type"] for _ in range(commands.qsize())]
    assert kinds == ["portrait", "interrupt", "speak", "stop"]       # EOF => stop
    assert wake.is_set() and interrupt.is_set()


def test_stale_microphone_buffers_never_block_an_interrupt(worker, monkeypatch):
    module, _ = worker
    listen = '{"type":"listen","audio":""}\n'
    monkeypatch.setattr(sys, "stdin", io.StringIO(listen * 10 + '{"type":"interrupt"}\n'))
    commands = queue.Queue(maxsize=2)
    # The queue is tiny: listen packets are dropped when full, so the interrupt (a blocking put) must
    # still be reachable once the consumer drains.
    consumer_kinds = []

    def consume():
        while True:
            command = commands.get()
            consumer_kinds.append(command["type"])
            if command["type"] == "stop":
                return
    thread = threading.Thread(target=consume)
    thread.start()
    module.reader(commands, threading.Event(), threading.Event())
    thread.join(5)
    assert "interrupt" in consumer_kinds and consumer_kinds[-1] == "stop"


@pytest.mark.parametrize("source_size", [(512, 512), (400, 800), (1000, 400), (1920, 1080)])
def test_portrait_fit_preserves_circular_features_and_entire_source(worker, source_size):
    module, cv2 = worker
    width, height = source_size
    image = np.full((height, width, 3), 80, np.uint8)
    y, x = np.ogrid[:height, :width]
    radius = min(width, height) // 5
    image[(x - width // 2) ** 2 + (y - height // 2) ** 2 <= radius ** 2] = 255
    cv2.files["/p/source.png"] = image
    path = module.prepare_portrait("/p/source.png", "/tmp/d", "fitted")
    fitted = cv2.written[path]
    assert fitted.shape == (720, 1280, 3)
    marker_y, marker_x = np.where(fitted[:, :, 0] > 220)
    assert abs(np.ptp(marker_x) - np.ptp(marker_y)) <= 2
    # The source rectangle remains centered and fully visible, with uniform scale.
    content_y, content_x = np.where(fitted[:, :, 0] >= 70)
    scale = min(1280 / width, 720 / height)
    assert np.ptp(content_x) + 1 == round(width * scale)
    assert np.ptp(content_y) + 1 == round(height * scale)
    assert abs((content_x.min() + content_x.max()) / 2 - 639.5) <= 0.5
    assert abs((content_y.min() + content_y.max()) / 2 - 359.5) <= 0.5


def test_native_frame_is_not_resampled(worker):
    module, cv2 = worker
    source = np.random.default_rng(0).integers(0, 256, (720, 1280, 3), dtype=np.uint8)
    cv2.files["/p/native.png"] = source
    path = module.prepare_portrait("/p/native.png", "/tmp/d", "native")
    np.testing.assert_array_equal(cv2.written[path], source)


def test_rgba_padding_preserves_transparency_and_loader_dimensions(worker):
    module, cv2 = worker
    source = np.full((256, 256, 4), (20, 30, 40, 128), np.uint8)
    cv2.files["/p/alpha.png"] = source
    path = module.prepare_portrait("/p/alpha.png", "/tmp/d", "alpha", width=640, height=360)
    frame = cv2.written[path]
    assert frame.shape == (360, 640, 4)
    np.testing.assert_array_equal(frame[:, :140], 0)
    np.testing.assert_array_equal(frame[:, 500:], 0)
    np.testing.assert_array_equal(frame[180, 320], [20, 30, 40, 128])
