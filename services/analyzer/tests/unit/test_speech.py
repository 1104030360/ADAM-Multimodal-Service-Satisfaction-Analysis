# tests/unit/test_speech.py — 語音轉文字與翻譯（speech.py）。
# 不載入真模型：Whisper 和翻譯模型換成假的；OpenCC 用真的（純 Python，很快），繁簡轉換是真的結果。
import logging
import os

import pytest
import torch
from faster_whisper.transcribe import Segment, TranscriptionInfo, Word

import speech

AUDIO = "/tmp/work/S20261030120000_audio.wav"


# ---------- 假的 Whisper 與翻譯模型 ----------
def segment(*words):
    """一個 Whisper 段落，words 是 [(開始秒, 結束秒, 字), ...]。

    中文模式的「字」是一個個 token：新的一個詞前面帶空白，句尾標點併在前一個字後面（"好。"），和 faster-whisper 一樣。
    """
    items = [Word(start=start, end=end, word=text, probability=0.9) for start, end, text in words]
    return Segment(id=0, seek=0, start=items[0].start if items else 0.0, end=items[-1].end if items else 0.0,
                   text="".join(w.word for w in items), tokens=[], avg_logprob=-0.2, compression_ratio=1.5,
                   no_speech_prob=0.01, words=items, temperature=0.0)


class FakeWhisper:
    """假的 WhisperModel：記下 transcribe() 收到的參數，回傳（段落的 generator, info），和 faster-whisper 一樣。"""

    def __init__(self, segments):
        self.segments = segments
        self.calls = []

    def transcribe(self, audio, **options):
        self.calls.append((audio, options))
        info = TranscriptionInfo(language="zh", language_probability=1.0, duration=12.0, duration_after_vad=9.5,
                                 all_language_probs=None, transcription_options=None, vad_options=None)
        return (s for s in self.segments), info


class FakeTokenizer:
    """假的 MarianTokenizer：記下每一批句子與參數；「編碼」就是句子本身，「解碼」加上 EN<…>。"""

    def __init__(self):
        self.batches = []
        self.kwargs = None

    def __call__(self, sentences, **kwargs):
        self.batches.append(list(sentences))
        self.kwargs = kwargs
        return {"input_ids": list(sentences)}

    def batch_decode(self, output, skip_special_tokens):
        assert skip_special_tokens  # 不要把 </s>、<pad> 寫進英文
        return [f"EN<{s}>" for s in output]


class FakeTranslationModel:
    """假的 MarianMTModel：原樣交回句子；記下 generate() 的參數，以及當時有沒有關掉梯度。"""

    def __init__(self):
        self.kwargs = None
        self.grad_enabled = None

    def generate(self, input_ids, **kwargs):
        self.kwargs = kwargs
        self.grad_enabled = torch.is_grad_enabled()
        return input_ids


@pytest.fixture
def tokenizer(monkeypatch):
    """換上假的翻譯模型；回傳分詞器，測試可以看它收到了哪些句子。"""
    fake = FakeTokenizer()
    monkeypatch.setattr(speech, "translator", (fake, FakeTranslationModel()))
    return fake


def use_whisper(monkeypatch, segments):
    whisper = FakeWhisper(segments)
    monkeypatch.setattr(speech, "whisper_model", whisper)
    return whisper


# ---------- 每個字是誰說的 ----------
TURNS = [(0.0, 3.0, "SPEAKER_00"), (2.5, 6.0, "SPEAKER_01"), (8.0, 10.0, "SPEAKER_00")]


@pytest.mark.parametrize("start, end, speaker", [
    (1.0, 1.5, "SPEAKER_00"),  # 只在一段裡
    (2.6, 3.4, "SPEAKER_01"),  # 兩段都重疊（0.4 秒、0.8 秒）：重疊久的
    (2.6, 2.9, "SPEAKER_00"),  # 重疊一樣久：先開始的
    (4.0, 4.0, "SPEAKER_01"),  # 開始和結束一樣的字：落在哪一段就是哪一段
    (6.5, 6.9, "SPEAKER_01"),  # 沒有重疊：最近的分段（前面的差 0.5 秒，後面的差 1.1 秒）
    (7.4, 7.8, "SPEAKER_00"),  # 沒有重疊：最近的是後面的分段（差 0.2 秒，前面的差 1.4 秒）
], ids=["inside", "longest-overlap", "tie-earlier", "zero-length", "nearest-before", "nearest-after"])
def test_speaker_is_the_longest_overlap_else_the_nearest_turn(start, end, speaker):
    assert speech.speaker_at(start, end, TURNS) == speaker


def test_without_turns_everyone_is_speaker_00():
    assert speech.speaker_at(1.0, 2.0, []) == "SPEAKER_00"
    assert speech.to_sentences([segment((0.0, 0.5, " 你好。"), (3.0, 3.5, " 再見"))], []) == [
        ("SPEAKER_00", "你好。"), ("SPEAKER_00", "再見")]


