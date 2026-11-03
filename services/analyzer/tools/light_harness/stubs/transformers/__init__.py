# 輕量測試映像檔的空殼：真的 transformers 很大，測試會把模型換成假的。
class Wav2Vec2FeatureExtractor:
    @classmethod
    def from_pretrained(cls, name):
        raise RuntimeError("transformers stub")


class Wav2Vec2ForSequenceClassification:
    @classmethod
    def from_pretrained(cls, name):
        raise RuntimeError("transformers stub")


class MarianTokenizer:
    @classmethod
    def from_pretrained(cls, name, **kwargs):
        raise RuntimeError("transformers stub")


class MarianMTModel:
    @classmethod
    def from_pretrained(cls, name, **kwargs):
        raise RuntimeError("transformers stub")
