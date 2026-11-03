# speech.py — 語音轉文字與翻譯，全部在這台機器上跑（docs/rebuild/real-analyze/PLAN.md，取代 Amazon Transcribe 與 Translate）：
#   faster-whisper（Whisper，CPU、int8）轉成中文 → 依 pyannote 的分段把每個字歸給說話的人，接成句子
#   （pyannote 沒聽到有人說的句子拿掉）→ OpenCC 轉成台灣繁體 → opus-mt-zh-en 逐句翻成英文（給 VADER）。
# 模型由 download_models.py 先下載到 /models；執行時不連網（HF_HUB_OFFLINE=1）。
import logging
import os
import re
import time

import torch
from faster_whisper import WhisperModel
from faster_whisper.utils import download_model
from opencc import OpenCC
from transformers import MarianMTModel, MarianTokenizer

log = logging.getLogger("adam-analysis")

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "medium")  # small、medium、large-v3-turbo（要先下載）
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_DIR = os.getenv("WHISPER_DIR", "/models/whisper")
TRANSLATION_MODEL = "Helsinki-NLP/opus-mt-zh-en"

SENTENCE_END = ("。", "？", "！", "?", "!")  # 不含 "."：Whisper 把 "3.5" 切成 "3." 和 "5"
DEFAULT_SPEAKER = "SPEAKER_00"  # pyannote 沒有偵測到任何人說話時，全部歸給同一個人
TRANSLATE_BATCH = 16  # 一次翻幾句
KEEP_SPACE = re.compile(r"(?<=[A-Za-z0-9])\s+(?=[A-Za-z0-9])")  # 兩邊都是英文字母或數字的空白（"iPhone 15"）

TO_TAIWAN = OpenCC("s2twp")  # 簡體 → 台灣繁體與台灣用語（软件 → 軟體），存進逐字稿
# 反方向（軟體 → 软件、影片 → 视频）：翻譯模型學的大多是簡體與大陸用語。只換字（t2s）會留下「软体」這種兩邊都不用的詞
TO_SIMPLIFIED = OpenCC("tw2sp")

# 由 load_models() 載入（analyzer.load_models() 在背景執行緒呼叫一次）
whisper_model = None
translator = None  # (tokenizer, model)


def download_whisper(size):
    """下載一個大小的 Whisper（CTranslate2 格式）到 WHISPER_DIR，回傳本機路徑。download_models.py 用。"""
    return download_model(size, cache_dir=WHISPER_DIR)


def download_translation():
    """下載翻譯模型到 HF_HOME。download_models.py 用。"""
    MarianTokenizer.from_pretrained(TRANSLATION_MODEL)
    MarianMTModel.from_pretrained(TRANSLATION_MODEL)


def load_models():
    """從 /models 載入 Whisper 和翻譯模型；沒有先下載的話丟錯（不連網）。"""
    global whisper_model, translator
    # cpu_threads：不設的話 CTranslate2 在 CPU 上用 4 個執行緒，EC2 只有 2 vCPU 時太多、Mac 的 Docker 又用不滿；改成全部的核心
    whisper_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type=WHISPER_COMPUTE_TYPE,
                                 cpu_threads=os.cpu_count(), download_root=WHISPER_DIR, local_files_only=True)
    translator = (MarianTokenizer.from_pretrained(TRANSLATION_MODEL, local_files_only=True),
                  MarianMTModel.from_pretrained(TRANSLATION_MODEL, local_files_only=True))


def transcribe_and_translate(local_audio_path, turns):
    """本機的錄音 → [{"speaker": 說話者, "zh": 繁體中文的句子, "en": 英文翻譯}, ...]，依時間先後排列；沒有人說話時是 []。

    turns：pyannote 的分段 [(開始秒, 結束秒, 說話者), ...]（說話者是 SPEAKER_00 這種標籤），可能是空的。
    """
    started = time.monotonic()
    segments, info = whisper_model.transcribe(
        local_audio_path, language="zh", task="transcribe",
        beam_size=5,  # faster-whisper 的預設：中文的同音字多，beam search 比每一步只取最可能的字準，但比較慢
        vad_filter=True,  # 先拿掉沒有人說話的部分：比較快，也比較不會在靜音裡「聽」出字來（剩下的由 heard() 擋）
        word_timestamps=True,  # 每個字的時間：一段裡換人說話時才切得開
        # 每個 30 秒的窗格各自解碼：前面聽錯或一直重複（Whisper 常見的跳針）不會一路帶到後面
        # 不給提示（hotwords、initial_prompt）：真的錄音上，提示讓 Whisper 一直重複同一句、一再提高 temperature 重試，
        # 23 秒的錄音要 7 分鐘；拿掉之後 29 秒，合成對話也更準。繁體交給 OpenCC，句子在段落結束時也會切開（T48）
        condition_on_previous_text=False)
    sentences = to_sentences(segments, turns)  # segments 是 generator：一段一段取出來時，Whisper 才真的在跑
    transcribed = time.monotonic()
    zh = [TO_TAIWAN.convert(text) for _, text in sentences]
    en = translate([TO_SIMPLIFIED.convert(text) for text in zh])
    # 逐字稿是個人資料，log 只記數量和時間
    log.info("speech: %.1f s audio (%.1f s speech), %d sentence(s) from %d speaker(s); whisper %s %.1f s, translation %.1f s",
             info.duration, info.duration_after_vad, len(sentences), len({speaker for speaker, _ in sentences}),
             WHISPER_MODEL, transcribed - started, time.monotonic() - transcribed)
    return [{"speaker": speaker, "zh": z, "en": e} for (speaker, _), z, e in zip(sentences, zh, en)]


