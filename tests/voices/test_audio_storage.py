"""Upload validation/normalization and on-disk preview/library storage."""
import io
import json
import wave

import numpy as np
import pytest

from chatbot.voices.audio import (
    REFERENCE_SAMPLE_RATE,
    AudioValidationError,
    decode_reference,
    encode_wav,
    read_wav,
    sha256,
    voice_fingerprint,
)
from chatbot.voices.storage import PreviewNotFound, StorageError, VoiceStorage

USER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
LIMITS = dict(max_bytes=5 * 1024 * 1024, min_seconds=3.0, max_seconds=30.0)


def speechlike(seconds, rate=16000, channels=1, level=0.4):
    t = np.arange(int(seconds * rate)) / rate
    mono = (level * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)
    return np.repeat(mono[:, None], channels, axis=1) if channels > 1 else mono


def wav_bytes(samples, rate=16000):
    samples = np.asarray(samples)
    channels = 1 if samples.ndim == 1 else samples.shape[1]
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return out.getvalue()


def encoded(fmt, codec, seconds=5.0, rate=44100):
    import av

    out = io.BytesIO()
    container = av.open(out, "w", format=fmt)
    stream = container.add_stream(codec, rate=rate)
    stream.layout = "mono"
    samples = speechlike(seconds, rate).reshape(1, -1)
    frame = av.AudioFrame.from_ndarray(samples, format="flt", layout="mono")
    frame.sample_rate = rate
    for packet in stream.encode(frame):
        container.mux(packet)
    for packet in stream.encode(None):
        container.mux(packet)
    container.close()
    return out.getvalue()


# ---------------------------------------------------------------- validation


def test_a_valid_wav_is_normalized_to_the_canonical_reference():
    ref = decode_reference(wav_bytes(speechlike(5.0)), "me.wav", **LIMITS)
    assert ref.sample_rate == REFERENCE_SAMPLE_RATE == 24000
    assert ref.duration == pytest.approx(5.0, abs=0.05)
    assert ref.samples.dtype == np.float32 and ref.samples.ndim == 1
    assert np.max(np.abs(ref.samples)) == pytest.approx(0.89, abs=0.01)
    assert (ref.source_format, ref.source_sample_rate, ref.source_channels) == ("wav", 16000, 1)


def test_stereo_is_downmixed_and_other_rates_resampled():
    ref = decode_reference(wav_bytes(speechlike(4.0, 48000, channels=2), 48000), "s.wav", **LIMITS)
    assert ref.source_channels == 2 and ref.source_sample_rate == 48000
    assert ref.samples.ndim == 1 and ref.duration == pytest.approx(4.0, abs=0.05)


@pytest.mark.parametrize("fmt, codec, name", [("flac", "flac", "a.flac"), ("mp3", "libmp3lame", "a.mp3")])
def test_compressed_uploads_are_accepted(fmt, codec, name):
    ref = decode_reference(encoded(fmt, codec), name, **LIMITS)
    assert ref.duration == pytest.approx(5.0, abs=0.15)


@pytest.mark.parametrize("data, name, message", [
    (b"", "a.wav", "empty"),
    (b"this is not audio at all" * 100, "a.wav", "not a readable audio"),
    (b"#EXTM3U\n#EXTINF:10,\nhttp://example.com/a.wav\n", "a.wav", "not a readable audio"),
    (wav_bytes(speechlike(5.0)), "a.exe", "Unsupported file type"),
    (wav_bytes(speechlike(5.0)), "noextension", "Unsupported file type"),
    (wav_bytes(speechlike(1.0)), "a.wav", "shorter than 3"),
    (wav_bytes(speechlike(31.0, 8000), 8000), "a.wav", "longer than 30"),
    (wav_bytes(np.zeros(16000 * 5, np.float32)), "a.wav", "silent"),
])
def test_invalid_uploads_are_rejected_with_a_reason(data, name, message):
    with pytest.raises(AudioValidationError, match=message):
        decode_reference(data, name, **LIMITS)


def test_size_limit_is_enforced_before_decoding():
    with pytest.raises(AudioValidationError, match="larger than"):
        decode_reference(b"0" * 2048, "a.wav", max_bytes=1024, min_seconds=3, max_seconds=30)


def test_a_playlist_disguised_with_an_audio_extension_is_never_followed(tmp_path):
    target = tmp_path / "private.wav"
    target.write_bytes(wav_bytes(speechlike(5.0)))
    playlist = f"#EXTM3U\n#EXTINF:5,\nfile://{target}\n{target}\n".encode()
    for name in ("a.m4a", "a.mp3", "a.wav", "a.ogg"):
        with pytest.raises(AudioValidationError):
            decode_reference(playlist, name, **LIMITS)


