"""比較 Whisper 的大小（small、medium、large-v3-turbo）：載入時間、轉寫時間、即時倍率（RTF）、記憶體峰值，
以及合成對話（fixtures/）的字錯率（CER）。直接用 faster-whisper，轉寫的選項和 speech.transcribe_and_translate() 相同。
再用分析服務自己的程式跑一次合成對話：pyannote 分段（analyzer.diarize()，只跑一次）→ speech.transcribe_and_translate()，
說話者照分析服務的規則對成服務人員、顧客（analyzer.main_speakers()），算每個字歸對人的比例和逐字稿的字錯率。

在分析服務的映像檔裡跑，離線、只用 /models 裡下載好的模型。每一項在自己的子行程裡跑，記憶體峰值（ru_maxrss）才不會互相影響。
合成的對話一定會跑；其他錄音從命令列給。使用者的錄音是個人資料：只印時間和字數，逐字稿不印也不存。

  docker run --rm --network none -e HF_HUB_OFFLINE=1 -v "$HOME/adam-models:/models:ro" \
    -v "$PWD/services/analyzer/e2e:/e2e:ro" -v "$HOME/audio:/data:ro" -v "<輸出的資料夾>:/out" \
    adam-analyzer:dev python /e2e/bench_whisper.py --out /out/bench.json /data/S20261101030634_audio.wav
"""
import argparse
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

MODELS = ("small", "medium", "large-v3-turbo")
DIALOGUE = Path(__file__).resolve().parent / "fixtures" / "dialogue.wav"
APP = "/app"  # 映像檔裡分析服務的程式（Dockerfile 的 WORKDIR）
WHISPER_DIR = os.getenv("WHISPER_DIR", "/models/whisper")
COMPUTE_TYPE = "int8"
# 和 speech.transcribe_and_translate() 裡的 whisper_model.transcribe(...) 相同：那邊改了，這裡要跟著改
OPTIONS = dict(language="zh", task="transcribe", beam_size=5, vad_filter=True, word_timestamps=True,
               condition_on_previous_text=False)
WARMUP_SEC = 3  # 第一次轉寫要載入 VAD 模型、建立執行緒：先轉寫合成對話的前 3 秒，不算進時間


def peak_rss_mib():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux 的單位是 KiB


def measure(size, files):
    """子行程：載入一個大小的 Whisper，依序轉寫每個檔案，結果印成一行 JSON。只 import faster-whisper，
    記憶體峰值才只有 Whisper 自己（分析服務還有 pyannote、Wav2Vec2、DeepFace、翻譯模型）。"""
    from faster_whisper import WhisperModel, decode_audio

    started = time.monotonic()
    model = WhisperModel(size, device="cpu", compute_type=COMPUTE_TYPE, cpu_threads=os.cpu_count(),
                         download_root=WHISPER_DIR, local_files_only=True)
    result = {"model": size, "load_sec": round(time.monotonic() - started, 2), "load_rss_mib": round(peak_rss_mib())}
    segments, _ = model.transcribe(decode_audio(str(DIALOGUE))[:WARMUP_SEC * 16000], **OPTIONS)
    list(segments)
    result["files"] = []
    for path in files:
        started = time.monotonic()
        segments, info = model.transcribe(path, **OPTIONS)
        text = "".join(segment.text for segment in segments)  # segments 是 generator：取完才算轉寫完
        seconds = time.monotonic() - started
        result["files"].append({"file": os.path.basename(path), "audio_sec": round(info.duration, 2),
                                "speech_sec": round(info.duration_after_vad, 2), "transcribe_sec": round(seconds, 2),
                                "rtf": round(seconds / info.duration, 3), "text": text})
    result["peak_rss_mib"] = round(peak_rss_mib())
    print(json.dumps(result, ensure_ascii=False))


