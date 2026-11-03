# 輕量測試映像檔的空殼：真的 faster-whisper（CTranslate2）很大，測試會把 Whisper 換成假的。
class WhisperModel:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("faster_whisper stub")
