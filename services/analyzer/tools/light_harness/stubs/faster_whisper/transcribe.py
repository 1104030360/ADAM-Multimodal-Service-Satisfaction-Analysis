# 輕量測試映像檔的空殼：測試用這三個資料類別組出假的 Whisper 結果，欄位和 faster-whisper 1.2.1 相同。
from dataclasses import dataclass


@dataclass
class Word:
    start: float
    end: float
    word: str
    probability: float


@dataclass
class Segment:
    id: int
    seek: int
    start: float
    end: float
    text: str
    tokens: list
    avg_logprob: float
    compression_ratio: float
    no_speech_prob: float
    words: list
    temperature: float


@dataclass
class TranscriptionInfo:
    language: str
    language_probability: float
    duration: float
    duration_after_vad: float
    all_language_probs: list
    transcription_options: object
    vad_options: object