def to_sentences(segments, turns):
    """Whisper 的段落 → [(說話者, 句子), ...]。每個字歸給說話的人，同一個人連續說的字接成一句，
    遇到句尾標點、換人說話或段落結束就切開（Whisper 常常整段都沒有標點，這時一段就是一句）。
    只有標點的句子，和 pyannote 沒聽到有人說的句子（heard()）拿掉。
    """
    pieces = []
    for segment in segments:
        speaker, words = None, []
        for word in segment.words:
            who = speaker_at(word.start, word.end, turns)
            if words and who != speaker:
                pieces.append((speaker, words))
                words = []
            speaker = who
            words.append(word)
            if word.word.endswith(SENTENCE_END):  # faster-whisper 把標點併進前一個字（"嗎？"）
                pieces.append((speaker, words))
                words = []
        if words:
            pieces.append((speaker, words))
    sentences = [(speaker, join_words(w.word for w in words)) for speaker, words in pieces if heard(words, turns)]
    return [(speaker, text) for speaker, text in sentences if any(ch.isalnum() for ch in text)]


def overlap(start, end, turn):
    """這段時間和一個分段重疊幾秒；沒有重疊時是負的，也就是中間隔了幾秒。"""
    return min(end, turn[1]) - max(start, turn[0])


def speaker_at(start, end, turns):
    """這段時間（一個字）是誰說的：重疊最久的分段；都沒有重疊時取最近的分段；沒有分段時是 SPEAKER_00。

    和 WhisperX 的 assign_word_speakers(fill_nearest=True) 同一個想法（PLAN 3-2）；一樣久時取排在前面（先開始）的分段。
    """
    if not turns:
        return DEFAULT_SPEAKER
    return max(turns, key=lambda turn: overlap(start, end, turn))[2]  # 都是負的時候，最大的就是離得最近的


def heard(words, turns):
    """pyannote 有沒有聽到有人說這句：這句至少一半的時間落在某個分段裡（沒有分段時不檢查）。

    Whisper 會在錄音結尾的靜音裡編出整句話（例如「請不吝點贊、訂閱」「感謝觀看」），這種句子的時間幾乎都在所有分段的外面；
    真的句子只有頭尾幾個字可能因為時間對不準而落在外面。算時間而不是算字數：Whisper 常給 0 秒長的字，
    剛好碰到分段邊界的 0 秒字不能讓編出來的句子過關（「感谢观看」以字數算有一半，以時間算只有 16%，T48）。
    每個字都是 0 秒（Whisper 對不準時間）的句子也拿掉。
    """
    if not turns:
        return True
    spoken = sum(w.end - w.start for w in words)
    inside = sum(max(0.0, max(overlap(w.start, w.end, turn) for turn in turns)) for w in words)
    return spoken > 0 and inside * 2 >= spoken


def join_words(words):
    """把 Whisper 的字接成一句。中文模式的「字」是一個個 token（"iPhone" 是 "i"、"Phone"，"1500" 是 "15"、"00"），
    新的一個詞前面才帶空白（" 15"）：照原樣接起來，只留下兩邊都是英文字母或數字的空白（"iPhone 15"），其他空白都拿掉。
    """
    return " ".join("".join(part.split()) for part in KEEP_SPACE.split("".join(words)))


def translate(sentences):
    """中文的句子（簡體）→ 英文，一次翻 TRANSLATE_BATCH 句。照模型的設定用 beam search、不抽樣：同一句每次翻的都一樣。"""
    tokenizer, model = translator
    english = []
    for i in range(0, len(sentences), TRANSLATE_BATCH):
        # truncation：超過模型上限（512 個 token）的句子只翻前面，不讓整筆分析失敗
        batch = tokenizer(sentences[i:i + TRANSLATE_BATCH], return_tensors="pt", padding=True, truncation=True)
        with torch.no_grad():
            output = model.generate(**batch, do_sample=False)
        english += tokenizer.batch_decode(output, skip_special_tokens=True)
    return english
