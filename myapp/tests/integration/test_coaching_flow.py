"""員工輔導與後續追蹤（PLAN1 W2，T45）：真的 MySQL（migration 006 的 13 個 sp_coaching_* 程序）＋兩端的畫面。

- 管理員在明細頁的輔導卡建立目標（評語、時間註記、期限）→ 服務人員在 My Actions 看到、按註記跳到原始服務、
  回應、確認 → 管理員在 Coaching 頁連結後來的一筆服務、在它的影片上加註記、完成；調整保留歷史。
- 權限在資料庫：服務人員看不到、寫不了別人的行動，也改不了目標、期限、結論；後續服務要是同一位員工、後來、分析完成的。
- 兩個管理員改同一項：後到的是「changed elsewhere」＋ Reload；重複按只寫一次；連不上、交易中途失敗什麼都不留。
- 每個測試都要求 QML 零警告，GUI 執行緒碰網路就失敗（no_network_on_gui_thread）。
"""
from datetime import date, datetime, timedelta

import mysql.connector
import pytest
from PySide6.QtCore import QObject
from PySide6.QtQml import QQmlApplicationEngine

from myapp import coaching
from myapp.init import admin_home_init, server_home_init
from tests.integration.helpers import ADMIN_ID, STAFF_ID, qml, toasts, wait_until

pytestmark = pytest.mark.integration

OTHER_ID = "00000000-0000-0000-0000-0000000000b1"      # 另一位服務人員 Leo
ADMIN2_ID = "00000000-0000-0000-0000-0000000000b2"     # 第二位管理員 Jordan
SOURCE = "S20261020093000"       # Mia：原始服務
EARLY = "S20261019090000"        # Mia：比原始服務早
UNANALYZED = "S20261024100000"   # Mia：比較晚，但還在分析
LATE1 = "S20261026101500"        # Mia：比較晚、分析完成
LATE2 = "S20261029141000"        # Mia：更晚、分析完成
LEO = "S20261027110000"          # Leo 的服務
GOAL = "Explain the next step before the service ends and check the customer understood."
COMMENT = "The customer asked twice what to bring next time. Close with the next step and ask them to repeat it."


# ── fixture 與小工具 ─────────────────────────────────────────────────────────

@pytest.fixture
def qml_warnings(engine):
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    yield warnings
    assert warnings == []


@pytest.fixture
def ui(engine, services, db, qml_warnings, no_network_on_gui_thread):
    """共用的條件：真的 TaskRunner、拋棄式 MySQL；GUI 執行緒不准碰網路；QML 零警告。"""
    return engine


@pytest.fixture
def second_engine(qml_warnings):
    """另一端用自己的 engine：兩端的 context property 名稱一樣，不能放在同一個 engine。"""
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    yield engine
    engine.deleteLater()
    assert warnings == []


def seed(db):
    db.execute("UPDATE tbl_account SET account_name = 'Mia Chen' WHERE account_id = %s", (STAFF_ID,))
    db.execute("UPDATE tbl_account SET account_name = 'Alex Wang' WHERE account_id = %s", (ADMIN_ID,))
    db.add_account(OTHER_ID, "leo", "Leo Park")
    db.add_account(ADMIN2_ID, "jordan", "Jordan Lee", level="3")
    for service_id, start, status, account in ((SOURCE, "2026-10-20 09:30:00", 3, STAFF_ID),
                                               (EARLY, "2026-10-19 09:00:00", 3, STAFF_ID),
                                               (UNANALYZED, "2026-10-24 10:00:00", 2, STAFF_ID),
                                               (LATE1, "2026-10-26 10:15:00", 3, STAFF_ID),
                                               (LATE2, "2026-10-29 14:10:00", 3, STAFF_ID),
                                               (LEO, "2026-10-27 11:00:00", 3, OTHER_ID)):
        db.add_service(service_id, start, status=status, account_id=account)


def call(db, sql, params=()):
    """測試自己的連線（root）呼叫程序：程序自己檢查「呼叫者」參數。回傳每一個結果集。"""
    cursor = db.connection.cursor(dictionary=True, buffered=True)
    try:
        cursor.execute(sql, params)
        sets = [cursor.fetchall() if cursor.with_rows else []]
        while cursor.nextset():
            sets.append(cursor.fetchall() if cursor.with_rows else [])
        return sets
    finally:
        cursor.close()


def refused(db, sql, params):
    """程序拒絕的原因（coaching:xxx 的 xxx）；沒有拒絕是 None。"""
    try:
        call(db, sql, params)
    except mysql.connector.Error as error:
        assert error.errno == coaching.SIGNAL_ERRNO, error
        return coaching.problem_code(error)
    return None


def make_action(db, action_id="a0000000-0000-4000-8000-000000000001", source=SOURCE, goal=GOAL, comment=COMMENT,
                due=None, caller=ADMIN_ID):
    """用程序建一項（和桌面端一樣經過 sp_coaching_create）。"""
    due = due or (date.today() + timedelta(days=14))
    call(db, coaching.CREATE_SQL, (caller, action_id, source, goal, comment, due))
    return action_id


def action_row(db, action_id):
    rows = db.rows("SELECT action_status, goal, due_date, follow_up_service_id, conclusion, version, "
                   "acknowledged_at IS NOT NULL FROM tbl_coaching_action WHERE action_id = %s", (action_id,))
    return rows[0] if rows else None


def events(db, action_id):
    return [row[0] for row in db.rows("SELECT event_type FROM tbl_coaching_event WHERE action_id = %s "
                                      "ORDER BY event_id", (action_id,))]


def launch(module, engine, account_id, services):
    home = module.launch(engine, account_id, services)
    window = engine.rootObjects()[-1]
    keys = server_home_init.LAUNCH_KEYS if module is server_home_init else ("admin:profile",)
    wait_until(lambda: not any(services.tasks.isBusy(key) for key in keys))
    return home, window


def show(window, page):
    """和側邊欄的按鈕一樣換頁。"""
    qml(window, f'mainContentLoader.source = "../../qml/pages/{page}"')
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready"))
    return qml(window, "mainContentLoader.item")


def idle(services):
    """coaching 的背景工作都做完了。"""
    wait_until(lambda: not any(key.startswith("coaching") for key in services.tasks._running))


def find_all(root, name):
    found, pending = [], [root]
    while pending:
        item = pending.pop(0)
        if item.objectName() == name:
            found.append(item)
        pending.extend(item.childItems())
    return found


def find(root, name):
    found = root.findChild(QObject, name)
    if found is not None:
        return found
    matches = find_all(root, name)
    assert matches, f"找不到 {name}"
    return matches[0]


def visible(root, name):
    return [item for item in find_all(root, name) if item.isVisible()]


def toast_titles(services):
    return [(toast["severity"], toast["title"]) for toast in toasts(services.notifier)]


def open_detail(home, window, service_id, start):
    """和 Services 的 Details 一樣：先換頁，再等明細查好。"""
    page = qml(window, f'openServiceDetail("{service_id}", "{start}")')
    wait_until(lambda: home.mediaModel.state == "ready" and not home.mediaModel.pending)
    return page


