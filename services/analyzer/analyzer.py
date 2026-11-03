import os
from collections import defaultdict, Counter

import matplotlib
matplotlib.use("Agg")  # 伺服器沒有螢幕；一定要在 import matplotlib.pyplot 之前

import torch
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from pyannote.audio import Pipeline
from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForSequenceClassification
import librosa
import numpy as np
import matplotlib.pyplot as plt
import cv2
from deepface import DeepFace

import report_model
import speech

DIARIZATION_MODEL = "pyannote/speaker-diarization-3.1"
AUDIO_MODEL = "HowMannyMore/wav2vec2-lg-xlsr-ur-speech-emotion-recognition"
# pyannote 的聲紋一次算幾段（設定檔是 32）。1 分鐘的錄音：32 時記憶體峰值約 5.9 GiB，8 時約 2.0 GiB，
# 分出來的段落一樣、時間也差不多；EC2 只有 8 GiB，還要放 Whisper 和其他模型（docs/rebuild/tradeoff/T48）
EMBEDDING_BATCH_SIZE = 8
NO_DATA = -1           # 沒有資料；桌面端用 -1 判斷，統計時會排除
MAX_SEGMENT_SEC = 15   # 超過這個長度的語音片段切開來分析

# 模型由 load_models() 載入：worker.py 經由 analysis_queue.serve() 在 worker process 開始領工作之前呼叫一次（SQS，T49）
diarization_pipeline = None
feature_extractor = None
audio_model = None

text_analyzer = SentimentIntensityAnalyzer()

# 情绪类别映射（音频）
emotion_map = {
    'Negative': ['Anger', 'Disgust', 'Fearful', 'Sadness'],
    'Neutral': ['Neutral', 'Boredom'],
    'Positive': ['Happiness', 'Surprise']
}


def load_models():
    """載入模型。模型已由 download_models.py 下載到 /models，執行時不連網（HF_HUB_OFFLINE=1）。"""
    global diarization_pipeline, feature_extractor, audio_model
    pipeline = Pipeline.from_pretrained(DIARIZATION_MODEL)
    if pipeline is None:  # pyannote 找不到模型時不丟錯，只回傳 None
        raise RuntimeError(f"cannot load {DIARIZATION_MODEL}; run download_models.py first")
    pipeline.embedding_batch_size = EMBEDDING_BATCH_SIZE
    diarization_pipeline = pipeline
    feature_extractor = Wav2Vec2FeatureExtractor.from_pretrained(AUDIO_MODEL)
    audio_model = Wav2Vec2ForSequenceClassification.from_pretrained(AUDIO_MODEL)
    DeepFace.build_model("Emotion")  # 先載入，缺檔時啟動就會發現
    speech.load_models()  # 語音轉文字：Whisper 與翻譯模型


