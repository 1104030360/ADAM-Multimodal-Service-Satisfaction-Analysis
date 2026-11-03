# 輕量測試映像檔的空殼：真的 deepface 會帶進 TensorFlow。測試會用 monkeypatch 換掉 analyze()。
def analyze(*args, **kwargs):
    raise RuntimeError("deepface stub: analyze() was not replaced by the test")


def build_model(name):
    return None
