# tests/unit/test_download_models.py — 一次性的模型下載程式：不連網、不下載，下載的函式都換成假的
import runpy

import huggingface_hub
import pytest
from deepface import DeepFace
from pyannote.audio import Pipeline
from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForSequenceClassification

import speech


@pytest.fixture
def downloads(monkeypatch):
    """其他模型的下載都換成記錄呼叫；pyannote 預設「還沒下載」。回傳記錄的清單。"""
    calls = []
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda repo, name, cache_dir=None: None)
    monkeypatch.setattr(Wav2Vec2FeatureExtractor, "from_pretrained", lambda name: calls.append(("features", name)))
    monkeypatch.setattr(Wav2Vec2ForSequenceClassification, "from_pretrained", lambda name: calls.append(("wav2vec2", name)))
    monkeypatch.setattr(DeepFace, "build_model", lambda name: calls.append(("deepface", name)))
    monkeypatch.setattr(speech, "download_whisper", lambda size: calls.append(("whisper", size)) or f"/models/whisper/{size}")
    monkeypatch.setattr(speech, "download_translation", lambda: calls.append(("translation", speech.TRANSLATION_MODEL)))
    monkeypatch.delenv("WHISPER_DOWNLOAD", raising=False)
    return calls


def test_stops_without_hf_token(monkeypatch, downloads):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="HF_TOKEN is not set"):
        runpy.run_module("download_models")  # 和 python download_models.py 一樣，從頭執行一次


def test_explains_gated_pyannote_model(monkeypatch, downloads):
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    # 沒有在 Hugging Face 同意使用條款時，pyannote 不丟錯，只回傳 None
    monkeypatch.setattr(Pipeline, "from_pretrained", lambda name, use_auth_token: None)
    with pytest.raises(SystemExit, match="accept the conditions"):
        runpy.run_module("download_models")


def test_downloads_everything_with_a_token(monkeypatch, downloads, capsys):
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    monkeypatch.setattr(Pipeline, "from_pretrained", lambda name, use_auth_token: object())
    runpy.run_module("download_models")
    assert [kind for kind, _ in downloads] == ["features", "wav2vec2", "deepface", "whisper", "translation"]
    assert ("whisper", speech.WHISPER_MODEL) in downloads  # 不設 WHISPER_DOWNLOAD：只下載要用的大小
    assert capsys.readouterr().out.rstrip().endswith("all models downloaded")


def test_skips_pyannote_once_downloaded_and_needs_no_token(monkeypatch, downloads, capsys):
    """pyannote 下載過之後，再執行（例如多下載一個大小的 Whisper）不用 token，也不再連 pyannote。"""
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache",
                        lambda repo, name, cache_dir=None: f"/models/pyannote/{repo}/{name}")
    monkeypatch.setattr(Pipeline, "from_pretrained", lambda *a, **k: pytest.fail("pyannote should not be downloaded"))
    monkeypatch.setenv("WHISPER_DOWNLOAD", "small, medium,large-v3-turbo")
    runpy.run_module("download_models")
    assert [size for kind, size in downloads if kind == "whisper"] == ["small", "medium", "large-v3-turbo"]
    out = capsys.readouterr().out
    assert "pyannote: already downloaded" in out and "all models downloaded" in out
