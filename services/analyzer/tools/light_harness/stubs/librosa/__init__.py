# 輕量測試映像檔的空殼：測試會把 analyzer.librosa 換成 silent_audio()。
def load(path, sr=None):
    raise RuntimeError("librosa stub: load() was not replaced by the test")