class Analyzer:
    def __init__(self):
        # seconds：每個情緒是錄影的第幾秒（報表的 Facial 折線用，和 emotions 一一對應）
        self.customer_details = {"emotions": [], "seconds": []}
        self.staff_details = {"emotions": [], "seconds": []}

        self.emotion_categories = {
            'positive': ['happy', 'surprise'],
            'negative': ['angry', 'disgust', 'fear', 'sad'],
            'neutral': ['neutral']
        }

    def analyze_frame(self, frame, details, second=None):
        try:
            analyze = DeepFace.analyze(frame, actions=['emotion'], enforce_detection=False)[0]
            details["emotions"].append(analyze['dominant_emotion'])
            details.setdefault("seconds", []).append(second)
        except Exception as e:
            print(f"Error in emotion detection: {e}")
            return None

    def analyze_video(self, video_path, details):
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not (fps > 0):  # 讀不到 fps（0 或 NaN）：當成沒有影格
            cap.release()
            return
        frame_interval = max(1, round(fps))  # 每秒提取一幀

        frame_count = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            if frame_count % frame_interval == 0:
                self.analyze_frame(frame, details, frame_count / fps)

            frame_count += 1

        cap.release()

    def calculate_emotion_scores(self, emotions):
        negative = 0
        neutral = 0
        positive = 0
        emotions_mapped = []
        for e in emotions:
            if e in self.emotion_categories['positive']:
                emotions_mapped.append(1)
                positive += 1
            elif e in self.emotion_categories['negative']:
                emotions_mapped.append(-1)
                negative += 1
            elif e in self.emotion_categories['neutral']:
                emotions_mapped.append(0)
                neutral += 1
        total_emotions = negative + neutral + positive
        if total_emotions > 0:
            negative_perc = round(negative / total_emotions, 2)
            neutral_perc = round(neutral / total_emotions, 2)
            positive_perc = round(positive / total_emotions, 2)
        else:
            negative_perc = neutral_perc = positive_perc = 0
        return emotions_mapped, negative_perc, neutral_perc, positive_perc

    def plot_results(self, service_id, work):
        basicpoint = 60
        remain = 100 - basicpoint
        negativeweight = -1
        neutralweight = 0
        positiveweight = 1

        emotions_mapped_customer, negative_customer_perc, neutral_customer_perc, positive_customer_perc = self.calculate_emotion_scores(self.customer_details["emotions"])
        emotions_mapped_staff, negative_staff_perc, neutral_staff_perc, positive_staff_perc = self.calculate_emotion_scores(self.staff_details["emotions"])

        customer_score = basicpoint + negative_customer_perc * remain * negativeweight + neutral_customer_perc * remain * neutralweight + positive_customer_perc * remain * positiveweight
        staff_score = basicpoint + negative_staff_perc * remain * negativeweight + neutral_staff_perc * remain * neutralweight + positive_staff_perc * remain * positiveweight

        # 沒有任何影格的情緒：記為沒有資料，不要給 60
        if not emotions_mapped_customer:
            customer_score = NO_DATA
        if not emotions_mapped_staff:
            staff_score = NO_DATA

        chart_names = [f'{service_id}_emotion_wave_face.png',
                       f'{service_id}_customer_emotions_face.png',
                       f'{service_id}_staff_emotions_face.png']

        # 繪製合成折線圖
        plt.figure(figsize=(10, 5))
        plt.plot(emotions_mapped_customer, label='Customer Emotion Wave', color='blue')
        plt.plot(emotions_mapped_staff, label='Staff Emotion Wave', color='orange')
        plt.axhline(y=0, color='gray', linestyle='--')
        plt.yticks([-1, 0, 1], ['Negative', 'Neutral', 'Positive'])
        plt.title("Emotion Wave Over Time")
        plt.xlabel("Frame")
        plt.ylabel("Emotion")
        plt.legend()
        plt.savefig(os.path.join(work, chart_names[0]))
        plt.close()

        # 繪製顧客情緒條形圖
        emotions = ['Negative', 'Neutral', 'Positive']
        percentages_customer = [negative_customer_perc, neutral_customer_perc, positive_customer_perc]
        plt.figure(figsize=(8, 4))
        bars_customer = plt.bar(emotions, percentages_customer, color=['red', 'gray', 'green'])
        plt.title(f'Percentage of Each Emotion in Customer - Full Service Score: {customer_score:.2f}')
        plt.xlabel('Emotion')
        plt.ylabel('Percentage')
        plt.ylim(0, 1)
        for bar in bars_customer:
            yval = bar.get_height()
            plt.text(bar.get_x() + bar.get_width() / 2, yval, f'{yval:.2%}', ha='center', va='bottom', fontsize=10, color='black')
        plt.savefig(os.path.join(work, chart_names[1]))
        plt.close()

        # 繪製員工情緒條形圖
        percentages_staff = [negative_staff_perc, neutral_staff_perc, positive_staff_perc]
        plt.figure(figsize=(8, 4))
        bars_staff = plt.bar(emotions, percentages_staff, color=['red', 'gray', 'green'])
        plt.title(f'Percentage of Each Emotion in Staff - Full Service Score: {staff_score:.2f}')
        plt.xlabel('Emotion')
        plt.ylabel('Percentage')
        plt.ylim(0, 1)
        for bar in bars_staff:
            yval = bar.get_height()
            plt.text(bar.get_x() + bar.get_width() / 2, yval, f'{yval:.2%}', ha='center', va='bottom', fontsize=10, color='black')
        plt.savefig(os.path.join(work, chart_names[2]))
        plt.close()

        return round(customer_score, 2), round(staff_score, 2), chart_names

    def facial_series(self, details):
        """報表的 Facial 折線（report-data JSON）：[(錄影的第幾秒, 1 POSITIVE／0 NEUTRAL／-1 NEGATIVE), …]。

        和人臉分數用的是同一批影格、同一組分類；沒有任何影格（或不知道時間）是 None。
        """
        values = {**{e: 1 for e in self.emotion_categories['positive']},
                  **{e: -1 for e in self.emotion_categories['negative']},
                  **{e: 0 for e in self.emotion_categories['neutral']}}
        points = [(second, values[emotion])
                  for emotion, second in zip(details["emotions"], details.get("seconds", []))
                  if emotion in values and second is not None]
        return points or None

    def analyze_videos(self, video_paths, service_id, work):
        """分析视频，回傳（顧客分數, 員工分數, 圖表檔名）"""
        if not video_paths:
            return NO_DATA, NO_DATA, []

        if 'cam0' in video_paths:
            self.analyze_video(video_paths['cam0'], self.customer_details)

        if 'cam1' in video_paths:
            self.analyze_video(video_paths['cam1'], self.staff_details)

        return self.plot_results(service_id, work)