def diarize():
    """子行程：和分析服務一樣，用 pyannote 分出合成對話的說話者（analyzer.diarize()），印出分段和片段最多的兩位。"""
    sys.path.append(APP)
    import analyzer
    from pyannote.audio import Pipeline

    # 和 analyzer.load_models() 一樣載入 pyannote；其他模型這裡用不到
    analyzer.diarization_pipeline = Pipeline.from_pretrained(analyzer.DIARIZATION_MODEL)
    analyzer.diarization_pipeline.embedding_batch_size = analyzer.EMBEDDING_BATCH_SIZE
    started = time.monotonic()
    _, _, turns = analyzer.diarize(str(DIALOGUE))
    print(json.dumps({"seconds": round(time.monotonic() - started, 2), "peak_rss_mib": round(peak_rss_mib()),
                      "embedding_batch_size": analyzer.EMBEDDING_BATCH_SIZE, "turns": turns,
                      "top_speakers": analyzer.main_speakers(speaker for _, _, speaker in turns)}))


def transcribe(size):
    """子行程：用分析服務的 speech.transcribe_and_translate() 轉寫合成對話，pyannote 的分段從 stdin 讀。
    記憶體峰值是語音這一步要的（Whisper＋翻譯模型＋PyTorch）。"""
    os.environ["WHISPER_MODEL"] = size  # speech.py 在 import 時讀
    sys.path.append(APP)
    import speech
    from faster_whisper import decode_audio

    speech.load_models()
    turns = [tuple(turn) for turn in json.load(sys.stdin)["turns"]]
    speech.transcribe_and_translate(decode_audio(str(DIALOGUE))[:WARMUP_SEC * 16000], [])  # 暖機，和 measure() 一樣
    started = time.monotonic()
    sentences = speech.transcribe_and_translate(str(DIALOGUE), turns)
    print(json.dumps({"seconds": round(time.monotonic() - started, 2), "peak_rss_mib": round(peak_rss_mib()),
                      "sentences": [[s["speaker"], s["zh"], s["en"]] for s in sentences]}, ensure_ascii=False))


def run(*args, stdin=None):
    """跑一個子行程，回傳它印的最後一行 JSON；失敗時把 stderr 的最後一段印出來，回傳 None。"""
    process = subprocess.run([sys.executable, __file__, *args], input=stdin, capture_output=True, text=True)
    if process.returncode != 0:
        print(f"{' '.join(args)} 失敗：\n{process.stderr[-3000:]}", file=sys.stderr)
        return None
    return json.loads(process.stdout.splitlines()[-1])


def compare(sizes, files):
    import dialogue_truth  # 只在主行程用（OpenCC 的字典）：不算進子行程的記憶體

    truth = dialogue_truth.turns()
    reference = "".join(turn["text"] for turn in truth)
    diarized = run("--diarize")
    results = []
    for size in sizes:
        result = run("--measure", size, str(DIALOGUE), *files)
        if result is None:
            results.append({"model": size, "failed": True})
            continue
        for entry in result["files"]:
            if entry["file"] == DIALOGUE.name:
                entry["cer"] = round(dialogue_truth.cer(entry["text"], reference), 4)
            else:
                entry["chars"] = len(dialogue_truth.normalize(entry.pop("text")))  # 個人資料：只留字數
        spoken = run("--transcribe", size, stdin=json.dumps(diarized)) if diarized else None
        if spoken is None:
            result["failed"] = True
        else:
            # 和 analyzer.process_text() 一樣：片段最多的是服務人員，第二多的是顧客，其他人說的句子不寫進逐字稿
            roles = dict(zip(diarized["top_speakers"], ("Staff", "Customer")))
            lines = [(roles[speaker], zh, en) for speaker, zh, en in spoken["sentences"] if speaker in roles]
            pairs = dialogue_truth.aligned_speakers([(role, zh) for role, zh, _ in lines], truth)
            result["speech"] = {
                "seconds": spoken["seconds"], "peak_rss_mib": spoken["peak_rss_mib"], "sentences": len(lines),
                "cer": round(dialogue_truth.cer("".join(zh for _, zh, _ in lines), reference), 4),
                "attribution": round(sum(heard == expected for heard, expected in pairs) / len(pairs), 4) if pairs else 0,
                "transcript": lines}
        results.append(result)
    return diarized, results


