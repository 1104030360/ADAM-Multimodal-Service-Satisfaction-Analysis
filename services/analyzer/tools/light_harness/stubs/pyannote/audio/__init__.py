# 輕量測試映像檔的空殼：真的 pyannote.audio 要 torchaudio 和需要授權的模型。測試會換掉用到的部分。
# pyannote/ 底下沒有 __init__.py：和 pip 裝的 pyannote.core 合成同一個 namespace package。
class Pipeline:
    @classmethod
    def from_pretrained(cls, name, use_auth_token=None):
        raise RuntimeError("pyannote stub: from_pretrained() was not replaced by the test")
