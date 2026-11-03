# tests/unit/test_predict.py — 總分（predict_0.py）：有 .pkl 就用模型，沒有就用平均；-1 表示沒有資料
import predict_0


class FixedModel:
    """假的 scikit-learn 模型：記下收到的資料，回傳固定的值。"""

    def __init__(self, value):
        self.value = value

    def predict(self, rows):
        self.rows = rows
        return [self.value]


def test_total_score_average_and_no_data():
    cols = ["text", "audio", "face"]
    assert predict_0.total_score(None, cols, [-1, -1, -1]) == -1
    assert predict_0.total_score(None, cols, [80, -1, 61]) == 70.5


def test_total_score_model_is_clipped_and_gets_60_for_missing():
    model = FixedModel(123.456)
    assert predict_0.total_score(model, ["text", "audio", "face"], [80, -1, 61]) == 100.0
    assert model.rows.iloc[0].tolist() == [80, 60.0, 61]


def test_predict_total_scores_maps_roles(monkeypatch):
    monkeypatch.setattr(predict_0, "customer_model_rf", None)  # 不管 /models 裡有沒有 .pkl，都用平均
    monkeypatch.setattr(predict_0, "staff_model_rf", None)
    data = {"text": {"staff": 80, "customer": -1}, "audio": {"staff": 70, "customer": 90},
            "face": {"staff": 60, "customer": -1}}
    assert predict_0.predict_total_scores(data) == (90.0, 70.0)