def card_of(page):
    loader = find(page, "coachingCardLoader")
    wait_until(lambda: qml(loader, "status === Loader.Ready"))
    card = qml(loader, "item")
    wait_until(lambda: qml(card, "cardState") == "ready")
    return card


# ── 主要流程：管理員從明細頁訂目標 → 服務人員看到、跳到那一秒、回應、確認 ─────────────────

def test_admin_sets_a_goal_on_the_detail_page_and_staff_see_respond_and_acknowledge(ui, second_engine, services,
                                                                                   db):
    seed(db)
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    page = open_detail(admin, window, SOURCE, "2026-10-20 09:30:00")
    card = card_of(page)
    assert qml(card, "staffName") == "Mia Chen"
    assert find(card, "cardNote").property("text").startswith("No goals use this service yet.")
    assert not find(card, "goalComposer").isVisible()

    # New Goal：表單展開。沒有影片可以播：不能在「目前的位置」加註記
    qml(find(card, "newGoalButton"), "clicked()")
    assert find(card, "goalComposer").isVisible() and not find(card, "newGoalButton").isVisible()
    assert qml(card, "dueField.text") == (date.today() + timedelta(days=14)).strftime("%Y-%m-%d")
    assert not find(card, "addDraftMarkerButton").property("enabled")
    qml(find(card, "createGoalButton"), "clicked()")  # 空的：欄位錯誤，不送
    assert admin.coachingModel.createErrors["goal"] == "Enter the goal the staff member should practise."
    assert admin.coachingModel.createErrors["comment"] == "Explain why you set this goal."
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_action") == [(0,)]

    qml(card, f'goalField.text = "{GOAL}"')
    qml(card, f'commentField.text = "{COMMENT}"')
    assert admin.coachingModel.createErrors["goal"] == ""  # 改了那一格就拿掉錯誤
    assert admin.coachingModel.addDraftMarker(135, "Explained the fee, not the next step")
    assert admin.coachingModel.addDraftMarker(262, "")
    assert [marker["clock"] for marker in admin.coachingModel.draft["markers"]] == ["02:15", "04:22"]

    # 換頁再回來：草稿還在（存在 coachingModel，每一筆服務一份）
    show(window, "admin_service_page.qml")
    page = open_detail(admin, window, SOURCE, "2026-10-20 09:30:00")
    card = card_of(page)
    assert qml(card, "goalField.text") == GOAL and qml(card, "composing")
    assert len(admin.coachingModel.draft["markers"]) == 2

    qml(find(card, "createGoalButton"), "clicked()")
    wait_until(lambda: len(admin.coachingModel.cardActions) == 1)
    [created] = db.rows("SELECT action_id, staff_account_id, created_by, goal, manager_comment, action_status, version "
                        "FROM tbl_coaching_action")
    action_id = created[0]
    assert created[1:] == (STAFF_ID, ADMIN_ID, GOAL, COMMENT, "open", 1)
    assert db.rows("SELECT service_id, offset_seconds, note FROM tbl_coaching_marker ORDER BY offset_seconds") == [
        (SOURCE, 135, "Explained the fee, not the next step"), (SOURCE, 262, "")]
    assert events(db, action_id) == ["created", "marker_added", "marker_added"]
    assert ("success", "Goal created") in toast_titles(services)
    assert not qml(card, "composing") and qml(card, "goalField.text") == ""  # 表單收起、草稿清掉
    [row] = admin.coachingModel.cardActions
    assert (row["relation"], row["chipState"], row["canMark"]) == ("source", "open", True)
    assert [marker["clock"] for marker in row["markers"]] == ["02:15", "04:22"]

    # 服務人員：My Actions
    staff, staff_window = launch(server_home_init, second_engine, STAFF_ID, services)
    staff_page = show(staff_window, "server_actions_page.qml")
    wait_until(lambda: staff.coachingModel.detailState == "ready")
    [item] = visible(staff_page, "actionItem")
    assert find(item, "actionGoal").property("text") == GOAL
    assert find(item, "actionFlag").property("visible")  # New：還沒確認
    detail = qml(staff_page, "detail")
    assert find(detail, "detailGoal").property("text") == GOAL
    assert find(detail, "detailComment").property("text") == COMMENT
    assert [qml(chip, "text") for chip in visible(detail, "markerButton")] == [
        "02:15 · Explained the fee, not the next step", "04:22"]
    assert not visible(detail, "removeMarkerButton")  # 服務人員不能拿掉
    assert not visible(detail, "adminActions") and visible(detail, "acknowledgeRow")

    # 按時間註記：換到原始服務的明細，影片載入後跳到 02:15；頁首的「‹」回到 My Actions
    qml(visible(detail, "markerButton")[0], "clicked()")
    wait_until(lambda: qml(staff_window, "currentPage") == "server_service_detail_page.qml")
    detail_page = qml(staff_window, "mainContentLoader.item")
    assert staff.mediaModel.serviceId == SOURCE
    assert qml(detail_page, "pendingSeekMs") == 135000  # 沒有影片：一直等著，不會跳到別的地方
    assert qml(staff_window, "pageValue('headerBackText', '')") == "My Actions"
    # 側邊欄標的是 My Actions（和「‹」一致），不是 Services
    assert qml(staff_window, "actionsButton.current") and not qml(staff_window, "servicesButton.current")
    qml(detail_page, "goBack()")
    assert qml(staff_window, "currentPage") == "server_actions_page.qml"
    staff_page = qml(staff_window, "mainContentLoader.item")
    wait_until(lambda: staff.coachingModel.detailState == "ready" and staff.coachingModel.selectedId == action_id)
    detail = qml(staff_page, "detail")

    # 回應：保存作者和時間
    response = "I was rushed because the queue was long. I'll ask them to repeat the next step."
    qml(detail, f'responseField.text = "{response}"')
    qml(find(detail, "sendResponseButton"), "clicked()")
    wait_until(lambda: ("success", "Response sent") in toast_titles(services))
    assert db.rows("SELECT author_id, author_role, body FROM tbl_coaching_event WHERE event_type = 'response'") == [
        (STAFF_ID, "staff", response)]
    assert qml(detail, "responseField.text") == ""
    history = staff.coachingModel.current["history"]
    assert [entry["title"] for entry in history][-1] == "Mia Chen responded"
    assert history[-1]["body"] == response and history[-1]["roleText"] == "Staff"

    # 確認：只代表已回應，狀態仍是進行中
    qml(find(detail, "acknowledgeButton"), "clicked()")
    wait_until(lambda: staff.coachingModel.current.get("acknowledged"))
    assert action_row(db, action_id)[0] == "open" and action_row(db, action_id)[6] == 1
    assert not find(detail, "acknowledgeButton").isVisible()
    assert find(detail, "acknowledgeText").property("text").startswith("Acknowledged ")
    assert not find(item, "actionFlag").property("visible")  # 清單上的 New 也不見了
    assert events(db, action_id)[-2:] == ["response", "acknowledged"]

    # 管理員：Coaching 頁看到「Staff replied」
    coaching_page = show(window, "admin_coaching_page.qml")
    wait_until(lambda: admin.coachingModel.listState == "ready")
    [admin_item] = visible(coaching_page, "actionItem")
    assert find(admin_item, "actionFlag").property("visible")
    assert qml(admin_item, "flagText") == "Staff replied"