def show(diarized, results):
    print(f"CPU {os.cpu_count()} 核、{COMPUTE_TYPE}；選項 {OPTIONS}")
    print(f"\n{'model':<16}{'load s':>7}{'peak MiB':>10}  {'file':<28}{'audio s':>8}{'speech s':>9}{'wall s':>8}"
          f"{'RTF':>7}  CER（或字數）")
    for result in results:
        if "files" not in result:
            print(f"{result['model']:<16}失敗")
            continue
        head = f"{result['model']:<16}{result['load_sec']:>7.1f}{result['peak_rss_mib']:>10}"
        for entry in result["files"]:
            score = f"{entry['cer']:.1%}" if "cer" in entry else f"({entry['chars']} 字)"
            print(f"{head:<33}  {entry['file']:<28}{entry['audio_sec']:>8.1f}{entry['speech_sec']:>9.1f}"
                  f"{entry['transcribe_sec']:>8.1f}{entry['rtf']:>7.3f}  {score}")
            head = ""
    if diarized:
        print(f"\npyannote（analyzer.diarize()，embedding batch {diarized['embedding_batch_size']}）："
              f"{diarized['seconds']:.1f} 秒，記憶體峰值 {diarized['peak_rss_mib']} MiB，{len(diarized['turns'])} 段，"
              f"服務人員、顧客：{', '.join(diarized['top_speakers'])}")
    print(f"\nspeech.transcribe_and_translate()，{DIALOGUE.name}（attribution：歸對人的字）")
    print(f"{'model':<16}{'wall s':>8}{'peak MiB':>10}{'sentences':>11}{'CER':>8}  attribution")
    for result in results:
        spoken = result.get("speech")
        if spoken:
            print(f"{result['model']:<16}{spoken['seconds']:>8.1f}{spoken['peak_rss_mib']:>10}{spoken['sentences']:>11}"
                  f"{spoken['cer']:>8.1%}  {spoken['attribution']:.1%}")
        else:
            print(f"{result['model']:<16}失敗")
    for result in results:
        for entry in result.get("files", ()):
            if "cer" in entry:
                print(f"\n{result['model']}（faster-whisper）：{entry['text']}")
        for role, zh, _ in (result.get("speech") or {}).get("transcript", ()):
            print(f"  {role}: {zh}")


def main():
    parser = argparse.ArgumentParser(description="比較 Whisper 的大小（說明見檔案開頭）")
    parser.add_argument("audio", nargs="*", help="其他錄音：沒有標準答案，只量時間和字數")
    parser.add_argument("--models", default=",".join(MODELS), help="逗號分隔，預設 %(default)s")
    parser.add_argument("--out", help="結果另存成 JSON（不含使用者錄音的逐字稿）")
    parser.add_argument("--measure", help=argparse.SUPPRESS)  # 子行程：只量這一個大小的 faster-whisper
    parser.add_argument("--diarize", action="store_true", help=argparse.SUPPRESS)  # 子行程：pyannote
    parser.add_argument("--transcribe", help=argparse.SUPPRESS)  # 子行程：這一個大小的 speech.py
    args = parser.parse_args()
    if args.measure:
        measure(args.measure, args.audio)
        return
    if args.diarize:
        diarize()
        return
    if args.transcribe:
        transcribe(args.transcribe)
        return
    diarized, results = compare(args.models.split(","), args.audio)
    show(diarized, results)
    if args.out:
        Path(args.out).write_text(json.dumps({"cpus": os.cpu_count(), "compute_type": COMPUTE_TYPE, "options": OPTIONS,
                                              "diarization": diarized, "results": results},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
    sys.exit(1 if diarized is None or any(result.get("failed") for result in results) else 0)


if __name__ == "__main__":
    main()
