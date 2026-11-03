# tests/unit/test_analyzer.py — 三個模態的分析與 5 張圖（analyzer.py）。
# 不載入真模型：pyannote、wav2vec2、DeepFace 都換成假的；語音轉文字（speech.py 的 Whisper＋翻譯）換成固定的句子。
from collections import defaultdict
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
import torch
from pyannote.core import Annotation, Segment

import analyzer

SID = "S20261030120000"


# ---------- 假的模型、測試用的影音檔 ----------
class FakeDiarization:
    """假的 pyannote：回傳固定的說話片段 [(開始秒, 結束秒, 說話者), ...]，記下被呼叫幾次。"""

    def __init__(self, turns):
        self.turns = turns
        self.calls = 0

    def __call__(self, audio):
        self.calls += 1
        result = Annotation()
        for start, end, speaker in self.turns:
            result[Segment(start, end)] = speaker
        return result


class FakeFeatureExtractor:
    """假的 wav2vec2 特徵擷取：記下每次送進模型的樣本數。"""

    def __init__(self):
        self.lengths = []

    def __call__(self, chunk, **kwargs):
        self.lengths.append(len(chunk))
        return {"input_values": torch.as_tensor(chunk).reshape(1, -1)}


class FakeEmotionModel:
    """假的 wav2vec2 情緒模型：靜音的段是 Anger 0.2、Neutral 0.3、Happiness 0.5；有聲音的段是 0.6、0.3、0.1。"""

    config = SimpleNamespace(label2id={"Anger": 0, "Neutral": 1, "Happiness": 2})

    def __call__(self, input_values):
        probs = [0.6, 0.3, 0.1] if input_values.any() else [0.2, 0.3, 0.5]
        return SimpleNamespace(logits=torch.log(torch.tensor([probs])))


@pytest.fixture
def audio_models(monkeypatch):
    """換上假的聲音模型；回傳特徵擷取器，測試可以看它收到幾段、每段多長。"""
    extractor = FakeFeatureExtractor()
    monkeypatch.setattr(analyzer, "feature_extractor", extractor)
    monkeypatch.setattr(analyzer, "audio_model", FakeEmotionModel())
    return extractor


def fake_face(frame, **kwargs):
    """假的 DeepFace.analyze：亮的畫面是開心，暗的畫面是難過。"""
    return [{"dominant_emotion": "happy" if frame.mean() > 127 else "sad"}]


def fake_audio(seconds, loud=()):
    """取代 analyzer 裡的 librosa：load() 回傳 seconds 秒的靜音，只有 loud 裡的（開始秒, 結束秒）有聲音。
    真的 librosa 第一次 load 要先編譯 numba，在新的容器裡要十幾秒；這裡只測我們自己的邏輯。
    """

    def load(path, sr):
        assert sr == 16000  # wav2vec2 要 16 kHz 的音訊
        audio = np.zeros(16000 * seconds, np.float32)
        for start, end in loud:
            audio[16000 * start:16000 * end] = 0.1
        return audio, sr

    return SimpleNamespace(load=load)


def fake_loader(loaded, name):
    """假的模型載入函式：記下載入的順序，回傳名稱當作模型。"""

    def load(*args, **kwargs):
        loaded.append(name)
        return name

    return load


def write_video(path, brightness, seconds=3, fps=5):
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (32, 32))
    for _ in range(seconds * fps):
        out.write(np.full((32, 32, 3), brightness, np.uint8))
    out.release()
    return str(path)


# ---------- 文字：逐字稿 → VADER ----------
def vader_score(texts):
    # 原本的文字公式：三種比例先各自平均，再算 (−neg + 0.3·neu + pos)·40 + 60
    vs = [analyzer.analyze_sentiment_vader(t) for t in texts]
    neg, neu, pos = (sum(v[k] for v in vs) / len(vs) for k in ("neg", "neu", "pos"))
    return (-neg + 0.3 * neu + pos) * 40 + 60


def test_text_roles_fall_back_to_sentence_count(tmp_path):
    # pyannote 沒有分出任何人（top_speakers 是空的）時才用原本的規則：句子數多的那位是服務人員
    segments = [{"speaker": "spk_1", "zh": "您好", "en": "Hello, welcome!"},
                {"speaker": "spk_0", "zh": "我很生氣", "en": "I am very angry."},
                {"speaker": "spk_1", "zh": "抱歉", "en": "Sorry about that."}]
    scores, transcript, chart, proportions = analyzer.process_text(SID, segments, [], str(tmp_path))
    assert scores["staff"] == pytest.approx(vader_score(["Hello, welcome!", "Sorry about that."]), abs=0.01)
    assert scores["customer"] == pytest.approx(vader_score(["I am very angry."]), abs=0.01)
    # 報表的 Text 長條：和分數同一組平均（VADER 的 pos、neu、neg）
    angry = analyzer.analyze_sentiment_vader("I am very angry.")
    assert proportions["customer"] == pytest.approx(
        {"positive": angry["pos"], "neutral": angry["neu"], "negative": angry["neg"]})
    assert sum(proportions["staff"].values()) == pytest.approx(1, abs=0.01)
    assert (tmp_path / transcript).read_text(encoding="utf-8").startswith(
        "Staff: 您好\nTranslated: Hello, welcome!\n")
    assert chart == f"{SID}_emotion_TEXT.png" and (tmp_path / chart).exists()


