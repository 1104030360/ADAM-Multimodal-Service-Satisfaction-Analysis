"""合成的兩人對話（台灣華語）：端對端測試（run_e2e.sh）與 Whisper 比較（bench_whisper.py）用的錄音和標準答案。

用 macOS 的 say 念出每一句（服務人員是 Eddy (Chinese (Taiwan))，男聲；顧客是 Meijia，女聲），
去掉每一句前後的靜音，句子之間隔 0.6 秒，存成 16 kHz、單聲道、16 位元的 WAV。
桌面端錄的是 44.1 kHz；分析服務一律重新取樣成 16 kHz，用 16 kHz 是為了讓檔案小於 2 MB（44.1 kHz 由使用者的錄音測）。
對話有明確的結果（換了一副新耳機：Resolved）和一項答應的待辦（明天下午三點以前寄延長保固的確認信）。

輸出在 fixtures/（沒有個人資料，可以放在 repo 的資料夾裡）：
  dialogue.wav         錄音
  dialogue_zh_tw.txt   標準答案：一句一行，前面是 Staff: 或 Customer:（和分析服務的逐字稿一樣）
  dialogue_turns.json  每一句的說話者、開始與結束的秒數、文字
用法（只能在 Mac 上執行，只用標準函式庫）：python3 services/analyzer/e2e/make_dialogue.py
"""
import array
import json
import subprocess
import tempfile
import wave
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RATE = 16000
GAP_SEC = 0.6      # 兩句之間的靜音
EDGE_SEC = 0.5     # 錄音開頭與結尾的靜音
MARGIN_SEC = 0.05  # 去掉靜音時，聲音前後各留一點
SILENCE = 328      # 振幅不到滿刻度的 1% 當成靜音
MAX_BYTES = 2 * 1024 * 1024
VOICES = {"Staff": "Eddy (Chinese (Taiwan))", "Customer": "Meijia"}

# 數字只用一位數（七天、三點）：bench_whisper.py 算字錯率時把阿拉伯數字逐字換成國字
DIALOGUE = [
    ("Staff", "您好，歡迎光臨，請問需要什麼幫忙嗎？"),
    ("Customer", "你好，我上禮拜在這裡買的藍牙耳機，右耳突然沒有聲音了。"),
    ("Staff", "不好意思造成您的不便，可以先讓我看一下耳機跟發票嗎？"),
    ("Customer", "好，發票在這裡。我重新配對了好幾次，還是一樣。"),
    ("Staff", "我幫您測試一下。確實是右耳故障，還在七天鑑賞期內，我直接幫您換一副新的。"),
    ("Customer", "太好了。那我加購的延長保固，也可以轉到新的耳機嗎？"),
    ("Staff", "可以，我已經幫您把延長保固轉到新耳機的序號上了。"),
    ("Customer", "那我會收到什麼證明嗎？"),
    ("Staff", "我會在明天下午三點以前，把延長保固的確認信寄到您的電子信箱。"),
    ("Customer", "好，麻煩你了。新的耳機兩邊都有聲音，謝謝。"),
    ("Staff", "不客氣，之後有任何問題，歡迎再來找我們。"),
]


def speak(voice, text, path):
    subprocess.run(["say", "-v", voice, "--file-format=WAVE", f"--data-format=LEI16@{RATE}", "-o", str(path), text],
                   check=True)
    with wave.open(str(path), "rb") as clip:
        if (clip.getnchannels(), clip.getsampwidth(), clip.getframerate()) != (1, 2, RATE):
            raise RuntimeError(f"say wrote an unexpected format: {path}")
        return array.array("h", clip.readframes(clip.getnframes()))  # Mac 是 little-endian，和 WAV 相同


def trim(samples):
    """去掉前後的靜音，聲音前後各留 MARGIN_SEC。"""
    loud = [i for i, value in enumerate(samples) if abs(value) > SILENCE]
    margin = int(MARGIN_SEC * RATE)
    return samples[max(0, loud[0] - margin):loud[-1] + 1 + margin]


def silence(seconds):
    return array.array("h", bytes(2 * int(seconds * RATE)))


def main():
    FIXTURES.mkdir(exist_ok=True)
    audio, turns = silence(EDGE_SEC), []
    with tempfile.TemporaryDirectory() as work:
        for number, (speaker, text) in enumerate(DIALOGUE):
            clip = trim(speak(VOICES[speaker], text, Path(work, f"{number}.wav")))
            start = len(audio) / RATE
            audio.extend(clip)
            turns.append({"speaker": speaker, "start": round(start, 3), "end": round(len(audio) / RATE, 3),
                          "text": text})
            audio.extend(silence(GAP_SEC if number < len(DIALOGUE) - 1 else EDGE_SEC))

    path = FIXTURES / "dialogue.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(audio.tobytes())
    (FIXTURES / "dialogue_zh_tw.txt").write_text("".join(f"{speaker}: {text}\n" for speaker, text in DIALOGUE),
                                                encoding="utf-8")
    truth = {"audio": path.name, "sample_rate": RATE, "duration": round(len(audio) / RATE, 3), "voices": VOICES,
             "turns": turns}
    (FIXTURES / "dialogue_turns.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
    size = path.stat().st_size
    print(f"{path}: {truth['duration']} 秒、{len(turns)} 句、{size / 1024 / 1024:.2f} MB")
    if size >= MAX_BYTES:
        raise SystemExit("錄音超過 2 MB：縮短對話")


if __name__ == "__main__":
    main()