# ── 時間註記跳到那一秒：真的（很小的）影片，載入之後播放器停在註記的位置 ─────────────────────

def small_video(path, seconds=6, fps=10):
    """一段很短的 MJPG AVI（OpenCV 寫；測試換掉的是 VideoCapture，VideoWriter 是真的）。"""
    import cv2
    import numpy as np

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (64, 48))
    for frame in range(seconds * fps):
        writer.write(np.full((48, 64, 3), (frame * 4) % 255, dtype=np.uint8))
    writer.release()
    return path.read_bytes()


def test_a_marker_opens_the_service_and_seeks_its_video_to_that_second(ui, services, db, s3, tmp_path):
    seed(db)
    action_id = make_action(db)
    call(db, coaching.ADD_MARKER_SQL, (ADMIN_ID, action_id, SOURCE, 4, "Here"))
    data = small_video(tmp_path / "clip.avi")
    for name in (f"{SOURCE}_cam0.avi", f"{SOURCE}_cam1.avi"):
        s3.put_object(Bucket="adam-test", Key=f"video/{name}", Body=data)
        db.add_media(SOURCE, "video", f"s3://adam-test/video/{name}")
    staff, window = launch(server_home_init, ui, STAFF_ID, services)
    page = show(window, "server_actions_page.qml")
    wait_until(lambda: staff.coachingModel.detailState == "ready")
    [chip] = visible(qml(page, "detail"), "markerButton")
    qml(chip, "clicked()")
    wait_until(lambda: qml(window, "currentPage") == "server_service_detail_page.qml")
    detail = qml(window, "mainContentLoader.item")
    wait_until(lambda: staff.mediaModel.canPlay, timeout=10)
    wait_until(lambda: qml(detail, "pendingSeekMs") == -1, timeout=10)   # 影片載入了：跳過去
    wait_until(lambda: 3500 <= qml(detail, "playbackMs") <= 4500, timeout=10,
               describe=lambda: f"position {qml(detail, 'playbackMs')}")


# ── 管理員：連結後續服務、在它自己的影片上加註記、完成 ───────────────────────────────

def open_coaching(home, window):
    page = show(window, "admin_coaching_page.qml")
    wait_until(lambda: home.coachingModel.detailState == "ready")
    return page, qml(page, "detail")


def test_admin_links_a_later_follow_up_marks_its_own_video_and_completes(ui, second_engine, services, db):
    seed(db)
    action_id = make_action(db)
    call(db, coaching.ADD_MARKER_SQL, (ADMIN_ID, action_id, SOURCE, 135, "Explained the fee"))
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    model = admin.coachingModel
    page, detail = open_coaching(admin, window)
    assert model.selectedId == action_id  # 一進來選第一項
    assert (model.completedShare, model.completedText, model.reviewTimeText, model.openCountText) == (
        "0%", "0 of 1 action", "—", "1")
    assert find(page, "completedMetric").isVisible() and find(page, "coachingFilters").isVisible()

    # 完成要先有後續服務
    assert not find(detail, "completeButton").property("enabled") and find(detail, "completeHint").isVisible()
    # 後續服務的選單：同一位員工、比原始服務晚、分析完成的，新的在前（早的、分析中的、Leo 的都不在）
    wait_until(lambda: model.followUpState == "ready")
    assert [option["value"] for option in model.followUpOptions] == [LATE2, LATE1]
    select = find(detail, "followUpSelect")
    assert [option["text"] for option in qml(select, "options").toVariant()] == [
        "Choose a later service", f"{LATE2} · Oct 29, 14:10", f"{LATE1} · Oct 26, 10:15"]
    qml(select, f'picked("{LATE1}")')
    qml(find(detail, "linkFollowUpButton"), "clicked()")
    wait_until(lambda: model.current.get("followUp", {}).get("serviceId") == LATE1)
    assert action_row(db, action_id)[3:6] == (LATE1, None, 2)
    assert ("success", "Follow-up linked") in toast_titles(services)
    assert find(detail, "completeButton").property("enabled") and not find(detail, "completeHint").isVisible()

    # 看後續服務：Open Service 換到它的明細，「‹」回到 Coaching；註記加在它自己的影片上
    qml(find(find(detail, "followUpBlock"), "openServiceButton"), "clicked()")
    wait_until(lambda: qml(window, "currentPage") == "admin_service_detail_page.qml")
    assert qml(window, "pageValue('headerBackText', '')") == "Coaching"
    assert qml(window, "coachingButton.current") and not qml(window, "servicesButton.current")
    detail_page = qml(window, "mainContentLoader.item")
    wait_until(lambda: admin.mediaModel.serviceId == LATE1 and admin.mediaModel.state == "ready")
    card_of(detail_page)
    [row] = model.cardActions
    assert (row["relation"], row["canMark"], row["relationText"]) == ("follow_up", True, "Follow-up for this goal")
    assert model.addMarker(action_id, LATE1, 48, "Asked the customer to repeat the next step")  # 沒有影片：直接呼叫
    wait_until(lambda: len(model.cardActions[0]["markers"]) == 1)
    assert db.rows("SELECT service_id, offset_seconds FROM tbl_coaching_marker WHERE removed_at IS NULL "
                   "ORDER BY marker_id") == [(SOURCE, 135), (LATE1, 48)]
    qml(detail_page, "goBack()")
    page = qml(window, "mainContentLoader.item")
    wait_until(lambda: model.detailState == "ready")
    detail = qml(page, "detail")
    # 兩筆服務各有自己影片上的註記：原始的 02:15、後續的 00:48
    assert [marker["clock"] for marker in model.current["source"]["markers"]] == ["02:15"]
    assert [marker["clock"] for marker in model.current["followUp"]["markers"]] == ["00:48"]

    # 完成：結論必填
    qml(find(detail, "completeButton"), "clicked()")
    dialog = qml(detail, "dialog")
    wait_until(lambda: qml(dialog, "opened"))
    qml(dialog, "submitButton.clicked()")
    assert model.actionError["text"] == "Write what you saw in the follow-up."
    assert find(qml(dialog, "contentItem"), "dialogError").isVisible()  # 寫在按鈕上面，對話框不關
    assert action_row(db, action_id)[0] == "open"
    conclusion = "Explained the next step and asked the customer to repeat it."
    qml(dialog, f'reasonField.text = "{conclusion}"')
    qml(dialog, "submitButton.clicked()")
    wait_until(lambda: not qml(dialog, "visible"))
    assert action_row(db, action_id)[0] == "completed" and action_row(db, action_id)[4] == conclusion
    assert events(db, action_id)[-1] == "completed"
    assert model.current["concludedText"].startswith("Completed by Alex Wang · ")
    assert model.current["concludedText"].endswith("Under 1 day after the goal was set")
    assert (model.completedShare, model.completedText, model.reviewTimeText, model.openCountText) == (
        "100%", "1 of 1 action", "Under 1 day", "0")
    assert not visible(detail, "adminActions") and not visible(detail, "respondSection")

    # 服務人員看到結論，不能再回應或確認
    staff, staff_window = launch(server_home_init, second_engine, STAFF_ID, services)
    staff_page = show(staff_window, "server_actions_page.qml")
    wait_until(lambda: staff.coachingModel.detailState == "ready")
    staff_detail = qml(staff_page, "detail")
    assert find(staff_detail, "conclusionText").property("text") == conclusion
    assert find(staff_detail, "detailChip").property("label") == "Completed"
    assert not visible(staff_detail, "respondSection") and not visible(staff_detail, "acknowledgeRow")
    assert refused(db, coaching.RESPOND_SQL, (STAFF_ID, action_id, "Thanks")) == "not_open"


