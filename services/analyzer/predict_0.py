import os
import joblib
import pandas as pd

NO_DATA = -1
MODEL_DIR = os.getenv("MODEL_DIR", "/models/adam")


def load_model(file_name):
    # 兩個 .pkl 目前找不到；檔案不存在時不載入，改用三個模態分數的平均（見 total_score）
    path = os.path.join(MODEL_DIR, file_name)
    return joblib.load(path) if os.path.exists(path) else None


# Load the random forest models, make sure the path is correct
customer_model_rf = load_model('customer_score_predictor_rf.pkl')
staff_model_rf = load_model('staff_score_predictor_rf.pkl')


def total_score(model, columns, values):
    """values：文字、聲音、人臉三個分數，-1 表示沒有資料。回傳 0～100、小數 2 位的總分，三個都沒有資料時回傳 -1。"""
    if all(v == NO_DATA for v in values):
        return NO_DATA
    if model is not None:
        # 模型訓練時沒有 -1：沒有資料的模態照原本的做法給 60
        row = pd.DataFrame([dict(zip(columns, [60.0 if v == NO_DATA else v for v in values]))])
        score = float(model.predict(row)[0])
    else:
        valid = [v for v in values if v != NO_DATA]
        score = sum(valid) / len(valid)
    return round(max(0.0, min(100.0, score)), 2)


def predict_total_scores(data):
    """data 是 analyzer.process_service() 的回傳值。回傳（顧客總分, 員工總分）。"""
    customer_total_score = total_score(
        customer_model_rf, ['customer_text', 'customer_audio', 'customer_face'],
        [data['text']['customer'], data['audio']['customer'], data['face']['customer']])
    staff_total_score = total_score(
        staff_model_rf, ['staff_text', 'staff_audio', 'staff_face'],
        [data['text']['staff'], data['audio']['staff'], data['face']['staff']])
    return customer_total_score, staff_total_score