def analyze_sentiment_vader(text):
    """使用VADER进行情绪分析"""
    return text_analyzer.polarity_scores(text)


def diarize(local_audio_path):
    """讀進錄音、用 pyannote 分出誰在什麼時候說話。回傳（音訊, 取樣率, 分段 [(開始秒, 結束秒, 說話者), ...]）。

    一筆服務只跑一次（real-analyze PLAN 3-2）：分段同時給聲音情緒（analyze_audio）和逐字稿（speech.py）。
    """
    # 使用 librosa 加载音频
    audio_input, sr = librosa.load(local_audio_path, sr=16000)  # 强制重采样为 16000 Hz

    # 进行Speaker分割：直接傳入已載入的音訊，不讓 pyannote 自己讀檔
    waveform = torch.from_numpy(audio_input).unsqueeze(0)
    diarization_result = diarization_pipeline({"waveform": waveform, "sample_rate": sr})
    turns = [(segment.start, segment.end, speaker)
             for segment, _, speaker in diarization_result.itertracks(yield_label=True)]
    return audio_input, sr, turns


def main_speakers(speakers):
    """出現次數最多的兩位說話者，多的在前：第一位是服務人員，第二位是顧客。"""
    return [speaker for speaker, _ in Counter(speakers).most_common(2)]