# ── 調整：新的目標、期限，舊的值和看過的後續服務留在歷史，下一次要更晚 ─────────────────────

def test_adjusting_keeps_the_history_and_the_next_follow_up_must_be_later(ui, second_engine, services, db):
    seed(db)
    action_id = make_action(db)
    call(db, coaching.ACKNOWLEDGE_SQL, (STAFF_ID, action_id, 1))
    call(db, coaching.LINK_SQL, (ADMIN_ID, action_id, 1, LATE1))
    call(db, coaching.ADD_MARKER_SQL, (ADMIN_ID, action_id, LATE1, 40, "Explained, but didn't check"))
    old_due = action_row(db, action_id)[2]
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    model = admin.coachingModel
    page, detail = open_coaching(admin, window)

    qml(find(detail, "adjustButton"), "clicked()")
    dialog = qml(detail, "dialog")
    wait_until(lambda: qml(dialog, "opened"))
    assert qml(dialog, "goalField.text") == GOAL  # 預先填目前的目標
    new_goal = "Ask the customer to repeat the next step before they leave."
    due = date.today() + timedelta(days=21)
    reason = "Explained the next step but didn't check the customer understood."
    qml(dialog, f'reasonField.text = "{reason}"')
    qml(dialog, f'goalField.text = "{new_goal}"')
    qml(dialog, f'dueField.text = "{due:%Y-%m-%d}"')
    qml(dialog, "submitButton.clicked()")
    wait_until(lambda: not qml(dialog, "visible"))
    assert action_row(db, action_id) == ("open", new_goal, due, None, None, 3, 0)  # 後續服務拿掉、要再確認
    assert db.rows("SELECT goal_before, goal_after, due_before, due_after, service_id, body FROM tbl_coaching_event "
                   "WHERE event_type = 'adjusted'") == [(GOAL, new_goal, old_due, due, LATE1, reason)]
    assert ("success", "Goal adjusted") in toast_titles(services)

    # 看過的後續服務和它的註記留著；下一次只能選更晚的
    current = model.current
    assert current["goal"] == new_goal and current["followUp"] == {}
    assert [block["serviceId"] for block in current["reviewed"]] == [LATE1]
    assert current["reviewed"][0]["reason"] == reason
    assert [marker["clock"] for marker in current["reviewed"][0]["markers"]] == ["00:40"]
    assert visible(detail, "reviewedBlock")
    wait_until(lambda: model.followUpFor == action_id and model.followUpState == "ready")
    assert [option["value"] for option in model.followUpOptions] == [LATE2]
    assert refused(db, coaching.LINK_SQL, (ADMIN_ID, action_id, 3, LATE1)) == "follow_up"
    entry = current["history"][-1]
    assert entry["title"] == "Alex Wang adjusted the goal" and entry["body"] == reason
    assert entry["changes"] == [f"Reviewed follow-up: {LATE1} · Oct 26, 10:15", f"Goal: {GOAL} → {new_goal}",
                                f"Follow up by: {coaching.short_day(old_due, date.today())} → "
                                f"{coaching.short_day(due, date.today())}"]
    # 員工之前的確認是對舊的目標：畫面上的版本不對就是 changed
    assert refused(db, coaching.ACKNOWLEDGE_SQL, (STAFF_ID, action_id, 1)) == "changed"

    # 服務人員：新的目標又是「New」，要再確認
    staff, staff_window = launch(server_home_init, second_engine, STAFF_ID, services)
    staff_page = show(staff_window, "server_actions_page.qml")
    wait_until(lambda: staff.coachingModel.detailState == "ready")
    [item] = visible(staff_page, "actionItem")
    assert qml(item, "flagText") == "New"
    staff_detail = qml(staff_page, "detail")
    assert find(staff_detail, "detailGoal").property("text") == new_goal
    assert find(staff_detail, "acknowledgeButton").isVisible()
    assert [block["serviceId"] for block in staff.coachingModel.current["reviewed"]] == [LATE1]


# ── 權限：資料庫決定（程序帶呼叫者），不靠畫面 ────────────────────────────────────

LEOS_ACTION = "a0000000-0000-4000-8000-0000000000aa"


