"""員工輔導（PLAN1 W2，T45；畫面改版 T47）：myapp/coaching.py 不連網路的部分。

- 文字（期限、標籤、對話、步驟、服務和時間註記、明細、面板）、指標（有後續證據的完成比例、回顧時間）、表單的檢查、錯誤的原因。
- 資料庫的函式用假的 cursor（照 SQL 回傳準備好的結果集），CoachingModel 用假的 TaskRunner（finish() 才執行工作）：
  清單、分頁、能選的後續服務（每次都重查）、明細、面板（說明欄、送出、對準一項）、寫入的成功與失敗（Retry、Reload）、
  過期的結果不算數。
真的 SQL（13 個程序）、兩端的畫面在整合測試 test_coaching_flow.py。
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import mysql.connector
import pytest

from myapp import coaching
from myapp.coaching import Action, CoachingModel, Detail, Event, Marker, ServiceAction, Snapshot, Views
from myapp.notifications import Notifier
from tests.integration.helpers import toasts

ADMIN = "00000000-0000-0000-0000-000000000001"
STAFF = "00000000-0000-0000-0000-000000000002"
TODAY = date(2026, 11, 1)
A1 = "a0000000-0000-4000-8000-000000000001"
A2 = "a0000000-0000-4000-8000-000000000002"


def action(action_id=A1, status="open", due=TODAY + timedelta(days=5), **fields):
    values = dict(source_service_id="S20261020093000", staff_id=STAFF, goal="Explain the next step.", due_date=due,
                  status=status, source_start=datetime(2026, 10, 20, 9, 30), staff_name="Mia Chen",
                  staff_username="mia", created_by=ADMIN, created_by_name="Alex Wang",
                  created_at=datetime(2026, 10, 21, 9, 0), version=1, manager_comment="Close with the next step.")
    values.update(fields)
    return Action(action_id, **values)


def signal_error(code):
    return mysql.connector.errors.DatabaseError(msg=f"coaching:{code}", errno=coaching.SIGNAL_ERRNO, sqlstate="45000")


def offline():
    return mysql.connector.errors.InterfaceError(msg="Can't connect to MySQL server", errno=2003)


# ── 文字 ─────────────────────────────────────────────────────────────────────

def test_clock_days_and_names():
    assert [coaching.clock(value) for value in (0, 75, 3599, 3725, -3)] == ["00:00", "01:15", "59:59", "1:02:05",
                                                                            "00:00"]
    assert coaching.short_day(date(2026, 11, 15), TODAY) == "Nov 15"
    assert coaching.short_day(datetime(2027, 1, 2, 9), TODAY) == "Jan 2, 2027"
    assert coaching.short_day(None, TODAY) == "" and coaching.moment_text(None, TODAY) == ""
    assert coaching.moment_text(datetime(2026, 10, 20, 9, 30), TODAY) == "Oct 20, 09:30"
    assert [coaching.days_text(value) for value in (None, 0.4, 0.99, 1.2, 1.6, 9.0)] == [
        "", "Under 1 day", "Under 1 day", "1 day", "2 days", "9 days"]
    assert [coaching.first_name(name) for name in ("Mia Chen", "Leo", "", None)] == ["Mia", "Leo", "", ""]
    assert [coaching.initials(name) for name in ("Mia Chen", "leo park wu", "Ava", "服務人員（佔位）", "")] == [
        "MC", "LP", "A", "服", ""]


def test_due_text_and_the_chips_say_the_next_step():
    assert coaching.due_text(action(due=TODAY + timedelta(days=14)), TODAY) == "Due Nov 15"
    assert coaching.due_text(action(due=TODAY), TODAY) == "Due today"
    assert coaching.due_text(action(due=None), TODAY) == ""
    done = action(status="completed", concluded_at=datetime(2026, 11, 10, 8), due=TODAY - timedelta(days=9))
    assert coaching.due_text(done, TODAY) == "Done Nov 10"
    cancelled = action(status="cancelled", concluded_at=datetime(2026, 11, 3, 8))
    assert coaching.due_text(cancelled, TODAY) == "Cancelled Nov 3"
    overdue = action(due=TODAY - timedelta(days=2), follow_up_service_id="S4")
    assert coaching.is_overdue(overdue, TODAY) and not coaching.is_overdue(done, TODAY)
    # 管理員：過期優先，再來是有後續服務（等你看）、有能選的、還沒有、還不知道
    assert [coaching.admin_chip(item, TODAY, eligible) for item, eligible in (
        (overdue, 3), (action(follow_up_service_id="S4"), 0), (action(), 2), (action(), 0), (action(), None),
        (done, 1), (cancelled, None))] == ["overdue", "review", "pick", "waiting", "open", "completed", "cancelled"]
    # 服務人員：還沒按 Got It 是 new
    assert [coaching.staff_chip(item) for item in (action(), action(acknowledged_at=datetime(2026, 11, 1)), done,
                                                   cancelled)] == ["new", "progress", "completed", "cancelled"]
    assert coaching.unread(action()) and not coaching.unread(cancelled)


def test_review_time_and_metrics_leave_out_cancelled_goals():
    done = action(status="completed", created_at=datetime(2026, 10, 1, 12), concluded_at=datetime(2026, 10, 10, 0))
    assert coaching.review_days(done) == 8.5
    assert coaching.review_days(action()) is None
    assert coaching.review_days(action(status="completed", created_at=None)) is None

    def completed(days):
        return action(status="completed", created_at=datetime(2026, 10, 1),
                      concluded_at=datetime(2026, 10, 1) + timedelta(days=days))

    assert coaching.metrics([completed(3), completed(9), completed(12), action(), action(status="cancelled")]) == (
        3, 4, 9)
    assert coaching.metrics([]) == (0, 0, None) and coaching.metrics([action()]) == (0, 1, None)
    assert [coaching.share_percent(*pair) for pair in ((3, 4), (1, 1), (0, 0), (1, 3), (2, 3))] == [
        "75%", "100%", "—", "33%", "67%"]


def test_rows_name_the_staff_member_and_carry_the_chip():
    shared = coaching.shared_names([action(), action(A2, staff_id="other", staff_username="mia.c")])
    assert shared == {"Mia Chen"}
    assert coaching.staff_text(action(), shared) == "Mia Chen (mia)"
    assert coaching.staff_text(action(staff_name=""), frozenset()) == "mia"
    row = coaching.action_row(action(due=TODAY - timedelta(days=1)), True, TODAY, eligible=2)
    assert (row["staffName"], row["initials"], row["dueText"], row["dueAlert"], row["chipState"]) == (
        "Mia Chen", "MC", "Due Oct 31", True, "overdue")
    staff_row = coaching.action_row(action(), False, TODAY)
    assert (staff_row["dueText"], staff_row["dueAlert"], staff_row["chipState"]) == ("Due Nov 6", False, "new")
    assert set(row) == set(coaching.ACTION_ROLES)
    assert coaching.role_text("admin") == "Admin" and coaching.role_text("staff") == "Staff"
    assert [coaching.relation_text(name) for name in ("source", "follow_up", "reviewed", "x")] == [
        "Example", "Follow-up", "Reviewed", ""]


def event(kind, role="admin", **fields):
    values = dict(author_id=ADMIN if role == "admin" else STAFF, author_name="Alex Wang" if role == "admin" else "Mia Chen",
                  author_role=role, created_at=datetime(2026, 11, 1, 9, 40))
    values.update(fields)
    return Event(fields.pop("event_id", 1), kind, **values)


def test_the_conversation_has_bubbles_for_messages_and_lines_for_everything_else():
    events = [
        event("created", body="Why", goal_after="Goal A", due_after=date(2026, 11, 15), service_id="S1"),
        event("response", "staff", body="Rushed"),
        event("response", body="Thanks"),
        event("acknowledged", "staff"),
        event("follow_up_linked", service_id="S2", service_start=datetime(2026, 10, 26, 10, 15)),
        event("follow_up_linked", service_id="S3", service_start=datetime(2026, 10, 29, 14, 10)),
        event("marker_added", service_id="S3", marker_seconds=48, body="Here"),
        event("marker_removed", service_id="S3", marker_seconds=48),
        event("adjusted", body="Needs more", goal_before="Goal A", goal_after="Goal B", due_before=date(2026, 11, 15),
              due_after=date(2026, 11, 30), service_id="S3", service_start=datetime(2026, 10, 29, 14, 10)),
        event("adjusted", body="Same goal, more time", goal_before="Goal B", goal_after="Goal B",
              due_before=date(2026, 11, 30), due_after=date(2026, 11, 30)),
        event("follow_up_linked", service_id="S4"),
        event("completed", body="Did it", service_id="S4"),
        event("cancelled", body="Wrong person", author_id="someone-else", author_name="Jordan Lee"),
        event("response", author_id="gone", author_name="", body="?"),
        event("mystery", author_id="gone", author_name=""),
    ]
    admin = coaching.conversation_items(events, ADMIN, TODAY)
    assert [(item["kind"], item["text"]) for item in admin] == [
        ("event", "You set the goal"), ("message", "Rushed"), ("message", "Thanks"), ("event", "Mia read the goal"),
        ("event", "You picked the Oct 26 service as the follow-up"),
        ("event", "You changed the follow-up to the Oct 29 service"),       # 時間註記不放進對話
        ("event", "You asked for more practice after the Oct 29 service"), ("event", "You edited the goal"),
        ("event", "You picked the S4 service as the follow-up"),            # 調整之後是重新選，不是「換掉」
        ("event", "You marked the goal complete"), ("event", "Jordan cancelled the goal"), ("message", "?")]
    # 不認得的事（以後新加的種類）不放進對話
    assert [(item["mine"], item["author"]) for item in admin if item["kind"] == "message"] == [
        (False, "Mia Chen"), (True, "You"), (False, "Admin")]
    assert admin[6]["lines"] == ["Needs more", "New goal: Goal B", "Follow up by Nov 30"]
    assert admin[7]["lines"] == ["Same goal, more time"] and admin[9]["lines"] == ["Did it"]
    assert admin[0]["whenText"] == "Nov 1, 09:40" and admin[0]["lines"] == []
    staff = coaching.conversation_items(events[:4], STAFF, TODAY)
    assert [item["text"] for item in staff] == ["Alex set the goal", "Rushed", "Thanks", "You read the goal"]
    assert [(item["mine"], item["author"]) for item in staff[1:3]] == [(True, "You"), (False, "Alex Wang")]


def test_progress_restarts_after_an_adjustment_and_only_open_goals_have_a_current_step():
    def states(detail, admin=True):
        return [step["state"] for step in coaching.progress_steps(detail, admin)]

    fresh = Detail(action(), (), (event("created"),))
    assert [step["label"] for step in coaching.progress_steps(fresh, True)] == [
        "Goal set", "Mia replied", "Follow-up", "Your review"]
    assert [step["label"] for step in coaching.progress_steps(fresh, False)] == [
        "Goal set", "You replied", "Later service", "Review"]
    assert states(fresh) == ["done", "current", "todo", "todo"]
    replied = Detail(action(follow_up_service_id="S4"), (), (event("created"), event("response", "staff")))
    assert states(replied) == ["done", "done", "done", "current"]
    adjusted = Detail(action(), (), (event("created"), event("response", "staff"), event("adjusted")))
    assert states(adjusted) == ["done", "current", "todo", "todo"]  # 新的目標：要再回應
    assert states(Detail(action(acknowledged_at=datetime(2026, 11, 1)), (), ())) == ["done", "done", "current", "todo"]
    closed = Detail(action(status="completed", follow_up_service_id="S4"), (), ())
    assert states(closed) == ["done", "todo", "done", "done"]  # 結案的沒有「進行中」的那一步


def test_detail_map_groups_moments_by_service_and_says_what_each_side_can_do():
    detail = Detail(
        action(follow_up_service_id="S4", follow_up_start=datetime(2026, 10, 30, 11)),
        (Marker(1, "S20261020093000", 135, "Fee"), Marker(2, "S3", 40), Marker(3, "S4", 12, "Good")),
        (event("adjusted", body="Not yet", service_id="S3", service_start=datetime(2026, 10, 29, 14, 10),
               created_at=datetime(2026, 10, 30, 8)),
         event("adjusted", body="Again", service_id="S3", service_start=datetime(2026, 10, 29, 14, 10),
               created_at=datetime(2026, 10, 31, 8))))
    admin = coaching.detail_map(detail, True, ADMIN, TODAY)
    assert [(block["relation"], block["label"]) for block in admin["services"]] == [
        ("source", "Example · Oct 20"), ("follow_up", "Follow-up · Oct 30"), ("reviewed", "Reviewed · Oct 29")]
    assert [marker["clock"] for marker in admin["services"][0]["markers"]] == ["02:15"]
    assert admin["services"][2]["reason"] == "Again"  # 同一筆只列一次，原因是最新的
    assert admin["followUp"] == {"serviceId": "S4", "startTime": "2026-10-30 11:00:00", "whenText": "Oct 30, 11:00"}
    assert (admin["setText"], admin["staffFirst"], admin["initials"], admin["adminFirst"]) == (
        "Set Oct 21 · Due Nov 6", "Mia", "MC", "Alex")
    assert (admin["canAcknowledge"], admin["canRespond"], admin["unread"]) == (False, True, True)
    staff = coaching.detail_map(detail, False, STAFF, TODAY)
    assert staff["canAcknowledge"] and staff["fromText"] == "From Alex Wang · Due Nov 6"
    read = coaching.detail_map(Detail(action(acknowledged_at=datetime(2026, 11, 1, 10, 5))), False, STAFF, TODAY)
    assert read["followUp"] == {} and not read["canAcknowledge"]
    done = coaching.detail_map(Detail(action(
        status="completed", conclusion="Did it", concluded_by_name="Alex Wang", concluded_at=datetime(2026, 10, 30, 9),
        created_at=datetime(2026, 10, 21, 9), follow_up_service_id="S4")), True, ADMIN, TODAY)
    assert done["concludedText"] == "Completed by Alex Wang · Oct 30 · 9 days after the goal was set"
    assert (done["closedDay"], done["setText"], done["canRespond"]) == ("Oct 30", "Set Oct 21 · Done Oct 30", False)
    cancelled = coaching.detail_map(Detail(action(status="cancelled", concluded_by_name="Alex Wang",
                                                  concluded_at=datetime(2026, 10, 22, 9))), True, ADMIN, TODAY)
    assert cancelled["concludedText"] == "Cancelled by Alex Wang · Oct 22"
    nameless = coaching.detail_map(Detail(action(created_by_name="", source_start=None)), False, STAFF, TODAY)
    assert (nameless["adminName"], nameless["adminFirst"]) == ("your admin", "your admin")
    assert nameless["fromText"].startswith("From your admin · ")
    assert nameless["services"][0]["label"] == "Example · S20261020093000"  # 沒有開始時間：寫服務 ID


def test_card_items_keep_the_moments_of_this_video_per_goal():
    actions = (ServiceAction(A1, "Goal 1", "Mia Chen", TODAY + timedelta(days=3), "open", "source"),
               ServiceAction(A2, "Goal 2", "Mia Chen", TODAY - timedelta(days=1), "open", "reviewed"),
               ServiceAction("a3", "Goal 3", "Mia Chen", TODAY, "completed", "follow_up"),
               ServiceAction("a4", "Goal 4", "Mia Chen", TODAY, "open", "follow_up", follow_up_service_id="S9"))
    markers = (Marker(1, "S1", 5, action_id=A1), Marker(2, "S1", 9, "x", action_id=A2))
    items = coaching.card_items(actions, markers, TODAY)
    assert [(item["canMark"], item["chipState"], item["dueText"], item["relationText"]) for item in items] == [
        (True, "open", "Due Nov 4", "Example"), (False, "overdue", "Due Oct 31", "Reviewed"),
        (False, "completed", "", "Follow-up"), (True, "review", "Due today", "Follow-up")]
    assert [marker["editable"] for item in items for marker in item["markers"]] == [True, False]


# ── 表單的檢查、錯誤 ────────────────────────────────────────────────────────────

def test_goal_form_checks_every_field():
    values, errors = coaching.validate_goal("  Goal  ", " Why ", "2026-11-15", TODAY)
    assert values == {"goal": "Goal", "comment": "Why", "due": date(2026, 11, 15)} and not any(errors.values())
    values, errors = coaching.validate_goal("", "", "", TODAY)
    assert values is None and errors == {"goal": "Enter the goal the staff member should practise.",
                                         "comment": "Explain why you set this goal.",
                                         "due": "Choose a follow-up date."}
    assert coaching.parse_due("2026/11/15", TODAY)[1] == "Enter the date as YYYY-MM-DD, for example 2026-11-15."
    assert coaching.parse_due("2026-02-30", TODAY)[1].startswith("Enter the date")
    assert coaching.parse_due("2026-10-31", TODAY) == (None, "Choose today or a later date.")
    assert coaching.parse_due("2026-11-01", TODAY) == (TODAY, "")
    assert coaching.required("x" * 301, 300, "Empty")[1] == "Keep this under 300 characters."
    assert coaching.default_due(TODAY) == "2026-11-15"


def test_problems_from_the_procedures_become_sentences():
    assert coaching.problem_code(signal_error("changed")) == "changed"
    assert coaching.problem_code(offline()) == ""
    assert coaching.problem_code(mysql.connector.errors.DatabaseError(msg="other", errno=1644)) == ""
    for code in ("forbidden", "not_found", "not_open", "changed", "invalid", "not_staff", "follow_up",
                 "no_follow_up", "marker_service"):
        assert coaching.problem_text(code).endswith(".") and "coaching" not in coaching.problem_text(code)
    assert coaching.problem_text("???") == "This change couldn't be saved."
    assert coaching.is_retryable(offline()) and not coaching.is_retryable(signal_error("changed"))
    assert coaching.reason_text(offline()) == "Can't reach the database. Check your network and try again."
    assert coaching.technical_text(ValueError()) == "ValueError"
    assert coaching.technical_text(ValueError("boom")) == "ValueError: boom"


def test_rows_from_the_database_tolerate_bytes_and_missing_columns():
    row = {"action_id": b"a1", "source_service_id": "S1", "staff_account_id": STAFF, "goal": "G",
           "due_date": datetime(2026, 11, 15, 0, 0), "action_status": "open", "version": None}
    parsed = Action.from_row(row)
    assert (parsed.action_id, parsed.due_date, parsed.version, parsed.staff_name) == ("a1", date(2026, 11, 15), 1, "")
    event_row = {"event_id": 3, "event_type": "marker_added", "author_id": ADMIN, "marker_seconds": 48}
    assert Event.from_row(event_row).marker_seconds == 48
    assert Event.from_row({**event_row, "marker_seconds": None}).marker_seconds is None
    assert [coaching._score(value) for value in (None, -1, -1.0, 0, 84.25)] == [None, None, None, 0.0, 84.25]


# ── 資料庫的函式（假的 cursor）─────────────────────────────────────────────────

def call_of(sql):
    """「CALL sp_coaching_list」：假的資料庫用 SQL 的開頭找要回傳的結果集。"""
    return sql.split("(")[0]


class FakeDb:
    """DbLane 的替身：照 SQL 的開頭回傳準備好的結果集（list of lists）；errors 讓那個 SQL 丟例外。"""

    def __init__(self):
        self.results = {}
        self.errors = {}
        self.executed = []

    @contextmanager
    def cursor(self, dictionary=False):
        yield FakeCursor(self)

    def answer(self, sql, params):
        for prefix, sets in self.results.items():
            if sql.startswith(prefix):
                return sets(params) if callable(sets) else sets
        return []


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.sets = []
        self.index = 0

    @property
    def with_rows(self):
        return self.index < len(self.sets)

    def execute(self, sql, params=()):
        self.db.executed.append((sql.split("(")[0], params))
        for prefix, error in list(self.db.errors.items()):
            if sql.startswith(prefix):
                errors = error if isinstance(error, list) else [error]
                raised = errors.pop(0)
                if not errors:
                    del self.db.errors[prefix]
                raise raised
        self.sets = [list(rows) for rows in self.db.answer(sql, params)]
        self.index = 0

    def fetchall(self):
        return self.sets[self.index] if self.index < len(self.sets) else []

    def nextset(self):
        self.index += 1
        return self.index < len(self.sets)


def list_row(item):
    return {"action_id": item.action_id, "source_service_id": item.source_service_id, "staff_account_id": item.staff_id,
            "goal": item.goal, "due_date": item.due_date, "action_status": item.status,
            "source_start": item.source_start, "staff_name": item.staff_name, "staff_username": item.staff_username,
            "created_by_name": item.created_by_name, "follow_up_service_id": item.follow_up_service_id or None,
            "follow_up_start": item.follow_up_start, "acknowledged_at": item.acknowledged_at,
            "concluded_at": item.concluded_at, "concluded_by_name": item.concluded_by_name or None,
            "created_at": item.created_at, "version": item.version, "last_staff_at": item.last_staff_at,
            "last_admin_at": item.last_admin_at, "manager_comment": item.manager_comment,
            "conclusion": item.conclusion or None}


SCORES = "SELECT service_id, server_total"


def test_reading_functions_use_one_call_each():
    db = FakeDb()
    db.results = {call_of(coaching.LIST_SQL): [[list_row(action())]],
                  call_of(coaching.DETAIL_SQL): [[list_row(action())],
                                             [{"marker_id": 1, "service_id": "S1", "offset_seconds": 5}],
                                             [{"event_id": 1, "event_type": "created", "author_id": ADMIN}], []],
                  call_of(coaching.FOR_SERVICE_SQL): [[{"action_id": A1, "goal": "G", "due_date": TODAY,
                                                    "action_status": "open", "relation": "source",
                                                    "follow_up_service_id": "S2"}],
                                                  [{"marker_id": 1, "action_id": A1, "offset_seconds": 9}], []],
                  call_of(coaching.OPTIONS_SQL): [[{"service_id": "S2", "service_start": datetime(2026, 10, 26)},
                                                   {"service_id": "S3", "service_start": datetime(2026, 10, 25)}], []],
                  SCORES: [[{"service_id": "S2", "server_total": 86.5}, {"service_id": "S3", "server_total": -1}]]}
    assert [item.action_id for item in coaching.list_actions(db, ADMIN)] == [A1]
    detail = coaching.load_detail(db, ADMIN, A1)
    assert (detail.action.action_id, detail.markers[0].seconds, detail.events[0].kind) == (A1, 5, "created")
    actions, markers = coaching.load_card(db, ADMIN, "S1")
    assert actions[0].follow_up_service_id == "S2" and markers[0].service_id == "S1" and markers[0].action_id == A1
    assert coaching.load_follow_up_options(db, ADMIN, A1) == [("S2", datetime(2026, 10, 26), 86.5),
                                                              ("S3", datetime(2026, 10, 25), None)]
    db.results[call_of(coaching.DETAIL_SQL)] = [[], [], [], []]
    assert coaching.load_detail(db, STAFF, A1) is None
    db.results[call_of(coaching.OPTIONS_SQL)] = [[], []]
    assert coaching.load_follow_up_options(db, ADMIN, A1) == []  # 沒有能選的：不讀分數
    assert [sql for sql, _ in db.executed] == ["CALL sp_coaching_list", "CALL sp_coaching_detail",
                                               "CALL sp_coaching_for_service", "CALL sp_coaching_follow_up_options",
                                               "SELECT service_id, server_total FROM tbl_service_score WHERE service_id IN ",
                                               "CALL sp_coaching_detail", "CALL sp_coaching_follow_up_options"]
    assert db.executed[4][1] == ["S2", "S3"]


def test_the_admin_list_asks_each_open_goal_without_a_follow_up_whether_a_later_service_exists(monkeypatch):
    db = FakeDb()
    actions = [action("o1"), action("o2", follow_up_service_id="S4"), action("o3"), action("c1", status="completed")]
    db.results = {call_of(coaching.LIST_SQL): [[list_row(item) for item in actions]],
                  call_of(coaching.OPTIONS_SQL): lambda params: [[{"service_id": "S5"}] if params[1] == "o1" else []]}
    listed, eligible = coaching.list_with_eligibility(db, ADMIN)
    assert [item.action_id for item in listed] == ["o1", "o2", "o3", "c1"]
    assert eligible == {"o1": 1, "o3": 0}  # 有後續服務的、結案的不問
    assert [params[1] for sql, params in db.executed if sql == "CALL sp_coaching_follow_up_options"] == ["o1", "o3"]
    monkeypatch.setattr(coaching, "ELIGIBLE_MAX", 1)  # 太多項：只問前面幾項，其他的不知道
    assert coaching.list_with_eligibility(db, ADMIN)[1] == {"o1": 1}


def test_write_is_one_transaction_then_reads_the_open_views():
    db = FakeDb()
    db.results = {call_of(coaching.ADD_MARKER_SQL): [[{"marker_id": 7}], []], call_of(coaching.LIST_SQL): [[], []],
                  call_of(coaching.DETAIL_SQL): [[], [], [], []], call_of(coaching.FOR_SERVICE_SQL): [[], [], []]}
    snapshot = coaching.write(db, ADMIN, [(coaching.ADD_MARKER_SQL, (ADMIN, A1, "S1", 5, ""))],
                              Views(list=True, detail=A1, card="S1"))
    assert [sql for sql, _ in db.executed] == ["START TRANSACTION", "CALL sp_coaching_add_marker", "COMMIT",
                                               "CALL sp_coaching_list", "CALL sp_coaching_detail",
                                               "CALL sp_coaching_for_service"]
    assert snapshot == Snapshot((), False, ((), ()), ({"marker_id": 7},))  # 看不到了：detail 是 False
    db.executed.clear()
    assert coaching.write(db, ADMIN, [(coaching.RESPOND_SQL, (ADMIN, A1, "x"))], Views()) == Snapshot()
    assert [sql for sql, _ in db.executed] == ["START TRANSACTION", "CALL sp_coaching_respond", "COMMIT"]


def test_create_treats_a_duplicate_key_as_already_saved_only_when_the_action_is_there():
    db = FakeDb()
    duplicate = mysql.connector.errors.IntegrityError(msg="Duplicate entry", errno=coaching.DUPLICATE_KEY)
    db.errors = {call_of(coaching.CREATE_SQL): duplicate}
    db.results = {call_of(coaching.DETAIL_SQL): [[list_row(action())], [], [], []]}
    snapshot = coaching.create(db, ADMIN, A1, "S1", "G", "C", TODAY, [(5, "")], Views(detail=A1))
    assert snapshot.detail.action.action_id == A1
    db.errors = {call_of(coaching.CREATE_SQL): duplicate}
    db.results = {call_of(coaching.DETAIL_SQL): [[], [], [], []]}
    with pytest.raises(mysql.connector.errors.IntegrityError):
        coaching.create(db, ADMIN, A1, "S1", "G", "C", TODAY, [], Views())
    db.errors = {call_of(coaching.CREATE_SQL): signal_error("not_staff")}
    with pytest.raises(mysql.connector.errors.DatabaseError):
        coaching.create(db, ADMIN, A1, "S1", "G", "C", TODAY, [], Views())


# ── CoachingModel（假的 TaskRunner）──────────────────────────────────────────────

class FakeRunner:
    """TaskRunner 的替身：工作先不執行，finish() 才在這裡執行並交回結果。"""

    def __init__(self):
        self.running = {}

    def submit(self, lane, key, fn, on_ok=None, on_err=None, replace=False):
        if key in self.running and not replace:
            return False
        self.running[key] = (fn, on_ok, on_err)
        return True

    def isBusy(self, key):
        return key in self.running

    def finish(self, key, result=None, error=None):
        """執行工作（或直接用給的 result、error）並交回。"""
        fn, on_ok, on_err = self.running.pop(key)
        if error is None and result is None:
            try:
                result = fn(None, None)
            except Exception as raised:  # noqa: BLE001  和 TaskRunner 一樣交給 on_err
                error = raised
        if error is not None:
            if on_err is not None:
                on_err(error)
        elif on_ok is not None:
            on_ok(result)


class Services:
    def __init__(self):
        self.tasks = FakeRunner()
        self.notifier = Notifier()
        self.db = FakeDb()


@pytest.fixture
def services():
    return Services()


def model_for(services, admin=True):
    return CoachingModel(services, ADMIN if admin else STAFF, admin=admin, today=TODAY)


def titles(services):
    return [(toast["severity"], toast["title"]) for toast in toasts(services.notifier)]


def listed(services, actions, eligible=None):
    """清單的工作回來了：管理員的是 (行動, {action_id: 能選的後續服務幾筆})，服務人員的第二個是 None。"""
    services.tasks.finish(coaching.LIST_KEY, result=(tuple(actions), eligible))


def test_without_services_nothing_is_sent():
    model = CoachingModel(None, ADMIN, admin=True, today=TODAY)
    model.pageOpened("coaching")
    model.pageOpened("card")
    model.select(A1)
    model.watchService("S1")
    model.loadFollowUps(A1)
    assert model.listState == "idle" and not model.createGoal() and not model.respond(A1, "Hi")
    assert model._refresh_list() is False and model._load_detail(A1) is False and model._load_card() is False
    assert model.defaultDue() == "2026-11-15" and model.followUpState == "idle"
    assert CoachingModel(None, ADMIN).today() == date.today()


def test_list_loads_on_open_and_keeps_the_old_list_when_a_reload_fails(services):
    model = model_for(services)
    model.pageOpened("nowhere")  # 不認得的頁面：不讀
    assert not services.tasks.running
    model.pageOpened("coaching")
    assert model.listState == "loading"
    services.tasks.finish(coaching.LIST_KEY, error=offline())
    assert (model.listState, model.failureCount) == ("error", 1)
    assert model.errorDetail == "Can't reach the database. Check your network and try again."
    assert model.errorTechnical.startswith("InterfaceError")
    model.refresh()
    listed(services, (action(), action(A2, staff_id="o", staff_name="Leo Park")), {A1: 0, A2: 2})
    assert (model.listState, model.totalCount, model.shownCount) == ("ready", 2, 2)
    assert [row["chipState"] for row in model.listModel.rows()] == ["waiting", "pick"]
    model.refresh()
    assert model.refreshing
    services.tasks.finish(coaching.LIST_KEY, error=offline())
    assert model.listState == "ready" and model.totalCount == 2 and not model.refreshing
    model.refresh()
    listed(services, ())
    assert model.listState == "empty" and model.countText == "0 goals"
    model.pageClosed("coaching")
    model.pageClosed("coaching")  # 多關一次沒關係
    assert not model._list_open()


def test_the_admin_list_job_asks_for_follow_ups_and_staff_only_list(services):
    admin = model_for(services)
    admin.pageOpened("coaching")
    services.db.results = {call_of(coaching.LIST_SQL): [[list_row(action())]],
                           call_of(coaching.OPTIONS_SQL): [[{"service_id": "S5"}], []]}
    services.tasks.finish(coaching.LIST_KEY)
    assert [row["chipState"] for row in admin.listModel.rows()] == ["pick"]
    staff = model_for(Services(), admin=False)
    staff.services.db.results = {call_of(coaching.LIST_SQL): [[list_row(action())]]}
    staff.pageOpened("actions")
    staff.services.tasks.finish(coaching.LIST_KEY)
    assert [sql for sql, _ in staff.services.db.executed] == ["CALL sp_coaching_list"]
    assert [row["chipState"] for row in staff.listModel.rows()] == ["new"]
    assert staff.countText == "1 goal" and staff.tab == "open"
    staff.setTab("completed")  # 服務人員沒有分頁
    assert staff.tab == "open"


def test_tabs_split_the_admin_list_and_metrics_count_every_goal(services):
    model = model_for(services)
    model.pageOpened("coaching")
    leo = dict(staff_id="leo", staff_name="Leo Park", staff_username="leo")
    listed(services, (action(A1, due=TODAY - timedelta(days=1)), action(A2, due=TODAY + timedelta(days=3), **leo),
                      action("a3", status="completed", created_at=datetime(2026, 10, 1),
                             concluded_at=datetime(2026, 10, 5), **leo),
                      action("a4", status="cancelled", concluded_at=datetime(2026, 10, 6))), {A2: 0})
    ids = lambda: [row["actionId"] for row in model.listModel.rows()]  # noqa: E731
    assert ids() == [A1, A2] and (model.openCount, model.completedCount, model.cancelledCount) == (2, 1, 1)
    assert (model.completedShare, model.completedOf, model.reviewTimeText, model.openCountText,
            model.overdueCount) == ("33%", "1 of 3", "4 days", "2", 1)
    model.select(A2)
    services.tasks.running.clear()
    model.setTab("nowhere")  # 不認得的分頁：不換
    model.setTab("open")     # 一樣的：不換
    assert model.tab == "open" and model.selectedId == A2
    model.setTab("completed")  # 換分頁：選那一頁的第一項
    assert ids() == ["a3"] and model.selectedId == "a3" and model.detailState == "loading"
    model.setTab("cancelled")
    assert ids() == ["a4"] and model.firstActionId() == "a4"
    model.select(A1)  # 選另一頁的一項（例如 Open in Coaching）：跟著換分頁
    assert model.tab == "open" and A1 in ids()
    services.tasks.running.clear()
    model.refresh()
    listed(services, (action(A1, status="completed", concluded_at=datetime(2026, 11, 1)),))  # 剛完成：跟著它換分頁
    assert model.tab == "completed" and ids() == [A1]
    assert model.reviewTimeText == "11 days" and model.completedShare == "100%"
    model.setTab("open")
    assert model.selectedId == "" and model.detailState == "idle" and model.firstActionId() == ""


def test_detail_loads_on_select_and_old_results_do_not_count(services):
    model = model_for(services, admin=False)
    model.select(A1)
    assert model.detailState == "loading" and model.current == {}
    _, stale_ok, stale_err = services.tasks.running[coaching.DETAIL_KEY]
    model.select(A2)  # 換了一項：上一個還沒回來的結果不算
    stale_ok(Detail(action(A1)))
    stale_err(offline())
    assert model.detailState == "loading"
    services.tasks.finish(coaching.DETAIL_KEY, result=Detail(action(A2)))
    assert model.detailState == "ready" and model.current["actionId"] == A2
    assert not services.tasks.isBusy(coaching.OPTIONS_KEY)  # 服務人員不查後續服務
    model.select(A2)  # 同一項再選一次：重讀，舊的留著
    services.tasks.finish(coaching.DETAIL_KEY, error=offline())
    assert model.detailState == "ready" and model.detailError.startswith("Can't reach")
    model.select("a9")
    services.tasks.finish(coaching.DETAIL_KEY, error=offline())
    assert model.detailState == "error" and model.detailTechnical.startswith("InterfaceError")
    model.select("a9")
    services.tasks.finish(coaching.DETAIL_KEY, result=None)  # 看不到（別人的、不存在）
    assert model.detailState == "missing"
    model.select("")
    assert model.detailState == "idle"


def test_follow_up_options_are_checked_again_every_time_the_goal_is_shown(services):
    model = model_for(services)
    model.pageOpened("coaching")
    listed(services, (action(),), {A1: 0})
    model.select(A1)
    services.tasks.finish(coaching.DETAIL_KEY, result=Detail(action()))
    assert services.tasks.isBusy(coaching.OPTIONS_KEY) and model.followUpState == "loading" and model.followUpBusy
    services.tasks.finish(coaching.OPTIONS_KEY, result=[])
    assert (model.followUpState, model.followUpBusy) == ("empty", False)
    # 頁面再打開：清單、明細重讀，後續服務也重查（舊版查一次就不再查，T47 第 2 節）
    model.pageOpened("coaching")
    listed(services, (action(),), {A1: 0})
    services.tasks.finish(coaching.DETAIL_KEY, result=Detail(action()))
    assert services.tasks.isBusy(coaching.OPTIONS_KEY) and model.followUpState == "empty" and model.followUpBusy
    services.tasks.finish(coaching.OPTIONS_KEY, result=[("S5", datetime(2026, 10, 30, 12), 86.5)])
    assert model.followUpState == "ready"
    assert model.followUpOptions == [{"value": "S5", "whenText": "Oct 30, 12:00", "startTime": "2026-10-30 12:00:00",
                                      "score": 86.5, "scoreText": "86.5"}]
    assert [row["chipState"] for row in model.listModel.rows()] == ["pick"]  # 清單的標籤跟著
    # Check Again：重查時已經有的留著；查不到也留著
    model.loadFollowUps(A1)
    assert model.followUpState == "ready" and model.followUpBusy and len(model.followUpOptions) == 1
    services.tasks.finish(coaching.OPTIONS_KEY, error=offline())
    assert model.followUpState == "ready" and not model.followUpBusy
    # 有後續服務的：不自動查；按了 Change（followUpFor 是它）才查
    model.select(A2)
    services.tasks.finish(coaching.DETAIL_KEY, result=Detail(action(A2, follow_up_service_id="S4")))
    assert not services.tasks.isBusy(coaching.OPTIONS_KEY)
    model.loadFollowUps(A2)
    assert model.followUpFor == A2 and model.followUpState == "loading" and model.followUpOptions == []
    services.tasks.finish(coaching.OPTIONS_KEY, result=[("S5", None, None)])
    assert model.followUpOptions[0]["scoreText"] == "" and model.followUpOptions[0]["score"] == -1
    model.select(A2)  # 換好了（或寫入之後重讀）：有後續服務，照樣不查
    services.tasks.finish(coaching.DETAIL_KEY, result=Detail(action(A2, follow_up_service_id="S4")))
    assert not services.tasks.isBusy(coaching.OPTIONS_KEY)
    # 結案的不查
    model.select("done")
    services.tasks.finish(coaching.DETAIL_KEY, result=Detail(action("done", status="completed")))
    assert not services.tasks.isBusy(coaching.OPTIONS_KEY)


def test_follow_up_options_failures_and_stale_results(services):
    model = model_for(services)
    model.loadFollowUps(A1)
    services.tasks.finish(coaching.OPTIONS_KEY, error=offline())
    assert model.followUpState == "error"
    model.loadFollowUps(A1)
    assert model.followUpState == "loading"  # 上一次失敗：重查時是 loading
    _, on_ok, on_err = services.tasks.running.pop(coaching.OPTIONS_KEY)
    model.loadFollowUps(A2)
    on_ok([("S9", None, None)])
    on_err(offline())
    assert model.followUpFor == A2 and model.followUpState == "loading"
    model.loadFollowUps("")  # 沒有：不讀
    model_for(Services(), admin=False).loadFollowUps(A1)


def test_opening_a_service_only_works_for_services_of_that_action(services):
    model = model_for(services, admin=False)
    opened = []
    model.openServiceRequested.connect(lambda *args: opened.append(args))
    model.pageOpened("actions")
    listed(services, (action(follow_up_service_id="S4", follow_up_start=datetime(2026, 10, 30, 11)),))
    assert model.openService(A1, "S4", 12)  # 清單就有：後續服務
    model.select(A1)
    services.tasks.finish(coaching.DETAIL_KEY, result=Detail(action(), (), (
        event("adjusted", service_id="S3", service_start=datetime(2026, 10, 29, 14, 10)),)))
    assert model.openService(A1, "S3", -1)  # 調整時看過的
    assert not model.openService(A1, "S99", 0) and not model.openService("a9", "S20261020093000", 0)
    assert opened == [("S4", "2026-10-30 11:00:00", 12, coaching.STAFF_PAGE),
                      ("S3", "2026-10-29 14:10:00", -1, coaching.STAFF_PAGE)]
    assert model._focus_next is None  # 服務人員沒有面板


def card(*actions, markers=()):
    return (tuple(actions), tuple(markers))


def open_card(services, model, service_id="S1", result=None):
    model.watchService(service_id)
    model.pageOpened("card")
    services.tasks.finish(coaching.CARD_KEY, result=result or card())


def test_the_panel_follows_the_service_and_keeps_a_draft_per_service(services):
    model = model_for(services)
    model.watchService("S1")
    assert not services.tasks.running  # 面板還沒打開：不讀
    model.pageOpened("card")
    assert model.cardState == "loading"
    services.tasks.finish(coaching.CARD_KEY, error=offline())
    assert model.cardState == "error"
    model.reloadCard()
    services.tasks.finish(coaching.CARD_KEY, result=card(ServiceAction(A1, "G", "Mia", TODAY, "open", "source")))
    assert model.cardState == "ready" and model.cardActions[0]["actionId"] == A1
    model.reloadCard()
    services.tasks.finish(coaching.CARD_KEY, error=offline())
    assert model.cardState == "ready"  # 已經有了：留著
    model.watchService("S1")  # 同一筆：不重讀
    assert not services.tasks.running

    model.updateDraft("goal", "Goal")
    model.updateDraft("goal", "Goal")  # 一樣的值：不算
    model.updateDraft("color", "red")  # 不認得的欄位
    assert model.startMoment(30) and not model.startMoment(40)  # 說明欄開著：不再開
    model.updatePending("  Here  ")
    assert model.draft["pending"] == {"open": True, "seconds": 30, "clock": "00:30", "note": "  Here  ", "target": ""}
    assert model.savePending() and not model.savePending()  # 存了就關；沒開著的不存
    assert model.startMoment(5) and model.savePending()      # 說明可以是空的
    assert model.startMoment(30)
    model.updatePending("Same second")
    assert model.savePending()                                # 同一秒也留著，不會丟掉
    assert not model.startMoment(-1)
    assert [(marker["clock"], marker["note"]) for marker in model.draft["markers"]] == [
        ("00:05", ""), ("00:30", "Here"), ("00:30", "Same second")]
    model.removeDraftMarker(9)
    model.removeDraftMarker(2)
    assert model.startMoment(50)
    model.cancelPending()  # Cancel：使用者自己按的，字不留
    model.cancelPending()
    assert model.draft == {"goal": "Goal", "comment": "", "due": "2026-11-15", "sent": {}, "pending": {"open": False},
                           "markers": [{"seconds": 5, "clock": "00:05", "note": ""},
                                       {"seconds": 30, "clock": "00:30", "note": "Here"}]}
    resets = []
    model.draftReset.connect(lambda: resets.append(True))
    model.watchService("S2")  # 另一筆服務：自己的草稿
    services.tasks.finish(coaching.CARD_KEY, result=card())
    assert model.draft["goal"] == "" and model.draft["markers"] == [] and resets
    model.watchService("S1")
    assert model.draft["goal"] == "Goal"
    _, on_ok, on_err = services.tasks.running[coaching.CARD_KEY]
    model.watchService("S2")  # 前一個（S1）的結果回來時已經換了：不算
    on_ok(card(ServiceAction(A1, "G", "Mia", TODAY, "open", "source")))
    on_err(offline())
    assert model.cardActions == []
    model.watchService("")
    model.updateDraft("goal", "Nowhere")  # 沒有服務：沒有草稿
    model.updatePending("x")
    model.cancelPending()
    model.removeDraftMarker(0)
    model.newGoal()
    assert not model.startMoment(1) and not model.savePending() and not model.focusGoal("")
    assert model.draft["goal"] == ""


def test_sending_saves_an_open_note_first_and_then_shows_the_sent_state(services):
    model = model_for(services)
    model.pageOpened("card")
    open_card(services, model)
    assert model.startMoment(12)
    model.updatePending("Customer looks unsure")
    assert not model.createGoal()  # 欄位空的：不送，但開著的說明先存成一個時間註記（不會默默丟掉，T47 第 1 節）
    assert model.createErrors["goal"] and model.createErrors["comment"]
    assert model.draft["pending"] == {"open": False}
    assert [(marker["seconds"], marker["note"]) for marker in model.draft["markers"]] == [(12, "Customer looks unsure")]
    model.updateDraft("goal", "Goal")
    assert model.createErrors["goal"] == ""
    model.updateDraft("comment", "Why")
    assert model.startMoment(20)
    model.updatePending("Leaves")
    assert model.createGoal()
    assert model.creating and not model.createGoal()  # 送出中：不送第二次
    action_id = model._drafts["S1"]["actionId"]
    fn, _, _ = services.tasks.running[coaching.CREATE_KEY]
    services.db.results = {}
    fn(None, None)
    assert [params[3:] for sql, params in services.db.executed if sql == "CALL sp_coaching_add_marker"] == [
        (12, "Customer looks unsure"), (20, "Leaves")]
    services.tasks.finish(coaching.CREATE_KEY, error=offline())
    assert not model.creating
    assert model.createErrors["form"] == "Can't reach the database. Check your network and try again."
    [toast] = toasts(services.notifier)
    assert (toast["title"], toast["actionLabel"]) == ("Goal wasn't sent", "Retry")
    services.notifier.trigger(toast["toastId"])  # Retry：同一個 action_id
    assert model.creating
    services.tasks.finish(coaching.CREATE_KEY, result=Snapshot(card=card(
        ServiceAction(action_id, "Goal", "Mia Chen", TODAY, "open", "source"))))
    assert model.draft["sent"] == {"actionId": action_id, "count": 2} and model.draft["goal"] == ""
    assert model.createErrors["form"] == "" and ("success", "Goal sent") not in titles(services)  # 面板上寫了
    assert not model.startMoment(3)  # 送出之後：先 Set Another Goal
    model.newGoal()
    model.newGoal()
    assert model.draft["sent"] == {} and model.startMoment(3)
    model.cancelPending()

    # 程序拒絕（例如服務是管理員錄的）：寫在表單上，沒有 Retry
    model.updateDraft("goal", "Again")
    model.updateDraft("comment", "Why")
    assert model.createGoal()
    services.tasks.finish(coaching.CREATE_KEY, error=signal_error("not_staff"))
    assert model.createErrors["form"] == coaching.problem_text("not_staff")
    assert [toast["title"] for toast in toasts(services.notifier)].count("Goal wasn't sent") == 0
    staff = model_for(Services(), admin=False)
    staff.watchService("S1")
    assert not staff.createGoal()  # 服務人員不能建立


def test_a_goal_sent_while_the_panel_is_closed_shows_a_toast_with_open(services):
    model = model_for(services)
    open_card(services, model)
    model.updateDraft("goal", "Goal")
    model.updateDraft("comment", "Why")
    assert model.createGoal()
    action_id = model._drafts["S1"]["actionId"]
    model.pageClosed("card")  # 送出後換頁了：看不到面板
    services.tasks.finish(coaching.CREATE_KEY, result=Snapshot(card=card(
        ServiceAction(action_id, "Goal", "Mia Chen", TODAY, "open", "source"))))
    [success] = [toast for toast in toasts(services.notifier) if toast["title"] == "Goal sent"]
    assert success["detail"] == "Mia can see it in My Actions."
    opened = []
    model.openActionRequested.connect(opened.append)
    services.notifier.trigger(success["toastId"])  # Open：換到 Coaching、選這一項
    assert opened == [action_id] and model.selectedId == action_id
    model.watchService("S2")
    model.updateDraft("goal", "Goal")
    model.updateDraft("comment", "Why")
    assert model.createGoal()
    services.tasks.finish(coaching.CREATE_KEY, result=Snapshot())  # 不知道是誰：寫「The staff member」
    assert [toast["detail"] for toast in toasts(services.notifier)][0] == "The staff member can see it in My Actions."


def test_the_panel_focuses_the_goal_opened_from_coaching_and_saves_moments_to_it(services):
    model = model_for(services)
    model.pageOpened("coaching")
    listed(services, (action(follow_up_service_id="S4", follow_up_start=datetime(2026, 10, 30, 11)),))
    assert model.openService(A1, "S4", -1)  # 從 Coaching 按 Watch
    assert model._focus_next == ("S4", A1)
    model.watchService("S4")
    model.pageOpened("card")
    assert model.cardFocusId == ""  # 面板還沒讀好：還不知道能不能加
    services.tasks.finish(coaching.CARD_KEY, result=card(
        ServiceAction(A1, "G", "Mia Chen", TODAY, "open", "follow_up", 1, "S4"),
        ServiceAction(A2, "Other", "Mia Chen", TODAY, "completed", "source")))
    assert model.cardFocusId == A1
    assert model.startMoment(48)
    assert model.draft["pending"]["target"] == A1
    assert not model.focusGoal("")  # 說明欄開著：先存或取消
    model.updatePending("x" * 301)
    assert not model.savePending()
    assert model.actionError == {"actionId": A1, "op": "marker", "text": "Keep the note under 300 characters."}
    model.updatePending("Asked them to repeat it")
    assert model.actionError["text"] == ""  # 改了字就拿掉錯誤
    assert model.savePending()  # 對準一項：寫進資料庫
    key = coaching.write_key(A1)
    assert services.tasks.isBusy(key) and model.draft["pending"]["open"]  # 寫好了才關
    services.tasks.finish(key, error=offline())
    assert model.draft["pending"]["open"] and model.actionError["op"] == "marker"  # 失敗：字留著
    assert model.savePending()
    services.tasks.finish(key, result=Snapshot(card=card(
        ServiceAction(A1, "G", "Mia Chen", TODAY, "open", "follow_up", 1, "S4"),
        markers=(Marker(9, "S4", 48, "Asked them to repeat it", action_id=A1),))))
    assert model.draft["pending"] == {"open": False}
    assert model.cardActions[0]["markers"][0]["editable"]
    assert not any(title == "success" for title, _ in titles(services))  # 時間註記不跳通知
    # Mark Moments、Set a New Goal
    assert not model.focusGoal(A2)  # 結案的：不能加
    assert not model.focusGoal("no-such")
    assert model.focusGoal("") and model.cardFocusId == ""
    assert model.focusGoal(A1) and model.cardFocusId == A1
    # 同一筆服務、從 Coaching 再打開：對準那一項；從 Services 打開（沒有 openService）：照舊
    model.focusGoal("")
    assert model.openService(A1, "S4", 5)
    model.watchService("S4")
    assert model.cardFocusId == A1
    model.watchService("S4")
    assert model.cardFocusId == A1 and model._focus_next is None
    # 原始服務也可以；看過的後續服務（不能加）不對準
    model.select(A1)
    services.tasks.finish(coaching.DETAIL_KEY, result=Detail(
        action(follow_up_service_id="S4", follow_up_start=datetime(2026, 10, 30, 11)), (),
        (event("adjusted", service_id="S3", service_start=datetime(2026, 10, 29)),)))
    assert model.openService(A1, "S20261020093000", 135) and model._focus_next == ("S20261020093000", A1)
    assert model.openService(A1, "S3", -1) and model._focus_next == ("S20261020093000", A1)
    model.watchService("S9")  # 打開的是別的服務：不對準
    assert model.cardFocusId == "" and model._focus_next is None
    model._current_draft()["sent"] = {"actionId": A1, "count": 0}  # 剛送出：Set a New Goal 也收起送出的那一項
    assert model.focusGoal("") and model.draft["sent"] == {}


def test_writes_check_their_fields_first_and_report_what_went_wrong(services):
    model = model_for(services)
    model.select(A1)
    services.tasks.finish(coaching.DETAIL_KEY, result=Detail(action()))
    services.tasks.running.clear()
    assert not model.addMarker(A1, "S1", -1, "")
    assert not model.addMarker(A1, "S1", 5, "x" * 301)
    assert model.actionError == {"actionId": A1, "op": "marker", "text": "Keep the note under 300 characters."}
    assert not model.respond(A1, "  ")
    assert model.actionError["text"] == "Write a message first."
    assert not model.linkFollowUp(A1, "")
    assert not model.complete(A1, "")
    assert model.actionError["text"] == "Write what you saw in the follow-up."
    assert not model.keepPracticing(A1, "", "Goal", "2026-11-20")
    assert model.actionError == {"actionId": A1, "op": "adjust", "text": "Write what still needs practice."}
    assert not model.adjust(A1, "", "Goal", "2026-11-20")
    assert model.actionError["text"] == "Write why you're changing the goal."
    assert not model.adjust(A1, "Why", "", "2026-11-20")
    assert not model.adjust(A1, "Why", "Goal", "2026-10-01")
    assert model.actionError["text"] == "Choose today or a later date."
    assert not model.cancelAction(A1, " ")
    assert model.actionError["text"] == "Explain why you're cancelling this goal."
    model.clearActionError()
    model.clearActionError()
    assert model.actionError["text"] == ""
    assert not model.acknowledge(A1)  # 管理員不確認
    assert not model.complete("unknown", "Done")  # 畫面上沒有這一項：不知道版本
    assert not model.removeMarker("", 1)
    staff = model_for(Services(), admin=False)
    assert not staff.addMarker(A1, "S1", 5, "") and not staff.removeMarker(A1, 1)
    assert not staff.linkFollowUp(A1, "S2") and not staff.cancelAction(A1, "No") and not staff.adjust(A1, "a", "b", "c")
    assert not staff.keepPracticing(A1, "a", "b", "c")


def test_a_write_reloads_the_open_views_and_says_so_only_when_it_is_not_in_view(services):
    model = model_for(services)
    saved = []
    model.saved.connect(lambda action_id, op: saved.append((action_id, op)))
    model.pageOpened("coaching")
    listed(services, (action(),), {A1: 1})
    model.select(A1)
    services.tasks.finish(coaching.DETAIL_KEY, result=Detail(action()))
    services.tasks.finish(coaching.OPTIONS_KEY, result=[("S2", datetime(2026, 10, 26, 10, 15), None)])
    assert model.linkFollowUp(A1, "S2")
    assert model.saving == [A1] and not model.respond(A1, "While saving")
    key = coaching.write_key(A1)
    fn, on_ok, on_err = services.tasks.running.pop(key)
    on_ok(Snapshot(actions=(action(version=2, follow_up_service_id="S2"),),
                   detail=Detail(action(version=2, follow_up_service_id="S2"))))
    assert saved == [(A1, "link")] and model.saving == []
    assert model.current["followUp"]["serviceId"] == "S2" and model.current["version"] == 2
    assert [row["chipState"] for row in model.listModel.rows()] == ["review"]
    assert not titles(services)  # Next step 換成「Watch the follow-up」：不跳通知
    assert not services.tasks.isBusy(coaching.OPTIONS_KEY)  # 已經有後續服務、沒有按 Change：不查
    # Keep Practicing：後續服務拿掉了 → 重查能選的
    assert model.keepPracticing(A1, "More", "Goal", "2026-11-20")
    services.tasks.running.pop(key)[1](Snapshot(actions=(action(version=3),), detail=Detail(action(version=3))))
    assert ("success", "Goal updated") in titles(services)
    assert [toast["detail"] for toast in toasts(services.notifier)][0] == "Mia will see what to practise next."
    assert services.tasks.isBusy(coaching.OPTIONS_KEY)
    services.tasks.finish(coaching.OPTIONS_KEY, result=[])
    assert model.followUpState == "empty" and [row["chipState"] for row in model.listModel.rows()] == ["waiting"]
    assert model.adjust(A1, "Clearer", "Goal", "2026-11-20")
    services.tasks.running.pop(key)[1](Snapshot())
    assert [toast["detail"] for toast in toasts(services.notifier)][0] == "Mia will see the new goal."
    assert model.complete(A1, "Done")
    services.tasks.running.pop(key)[1](Snapshot())
    assert [(toast["title"], toast["detail"]) for toast in toasts(services.notifier)][0] == (
        "Goal completed", "Mia can see your conclusion.")
    # 看不到了（別人撤回、資料庫不給）：明細是 missing
    assert model.respond(A1, "Hello")
    services.tasks.running.pop(key)[1](Snapshot(detail=False))
    assert model.detailState == "missing"
    # 不知道是誰的（清單、明細都沒有）：通知不寫名字
    model._actions = ()
    model._card = card(ServiceAction(A2, "G", "", TODAY, "open", "source", 4))
    assert model.complete(A2, "Done") and model.adjust(A2, "Why", "Goal", "2026-11-20") is False  # 同一項寫入中
    services.tasks.running.pop(coaching.write_key(A2))[1](Snapshot())
    assert [toast["detail"] for toast in toasts(services.notifier)][0] == ""
    assert model.keepPracticing(A2, "Why", "Goal", "2026-11-20")


def test_conflicts_say_changed_elsewhere_with_reload_and_offline_writes_can_be_retried(services):
    model = model_for(services)
    model.pageOpened("coaching")
    model.pageOpened("card")
    model.watchService("S1")
    services.tasks.running.clear()
    model.select(A1)
    services.tasks.finish(coaching.DETAIL_KEY, result=Detail(action(follow_up_service_id="S4")))
    key = coaching.write_key(A1)

    assert model.complete(A1, "Done")
    services.tasks.finish(key, error=signal_error("changed"))
    assert model.actionError == {"actionId": A1, "op": "complete", "text": coaching.problem_text("changed")}
    [toast] = [toast for toast in toasts(services.notifier) if toast["title"] == "This goal changed elsewhere"]
    assert toast["actionLabel"] == "Reload" and toast["severity"] == "warning"
    services.notifier.trigger(toast["toastId"])  # Reload：明細、清單、面板都重讀
    assert all(services.tasks.isBusy(name) for name in (coaching.DETAIL_KEY, coaching.LIST_KEY, coaching.CARD_KEY))
    services.tasks.running.clear()

    assert model.cancelAction(A1, "Wrong person")
    services.tasks.finish(key, error=offline())
    [toast] = [toast for toast in toasts(services.notifier) if toast["title"] == "Change wasn't saved"]
    assert toast["actionLabel"] == "Retry"
    services.notifier.trigger(toast["toastId"])
    assert services.tasks.isBusy(key)  # 同一個寫入再送一次
    services.tasks.finish(key, error=ValueError("bug"))  # 不是連線、也不是程序：說原因，沒有 Retry
    assert model.actionError["text"] == "Something went wrong. An unexpected error occurred and was logged. If it keeps happening, contact your administrator."
    services.notifier.trigger(999)  # 不存在的通知
    model._on_action("coaching-retry:" + A1, 0)  # 已經沒有要重送的
    model._on_action("coaching-reload:other", 0)
    model._on_action("something-else", 0)
    model._on_action("coaching-unknown:x", 0)


def test_staff_acknowledge_uses_the_version_on_screen(services):
    model = model_for(services, admin=False)
    model.pageOpened("actions")
    listed(services, (action(version=3),))
    assert model.acknowledge(A1)  # 明細還沒讀到：用清單的版本
    fn, on_ok, on_err = services.tasks.running[coaching.write_key(A1)]
    assert fn is not None
    services.tasks.finish(coaching.write_key(A1), result=Snapshot())
    assert model.respond(A1, "Context")
    services.tasks.finish(coaching.write_key(A1), result=Snapshot())
    assert not titles(services)  # Got It、訊息都在畫面上，不跳通知


def test_versions_come_from_the_detail_then_the_panel_then_the_list(services):
    model = model_for(services)
    model.watchService("S1")
    model.pageOpened("card")
    services.tasks.finish(coaching.CARD_KEY, result=card(ServiceAction(A1, "G", "Mia", TODAY, "open", "source", 4)))
    assert model._version(A1) == 4
    model._actions = (action(A2, version=7),)
    assert model._version(A2) == 7 and model._version("a9") is None


def test_after_stop_late_results_do_not_change_the_screen(services):
    model = model_for(services, admin=False)
    model.select(A1)
    services.tasks.finish(coaching.DETAIL_KEY, result=Detail(action()))
    assert model.respond(A1, "Late")
    model.stop()
    services.tasks.finish(coaching.write_key(A1), result=Snapshot(detail=Detail(action(version=9))))
    assert model.current["version"] == 1
