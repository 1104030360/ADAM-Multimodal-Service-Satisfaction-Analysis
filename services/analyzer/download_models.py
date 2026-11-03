# download_models.py — 第一次部署時執行一次：把模型下載到 /models（主機的 /opt/adam/models）
# pyannote 的模型需要 Hugging Face token，而且要先在網站上同意
# pyannote/speaker-diarization-3.1 與 pyannote/segmentation-3.0 的使用條款。pyannote 下載過之後，再執行就不需要 token
# （例如之後只多下載另一個大小的 Whisper）。Whisper 與翻譯模型（opus-mt-zh-en）是公開的，不需要 token。
# 一次下載好幾個大小的 Whisper（比較速度用）：WHISPER_DOWNLOAD=small,medium,large-v3-turbo；不設就只下載 WHISPER_MODEL。
import os
import sys

from deepface import DeepFace
from huggingface_hub import try_to_load_from_cache
from pyannote.audio import Pipeline
from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForSequenceClassification

import speech
from analyzer import AUDIO_MODEL, DIARIZATION_MODEL

# speaker-diarization-3.1 會用到後兩個；前兩個要同意條款，聲紋模型是公開的
PYANNOTE_FILES = ((DIARIZATION_MODEL, "config.yaml"),
                  ("pyannote/segmentation-3.0", "pytorch_model.bin"),
                  ("pyannote/wespeaker-voxceleb-resnet34-LM", "pytorch_model.bin"))


def pyannote_downloaded():
    cache = os.environ.get("PYANNOTE_CACHE")
    return all(isinstance(try_to_load_from_cache(repo, name, cache_dir=cache), str) for repo, name in PYANNOTE_FILES)


if pyannote_downloaded():
    print("pyannote: already downloaded")
else:
    token = os.environ.get("HF_TOKEN")
    if not token:
        sys.exit("HF_TOKEN is not set")
    if Pipeline.from_pretrained(DIARIZATION_MODEL, use_auth_token=token) is None:
        sys.exit("cannot download pyannote: accept the conditions of "
                 "pyannote/speaker-diarization-3.1 and pyannote/segmentation-3.0 on huggingface.co first")
Wav2Vec2FeatureExtractor.from_pretrained(AUDIO_MODEL)
Wav2Vec2ForSequenceClassification.from_pretrained(AUDIO_MODEL)
DeepFace.build_model("Emotion")
for size in filter(None, (s.strip() for s in os.environ.get("WHISPER_DOWNLOAD", speech.WHISPER_MODEL).split(","))):
    print(f"whisper {size}: {speech.download_whisper(size)}")
speech.download_translation()
print("all models downloaded")