def test_staff_can_only_read_and_answer_their_own_actions_and_cannot_change_them(db):
    seed(db)
    mine = make_action(db)
    marker = call(db, coaching.ADD_MARKER_SQL, (ADMIN_ID, mine, SOURCE, 30, "Here"))[0][0]["marker_id"]
    leos = make_action(db, LEOS_ACTION, source=LEO, goal="Greet the customer by name.")
    # 清單、明細、明細頁的卡片：只有自己的
    assert [row["action_id"] for row in call(db, coaching.LIST_SQL, (STAFF_ID,))[0]] == [mine]
    assert [row["action_id"] for row in call(db, coaching.LIST_SQL, (OTHER_ID,))[0]] == [leos]
    assert {row["action_id"] for row in call(db, coaching.LIST_SQL, (ADMIN_ID,))[0]} == {mine, leos}
    assert call(db, coaching.LIST_SQL, ("no-such-account",))[0] == []
    assert call(db, coaching.DETAIL_SQL, (OTHER_ID, mine))[:3] == [[], [], []]
    assert call(db, coaching.DETAIL_SQL, ("no-such-account", mine))[:3] == [[], [], []]
    assert call(db, coaching.FOR_SERVICE_SQL, (OTHER_ID, SOURCE))[:2] == [[], []]
    assert call(db, coaching.OPTIONS_SQL, (STAFF_ID, mine))[0] == []
    # 別人的行動：當作不存在
    assert refused(db, coaching.RESPOND_SQL, (OTHER_ID, mine, "Not mine")) == "not_found"
    assert refused(db, coaching.ACKNOWLEDGE_SQL, (OTHER_ID, mine, 1)) == "not_found"
    # 只有管理員能做的：服務人員（就算是自己的行動）一律 forbidden
    today = date.today()
    for sql, params in ((coaching.CREATE_SQL, (STAFF_ID, "a0000000-0000-4000-8000-0000000000ff", SOURCE, "g", "c",
                                               today)),
                        (coaching.ADD_MARKER_SQL, (STAFF_ID, mine, SOURCE, 5, "")),
                        (coaching.REMOVE_MARKER_SQL, (STAFF_ID, marker)),
                        (coaching.LINK_SQL, (STAFF_ID, mine, 1, LATE1)),
                        (coaching.COMPLETE_SQL, (STAFF_ID, mine, 1, "Done")),
                        (coaching.ADJUST_SQL, (STAFF_ID, mine, 1, "Easier", "A different goal", today)),
                        (coaching.CANCEL_SQL, (STAFF_ID, mine, 1, "No"))):
        assert refused(db, sql, params) == "forbidden", sql
    assert refused(db, coaching.ACKNOWLEDGE_SQL, (ADMIN_ID, mine, 1)) == "forbidden"  # 只有當事人能確認
    assert refused(db, coaching.RESPOND_SQL, ("no-such-account", mine, "Hi")) == "forbidden"
    # 目標、期限、結論、註記都沒變，歷史只有建立和加註記
    assert action_row(db, mine)[:2] == ("open", GOAL) and action_row(db, mine)[3:6] == (None, None, 1)
    assert events(db, mine) == ["created", "marker_added"]
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_marker WHERE removed_at IS NULL") == [(1,)]
    # 管理員帳號錄的服務（不是服務人員）不能當原始服務；空的目標、評語也不行
    db.add_service("S20261028090000", "2026-10-28 09:00:00", account_id=ADMIN2_ID)
    assert refused(db, coaching.CREATE_SQL, (ADMIN_ID, "a0000000-0000-4000-8000-0000000000fe", "S20261028090000",
                                             "g", "c", today)) == "not_staff"
    assert refused(db, coaching.CREATE_SQL, (ADMIN_ID, "a0000000-0000-4000-8000-0000000000fd", SOURCE, "  ", "c",
                                             today)) == "invalid"


def test_staff_cannot_open_other_services_through_an_action(ui, services, db):
    seed(db)
    mine = make_action(db)
    make_action(db, LEOS_ACTION, source=LEO, goal="Greet the customer by name.")
    staff, window = launch(server_home_init, ui, STAFF_ID, services)
    show(window, "server_actions_page.qml")
    model = staff.coachingModel
    wait_until(lambda: model.detailState == "ready")
    opened = []
    model.openServiceRequested.connect(lambda *args: opened.append(args))
    assert [row["actionId"] for row in model.listModel.rows()] == [mine]
    assert not model.openService(mine, LEO, 5)              # 不是這一項的服務
    assert not model.openService(LEOS_ACTION, LEO, -1)      # 別人的行動
    model.select(LEOS_ACTION)
    wait_until(lambda: model.detailState == "missing")      # 資料庫不給：當作不存在
    assert model.current == {} and not model.openService(LEOS_ACTION, LEO, -1)
    assert opened == [] and staff.mediaModel.serviceId == ""
    model.select(mine)
    wait_until(lambda: model.detailState == "ready")
    assert model.openService(mine, SOURCE, -1)
    assert opened == [(SOURCE, "2026-10-20 09:30:00", -1, "server_actions_page.qml")]
    wait_until(lambda: staff.mediaModel.serviceId == SOURCE)


def test_a_follow_up_must_be_a_later_analyzed_service_by_the_same_staff_member(ui, services, db):
    seed(db)
    action_id = make_action(db)
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    model = admin.coachingModel
    page, detail = open_coaching(admin, window)
    for bad in (EARLY, UNANALYZED, LEO, SOURCE, "S20991231000000"):  # 早的、還在分析、別人的、原始服務、不存在
        assert model.linkFollowUp(action_id, bad)
        idle(services)
        assert model.actionError == {"actionId": action_id, "op": "link",
                                     "text": coaching.problem_text("follow_up")}, bad
    assert action_row(db, action_id)[3] is None and events(db, action_id) == ["created"]
    assert visible(detail, "fieldErrorText")
    assert not model.linkFollowUp(action_id, "")  # 還沒選：不送
    assert model.actionError["text"] == "Choose a follow-up service."


def test_completing_needs_a_follow_up_and_a_conclusion_and_nothing_else_completes(ui, services, db):
    seed(db)
    action_id = make_action(db)
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    model = admin.coachingModel
    open_coaching(admin, window)
    assert model.complete(action_id, "Looked good.")  # 送出：資料庫擋下（沒有後續服務）
    idle(services)
    assert model.actionError["text"] == coaching.problem_text("no_follow_up")
    assert action_row(db, action_id)[0] == "open"
    assert not model.complete(action_id, "   ")  # 結論空的：不送
    call(db, coaching.LINK_SQL, (ADMIN_ID, action_id, 1, LATE1))
    assert refused(db, coaching.COMPLETE_SQL, (ADMIN_ID, action_id, 2, "   ")) == "invalid"
    # 員工的確認、後續服務的 AI 分數變高：都不會讓它完成
    call(db, coaching.ACKNOWLEDGE_SQL, (STAFF_ID, action_id, 2))
    db.add_scores(LATE1, customer=99, server=99)
    model.refresh()
    idle(services)
    assert action_row(db, action_id)[0] == "open" and model.current["status"] == "open"
    assert model.completedText == "0 of 1 action"


# ── 兩個管理員改同一項、重複按、交易中途失敗、連不上 ─────────────────────────────────

def test_two_admins_editing_the_same_action_get_changed_elsewhere_and_reload(ui, second_engine, services, db):
    seed(db)
    action_id = make_action(db)
    first, window = launch(admin_home_init, ui, ADMIN_ID, services)
    second, window2 = launch(admin_home_init, second_engine, ADMIN2_ID, services)
    open_coaching(first, window)
    open_coaching(second, window2)
    assert first.coachingModel.current["version"] == second.coachingModel.current["version"] == 1
    assert first.coachingModel.linkFollowUp(action_id, LATE1)
    idle(services)
    assert action_row(db, action_id)[5] == 2

    # 第二位管理員畫面上還是版本 1：被擋下，資料不變，說「changed elsewhere」、可以 Reload
    due = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
    assert second.coachingModel.adjust(action_id, "Needs more practice.", "A new goal", due)
    idle(services)
    assert second.coachingModel.actionError["text"] == coaching.problem_text("changed")
    assert ("warning", "This action changed elsewhere") in toast_titles(services)
    assert action_row(db, action_id)[1:2] == (GOAL,) and action_row(db, action_id)[5] == 2
    [toast] = [toast for toast in toasts(services.notifier) if toast["title"] == "This action changed elsewhere"]
    assert toast["actionLabel"] == "Reload"
    services.notifier.trigger(toast["toastId"])
    wait_until(lambda: second.coachingModel.current.get("version") == 2)
    assert second.coachingModel.current["followUp"]["serviceId"] == LATE1  # 看到第一位連的後續服務
    assert second.coachingModel.cancelAction(action_id, "Set for the wrong service.")
    idle(services)
    assert action_row(db, action_id)[0] == "cancelled"
    # 第一位還以為是進行中：已經結案了
    assert first.coachingModel.complete(action_id, "Done.")
    idle(services)
    assert first.coachingModel.actionError["text"] == coaching.problem_text("not_open")
    assert action_row(db, action_id)[0] == "cancelled"


