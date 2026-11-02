"""服務結果與待辦（PLAN1 W1，T44）在桌面端的資料：明細的查詢多讀三欄、服務人員只查自己的服務、MediaModel 的屬性。

分析服務寫、桌面端只讀：tbl_service.service_outcome（五個值之一，NULL＝還沒填）、service_todos（一項一行）、
service_outcome_model。畫面（ServiceOutcomeCard.qml）和兩端的流程在 tests/integration/test_service_outcome.py。
"""
from datetime import datetime

import pytest

from myapp import media

SID = "S20261101093000"
STAFF = "00000000-0000-0000-0000-000000000002"
START, END = datetime(2026, 11, 1, 9, 30), datetime(2026, 11, 1, 9, 36)


class Rows:
    """假的 cursor：execute 記下 SQL 和參數，fetchall 回傳 rows；tuple 的列用 column_names 對應欄位。"""

    column_names = ("service_start", "service_end", "service_status", "account_name",
                    "service_outcome", "service_todos", "service_outcome_model",
                    *(f"{who}_{kind}" for who in ("customer", "server") for kind in ("total", "text", "audio", "facial")),
                    "kind", "url")

    def __init__(self, rows):
        self.rows, self.sql, self.params = rows, None, None

    def execute(self, sql, params):
        self.sql, self.params = sql, params

    def fetchall(self):
        return self.rows


def row(outcome="Documents missing", todos="Bring the utility bill on Friday.\nFinish the address change.",
        model="gemma4:31b", kind=None, url=None):
    return (START, END, 3, "Mia Chen", outcome, todos, model, *([80.0] * 4), *([70.0] * 4), kind, url)


@pytest.mark.parametrize("value, key", [
    ("Resolved", "resolved"), ("Documents missing", "documentsMissing"), ("Handoff needed", "handoffNeeded"),
    ("Unresolved", "unresolved"), ("Unknown", "unknown"), (" Resolved ", "resolved"),
    pytest.param(b"Unresolved", "unresolved", id="bytes"),
    (None, ""), ("", ""), ("resolved", ""), ("Done", ""), (3, ""),
])
def test_outcome_keys_only_know_the_five_values(value, key):
    assert media.outcome_key(value) == key


def test_todo_items_are_one_per_line():
    assert media.todo_items("  Bring the bill  \n\n Finish the change\r\n") == ["Bring the bill", "Finish the change"]
    assert media.todo_items(b"Call back\n") == ["Call back"]
    assert media.todo_items(None) == media.todo_items("") == media.todo_items("\n \n") == []
    assert len(media.todo_items("\n".join(f"Step {n}" for n in range(30)))) == media.MAX_TODOS


def test_the_detail_query_reads_the_outcome_with_the_header():
    cursor = Rows([row(kind="cam0", url=f"s3://adam-test/video/{SID}_cam0.avi"), row(kind="audio", url=None)])
    summary, files = media.load_detail(cursor, SID)
    assert cursor.sql == media.DETAIL_SQL
    assert cursor.params == (SID, "%cam0%", SID, "%cam1%", SID, SID, SID)  # 管理員端：和以前一樣
    assert "s.service_outcome, s.service_todos, s.service_outcome_model" in media.DETAIL_SQL
    assert (summary.outcome, summary.outcome_model) == ("Documents missing", "gemma4:31b")
    assert summary.todos == "Bring the utility bill on Friday.\nFinish the address change."
    assert [file.kind for file in files] == ["cam0"]


def test_the_staff_query_only_finds_the_staff_members_own_service():
    cursor = Rows([])
    assert media.load_detail(cursor, SID, owner=STAFF) == (None, [])  # 別人的服務：和沒有這筆一樣
    assert cursor.sql == media.OWN_DETAIL_SQL
    assert cursor.params == (SID, "%cam0%", SID, "%cam1%", SID, SID, SID, STAFF)
    where = media.OWN_DETAIL_SQL.split("WHERE s.service_id")[1]
    assert where.split("ORDER BY")[0].strip() == "= %s AND s.account_id = %s"
    assert media.DETAIL_SQL.replace("WHERE s.service_id = %s", "WHERE s.service_id = %s AND s.account_id = %s") \
        == media.OWN_DETAIL_SQL


def test_an_old_cursor_without_the_new_columns_still_loads():
    """欄位名稱裡沒有服務結果的三欄（例如舊的測試替身）：當成還沒填。"""

    class OldRows(Rows):
        column_names = tuple(name for name in Rows.column_names if "outcome" not in name and name != "service_todos")

    summary, _ = media.load_detail(OldRows([(START, END, 3, "Mia Chen", *([80.0] * 8), None, None)]), SID)
    assert (summary.outcome, summary.todos, summary.outcome_model) == (None, None, None)


def summary(outcome, todos=None, model=None):
    return media.ServiceSummary(START, END, 3, "Mia Chen", {"customer": {}, "staff": {}}, outcome, todos, model)


def model_with(value):
    model = media.MediaModel()
    model._summary = value  # 查詢的結果（_listed 收到的），只測屬性
    return model


def test_a_filled_outcome_has_its_todos_and_model():
    model = model_with(summary("Handoff needed", "Send the form to the loans team\n", " gemma4:31b "))
    assert (model.outcome, model.outcomeFilled, model.todos, model.outcomeModel) == (
        "handoffNeeded", True, ["Send the form to the loans team"], "gemma4:31b")


def test_a_filled_outcome_without_todos_is_an_empty_list():
    model = model_with(summary("Resolved", "", "gemma4:31b"))
    assert (model.outcome, model.outcomeFilled, model.todos) == ("resolved", True, [])


@pytest.mark.parametrize("outcome", [None, "Done"])
def test_not_filled_is_unknown_without_todos_or_model(outcome):
    """PLAN1：尚未寫入時，結果顯示 Unknown，待辦不顯示項目（就算欄位裡有字）。"""
    model = model_with(summary(outcome, "Left over from somewhere", "gemma4:31b"))
    assert (model.outcome, model.outcomeFilled, model.todos, model.outcomeModel) == ("unknown", False, [], "")


def test_before_the_query_and_for_a_missing_service_there_is_no_outcome():
    model = media.MediaModel()
    assert (model.outcome, model.outcomeFilled, model.todos, model.outcomeModel) == ("", False, [], "")


def test_the_owner_is_kept_for_every_open():
    assert media.MediaModel(owner=STAFF)._owner == STAFF
    assert media.MediaModel()._owner is None
