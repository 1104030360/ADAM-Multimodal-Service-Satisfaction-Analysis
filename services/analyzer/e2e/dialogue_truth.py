"""合成對話（fixtures/，make_dialogue.py 產生）的標準答案：讀取、正規化、字錯率（CER）、每個字歸給誰。

e2e_steps.py（檢查分析服務寫的逐字稿）和 bench_whisper.py（比較 Whisper 的大小）共用。在分析服務的映像檔裡執行（要 OpenCC）。
"""
import json
from difflib import SequenceMatcher
from pathlib import Path

from opencc import OpenCC

TRUTH = Path(__file__).resolve().parent / "fixtures" / "dialogue_turns.json"
TO_TAIWAN = OpenCC("s2twp")  # 和 speech.py 存進逐字稿的轉換相同
DIGITS = str.maketrans("0123456789", "零一二三四五六七八九")  # 對話裡的數字都是一位數（make_dialogue.py）


def turns():
    """[{"speaker": "Staff" 或 "Customer", "start": 秒, "end": 秒, "text": 句子}, ...]，依時間先後。"""
    return json.loads(TRUTH.read_text(encoding="utf-8"))["turns"]


def normalize(text):
    """比較用的字串：台灣繁體、阿拉伯數字換成國字、去掉標點和空白、英文小寫。"""
    return "".join(ch for ch in TO_TAIWAN.convert(text).translate(DIGITS).lower() if ch.isalnum())


def cer(hypothesis, reference):
    """字錯率：兩邊正規化之後的編輯距離（替換、刪除、插入各算一個字）÷ 標準答案的字數。"""
    hyp, ref = normalize(hypothesis), normalize(reference)
    previous = list(range(len(hyp) + 1))
    for i, expected in enumerate(ref, 1):
        current = [i]
        for j, heard in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (expected != heard)))
        previous = current
    return previous[-1] / len(ref)


def aligned_speakers(sentences, truth):
    """sentences：轉寫的結果 [(說話者, 句子), ...]，依時間先後；truth：turns()。

    兩邊正規化之後依先後順序對齊（difflib），回傳每個對得上的字的（轉寫結果的說話者, 標準答案的說話者）。
    """
    def flatten(pairs):
        text, speakers = "", []
        for speaker, sentence in pairs:
            chars = normalize(sentence)
            text += chars
            speakers += [speaker] * len(chars)
        return text, speakers

    heard, heard_by = flatten(sentences)
    expected, expected_by = flatten((turn["speaker"], turn["text"]) for turn in truth)
    matcher = SequenceMatcher(None, heard, expected, autojunk=False)  # 中文字常重複出現：不能把常見的字當成雜訊略過
    return [(heard_by[block.a + k], expected_by[block.b + k])
            for block in matcher.get_matching_blocks() for k in range(block.size)]
