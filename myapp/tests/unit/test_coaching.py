"""員工輔導（PLAN1 W2，T45）：myapp/coaching.py 不連網路的部分。

- 文字（期限、狀態、歷史、明細、卡片）、指標（有後續證據的完成比例、回顧時間）、表單的檢查、錯誤的原因。
- 資料庫的函式用假的 cursor（照 SQL 回傳準備好的結果集），CoachingModel 用假的 TaskRunner（finish() 才執行工作）：
  清單、篩選、明細、卡片、草稿、寫入的成功與失敗（Retry、Reload）、過期的結果不算數。
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

def test_clock_and_days():
    assert [coaching.clock(value) for value in (0, 75, 3599, 3725, -3)] == ["00:00", "01:15", "59:59", "1:02:05",
                                                                            "00:00"]
    assert coaching.short_day(date(2026, 11, 15), TODAY) == "Nov 15"
    assert coaching.short_day(datetime(2027, 1, 2, 9), TODAY) == "Jan 2, 2027"
    assert coaching.short_day(None, TODAY) == "" and coaching.moment_text(None, TODAY) == ""
    assert coaching.moment_text(datetime(2026, 10, 20, 9, 30), TODAY) == "Oct 20, 09:30"
    assert coaching.service_text("S1", datetime(2026, 10, 20, 9, 30), TODAY) == "S1 · Oct 20, 09:30"
    assert coaching.service_text("S1", None, TODAY) == "S1"
    assert [coaching.days_text(value) for value in (None, 0.4, 0.99, 1.2, 1.6, 9.0)] == [
        "", "Under 1 day", "Under 1 day", "1 day", "2 days", "9 days"]


def test_due_text_and_chip_state():
    assert coaching.due_text(action(due=TODAY + timedelta(days=14)), TODAY) == "Due Nov 15"
    assert coaching.due_text(action(due=TODAY), TODAY) == "Due today"
    overdue = action(due=TODAY - timedelta(days=2))
    assert (coaching.due_text(overdue, TODAY), coaching.chip_state(overdue, TODAY)) == ("Due Oct 30", "overdue")
    done = action(status="completed", concluded_at=datetime(2026, 11, 10, 8), due=TODAY - timedelta(days=9))
    assert (coaching.due_text(done, TODAY), coaching.chip_state(done, TODAY)) == ("Completed Nov 10", "completed")
    cancelled = action(status="cancelled", concluded_at=datetime(2026, 11, 3, 8))
    assert coaching.due_text(cancelled, TODAY) == "Cancelled Nov 3"
    assert coaching.due_text(action(due=None), TODAY) == "" and coaching.chip_state(action(due=None), TODAY) == "open"


def test_replied_unread_and_review_time():
    staff_later = action(last_staff_at=datetime(2026, 11, 1, 10), last_admin_at=datetime(2026, 11, 1, 9))
    assert coaching.staff_replied(staff_later)
    assert not coaching.staff_replied(action(last_staff_at=datetime(2026, 11, 1, 8),
                                             last_admin_at=datetime(2026, 11, 1, 9)))
    assert coaching.staff_replied(action(last_staff_at=datetime(2026, 11, 1, 8)))  # 主管沒有動作過
    assert not coaching.staff_replied(action(status="completed", last_staff_at=datetime(2026, 11, 1, 10)))
    assert not coaching.staff_replied(action())
    assert coaching.unread(action()) and not coaching.unread(action(acknowledged_at=datetime(2026, 11, 1)))
    assert not coaching.unread(action(status="cancelled"))
    done = action(status="completed", created_at=datetime(2026, 10, 1, 12), concluded_at=datetime(2026, 10, 10, 0))
    assert coaching.review_days(done) == 8.5
    assert coaching.review_days(action()) is None
    assert coaching.review_days(action(status="completed", created_at=None)) is None


def test_metrics_leave_out_cancelled_actions_and_use_the_median():
    def done(days, staff=STAFF):
        return action(status="completed", staff_id=staff, created_at=datetime(2026, 10, 1),
                      concluded_at=datetime(2026, 10, 1) + timedelta(days=days))

    actions = [done(3), done(9), done(12), action(), action(status="cancelled")]
    assert coaching.metrics(actions) == (3, 4, 9)
    assert coaching.metrics([]) == (0, 0, None)
    assert coaching.metrics([action()]) == (0, 1, None)
    assert (coaching.share_percent(3, 4), coaching.share_text(3, 4)) == ("75%", "3 of 4 actions")
    assert (coaching.share_percent(1, 1), coaching.share_text(1, 1)) == ("100%", "1 of 1 action")
    assert (coaching.share_percent(0, 0), coaching.share_text(0, 0)) == ("—", "No actions yet")
    assert coaching.share_percent(1, 3) == "33%" and coaching.share_percent(2, 3) == "67%"


def test_rows_name_the_staff_member_and_flag_replies_and_new_goals():
    shared = coaching.shared_names([action(), action(A2, staff_id="other", staff_username="mia.c")])
    assert shared == {"Mia Chen"}
    assert coaching.staff_text(action(), shared) == "Mia Chen (mia)"
    assert coaching.staff_text(action(staff_name=""), frozenset()) == "mia"
    row = coaching.action_row(action(last_staff_at=datetime(2026, 11, 1)), True, TODAY)
    assert (row["metaText"], row["replied"], row["unread"], row["chipState"]) == (
        "Mia Chen · Due Nov 6", True, False, "open")
    staff_row = coaching.action_row(action(), False, TODAY)
    assert (staff_row["metaText"], staff_row["replied"], staff_row["unread"]) == ("Due Nov 6", False, True)
    assert set(row) == set(coaching.ACTION_ROLES)
    assert coaching.role_text("admin") == "Admin" and coaching.role_text("staff") == "Staff"
    assert coaching.relation_text("source") == "Set from this service" and coaching.relation_text("x") == ""


def event(kind, role="admin", **fields):
    values = dict(author_id=ADMIN if role == "admin" else STAFF, author_name="Alex Wang" if role == "admin" else "Mia Chen",
                  author_role=role, created_at=datetime(2026, 11, 1, 9, 40))
    values.update(fields)
    return Event(fields.pop("event_id", 1), kind, **values)


def test_history_says_who_did_what_and_what_changed():
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
        event("cancelled", body="Wrong person"),
        event("mystery", author_name=""),
    ]
    items = coaching.history_items(events, TODAY)
    assert [item["title"] for item in items] == [
        "Alex Wang set the goal", "Mia Chen responded", "Alex Wang replied", "Mia Chen acknowledged the goal",
        "Alex Wang linked the follow-up S2 · Oct 26, 10:15",
        "Alex Wang changed the follow-up to S3 · Oct 29, 14:10",
        "Alex Wang marked 00:48 on S3", "Alex Wang removed the marker at 00:48 on S3",
        "Alex Wang adjusted the goal", "Alex Wang adjusted the goal",
        "Alex Wang linked the follow-up S4",                       # 調整之後是重新連結，不是「換掉」
        "Alex Wang completed the action", "Alex Wang cancelled the action", "Admin"]
    assert items[0]["changes"] == ["Goal: Goal A", "Follow up by Nov 15"]
    assert items[8]["changes"] == ["Reviewed follow-up: S3 · Oct 29, 14:10", "Goal: Goal A → Goal B",
                                   "Follow up by: Nov 15 → Nov 30"]
    assert items[9]["changes"] == []
    assert items[11]["changes"] == ["Follow-up: S4"]
    assert (items[1]["roleText"], items[1]["isMessage"], items[3]["isMessage"]) == ("Staff", True, False)
    assert items[0]["whenText"] == "Nov 1, 09:40"


def test_detail_map_groups_markers_by_service_and_says_what_each_side_can_do():
    detail = Detail(
        action(follow_up_service_id="S4", follow_up_start=datetime(2026, 10, 30, 11)),
        (Marker(1, "S20261020093000", 135, "Fee"), Marker(2, "S3", 40), Marker(3, "S4", 12, "Good")),
        (event("adjusted", body="Not yet", service_id="S3", service_start=datetime(2026, 10, 29, 14, 10),
               created_at=datetime(2026, 10, 30, 8)),
         event("adjusted", body="Again", service_id="S3", created_at=datetime(2026, 10, 31, 8))))
    admin = coaching.detail_map(detail, True, TODAY)
    assert [marker["clock"] for marker in admin["source"]["markers"]] == ["02:15"]
    assert admin["source"]["markers"][0]["editable"] and admin["followUp"]["markers"][0]["editable"]
    assert [block["serviceId"] for block in admin["reviewed"]] == ["S3"]  # 同一筆只列一次，原因是最新的
    assert admin["reviewed"][0]["reason"] == "Again" and not admin["reviewed"][0]["markers"][0]["editable"]
    assert (admin["canComplete"], admin["canLink"], admin["canAcknowledge"], admin["canRespond"]) == (
        True, True, False, True)
    assert admin["setText"] == "Set by Alex Wang · Oct 21" and admin["dueDate"] == "2026-11-06"
    staff = coaching.detail_map(detail, False, TODAY)
    assert not staff["source"]["markers"][0]["editable"]
    assert (staff["canComplete"], staff["canLink"], staff["canAcknowledge"]) == (False, False, True)
    no_follow_up = coaching.detail_map(Detail(action(acknowledged_at=datetime(2026, 11, 1, 10, 5))), False, TODAY)
    assert no_follow_up["followUp"] == {} and not no_follow_up["canAcknowledge"]
    assert no_follow_up["acknowledgedText"] == "Acknowledged Nov 1, 10:05"
    done = coaching.detail_map(Detail(action(
        status="completed", conclusion="Did it", concluded_by_name="Alex Wang", concluded_at=datetime(2026, 10, 30, 9),
        created_at=datetime(2026, 10, 21, 9), follow_up_service_id="S4")), True, TODAY)
    assert done["concludedText"] == "Completed by Alex Wang · Oct 30 · 9 days after the goal was set"
    assert not done["canRespond"] and not done["canComplete"] and not done["canAdjust"]
    cancelled = coaching.detail_map(Detail(action(status="cancelled", concluded_by_name="Alex Wang",
                                                  concluded_at=datetime(2026, 10, 22, 9))), True, TODAY)
    assert cancelled["concludedText"] == "Cancelled by Alex Wang · Oct 22"
    undated = coaching.detail_map(Detail(action(due=None, created_by_name="")), True, TODAY)
    assert undated["dueDate"] == "" and undated["setText"].startswith("Set by an admin")


def test_card_items_keep_the_markers_of_this_video_per_action():
    actions = (ServiceAction(A1, "Goal 1", "Mia Chen", TODAY + timedelta(days=3), "open", "source"),
               ServiceAction(A2, "Goal 2", "Mia Chen", TODAY - timedelta(days=1), "open", "reviewed"),
               ServiceAction("a3", "Goal 3", "Mia Chen", TODAY, "completed", "follow_up"))
    markers = (Marker(1, "S1", 5, action_id=A1), Marker(2, "S1", 9, "x", action_id=A2))
    items = coaching.card_items(actions, markers, TODAY)
    assert [(item["canMark"], item["chipState"], item["dueText"]) for item in items] == [
        (True, "open", "Due Nov 4"), (False, "overdue", "Due Oct 31"), (False, "completed", "")]
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

    def answer(self, sql):
        for prefix, sets in self.results.items():
            if sql.startswith(prefix):
                return sets() if callable(sets) else sets
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
        self.sets = [list(rows) for rows in self.db.answer(sql)]
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


def test_reading_functions_use_one_call_each():
    db = FakeDb()
    db.results = {call_of(coaching.LIST_SQL): [[list_row(action())]],
                  call_of(coaching.DETAIL_SQL): [[list_row(action())],
                                             [{"marker_id": 1, "service_id": "S1", "offset_seconds": 5}],
                                             [{"event_id": 1, "event_type": "created", "author_id": ADMIN}], []],
                  call_of(coaching.FOR_SERVICE_SQL): [[{"action_id": A1, "goal": "G", "due_date": TODAY,
                                                    "action_status": "open", "relation": "source"}],
                                                  [{"marker_id": 1, "action_id": A1, "offset_seconds": 9}], []],
                  call_of(coaching.OPTIONS_SQL): [[{"service_id": "S2", "service_start": datetime(2026, 10, 26)}], []]}
    assert [item.action_id for item in coaching.list_actions(db, ADMIN)] == [A1]
    detail = coaching.load_detail(db, ADMIN, A1)
    assert (detail.action.action_id, detail.markers[0].seconds, detail.events[0].kind) == (A1, 5, "created")
    actions, markers = coaching.load_card(db, ADMIN, "S1")
    assert actions[0].relation == "source" and markers[0].service_id == "S1" and markers[0].action_id == A1
    assert coaching.load_follow_up_options(db, ADMIN, A1) == [("S2", datetime(2026, 10, 26))]
    db.results[call_of(coaching.DETAIL_SQL)] = [[], [], [], []]
    assert coaching.load_detail(db, STAFF, A1) is None
    assert [sql for sql, _ in db.executed] == ["CALL sp_coaching_list", "CALL sp_coaching_detail",
                                               "CALL sp_coaching_for_service", "CALL sp_coaching_follow_up_options",
                                               "CALL sp_coaching_detail"]


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


def test_without_services_nothing_is_sent():
    model = CoachingModel(None, ADMIN, admin=True, today=TODAY)
    model.pageOpened("coaching")
    model.pageOpened("card")
    model.select(A1)
    model.watchService("S1")
    model.loadFollowUps(A1)
    assert model.listState == "idle" and not model.createGoal() and not model.respond(A1, "Hi")
    assert model._refresh_list() is False and model._load_detail(A1) is False and model._load_card() is False
    assert model.defaultDue() == "2026-11-15"
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
    services.tasks.finish(coaching.LIST_KEY, result=(action(), action(A2, staff_id="o", staff_name="Leo Park")))
    assert (model.listState, model.totalCount, model.countText) == ("ready", 2, "2 actions")
    model.refresh()
    assert model.refreshing
    services.tasks.finish(coaching.LIST_KEY, error=offline())
    assert model.listState == "ready" and model.totalCount == 2 and not model.refreshing
    model.refresh()
    services.tasks.finish(coaching.LIST_KEY, result=())
    assert model.listState == "empty" and model.countText == "0 actions"
    model.pageClosed("coaching")
    model.pageClosed("coaching")  # 多關一次沒關係
    assert not model._list_open()


def test_filters_status_due_and_staff_and_metrics_follow_the_staff_filter(services):
    model = model_for(services)
    model.pageOpened("coaching")
    leo = dict(staff_id="leo", staff_name="Leo Park", staff_username="leo")
    services.tasks.finish(coaching.LIST_KEY, result=(
        action(A1, due=TODAY - timedelta(days=1)), action(A2, due=TODAY + timedelta(days=3), **leo),
        action("a3", status="completed", created_at=datetime(2026, 10, 1), concluded_at=datetime(2026, 10, 5), **leo),
        action("a4", due=TODAY + timedelta(days=30))))
    ids = lambda: [row["actionId"] for row in model.listModel.rows()]  # noqa: E731
    model.setFilter("due", "overdue")
    assert ids() == [A1]
    model.setFilter("due", "soon")
    assert ids() == [A2]
    model.setFilter("due", "later")  # 不認得的值：不改
    model.setFilter("color", "red")  # 不認得的篩選：不改
    assert model.filters["due"] == "soon"
    model.setFilter("due", "any")
    model.setFilter("status", "completed")
    assert ids() == ["a3"] and model.countText == "1 of 4 actions"
    model.setFilter("status", "all")
    model.setFilter("staff", "leo")
    assert ids() == [A2, "a3"]
    assert (model.completedShare, model.completedText, model.reviewTimeText, model.openCountText, model.openNote) == (
        "50%", "1 of 2 actions", "4 days", "1", "None overdue")
    assert model.reviewTimeNote == "Median, goal to completion · 1 completed"
    model.setFilter("staff", "")
    assert model.openNote == "1 overdue" and model.firstActionId() == A1
    assert [option["text"] for option in model.staffOptions] == ["All staff", "Leo Park", "Mia Chen"]
    model.setFilter("staff", "leo")
    model.refresh()
    services.tasks.finish(coaching.LIST_KEY, result=(action(),))  # Leo 不在了：篩選回到全部
    assert model.filters["staff"] == "" and ids() == [A1]
    model.setFilter("status", "cancelled")
    assert model.firstActionId() == "" and model.shownCount == 0
    assert model.reviewTimeNote == "No completed actions yet" and model.reviewTimeText == "—"


def test_detail_loads_on_select_and_old_results_do_not_count(services):
    model = model_for(services)
    model.select(A1)
    assert model.detailState == "loading" and model.current == {}
    _, stale_ok, stale_err = services.tasks.running[coaching.DETAIL_KEY]
    model.select(A2)  # 換了一項：上一個還沒回來的結果不算
    stale_ok(Detail(action(A1)))
    stale_err(offline())
    assert model.detailState == "loading"
    services.tasks.finish(coaching.DETAIL_KEY, result=Detail(action(A2)))
    assert model.detailState == "ready" and model.current["actionId"] == A2
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


def test_opening_a_service_only_works_for_services_of_that_action(services):
    model = model_for(services, admin=False)
    opened = []
    model.openServiceRequested.connect(lambda *args: opened.append(args))
    model.pageOpened("actions")
    services.tasks.finish(coaching.LIST_KEY, result=(action(follow_up_service_id="S4",
                                                            follow_up_start=datetime(2026, 10, 30, 11)),))
    assert model.openService(A1, "S4", 12)  # 清單就有：後續服務
    model.select(A1)
    services.tasks.finish(coaching.DETAIL_KEY, result=Detail(action(), (), (
        event("adjusted", service_id="S3", service_start=datetime(2026, 10, 29, 14, 10)),)))
    assert model.openService(A1, "S3", -1)  # 調整時看過的
    assert not model.openService(A1, "S99", 0) and not model.openService("a9", "S20261020093000", 0)
    assert opened == [("S4", "2026-10-30 11:00:00", 12, coaching.STAFF_PAGE),
                      ("S3", "2026-10-29 14:10:00", -1, coaching.STAFF_PAGE)]


def test_card_follows_the_watched_service_and_drafts_are_kept_per_service(services):
    model = model_for(services)
    model.watchService("S1")
    assert not services.tasks.running  # 卡片還沒打開：不讀
    model.pageOpened("card")
    assert model.cardState == "loading"
    services.tasks.finish(coaching.CARD_KEY, error=offline())
    assert model.cardState == "error"
    model.reloadCard()
    services.tasks.finish(coaching.CARD_KEY, result=((ServiceAction(A1, "G", "Mia", TODAY, "open", "source"),), ()))
    assert model.cardState == "ready" and model.cardActions[0]["actionId"] == A1
    model.reloadCard()
    services.tasks.finish(coaching.CARD_KEY, error=offline())
    assert model.cardState == "ready"  # 已經有了：留著
    model.watchService("S1")  # 同一筆：不重讀
    assert not services.tasks.running

    model.updateDraft("goal", "Goal")
    model.updateDraft("goal", "Goal")  # 一樣的值：不算
    model.updateDraft("color", "red")  # 不認得的欄位
    model.setDraftOpen(True)
    assert model.addDraftMarker(30, " Here ") and not model.addDraftMarker(30, "Again") and not model.addDraftMarker(-1, "")
    assert model.addDraftMarker(5, "")
    assert [marker["clock"] for marker in model.draft["markers"]] == ["00:05", "00:30"]
    model.removeDraftMarker(9)
    model.removeDraftMarker(0)
    assert model.draft == {"goal": "Goal", "comment": "", "due": "2026-11-15", "open": True,
                           "markers": [{"seconds": 30, "clock": "00:30", "note": "Here"}]}
    resets = []
    model.draftReset.connect(lambda: resets.append(True))
    model.watchService("S2")  # 另一筆服務：自己的草稿
    services.tasks.finish(coaching.CARD_KEY, result=((), ()))
    assert model.draft["goal"] == "" and not model.draft["open"] and resets
    model.watchService("S1")
    assert model.draft["goal"] == "Goal"
    _, on_ok, on_err = services.tasks.running[coaching.CARD_KEY]
    model.watchService("S2")  # 前一個（S1）的結果回來時已經換了：不算
    on_ok(((ServiceAction(A1, "G", "Mia", TODAY, "open", "source"),), ()))
    on_err(offline())
    assert model.cardActions == []
    model.discardDraft()
    model.watchService("")
    model.updateDraft("goal", "Nowhere")  # 沒有服務：沒有草稿
    assert not model.addDraftMarker(1, "") and model.draft["goal"] == ""
    model.setDraftOpen(True)
    model.removeDraftMarker(0)


def test_creating_checks_the_form_and_retries_after_a_network_failure(services):
    model = model_for(services)
    created = []
    model.actionCreated.connect(created.append)
    model.watchService("S1")
    model.pageOpened("card")
    services.tasks.finish(coaching.CARD_KEY, result=((), ()))
    assert not model.createGoal()  # 空的
    assert model.createErrors["goal"] and model.createErrors["comment"]
    model.updateDraft("goal", "Goal")
    assert model.createErrors["goal"] == ""
    model.updateDraft("comment", "Why")
    assert model.addDraftMarker(5, "")
    assert model.createGoal()
    assert model.creating and not model.createGoal()  # 送出中：不送第二次
    action_id = model._drafts["S1"]["actionId"]
    services.tasks.finish(coaching.CREATE_KEY, error=offline())
    assert not model.creating
    assert model.createErrors["form"] == "Can't reach the database. Check your network and try again."
    [toast] = toasts(services.notifier)
    assert (toast["title"], toast["actionLabel"]) == ("Goal wasn't saved", "Retry")
    services.notifier.trigger(toast["toastId"])  # Retry：同一個 action_id
    assert model.creating
    services.tasks.finish(coaching.CREATE_KEY, result=Snapshot(card=(
        (ServiceAction(action_id, "Goal", "Mia Chen", TODAY, "open", "source"),), ())))
    assert created == [action_id] and model.draft["goal"] == "" and model.createErrors["form"] == ""
    assert ("success", "Goal created") in titles(services)
    [success] = [toast for toast in toasts(services.notifier) if toast["title"] == "Goal created"]
    assert success["detail"] == "Mia Chen can see it in My Actions."
    opened = []
    model.openActionRequested.connect(opened.append)
    services.notifier.trigger(success["toastId"])  # Open：換到 Coaching、選這一項
    assert opened == [action_id] and model.selectedId == action_id

    # 程序拒絕（例如服務是管理員錄的）：寫在表單上，沒有 Retry
    model.updateDraft("goal", "Again")
    model.updateDraft("comment", "Why")
    assert model.createGoal()
    services.tasks.finish(coaching.CREATE_KEY, error=signal_error("not_staff"))
    assert model.createErrors["form"] == coaching.problem_text("not_staff")
    assert [toast["title"] for toast in toasts(services.notifier)].count("Goal wasn't saved") == 0
    services.tasks.running.clear()
    model.discardDraft()
    assert model.createErrors["form"] == ""
    staff = model_for(Services(), admin=False)
    staff.watchService("S1")
    assert not staff.createGoal()  # 服務人員不能建立


def detail_with(version=1, follow_up=""):
    return Detail(action(version=version, follow_up_service_id=follow_up))


def test_writes_check_their_fields_first_and_report_what_went_wrong(services):
    model = model_for(services)
    model.select(A1)
    services.tasks.finish(coaching.DETAIL_KEY, result=detail_with())
    assert not model.addMarker(A1, "S1", -1, "")
    assert not model.addMarker(A1, "S1", 5, "x" * 301)
    assert model.actionError == {"actionId": A1, "op": "marker", "text": "Keep the note under 300 characters."}
    assert not model.respond(A1, "  ")
    assert model.actionError["text"] == "Write a response first."
    assert not model.linkFollowUp(A1, "")
    assert not model.complete(A1, "")
    assert model.actionError["text"] == "Write what you saw in the follow-up."
    assert not model.adjust(A1, "", "Goal", "2026-11-20")
    assert model.actionError["text"] == "Explain why you're adjusting the goal."
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


def test_a_write_reloads_the_open_views_and_shows_the_result(services):
    model = model_for(services)
    saved = []
    model.saved.connect(lambda action_id, op: saved.append((action_id, op)))
    model.pageOpened("coaching")
    services.tasks.finish(coaching.LIST_KEY, result=(action(),))
    model.select(A1)
    services.tasks.finish(coaching.DETAIL_KEY, result=detail_with())
    model.loadFollowUps(A1)
    services.tasks.finish(coaching.OPTIONS_KEY, result=[("S2", datetime(2026, 10, 26, 10, 15))])
    assert model.followUpOptions == [{"value": "S2", "text": "S2 · Oct 26, 10:15", "startTime": "2026-10-26 10:15:00"}]
    assert model.linkFollowUp(A1, "S2")
    assert model.saving == [A1] and not model.respond(A1, "While saving")
    key = coaching.write_key(A1)
    fn, on_ok, on_err = services.tasks.running.pop(key)
    on_ok(Snapshot(actions=(action(version=2, follow_up_service_id="S2"),), detail=detail_with(2, "S2")))
    assert saved == [(A1, "link")] and model.saving == []
    assert model.current["followUp"]["serviceId"] == "S2" and model.current["version"] == 2
    assert ("success", "Follow-up linked") in titles(services)
    assert services.tasks.isBusy(coaching.OPTIONS_KEY)  # 能選的服務變了：重讀
    services.tasks.finish(coaching.OPTIONS_KEY, result=[])
    assert model.followUpState == "empty"
    # 看不到了（別人撤回、資料庫不給）：明細是 missing
    assert model.respond(A1, "Hello")
    services.tasks.running.pop(key)[1](Snapshot(detail=False))
    assert model.detailState == "missing"


def test_conflicts_say_changed_elsewhere_with_reload_and_offline_writes_can_be_retried(services):
    model = model_for(services)
    model.pageOpened("coaching")
    model.pageOpened("card")
    model.watchService("S1")
    services.tasks.running.clear()
    model.select(A1)
    services.tasks.finish(coaching.DETAIL_KEY, result=detail_with())
    key = coaching.write_key(A1)

    assert model.complete(A1, "Done")
    services.tasks.finish(key, error=signal_error("changed"))
    assert model.actionError == {"actionId": A1, "op": "complete", "text": coaching.problem_text("changed")}
    [toast] = [toast for toast in toasts(services.notifier) if toast["title"] == "This action changed elsewhere"]
    assert toast["actionLabel"] == "Reload" and toast["severity"] == "warning"
    services.notifier.trigger(toast["toastId"])  # Reload：明細、清單、卡片都重讀
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
    services.tasks.finish(coaching.LIST_KEY, result=(action(version=3),))
    assert model.acknowledge(A1)  # 明細還沒讀到：用清單的版本
    fn, on_ok, on_err = services.tasks.running[coaching.write_key(A1)]
    assert fn is not None
    services.tasks.finish(coaching.write_key(A1), result=Snapshot())
    assert ("success", "Goal acknowledged") in titles(services)
    assert model.respond(A1, "Context")
    services.tasks.finish(coaching.write_key(A1), result=Snapshot())
    assert ("success", "Response sent") in titles(services)


def test_versions_come_from_the_detail_then_the_card_then_the_list(services):
    model = model_for(services)
    model.watchService("S1")
    model.pageOpened("card")
    services.tasks.finish(coaching.CARD_KEY, result=((ServiceAction(A1, "G", "Mia", TODAY, "open", "source", 4),), ()))
    assert model._version(A1) == 4
    model._actions = (action(A2, version=7),)
    assert model._version(A2) == 7 and model._version("a9") is None


def test_after_stop_late_results_do_not_change_the_screen(services):
    model = model_for(services)
    model.select(A1)
    services.tasks.finish(coaching.DETAIL_KEY, result=detail_with())
    assert model.respond(A1, "Late")
    model.stop()
    services.tasks.finish(coaching.write_key(A1), result=Snapshot(detail=detail_with(9)))
    assert model.current["version"] == 1


def test_follow_up_options_failures_and_stale_results(services):
    model = model_for(services)
    model.loadFollowUps(A1)
    services.tasks.finish(coaching.OPTIONS_KEY, error=offline())
    assert model.followUpState == "error"
    model.loadFollowUps(A1)
    _, on_ok, on_err = services.tasks.running.pop(coaching.OPTIONS_KEY)
    model.loadFollowUps(A2)
    on_ok([("S9", None)])
    on_err(offline())
    assert model.followUpFor == A2 and model.followUpState == "loading"
    model.loadFollowUps("")  # 沒有：不讀
    model_for(Services(), admin=False).loadFollowUps(A1)