def test_pressing_twice_writes_once(ui, second_engine, services, db):
    seed(db)
    action_id = make_action(db)
    staff, window = launch(server_home_init, ui, STAFF_ID, services)
    page = show(window, "server_actions_page.qml")
    model = staff.coachingModel
    wait_until(lambda: model.detailState == "ready")
    assert model.respond(action_id, "First press")
    assert not model.respond(action_id, "Second press")  # 同一項還在寫：不送
    assert action_id in model.saving
    idle(services)
    assert db.rows("SELECT body FROM tbl_coaching_event WHERE event_type = 'response'") == [("First press",)]
    detail = qml(page, "detail")
    button = find(detail, "acknowledgeButton")
    qml(button, "clicked()")
    assert qml(button, "busy")  # 忙碌的按鈕擋住第二次（BusyButton）
    assert not model.acknowledge(action_id)
    idle(services)
    assert events(db, action_id).count("acknowledged") == 1

    admin, admin_window = launch(admin_home_init, second_engine, ADMIN_ID, services)
    card = card_of(open_detail(admin, admin_window, LATE2, "2026-10-29 14:10:00"))
    # 從 Services 那條路打開的明細：照舊標 Services
    assert qml(admin_window, "servicesButton.current") and not qml(admin_window, "coachingButton.current")
    admin.coachingModel.setDraftOpen(True)
    qml(card, f'goalField.text = "{GOAL}"')
    qml(card, f'commentField.text = "{COMMENT}"')
    assert admin.coachingModel.createGoal()
    assert not admin.coachingModel.createGoal()  # 還在建立：不送第二次
    idle(services)
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_action WHERE source_service_id = %s", (LATE2,)) == [(1,)]


def test_a_failure_in_the_middle_of_creating_leaves_nothing(ui, services, db):
    seed(db)
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    model = admin.coachingModel
    card = card_of(open_detail(admin, window, SOURCE, "2026-10-20 09:30:00"))
    model.setDraftOpen(True)
    qml(card, f'goalField.text = "{GOAL}"')
    qml(card, f'commentField.text = "{COMMENT}"')
    assert model.addDraftMarker(10, "Fine")
    assert model.addDraftMarker(90000, "Past the end of any recording")  # 第二個註記會被程序拒絕（超過一天）
    assert model.createGoal()
    idle(services)
    # 同一個交易：行動、第一個註記、歷史都沒有留下
    for table in ("tbl_coaching_action", "tbl_coaching_marker", "tbl_coaching_event"):
        assert db.rows(f"SELECT COUNT(*) FROM {table}") == [(0,)], table
    assert model.createErrors["form"] == coaching.problem_text("invalid")
    assert find(card, "composerError").isVisible()
    assert model.draft["goal"] == GOAL and len(model.draft["markers"]) == 2  # 草稿留著，可以改了再送
    model.removeDraftMarker(1)
    assert model.createGoal()
    idle(services)
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_marker") == [(1,)]


def test_offline_create_keeps_the_draft_and_retry_creates_it_once(ui, services, db, mysql_server, monkeypatch):
    from myapp.services import CLOSE_DB_KEY
    from tests.integration.helpers import unused_port

    seed(db)
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    model = admin.coachingModel
    card = card_of(open_detail(admin, window, SOURCE, "2026-10-20 09:30:00"))
    model.setDraftOpen(True)
    qml(card, f'goalField.text = "{GOAL}"')
    qml(card, f'commentField.text = "{COMMENT}"')
    assert model.addDraftMarker(75, "Here")
    monkeypatch.setenv("DB_PORT", str(unused_port()))  # 資料庫斷了：下一次連線連不上
    assert services.close_db()
    wait_until(lambda: not services.tasks.isBusy(CLOSE_DB_KEY))
    qml(find(card, "createGoalButton"), "clicked()")
    wait_until(lambda: not model.creating)
    assert model.createErrors["form"] == "Can't reach the database. Check your network and try again."
    assert find(card, "composerError").isVisible() and qml(card, "goalField.text") == GOAL
    [toast] = [toast for toast in toasts(services.notifier) if toast["title"] == "Goal wasn't saved"]
    assert toast["actionLabel"] == "Retry"
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_action") == [(0,)]

    monkeypatch.setenv("DB_PORT", str(mysql_server["port"]))  # 回來了：Retry 只建一項
    services.notifier.trigger(toast["toastId"])
    wait_until(lambda: len(model.cardActions) == 1)
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_action") == [(1,)]
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_marker") == [(1,)]


def test_a_lost_reply_after_commit_is_not_created_twice(ui, services, db, monkeypatch):
    seed(db)
    real_write = coaching.write
    dropped = []

    def write_then_drop(*args):
        snapshot = real_write(*args)
        if not dropped:  # 第一次：COMMIT 成功了，但回覆在路上斷了
            dropped.append(True)
            raise mysql.connector.errors.OperationalError(msg="Lost connection to MySQL server", errno=2013)
        return snapshot

    monkeypatch.setattr(coaching, "write", write_then_drop)
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    model = admin.coachingModel
    card = card_of(open_detail(admin, window, SOURCE, "2026-10-20 09:30:00"))
    model.setDraftOpen(True)
    qml(card, f'goalField.text = "{GOAL}"')
    qml(card, f'commentField.text = "{COMMENT}"')
    assert model.addDraftMarker(75, "Here")
    assert model.createGoal()
    idle(services)
    [toast] = [toast for toast in toasts(services.notifier) if toast["title"] == "Goal wasn't saved"]
    services.notifier.trigger(toast["toastId"])  # 同一個 action_id：撞到主鍵就是已經寫好了
    wait_until(lambda: len(model.cardActions) == 1)
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_action") == [(1,)]
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_marker") == [(1,)]
    assert ("success", "Goal created") in toast_titles(services)