# ---------- 把字接成句子 ----------
@pytest.mark.parametrize("words, text", [
    # 都是真的 Whisper tokenizer（中文模式）切出來、再照 faster-whisper 把標點併進前一個字的樣子
    ([" 你", "好，", "歡迎", "光", "臨。"], "你好，歡迎光臨。"),
    ([" 這", "支", "i", "Phone", " 15", "的", "價", "格", "是", "15", "00", "元。"], "這支iPhone 15的價格是1500元。"),
    ([" 電", "話", "是", "09", "12", "3", "45", "67", "8"], "電話是0912345678"),
    ([" Samsung", " Galaxy", " S", "24", " Ultra", "嗎？"], "Samsung Galaxy S24 Ultra嗎？"),
    ([" 好", "的", " 謝謝", "您", " OK"], "好的謝謝您OK"),  # 中文之間、中英文之間的空白拿掉
    ([" 您", "好,", "歡迎", "光", "臨,", "請", "問", "需要", "什麼", "幫", "忙", "嗎?"],
     "您好,歡迎光臨,請問需要什麼幫忙嗎?"),  # medium 常用半形標點：照原樣保留
], ids=["chinese", "iphone-and-price", "phone-number", "english-words", "spaces-between-chinese", "ascii-punctuation"])
def test_words_join_without_spaces_except_inside_english(words, text):
    assert speech.join_words(words) == text


def test_sentences_end_at_punctuation_speaker_change_and_segment_end():
    turns = [(0.0, 4.0, "SPEAKER_00"), (4.0, 6.0, "SPEAKER_01"), (6.5, 12.0, "SPEAKER_00")]
    segments = [
        segment((0.0, 0.3, " 您"), (0.3, 0.6, "好，"), (0.6, 1.0, "歡迎"), (1.0, 1.5, "光臨。"),
                (1.8, 2.1, "請問"), (2.1, 2.4, "需要"), (2.4, 2.8, "什麼"), (2.8, 3.4, "服務？"),
                # 同一段裡換人說話，中間沒有標點
                (4.1, 4.3, "我"), (4.3, 4.5, "想"), (4.5, 4.8, "退"), (4.8, 5.1, "貨")),
        segment((6.6, 6.9, " 好"), (6.9, 7.2, "的")),  # 整段沒有標點：段落結束就是一句
        segment((8.0, 8.3, " 請"), (8.3, 8.6, "稍"), (8.6, 9.0, "等")),
    ]
    assert speech.to_sentences(segments, turns) == [
        ("SPEAKER_00", "您好，歡迎光臨。"), ("SPEAKER_00", "請問需要什麼服務？"), ("SPEAKER_01", "我想退貨"),
        ("SPEAKER_00", "好的"), ("SPEAKER_00", "請稍等")]


def test_sentences_pyannote_did_not_hear_are_dropped():
    # 真的例子（Whisper small、合成的對話）：pyannote 的最後一段到 58.42 秒，錄音到 58.93 秒；
    # Whisper 在最後的靜音裡多寫了一句「請不吝點贊、訂閱…」，除了第一個字，時間都擠在分段外面
    turns = [(54.66, 58.42, "SPEAKER_00")]
    segments = [
        segment((54.57, 54.87, " 不"), (54.87, 55.21, "客氣，"), (55.47, 56.07, "之後"), (57.13, 57.79, "歡迎"),
                (57.79, 58.09, "再來"), (58.09, 58.33, "找我們。")),  # 第一個字比分段早一點開始：照樣算
        segment((58.33, 58.79, " 請"), (58.79, 58.79, "不"), (58.79, 58.79, "吝"), (58.79, 58.89, "點"),
                (58.89, 58.89, "贊、"), (58.89, 58.89, "訂閱。")),
        # large-v3-turbo 編的「感谢观看」（Mac 端對端）：0 秒的「感」剛好碰到分段結束，「谢」有 0.09 秒在分段裡；
        # 以字數算是一半，以時間算只有 16%
        segment((58.33, 58.33, " 感"), (58.33, 58.73, "谢"), (58.73, 58.75, "观"), (58.75, 58.89, "看")),
        segment((56.20, 56.20, " 嗯")),  # 每個字都是 0 秒：Whisper 對不準時間，不留
        segment((58.10, 58.40, " 好"), (58.40, 58.50, "的")),  # 大部分的時間在分段裡：留下
    ]
    assert speech.to_sentences(segments, turns) == [
        ("SPEAKER_00", "不客氣，之後歡迎再來找我們。"), ("SPEAKER_00", "好的")]


def test_punctuation_only_sentences_are_dropped():
    turns = [(0.0, 2.0, "SPEAKER_00"), (2.0, 4.0, "SPEAKER_01")]
    segments = [
        segment((0.0, 0.5, " 好"), (0.5, 1.0, "的"), (2.1, 2.3, "。")),  # 句號被歸給另一個人：只剩標點
        segment((3.0, 3.5, " …")),
        segment(),  # 沒有字的段落
    ]
    assert speech.to_sentences(segments, turns) == [("SPEAKER_00", "好的")]