def test_text_with_one_speaker_marks_customer_no_data(tmp_path):
    segments = [{"speaker": "spk_0", "zh": "您好", "en": "Hello"}]
    scores, _, _, proportions = analyzer.process_text(SID, segments, ["spk_0"], str(tmp_path))  # pyannote 只分出一位
    assert scores["customer"] == -1 and scores["staff"] != -1
    assert proportions["customer"] is None and proportions["staff"] is not None


def test_text_without_speech_is_no_data(tmp_path):
    scores, transcript, _, proportions = analyzer.process_text(SID, [], [], str(tmp_path))
    assert scores == {"staff": -1, "customer": -1}
    assert proportions == {"staff": None, "customer": None}
    assert (tmp_path / transcript).read_text(encoding="utf-8") == ""


# ---------- 聲音：pyannote 分人 → wav2vec2 情緒 ----------
def test_audio_uses_two_main_speakers_and_splits_long_segments(monkeypatch, audio_models):
    monkeypatch.setattr(analyzer, "librosa", fake_audio(22))
    monkeypatch.setattr(analyzer, "diarization_pipeline", FakeDiarization([
        (0, 20, "A"),  # 20 秒：超過 15 秒，切成兩段各 10 秒
        (20, 20.5, "B"),  # 0.5 秒：補零到 1 秒
        (20.5, 21, "C"),  # 第三位說話者，片段最少：不分析
        (21, 21.5, "B"),
        (21.5, 22, "A"),
    ]))
    audio, sr, turns = analyzer.diarize("a.wav")
    speakers = analyzer.main_speakers(speaker for _, _, speaker in turns)
    assert speakers == ["A", "B"]
    scores = analyzer.analyze_audio(audio, sr, turns, speakers)
    assert audio_models.lengths == [160000, 160000, 16000, 16000, 16000]
    assert scores["A"]["Positive"] == pytest.approx([0.5, 0.5])  # 每個片段一個值（切開的兩段先平均）
    assert "C" not in scores


def test_audio_without_speakers_is_no_data(tmp_path):
    scores, chart = analyzer.save_audio_results({}, SID, [], str(tmp_path))
    assert scores == {"staff": -1, "customer": -1}
    assert analyzer.audio_proportions({}, []) == {"staff": None, "customer": None}
    assert chart == f"{SID}_emotion_AUDIO.png" and (tmp_path / chart).exists()


def test_audio_one_speaker_uses_original_formula(tmp_path):
    probs = {"A": {"Negative": [0.1, 0.1], "Neutral": [0.2, 0.2], "Positive": [0.3, 0.3]}}
    scores, _ = analyzer.save_audio_results(probs, SID, ["A"], str(tmp_path))
    total = 0.1 + 0.2 + 0.3
    expected = 60 + (-0.1 / total + 0.5 * 0.2 / total + 0.3 / total) * 40
    assert scores == {"staff": round(expected, 2), "customer": -1}
    # 報表的 Audio 長條：和分數同一組比例（平均後除以三者的和）
    assert analyzer.audio_proportions(probs, ["A"]) == {
        "staff": pytest.approx({"positive": 0.3 / total, "neutral": 0.2 / total, "negative": 0.1 / total}),
        "customer": None}


def test_audio_speaker_without_probabilities_is_no_data(tmp_path):
    # 模型的標籤對不上 emotion_map 時，這位說話者沒有任何機率：記為 -1，不要除以 0
    scores, _ = analyzer.save_audio_results({"A": defaultdict(list)}, SID, ["A"], str(tmp_path))
    assert scores == {"staff": -1, "customer": -1}
    assert analyzer.audio_proportions({"A": defaultdict(list)}, ["A"]) == {"staff": None, "customer": None}