def test_offline_response_can_be_retried_and_is_saved_once(ui, services, db, mysql_server, monkeypatch):
    from myapp.services import CLOSE_DB_KEY
    from tests.integration.helpers import unused_port

    seed(db)
    action_id = make_action(db)
    staff, window = launch(server_home_init, ui, STAFF_ID, services)
    page = show(window, "server_actions_page.qml")
    model = staff.coachingModel
    wait_until(lambda: model.detailState == "ready")
    monkeypatch.setenv("DB_PORT", str(unused_port()))
    assert services.close_db()
    wait_until(lambda: not services.tasks.isBusy(CLOSE_DB_KEY))
    detail = qml(page, "detail")
    qml(detail, 'responseField.text = "Context the admin should know."')
    qml(find(detail, "sendResponseButton"), "clicked()")
    idle(services)
    assert model.actionError["text"] == "Can't reach the database. Check your network and try again."
    assert qml(detail, "responseField.text") == "Context the admin should know."  # 打的字留著
    [toast] = [toast for toast in toasts(services.notifier) if toast["title"] == "Change wasn't saved"]
    monkeypatch.setenv("DB_PORT", str(mysql_server["port"]))
    services.notifier.trigger(toast["toastId"])
    wait_until(lambda: ("success", "Response sent") in toast_titles(services))
    assert db.rows("SELECT body FROM tbl_coaching_event WHERE event_type = 'response'") == [
        ("Context the admin should know.",)]
    assert qml(detail, "responseField.text") == ""


# ── 時間註記：拿掉留歷史；換掉後續服務時，它上面的註記一起拿掉 ──────────────────────────

def test_markers_belong_to_one_video_and_removing_them_keeps_history(ui, services, db):
    seed(db)
    action_id = make_action(db)
    marker = call(db, coaching.ADD_MARKER_SQL, (ADMIN_ID, action_id, SOURCE, 75, "First"))[0][0]["marker_id"]
    call(db, coaching.LINK_SQL, (ADMIN_ID, action_id, 1, LATE1))
    call(db, coaching.ADD_MARKER_SQL, (ADMIN_ID, action_id, LATE1, 75, "Same second, other video"))
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    model = admin.coachingModel
    page, detail = open_coaching(admin, window)
    # 兩部影片的 01:15 是不同的兩秒：各自屬於自己的服務
    assert [(m["serviceId"], m["clock"]) for m in model.current["source"]["markers"]] == [(SOURCE, "01:15")]
    assert [(m["serviceId"], m["clock"]) for m in model.current["followUp"]["markers"]] == [(LATE1, "01:15")]
    assert refused(db, coaching.ADD_MARKER_SQL, (ADMIN_ID, action_id, LATE2, 5, "")) == "marker_service"

    remove = visible(find(detail, "sourceBlock"), "removeMarkerButton")[0]
    qml(remove, "clicked()")
    wait_until(lambda: model.current["source"]["markers"] == [])
    assert db.rows("SELECT removed_by FROM tbl_coaching_marker WHERE marker_id = %s", (marker,)) == [(ADMIN_ID,)]
    assert events(db, action_id)[-1] == "marker_removed"
    assert model.current["history"][-1]["title"] == f"Alex Wang removed the marker at 01:15 on {SOURCE}"

    # 換掉後續服務：舊的那部影片上的註記一起拿掉（各留一列歷史）
    qml(find(detail, "changeFollowUpButton"), "clicked()")
    wait_until(lambda: model.followUpState == "ready")
    qml(find(detail, "followUpSelect"), f'picked("{LATE2}")')
    qml(find(detail, "linkFollowUpButton"), "clicked()")
    wait_until(lambda: model.current.get("followUp", {}).get("serviceId") == LATE2)
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_marker WHERE removed_at IS NULL") == [(0,)]
    assert events(db, action_id)[-2:] == ["follow_up_linked", "marker_removed"]
    titles = [entry["title"] for entry in model.current["history"]]
    assert f"Alex Wang changed the follow-up to {LATE2} · Oct 29, 14:10" in titles


def test_cancelling_keeps_the_goal_and_its_history(ui, second_engine, services, db):
    seed(db)
    action_id = make_action(db)
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    model = admin.coachingModel
    page, detail = open_coaching(admin, window)
    qml(find(detail, "cancelGoalButton"), "clicked()")
    dialog = qml(detail, "dialog")
    wait_until(lambda: qml(dialog, "opened"))
    assert qml(dialog, "title") == "Cancel Goal"
    qml(dialog, 'reasonField.text = "Set for the wrong staff member."')
    qml(dialog, "submitButton.clicked()")
    wait_until(lambda: not qml(dialog, "visible"))
    assert action_row(db, action_id)[0] == "cancelled" and action_row(db, action_id)[4] == "Set for the wrong staff member."
    assert events(db, action_id) == ["created", "cancelled"]
    assert model.completedText == "No actions yet"  # 撤回的不算在比例裡
    [item] = visible(page, "actionItem")
    assert qml(item, "chipState") == "cancelled"
    staff, staff_window = launch(server_home_init, second_engine, STAFF_ID, services)
    staff_page = show(staff_window, "server_actions_page.qml")
    wait_until(lambda: staff.coachingModel.detailState == "ready")
    staff_detail = qml(staff_page, "detail")
    assert find(staff_detail, "detailChip").property("label") == "Cancelled"
    assert find(staff_detail, "conclusionText").property("text") == "Set for the wrong staff member."


# ── 兩個指標、篩選 ───────────────────────────────────────────────────────────

def insert_action(db, action_id, staff, source, status, created_days_ago, concluded_days_ago=None, due_days=7,
                  follow_up=None):
    """直接寫一項（控制時間），和程序寫的欄位一樣。"""
    concluded = status != "open"
    db.execute(
        "INSERT INTO tbl_coaching_action (action_id, source_service_id, staff_account_id, created_by, goal, "
        "manager_comment, due_date, action_status, follow_up_service_id, conclusion, concluded_by, concluded_at, "
        "created_at) VALUES (%s, %s, %s, %s, %s, 'Why', CURDATE() + INTERVAL %s DAY, %s, %s, %s, %s, "
        "IF(%s, NOW(3) - INTERVAL %s DAY, NULL), NOW(3) - INTERVAL %s DAY)",
        (action_id, source, staff, ADMIN_ID, f"Goal {action_id[-2:]}", due_days, status, follow_up,
         "Done." if concluded else None, ADMIN_ID if concluded else None, concluded, concluded_days_ago or 0,
         created_days_ago))