# ---------- 整個流程 ----------
def test_transcribe_translate_traditional_text_simplified_input(monkeypatch, tokenizer, caplog):
    turns = [(0.0, 3.0, "SPEAKER_00"), (3.2, 6.0, "SPEAKER_01")]
    whisper = use_whisper(monkeypatch, [
        # Whisper 常輸出簡體字和大陸用語
        segment((0.0, 0.4, " 这个"), (0.4, 0.8, "软件"), (0.8, 1.0, "的"), (1.0, 1.4, "网络"), (1.4, 1.6, "不"),
                (1.6, 1.8, "好，"), (1.8, 2.0, "我"), (2.0, 2.2, "想"), (2.2, 2.4, "退"), (2.4, 2.8, "货。")),
        segment((3.3, 3.6, " 請問"), (3.6, 3.8, "您"), (3.8, 4.0, "的"), (4.0, 4.4, "手機"), (4.4, 5.0, "號碼？")),
    ])
    with caplog.at_level(logging.INFO, logger="adam-analysis"):
        result = speech.transcribe_and_translate(AUDIO, turns)

    # zh 是台灣繁體與台灣用語；翻譯模型收到的是簡體與大陸用語（它學的大多是這種）
    assert result == [
        {"speaker": "SPEAKER_00", "zh": "這個軟體的網路不好，我想退貨。", "en": "EN<这个软件的网络不好，我想退货。>"},
        {"speaker": "SPEAKER_01", "zh": "請問您的手機號碼？", "en": "EN<请问您的手机号码？>"},
    ]
    assert tokenizer.batches == [["这个软件的网络不好，我想退货。", "请问您的手机号码？"]]

    audio, options = whisper.calls[0]
    assert audio == AUDIO
    assert options["language"] == "zh" and options["task"] == "transcribe"
    assert options["vad_filter"] and options["word_timestamps"]
    assert options["beam_size"] == 5 and options["condition_on_previous_text"] is False
    assert "hotwords" not in options and "initial_prompt" not in options  # 提示會讓真的錄音跳針（T48）

    # log 只有數量和時間，沒有逐字稿（個人資料）
    assert "12.0 s audio (9.5 s speech), 2 sentence(s) from 2 speaker(s)" in caplog.text
    assert not any(text in caplog.text for text in ("退", "货", "貨", "手機"))


def test_no_speech_returns_an_empty_list_without_translating(monkeypatch, tokenizer):
    use_whisper(monkeypatch, [])
    assert speech.transcribe_and_translate(AUDIO, []) == []
    assert tokenizer.batches == []


def test_translation_runs_in_batches_deterministically_without_gradients(tokenizer):
    sentences = [f"第{i}句" for i in range(2 * speech.TRANSLATE_BATCH + 3)]
    assert speech.translate(sentences) == [f"EN<{s}>" for s in sentences]
    assert [len(batch) for batch in tokenizer.batches] == [speech.TRANSLATE_BATCH, speech.TRANSLATE_BATCH, 3]
    assert tokenizer.kwargs == {"return_tensors": "pt", "padding": True, "truncation": True}
    model = speech.translator[1]
    assert model.kwargs == {"do_sample": False}
    assert model.grad_enabled is False


# ---------- 載入與下載模型 ----------
def test_load_models_is_offline_and_uses_every_core(monkeypatch):
    whisper_args = {}
    monkeypatch.setattr(speech, "WhisperModel", lambda size, **kwargs: whisper_args.update(size=size, **kwargs) or "whisper")
    monkeypatch.setattr(speech.MarianTokenizer, "from_pretrained", lambda name, **kwargs: ("tokenizer", name, kwargs))
    monkeypatch.setattr(speech.MarianMTModel, "from_pretrained", lambda name, **kwargs: ("model", name, kwargs))
    monkeypatch.setattr(speech, "whisper_model", None)
    monkeypatch.setattr(speech, "translator", None)

    speech.load_models()
    assert whisper_args == {"size": speech.WHISPER_MODEL, "device": "cpu", "compute_type": speech.WHISPER_COMPUTE_TYPE,
                            "cpu_threads": os.cpu_count(), "download_root": speech.WHISPER_DIR,
                            "local_files_only": True}
    assert speech.whisper_model == "whisper"
    offline = {"local_files_only": True}
    assert speech.translator == (("tokenizer", speech.TRANSLATION_MODEL, offline),
                                 ("model", speech.TRANSLATION_MODEL, offline))


def test_download_helpers_call_through(monkeypatch):
    calls = []
    monkeypatch.setattr(speech, "download_model",
                        lambda size, cache_dir: calls.append(("whisper", size, cache_dir)) or f"{cache_dir}/{size}")
    monkeypatch.setattr(speech.MarianTokenizer, "from_pretrained", lambda name: calls.append(("tokenizer", name)))
    monkeypatch.setattr(speech.MarianMTModel, "from_pretrained", lambda name: calls.append(("model", name)))

    assert speech.download_whisper("small") == f"{speech.WHISPER_DIR}/small"
    speech.download_translation()
    assert calls == [("whisper", "small", speech.WHISPER_DIR),
                     ("tokenizer", speech.TRANSLATION_MODEL), ("model", speech.TRANSLATION_MODEL)]