def test_wav_round_trip_and_fingerprint():
    samples = speechlike(3.0, 24000)
    back, rate = read_wav(encode_wav(samples, 24000))
    assert rate == 24000 and np.allclose(back, samples, atol=1e-4)
    assert voice_fingerprint("a", "x") != voice_fingerprint("a", "y") != voice_fingerprint("b", "x")


# ---------------------------------------------------------------- storage


@pytest.fixture
def storage(tmp_path):
    return VoiceStorage(tmp_path / "voices", preview_ttl_seconds=3600)


def test_previews_are_independent_and_isolated_per_user(storage):
    a = storage.create_preview(USER, "design", encode_wav(speechlike(3, 24000), 24000), {"transcript": "A"})
    b = storage.create_preview(USER, "design", encode_wav(speechlike(4, 24000), 24000), {"transcript": "B"})
    assert a["preview_id"] != b["preview_id"]
    assert storage.get_preview(USER, a["preview_id"])["transcript"] == "A"
    assert storage.preview_file(USER, a["preview_id"], "reference").read_bytes() != \
        storage.preview_file(USER, b["preview_id"], "reference").read_bytes()
    with pytest.raises(PreviewNotFound):
        storage.get_preview(OTHER, a["preview_id"])          # another user's preview does not exist
    assert storage.delete_preview(USER, a["preview_id"]) is True
    with pytest.raises(PreviewNotFound):
        storage.get_preview(USER, a["preview_id"])
    assert storage.get_preview(USER, b["preview_id"])["transcript"] == "B"


def test_promote_moves_the_exact_approved_bytes_and_cleans_up(storage):
    wav = encode_wav(speechlike(5, 24000), 24000)
    preview = storage.create_preview(USER, "design", wav, {})
    voice_id = "33333333-3333-3333-3333-333333333333"
    stored = storage.promote(USER, preview["preview_id"], voice_id)
    assert stored.reference_path == f"library/{voice_id}/reference.wav"
    assert storage.reference_file(stored.reference_path).read_bytes() == wav
    assert stored.sha256 == sha256(wav) and stored.sample_rate == 24000
    assert stored.duration == pytest.approx(5.0, abs=0.01)
    with pytest.raises(PreviewNotFound):
        storage.get_preview(USER, preview["preview_id"])
    assert storage.delete_voice(voice_id) is True
    assert not (storage.root / "library" / voice_id).exists()


def test_a_tampered_preview_cannot_be_promoted(storage):
    preview = storage.create_preview(USER, "design", encode_wav(speechlike(5, 24000), 24000), {})
    storage.preview_file(USER, preview["preview_id"], "reference").write_bytes(b"swapped")
    with pytest.raises(StorageError, match="changed"):
        storage.promote(USER, preview["preview_id"], "33333333-3333-3333-3333-333333333333")


def test_expired_and_orphaned_previews_are_purged(tmp_path):
    clock = [1000.0]
    storage = VoiceStorage(tmp_path / "voices", preview_ttl_seconds=60, clock=lambda: clock[0])
    old = storage.create_preview(USER, "design", encode_wav(speechlike(3, 24000), 24000), {})
    (storage.root / "previews" / USER / "44444444-4444-4444-4444-444444444444").mkdir(parents=True)
    clock[0] += 61
    fresh = storage.create_preview(USER, "design", encode_wav(speechlike(3, 24000), 24000), {})
    assert storage.purge_expired_previews() == 1
    with pytest.raises(PreviewNotFound):
        storage.get_preview(USER, old["preview_id"])
    assert storage.get_preview(USER, fresh["preview_id"])
    assert not (storage.root / "previews" / USER / "44444444-4444-4444-4444-444444444444").exists()


@pytest.mark.parametrize("bad", ["../../etc", "a/b", "", "..", "not-a-uuid"])
def test_ids_never_become_arbitrary_paths(storage, bad):
    with pytest.raises(PreviewNotFound):
        storage.get_preview(USER, bad)
    with pytest.raises((ValueError, PreviewNotFound)):
        storage.promote(USER, bad, "33333333-3333-3333-3333-333333333333")
    for path in ("../x.wav", "/etc/passwd", "library/../../x.wav"):
        with pytest.raises(StorageError):
            storage.absolute(path)


def test_meta_is_json_and_never_contains_absolute_paths(storage):
    preview = storage.create_preview(USER, "clone", encode_wav(speechlike(3, 24000), 24000), {"consent": True})
    meta = json.loads((storage.root / "previews" / USER / preview["preview_id"] / "meta.json").read_text())
    assert str(storage.root) not in json.dumps(meta)
    assert storage.preview_reference_relative(USER, preview["preview_id"]) == \
        f"previews/{USER}/{preview['preview_id']}/reference.wav"