# ---------- 人臉：每秒一格 → DeepFace 情緒 ----------
def test_video_is_sampled_once_per_second(tmp_path, monkeypatch):
    analysed = []

    def counting_face(frame, **kwargs):
        analysed.append(frame)
        return fake_face(frame)

    monkeypatch.setattr(analyzer.DeepFace, "analyze", counting_face)
    a = analyzer.Analyzer()
    a.analyze_video(write_video(tmp_path / "cam0.avi", 255, seconds=3, fps=5), a.customer_details)
    assert len(analysed) == 3  # 3 秒、每秒 5 格的影片：只分析第 0、5、10 格
    assert a.customer_details["emotions"] == ["happy"] * 3
    assert a.customer_details["seconds"] == [0.0, 1.0, 2.0]  # 報表的 Facial 折線：第幾秒
    assert a.facial_series(a.customer_details) == [(0.0, 1), (1.0, 1), (2.0, 1)]


def test_unreadable_video_has_no_frames(tmp_path):
    a = analyzer.Analyzer()
    a.analyze_video(str(tmp_path / "missing.avi"), a.customer_details)
    assert a.customer_details["emotions"] == []


def test_face_detection_error_only_skips_that_frame(monkeypatch):
    def no_face(frame, **kwargs):
        raise ValueError("Face could not be detected")

    monkeypatch.setattr(analyzer.DeepFace, "analyze", no_face)
    a = analyzer.Analyzer()
    a.analyze_frame(np.zeros((32, 32, 3), np.uint8), a.customer_details)  # 不丟錯，整個工作不會失敗
    assert a.customer_details["emotions"] == []
    assert a.facial_series(a.customer_details) is None  # 沒有任何影格：報表的 Facial 是 No data


def test_face_score_uses_original_formula(tmp_path):
    a = analyzer.Analyzer()
    a.customer_details["emotions"] = ["happy", "happy", "sad", "neutral"]
    customer, staff, _ = a.plot_results(SID, str(tmp_path))
    assert customer == 70.0  # 60 + (0.5 − 0.25) × 40
    assert staff == -1  # 沒有任何影格：沒有資料，不是 60


# ---------- 文字與聲音：同一次 pyannote、同一個角色對應 ----------
def test_text_and_audio_share_the_roles_from_pyannote(tmp_path, monkeypatch, audio_models):
    # pyannote：片段最多的 SPEAKER_01 是服務人員、第二多的 SPEAKER_00 是顧客，文字和聲音都一樣：
    # 逐字稿裡 SPEAKER_00 的句子比較多也不會換人；片段最少的 SPEAKER_02 兩邊都不算
    monkeypatch.setattr(analyzer, "librosa", fake_audio(6, loud=[(0, 1), (2, 3), (4, 5)]))  # 只有 SPEAKER_01 有聲音
    monkeypatch.setattr(analyzer, "diarization_pipeline", FakeDiarization(
        [(0, 1, "SPEAKER_01"), (1, 2, "SPEAKER_00"), (2, 3, "SPEAKER_01"), (3, 4, "SPEAKER_02"),
         (4, 5, "SPEAKER_01"), (5, 6, "SPEAKER_00")]))
    monkeypatch.setattr(analyzer.speech, "transcribe_and_translate", lambda local_audio_path, turns: [
        {"speaker": "SPEAKER_00", "zh": "我要退貨", "en": "I want a refund."},
        {"speaker": "SPEAKER_01", "zh": "好的", "en": "Sure."},
        {"speaker": "SPEAKER_02", "zh": "借過", "en": "Excuse me."},
        {"speaker": "SPEAKER_00", "zh": "謝謝", "en": "Thanks."}])

    text_scores, _, transcript, _, proportions = analyzer.process_audio(SID, "a.wav", str(tmp_path))

    # 逐字稿的格式不變（報表的 AI 說明、服務結果都讀它）
    assert (tmp_path / transcript).read_text(encoding="utf-8") == (
        "Customer: 我要退貨\nTranslated: I want a refund.\n\n"
        "Staff: 好的\nTranslated: Sure.\n\n"
        "Customer: 謝謝\nTranslated: Thanks.\n")
    assert text_scores["staff"] == pytest.approx(vader_score(["Sure."]), abs=0.01)
    assert proportions["audio"] == {
        "staff": pytest.approx({"positive": 0.1, "neutral": 0.3, "negative": 0.6}),  # 有聲音的 SPEAKER_01
        "customer": pytest.approx({"positive": 0.5, "neutral": 0.3, "negative": 0.2})}


# ---------- 整筆服務 ----------
def test_process_service_without_media(tmp_path):
    result = analyzer.process_service(SID, {}, str(tmp_path))
    no_data = {"staff": -1, "customer": -1}
    report_data = result.pop("report_data")
    assert result == {"text": no_data, "audio": no_data, "face": no_data, "charts": [], "transcript": None}
    assert (report_data["schema"], report_data["version"], report_data["source"]) == ("adam.report-data", 1,
                                                                                       "analyzer")
    assert [report_data[part] for part in ("audio", "text", "facial")] == [{"staff": None, "customer": None}] * 3