def analyze_audio(audio_input, sr, turns, top_speakers):
    """对音频进行情绪分析：turns 是 diarize() 的分段，只分析 top_speakers 這兩位的片段"""
    # 初始化每个Speaker情绪得分的累积器
    speaker_emotion_scores = defaultdict(lambda: defaultdict(list))

    min_length = 16000  # 假设最小长度为1秒
    max_length = sr * MAX_SEGMENT_SEC

    # 遍历由Speaker分割结果中获得的每个音频片段
    for start, end, speaker in turns:
        if speaker not in top_speakers:
            continue  # 只分析两个主要Speaker

        start_sample = int(sr * start)
        end_sample = int(sr * end)
        speaker_segment = audio_input[start_sample:end_sample]

        # 長片段切成幾段等長的小段，各自推論後把機率平均（wav2vec2 的記憶體用量隨長度平方成長）
        n_chunks = max(1, -(-len(speaker_segment) // max_length))
        chunk_probs = []
        for chunk in np.array_split(speaker_segment, n_chunks):
            if len(chunk) < min_length:
                chunk = np.pad(chunk, (0, min_length - len(chunk)), 'constant', constant_values=(0, 0))
            inputs = feature_extractor(chunk, return_tensors="pt", padding=True, sampling_rate=16000)  # 使用 16000 Hz 的采样率
            with torch.no_grad():
                logits = audio_model(**inputs).logits
            chunk_probs.append(torch.softmax(logits, dim=1)[0])
        probs = torch.stack(chunk_probs).mean(dim=0)

        for category, labels in emotion_map.items():
            for label in labels:
                label_idx = int(audio_model.config.label2id.get(label, -1))
                if label_idx != -1:
                    score = probs[label_idx].item()
                    speaker_emotion_scores[speaker][category].append(score)

    return speaker_emotion_scores


def text_proportions(sentiments_by_speaker):
    """報表的 Text 長條（report-data JSON）：每個角色 VADER 的 pos、neu、neg 各自平均（和文字分數同一組數字）。"""
    result = {}
    for role in ("staff", "customer"):
        sentiments = sentiments_by_speaker.get(role) or []
        if not sentiments:
            result[role] = None  # 這個角色沒有說話
            continue
        average = {key: sum(s[key] for s in sentiments) / len(sentiments) for key in ("pos", "neu", "neg")}
        result[role] = {"positive": average["pos"], "neutral": average["neu"], "negative": average["neg"]}
    return result


def audio_proportions(speaker_emotion_scores, top_speakers):
    """報表的 Audio 長條：每個角色三類機率的平均，除以三者的和（和聲音分數用的 normalized_scores 相同）。"""
    result = {"staff": None, "customer": None}
    for role, speaker in zip(("staff", "customer"), top_speakers):  # 片段多的是服務人員（和 save_audio_results 一樣）
        category_scores = speaker_emotion_scores.get(speaker, {})
        if not all(category_scores.get(category) for category in ['Negative', 'Neutral', 'Positive']):
            continue
        averages = {category: float(np.mean(category_scores[category]))
                    for category in ['Negative', 'Neutral', 'Positive']}
        total = sum(averages.values())
        if total > 0:
            result[role] = {"positive": averages['Positive'] / total, "neutral": averages['Neutral'] / total,
                            "negative": averages['Negative'] / total}
    return result


def process_text(service_id, segments, top_speakers, work):
    """把逐字稿分成服務人員與顧客、做文字情緒分析。回傳（分數, 逐字稿檔名, 圖表檔名, 報表用的比例）

    top_speakers：pyannote 片段最多的兩位（服務人員、顧客），和聲音分析是同一個對應。
    """
    if not top_speakers:  # pyannote 沒有分出任何人：用原本的規則，句子數多的那位是服務人員
        top_speakers = main_speakers(seg["speaker"] for seg in segments)
    roles = dict(zip(top_speakers, ["staff", "customer"]))

    sentiments_by_speaker = defaultdict(list)
    transcript = []
    for seg in segments:
        role = roles.get(seg["speaker"])
        if role is None:
            continue
        transcript.append(f"{role.capitalize()}: {seg['zh']}")
        transcript.append(f"Translated: {seg['en']}\n")
        sentiments_by_speaker[role].append(analyze_sentiment_vader(seg["en"]))

    # 保存转录文本
    transcript_name = f"{service_id}_transcript.txt"
    with open(os.path.join(work, transcript_name), "w", encoding="utf-8") as script_file:
        script_file.write("\n".join(transcript))

    scores, chart_name = save_text_results(sentiments_by_speaker, service_id, work)
    return scores, transcript_name, chart_name, text_proportions(sentiments_by_speaker)


def process_audio(service_id, local_audio_path, work):
    """处理音频：文字（Whisper＋opus-mt＋VADER，speech.py）與聲音情緒兩個模態。最後一個回傳值是報表用的比例。

    pyannote 只跑一次：分段同時給逐字稿與聲音情緒，兩個模態的服務人員、顧客也是同一個對應。
    """
    audio_input, sr, turns = diarize(local_audio_path)
    # 统计每个Speaker的出现次数，选择出现次数最多的两个Speaker：片段多的是服務人員
    top_speakers = main_speakers(speaker for _, _, speaker in turns)
    # 沒有偵測到任何人說話時 top_speakers 是空的：兩個角色的聲音分數都會是 -1，文字改用句子數分角色

    print(f"Detected speakers in audio: {top_speakers}")

    segments = speech.transcribe_and_translate(local_audio_path, turns)
    text_scores, transcript_name, text_chart, text_props = process_text(service_id, segments, top_speakers, work)

    # 执行音频情绪分析
    speaker_emotion_scores = analyze_audio(audio_input, sr, turns, top_speakers)
    audio_scores, audio_chart = save_audio_results(speaker_emotion_scores, service_id, top_speakers, work)

    proportions = {"text": text_props, "audio": audio_proportions(speaker_emotion_scores, top_speakers)}
    return text_scores, audio_scores, transcript_name, [text_chart, audio_chart], proportions


def save_text_results(sentiments_by_speaker, service_id, work):
    """将文本情绪分析结果保存为图表，并返回分数。"""
    labels = ['Negative', 'Neutral', 'Positive']
    roles = list(sentiments_by_speaker.keys())
    width = 0.3

    data = defaultdict(list)
    for role, sentiments in sentiments_by_speaker.items():
        avg_sentiments = defaultdict(float)
        for sentiment in sentiments:
            for key, value in sentiment.items():
                avg_sentiments[key] += value / len(sentiments)
        data['neg'].append(avg_sentiments['neg'])
        data['neu'].append(avg_sentiments['neu'])
        data['pos'].append(avg_sentiments['pos'])

    x = range(len(labels))
    fig, ax = plt.subplots(figsize=(6, 5))

    for idx, role in enumerate(roles):
        ax.bar(
            [p + width * idx for p in x],
            [data[key][idx] for key in ['neg', 'neu', 'pos']],
            width,
            alpha=0.7,
            label=f'{role.capitalize()}'
        )

    ax.set_xlabel('Sentiment')
    ax.set_ylabel('Proportion')
    ax.set_title(f'{service_id} emotion_TEXT')
    ax.set_xticks([p + width * (len(roles) - 1) / 2 for p in x])
    ax.set_xticklabels(labels)

    # 添加图例
    if roles:
        ax.legend()

    ax.set_ylim(0, 1)
    ax.set_yticks(np.arange(0, 1.1, 0.2))

    plt.tight_layout()
    chart_name = f"{service_id}_emotion_TEXT.png"
    plt.savefig(os.path.join(work, chart_name))
    plt.close()

    scores = {}
    for role in ["staff", "customer"]:
        if role in roles:
            idx = roles.index(role)
            neg = data['neg'][idx]
            neu = data['neu'][idx]
            pos = data['pos'][idx]
            score = (neg * -1 + neu * 0.3 + pos * 1) * 40 + 60
            scores[role] = round(score, 2)  # 分数保留两位小数
        else:
            scores[role] = NO_DATA  # 這個角色沒有說話；原本給 60

    return scores, chart_name


def save_audio_results(speaker_emotion_scores, service_id, top_speakers, work):
    """将音频情绪分析结果保存为图表，并返回分数。"""
    plt.figure(figsize=(6, 5))
    width = 0.3  # 柱状图的宽度
    x = np.arange(len(emotion_map))  # 情绪类别标签位置

    # 定义颜色
    colors = ['#1f77b4', '#ff7f0e']  # 蓝色和橙色

    scores = {}
    role_mapping = {0: "staff", 1: "customer"}  # top_speakers 依片段數排序：片段多的是服務人員

    for i, speaker in enumerate(top_speakers):  # 使用 top_speakers 确保只有两个speaker
        category_scores = speaker_emotion_scores[speaker]
        if not all(category_scores[category] for category in ['Negative', 'Neutral', 'Positive']):
            continue  # 這位說話者沒有任何機率：當成沒有資料，不要除以 0
        averages = [float(np.mean(category_scores[category])) for category in ['Negative', 'Neutral', 'Positive']]
        plt.bar(x + i * width, averages, width, label=role_mapping.get(i, f"Speaker {i}"), color=colors[i % len(colors)])

        # 计算加权得分
        normalized_scores = {
            category: avg / sum(averages) for category, avg in zip(['Negative', 'Neutral', 'Positive'], averages)
        }
        weighted_score = 60 + (normalized_scores['Negative'] * -1 + normalized_scores['Neutral'] * 0.5 + normalized_scores['Positive'] * 1) * 40
        scores[role_mapping.get(i, f"Speaker {i}")] = round(weighted_score, 2)  # 分数保留两位小数

    plt.xlabel('Sentiment')
    plt.ylabel('Proportion')
    plt.title(f'{service_id} emotion_Audio')
    plt.xticks(x + width/2, ['Negative', 'Neutral', 'Positive'])
    if scores:
        plt.legend()
    plt.ylim(0, 1)

    # 保存图表到暫存資料夾
    chart_name = f"{service_id}_emotion_AUDIO.png"
    plt.savefig(os.path.join(work, chart_name))
    plt.close()

    # 确保返回staff和customer的分数
    for role in ["staff", "customer"]:
        if role not in scores:
            scores[role] = NO_DATA  # 原本給 60

    return scores, chart_name


def process_service(service_id, media, work):
    """分析一筆服務。

    media：已下載到 work 的本機檔案 {"cam0": ..., "cam1": ..., "audio": ...}，沒有的鍵表示沒有這個檔。
    回傳各模態分數（-1 表示沒有資料）、依固定順序排列的圖表檔名、逐字稿檔名，
    以及報表要畫的數字 report_data（report-data/{service_id}.json，格式見 report_model.py；兩側都存）。
    """
    no_data = {"staff": NO_DATA, "customer": NO_DATA}
    text_scores, audio_scores, transcript_name, charts = dict(no_data), dict(no_data), None, []
    proportions = {"text": None, "audio": None}

    # 音频分析（文字＋聲音）
    if "audio" in media:
        text_scores, audio_scores, transcript_name, charts, proportions = process_audio(
            service_id, media["audio"], work)
    else:
        print(f"No audio file found for service_id: {service_id}")

    # 视频分析
    video_paths = {cam: media[cam] for cam in ("cam0", "cam1") if cam in media}
    if not video_paths:
        print(f"No video files found for service_id: {service_id}")
    analyzer = Analyzer()
    customer_face, staff_face, face_charts = analyzer.analyze_videos(video_paths, service_id, work)

    report_data = report_model.build_report_data(
        service_id, source="analyzer", audio=proportions["audio"], text=proportions["text"],
        facial={"staff": analyzer.facial_series(analyzer.staff_details),
                "customer": analyzer.facial_series(analyzer.customer_details)})
    return {
        "text": text_scores,
        "audio": audio_scores,
        "face": {"staff": staff_face, "customer": customer_face},
        "charts": charts + face_charts,  # TEXT、AUDIO、wave_face、customer_face、staff_face
        "transcript": transcript_name,
        "report_data": report_data,
    }