def test_metrics_show_completion_with_follow_up_and_median_review_time_and_filters_work(ui, services, db):
    seed(db)
    base = "a0000000-0000-4000-8000-0000000000"
    db.add_service("S20261031090000", "2026-10-31 09:00:00", account_id=OTHER_ID)  # Leo 後來的一筆
    insert_action(db, base + "01", STAFF_ID, SOURCE, "completed", 20, 17, follow_up=LATE1)     # 3 天
    insert_action(db, base + "02", STAFF_ID, SOURCE, "completed", 30, 21, follow_up=LATE2)     # 9 天
    insert_action(db, base + "03", OTHER_ID, LEO, "completed", 15, 3, follow_up="S20261031090000")  # 12 天
    insert_action(db, base + "04", OTHER_ID, LEO, "open", 5, due_days=-2)                      # 過期
    insert_action(db, base + "05", STAFF_ID, SOURCE, "cancelled", 4, 1)                        # 撤回：不算
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    model = admin.coachingModel
    page, detail = open_coaching(admin, window)
    assert (model.completedShare, model.completedText) == ("75%", "3 of 4 actions")
    assert (model.reviewTimeText, model.reviewTimeNote) == ("9 days", "Median, goal to completion · 3 completed")
    assert (model.openCountText, model.openNote) == ("1", "1 overdue")
    assert find(find(page, "completedMetric"), "metricValue").property("text") == "75%"
    assert model.countText == "5 actions"

    qml(find(page, "statusFilter"), 'picked("completed")')
    assert sorted(row["actionId"][-2:] for row in model.listModel.rows()) == ["01", "02", "03"]
    assert model.countText == "3 of 5 actions"
    qml(find(page, "statusFilter"), 'picked("all")')
    qml(find(page, "dueFilter"), 'picked("overdue")')
    assert [row["actionId"][-2:] for row in model.listModel.rows()] == ["04"]
    assert [row["chipState"] for row in model.listModel.rows()] == ["overdue"]
    qml(find(page, "dueFilter"), 'picked("any")')
    qml(find(page, "staffFilter"), f'picked("{OTHER_ID}")')
    assert sorted(row["actionId"][-2:] for row in model.listModel.rows()) == ["03", "04"]
    # 指標跟著員工的篩選：Leo 一項完成、一項進行中
    assert (model.completedShare, model.completedText, model.reviewTimeText) == ("50%", "1 of 2 actions", "12 days")
    assert [option["text"] for option in model.staffOptions] == ["All staff", "Leo Park", "Mia Chen"]
    qml(find(page, "statusFilter"), 'picked("cancelled")')
    assert model.listModel.rows() == [] and find(page, "noMatches").isVisible()


# ── 重新登入、換帳號：資料在資料庫，換人看到的是自己的 ─────────────────────────────────

def test_goals_stay_after_log_out_and_each_account_sees_its_own(engine, db, qml_warnings):
    from pathlib import Path

    from PySide6.QtCore import QCoreApplication, QEvent, QUrl

    from myapp import main
    from myapp.main import MainWindow
    from myapp.passwords import hash_password
    from myapp.services import CLOSE_DB_KEY

    seed(db)
    db.execute("UPDATE tbl_account SET account_password = %s WHERE account_id = %s",
               (hash_password("CHANGE-ME-leo-0000"), OTHER_ID))
    action_id = make_action(db)
    controller = MainWindow(engine)
    engine.load(QUrl.fromLocalFile(str(Path(main.__file__).parent / "qml" / "pages" / "login.qml")))
    login = engine.rootObjects()[0]

    def log_in(username, password):
        form = qml(login, "stackView.currentItem")
        qml(form, "username_textfield").setProperty("text", username)
        qml(form, "password_textfield").setProperty("text", password)
        qml(form, "loginButton.clicked()")
        wait_until(lambda: not login.isVisible() and len(engine.rootObjects()) > 1)
        wait_until(lambda: not controller.tasks._running)
        return engine.rootObjects()[-1]

    def log_out(shell):
        qml(shell, "logoutButton.click()")
        dialog = shell.findChild(QObject, "logoutDialog")
        wait_until(lambda: qml(dialog, "opened"))
        qml(dialog.findChild(QObject, "confirmButton"), "clicked()")
        wait_until(login.isVisible)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        wait_until(lambda: len(engine.rootObjects()) == 1)

    def listed(shell, page):
        qml(shell, f'showPageNow("{page}")')
        model = engine.rootContext().contextProperty("coachingModel")
        wait_until(lambda: model.listState in ("ready", "empty") and not controller.tasks._running)
        return [row["goal"] for row in model.listModel.rows()]

    try:
        shell = log_in("placeholder_staff", "CHANGE-ME-staff-0000")
        assert listed(shell, "server_actions_page.qml") == [GOAL]
        log_out(shell)
        shell = log_in("leo", "CHANGE-ME-leo-0000")
        assert listed(shell, "server_actions_page.qml") == []  # 換人：看不到 Mia 的
        log_out(shell)
        shell = log_in("placeholder_admin", "CHANGE-ME-admin-0000")
        assert listed(shell, "admin_coaching_page.qml") == [GOAL]
        log_out(shell)
        shell = log_in("placeholder_staff", "CHANGE-ME-staff-0000")  # 再登入：還在，確認過的也還在
        assert listed(shell, "server_actions_page.qml") == [GOAL]
        assert action_row(db, action_id)[0] == "open"
    finally:
        if controller.services.close_db():
            wait_until(lambda: not controller.tasks.isBusy(CLOSE_DB_KEY))
        controller.tasks.shutdown()


# ── 有資料的每一種畫面：零 QML 警告 ───────────────────────────────────────────

@pytest.mark.parametrize("side", ["staff", "admin"])
def test_coaching_screens_with_data_have_no_qml_warnings(ui, services, db, side):
    seed(db)
    first = make_action(db)
    call(db, coaching.ADD_MARKER_SQL, (ADMIN_ID, first, SOURCE, 75, "Here"))
    call(db, coaching.RESPOND_SQL, (STAFF_ID, first, "Context"))
    call(db, coaching.LINK_SQL, (ADMIN_ID, first, 1, LATE1))
    call(db, coaching.ADJUST_SQL, (ADMIN_ID, first, 2, "More practice", "A clearer goal", date.today()))
    second = make_action(db, "a0000000-0000-4000-8000-000000000002", due=date.today() - timedelta(days=1))
    call(db, coaching.LINK_SQL, (ADMIN_ID, second, 1, LATE2))
    call(db, coaching.COMPLETE_SQL, (ADMIN_ID, second, 2, "Did it."))
    module = admin_home_init if side == "admin" else server_home_init
    home, window = launch(module, ui, ADMIN_ID if side == "admin" else STAFF_ID, services)
    page = show(window, "admin_coaching_page.qml" if side == "admin" else "server_actions_page.qml")
    model = home.coachingModel
    wait_until(lambda: model.detailState == "ready")
    for action_id in (second, first):
        model.select(action_id)
        wait_until(lambda: model.detailState == "ready" and model.selectedId == action_id)
        idle(services)
    if side == "admin":
        detail = qml(page, "detail")
        for mode in ("complete", "adjust", "cancel"):
            dialog = qml(detail, "dialog")
            qml(dialog, f'ask("{mode}")')
            wait_until(lambda: qml(dialog, "opened"))
            qml(dialog, "close()")
            wait_until(lambda: not qml(dialog, "visible"))
        qml(find(page, "staffFilter"), "popup.open()")
        card = card_of(open_detail(home, window, SOURCE, "2026-10-20 09:30:00"))
        qml(find(card, "newGoalButton"), "clicked()")
        assert model.addDraftMarker(3, "Draft")
    else:
        # 服務人員的明細頁沒有輔導卡
        detail_page = open_detail(home, window, SOURCE, "2026-10-20 09:30:00")
        assert not qml(find(detail_page, "coachingCardLoader"), "active")
    wait_until(lambda: not any(key.startswith("coaching") for key in services.tasks._running))