def test_process_service_writes_five_charts_in_fixed_order(tmp_path, monkeypatch, audio_models):
    transcribed = []

    def fake_transcribe(local_audio_path, turns):
        transcribed.append((local_audio_path, turns))
        return [{"speaker": "SPEAKER_00", "zh": "您好", "en": "Hello, welcome!"},
                {"speaker": "SPEAKER_01", "zh": "我想退貨", "en": "I want a refund."},
                {"speaker": "SPEAKER_00", "zh": "沒問題", "en": "No problem."}]

    turns = [(0, 1, "SPEAKER_00"), (1, 2, "SPEAKER_01"), (2, 3, "SPEAKER_00")]
    diarization = FakeDiarization(turns)
    monkeypatch.setattr(analyzer.speech, "transcribe_and_translate", fake_transcribe)
    monkeypatch.setattr(analyzer, "librosa", fake_audio(3))
    monkeypatch.setattr(analyzer, "diarization_pipeline", diarization)
    monkeypatch.setattr(analyzer.DeepFace, "analyze", fake_face)
    media = {"audio": str(tmp_path / f"{SID}_audio.wav"),
             "cam0": write_video(tmp_path / f"{SID}_cam0.avi", 255),  # 顧客的鏡頭：一直開心
             "cam1": write_video(tmp_path / f"{SID}_cam1.avi", 0)}  # 服務人員的鏡頭：一直難過

    result = analyzer.process_service(SID, media, str(tmp_path))

    assert result["charts"] == [f"{SID}_emotion_TEXT.png", f"{SID}_emotion_AUDIO.png",
                                f"{SID}_emotion_wave_face.png", f"{SID}_customer_emotions_face.png",
                                f"{SID}_staff_emotions_face.png"]
    assert all((tmp_path / name).exists() for name in result["charts"] + [result["transcript"]])
    # 逐字稿讀本機的錄音，說話的人用聲音情緒的那一次 pyannote：一筆服務只跑一次
    assert transcribed == [(media["audio"], turns)]
    assert diarization.calls == 1
    assert result["face"] == {"customer": 100.0, "staff": 20.0}  # cam0 是顧客、cam1 是服務人員
    assert -1 not in [*result["text"].values(), *result["audio"].values()]  # 兩個人都有說話
    # 報表要畫的數字：兩側都有；Facial 是每秒一個點（服務人員一直難過、顧客一直開心）
    data = result["report_data"]
    assert data["facial"] == {"staff": {"points": [[0.0, -1], [1.0, -1], [2.0, -1]]},
                              "customer": {"points": [[0.0, 1], [1.0, 1], [2.0, 1]]}}
    assert data["audio"]["staff"] == {"positive": 0.5, "neutral": 0.3, "negative": 0.2}  # 假模型的機率
    assert data["text"]["staff"] is not None and data["text"]["customer"] is not None


def test_load_models_loads_speech_models_last(monkeypatch):
    loaded = []
    for name in ("diarization_pipeline", "feature_extractor", "audio_model"):
        monkeypatch.setattr(analyzer, name, None)  # load_models() 會改這些全域變數：測試結束後還原
    pipeline = SimpleNamespace(embedding_batch_size=32)  # 設定檔的值
    monkeypatch.setattr(analyzer.Pipeline, "from_pretrained", lambda name: loaded.append("pyannote") or pipeline)
    monkeypatch.setattr(analyzer.Wav2Vec2FeatureExtractor, "from_pretrained", fake_loader(loaded, "features"))
    monkeypatch.setattr(analyzer.Wav2Vec2ForSequenceClassification, "from_pretrained", fake_loader(loaded, "wav2vec2"))
    monkeypatch.setattr(analyzer.DeepFace, "build_model", fake_loader(loaded, "deepface"))
    monkeypatch.setattr(analyzer.speech, "load_models", fake_loader(loaded, "whisper+translator"))
    analyzer.load_models()
    assert loaded == ["pyannote", "features", "wav2vec2", "deepface", "whisper+translator"]
    assert (analyzer.diarization_pipeline, analyzer.feature_extractor, analyzer.audio_model) == \
        (pipeline, "features", "wav2vec2")
    assert pipeline.embedding_batch_size == 8  # 32 時 1 分鐘的錄音要 5.9 GiB（T48）


def test_missing_pyannote_model_stops_loading(monkeypatch):
    loaded = []
    monkeypatch.setattr(analyzer.Pipeline, "from_pretrained", lambda name: None)  # 找不到模型時不丟錯，只回傳 None
    monkeypatch.setattr(analyzer.speech, "load_models", fake_loader(loaded, "whisper+translator"))
    with pytest.raises(RuntimeError, match="download_models.py"):
        analyzer.load_models()
    assert loaded == []  # 停在缺的模型，不會再花時間載入 Whisper 與翻譯模型
