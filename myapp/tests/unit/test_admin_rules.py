"""管理員端不需要真的資料庫的規則：帳號等級、共用連線、排名的平均（步驟 2-4、4-2、4-3）。"""
import gc
from types import SimpleNamespace

from myapp.init.admin_home_init import AccountManager, RankingModel, TrendDataManager
from tests.fakes import FakeConnection, FakeDatabase


def connector(rows=()):
    """只有 .connection 的假 DatabaseConnector；查詢一律回傳 rows。"""
    return SimpleNamespace(connection=FakeConnection(FakeDatabase(rows=rows)))


def emitted_ranking(rows):
    """回傳 updateRanking 送出的四個清單：顧客前 5、顧客後 5、服務人員前 5、服務人員後 5。"""
    model = RankingModel(connector(rows))
    lists = []
    model.updateRanking.connect(lambda *ranking: lists.extend(ranking))
    model.load_ranking()
    return lists


def test_cannot_add_or_update_account_above_own_level():
    manager = AccountManager("3", db_connector=None)  # None：只要碰到資料庫就會出錯
    results = []
    manager.accountAdded.connect(lambda ok, message: results.append(ok))
    manager.accountUpdated.connect(lambda ok, message: results.append(ok))
    manager.add_account("new_admin", "pw", "新管理員", "4", "管理員")
    manager.update_account("acc-9", "boss", "pw", "老闆", "5", "管理員")
    assert results == [False, False]


def test_dropping_account_manager_keeps_shared_connection_open():
    connection = FakeConnection()
    shared = SimpleNamespace(connection=connection, close_connection=connection.close)
    manager = AccountManager("5", shared)
    del manager
    gc.collect()
    assert connection.is_connected()  # 整個 app 共用這條連線，不能跟著物件回收被關掉


def test_ranking_ignores_missing_scores():
    top_customers, _, top_servers, _ = emitted_ranking([
        ("小明", 80.0, -1.0),  # -1 代表那一項沒有資料
        ("小明", -1.0, 60.0),
        ("小華", -1.0, -1.0),  # 全部沒有資料的人不排名（也不能除以 0）
    ])
    assert top_customers == [{"name": "小明", "score": 80.0}]
    assert top_servers == [{"name": "小明", "score": 60.0}]


def test_ranking_keeps_best_and_worst_five():
    rows = [("員工1", 10.0, 90.0), ("員工1", 10.0, 90.0), ("員工1", 11.0, 90.0)]
    rows += [(f"員工{n}", n * 10.0, 100 - n * 10.0) for n in range(2, 8)]
    top_customers, bottom_customers, top_servers, bottom_servers = emitted_ranking(rows)
    assert [p["name"] for p in top_customers] == ["員工7", "員工6", "員工5", "員工4", "員工3"]
    assert [p["name"] for p in bottom_customers] == ["員工1", "員工2", "員工3", "員工4", "員工5"]
    assert bottom_customers[0]["score"] == 10.33  # (10 + 10 + 11) / 3，取到小數第 2 位
    assert [p["name"] for p in top_servers] == ["員工1", "員工2", "員工3", "員工4", "員工5"]
    assert [p["name"] for p in bottom_servers] == ["員工7", "員工6", "員工5", "員工4", "員工3"]


def test_trends_with_invalid_year_emit_nothing():
    manager = TrendDataManager(connector())
    points = []
    manager.serverPointAdded.connect(lambda day, score: points.append(day))
    manager.load_trends_for_month(10, 0)  # 年份選單還沒準備好時，QML 會傳 Number("") = 0
    assert points == []
