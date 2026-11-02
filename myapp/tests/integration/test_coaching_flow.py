"""員工輔導與後續追蹤（PLAN1 W2，T45；畫面依設計稿改版，T47）：真的 MySQL（migration 006 的 13 個 sp_coaching_* 程序）
＋兩端的畫面。主要的流程用真的滑鼠點（CLAUDE.md：只呼叫 picked()、clicked() 抓不到疊放、停用、看不到的問題）。

- 管理員在明細頁影片旁邊的輔導面板：Add Moment（播放器上）→ 說明欄 → Save Moment；送出時還開著的說明欄先存成時間註記
  （舊版會默默丟掉，T47 第 1 節）→ Goal sent to Mia → Open in Coaching。服務人員在 My Actions 看到、Got It、在對話裡回應。
- Coaching 頁一次一個下一步：還沒有後續服務時等，Check Again、重新打開頁面都會重查（舊版查一次就停在灰的選單，
  T47 第 2 節）→ Use as Follow-up → Watch（面板對準這一項，時間註記加在後續服務自己的影片上）→ Mark Complete 或 Keep Practicing。
- 權限在資料庫：服務人員看不到、寫不了別人的目標，也改不了目標、期限、結論；後續服務要是同一位員工、後來、分析完成的。
- 兩個管理員改同一項：後到的是「changed elsewhere」＋ Reload；重複按只寫一次；連不上、交易中途失敗什麼都不留。
- 每個測試都要求 QML 零警告，GUI 執行緒碰網路就失敗（no_network_on_gui_thread）。
"""
from datetime import date, timedelta

import mysql.connector
import pytest
from PySide6.QtCore import QCoreApplication, QObject, QPointF, Qt
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

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
LATER = "S20261030120000"        # Mia：之後才分析完成的一筆（測試裡才加）
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


def widen(window, width=1440, height=900):
    """和截圖一樣的視窗大小（離線的測試視窗是 800×800）。"""
    window.showNormal()
    window.setGeometry(0, 0, width, height)
    wait_until(lambda: window.width() == width)


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


def text(root, name):
    return find(root, name).property("text")


def toast_titles(services):
    return [(toast["severity"], toast["title"]) for toast in toasts(services.notifier)]


def scroll_into_view(item):
    """item 在可以捲動的區域裡、沒有整個露出來時，從裡到外把每一層 Flickable 捲到它在中間。"""
    parent = item.parentItem()
    while parent is not None:
        if parent.inherits("QQuickFlickable"):
            top = item.mapToItem(parent, QPointF(0, 0)).y()
            if not (0 <= top and top + item.height() <= parent.height()):
                lowest = parent.property("originY")
                highest = lowest + max(0.0, parent.property("contentHeight") - parent.height())
                wanted = parent.property("contentY") + top - (parent.height() - item.height()) / 2
                parent.setProperty("contentY", min(highest, max(lowest, wanted)))
        parent = parent.parentItem()


def click(window, item):
    """像使用者一樣用滑鼠按：先捲到看得到，按在中間。按不到（看不到、停用）的不按。
    剛長出來的內容（例如剛查到的後續服務）要等版面排好，捲動的範圍才對：一直捲到它整個在視窗裡。
    兩個視窗（兩端）的測試：先讓這個視窗在前面，打字才會進到它的輸入框。"""
    assert item.isVisible() and item.property("enabled"), item.objectName()
    if not window.isActive():
        window.requestActivate()
        wait_until(window.isActive)

    def in_view():
        top = item.mapToScene(QPointF(0, 0)).y()
        return 0 <= top and top + item.height() <= window.height()

    def scrolled():
        scroll_into_view(item)
        QCoreApplication.processEvents()  # 排版面：位置可能又變了，再看一次
        return in_view()

    wait_until(scrolled, describe=lambda: f"{item.objectName()} at {item.mapToScene(QPointF(0, 0)).y()}")
    center = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)


def type_text(window, value):
    """像使用者一樣打字，送到有焦點的輸入框（QTest.keyClicks 只收 QWidget）。"""
    for char in value:
        QTest.sendKeyEvent(QTest.KeyAction.Click, window, Qt.Key.Key_unknown, char, Qt.KeyboardModifier.NoModifier)


def open_detail(home, window, service_id, start):
    """和 Services 的 Details 一樣：先換頁，再等明細查好。"""
    page = qml(window, f'openServiceDetail("{service_id}", "{start}")')
    wait_until(lambda: home.mediaModel.state == "ready" and not home.mediaModel.pending)
    return page


def panel_of(page):
    loader = find(page, "coachingCardLoader")
    wait_until(lambda: qml(loader, "status === Loader.Ready"))
    panel = qml(loader, "item")
    wait_until(lambda: qml(panel, "cardState") == "ready")
    return panel


def open_coaching(home, window):
    """換到 Coaching，等清單、明細和能選的後續服務都讀好（後續服務晚一點才出現，會把下面的內容往下推）。"""
    page = show(window, "admin_coaching_page.qml")
    wait_until(lambda: home.coachingModel.detailState == "ready")
    idle(home.coachingModel.services)
    return page, qml(page, "detail")


def open_actions(home, window):
    page = show(window, "server_actions_page.qml")
    wait_until(lambda: home.coachingModel.detailState == "ready")
    idle(home.coachingModel.services)
    return page, qml(page, "detail")


# ── 影片：真的（很小的）影片，才能播、能 seek（Add Moment 要影片能播）─────────────────────────

def small_video(path, seconds=6, fps=10):
    """一段很短的 MJPG AVI（OpenCV 寫；測試換掉的是 VideoCapture，VideoWriter 是真的）。"""
    import cv2
    import numpy as np

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (64, 48))
    for frame in range(seconds * fps):
        writer.write(np.full((48, 64, 3), (frame * 4) % 255, dtype=np.uint8))
    writer.release()
    return path.read_bytes()


def add_video(db, s3, tmp_path, service_id):
    data = small_video(tmp_path / f"{service_id}.avi")
    for name in (f"{service_id}_cam0.avi", f"{service_id}_cam1.avi"):
        s3.put_object(Bucket="adam-test", Key=f"video/{name}", Body=data)
        db.add_media(service_id, "video", f"s3://adam-test/video/{name}")


def seek(page, ms):
    """把影片拉到第 ms 毫秒（和拖進度條一樣，三個檔案一起跳），等播放器真的到了。"""
    qml(page, f"seekTo({ms})")
    wait_until(lambda: abs(qml(page, "playbackMs") - ms) <= 150, timeout=10,
               describe=lambda: f"position {qml(page, 'playbackMs')}")


def moment_clocks(root, name="panelMoment"):
    return [qml(row, "modelData.clock") for row in visible(root, name)]


# ── 主要流程：管理員在影片旁邊標時間註記、送出目標 → 服務人員看到 ─────────────────────────────

def test_admin_marks_moments_beside_the_video_and_sends_a_goal(ui, second_engine, services, db, s3, tmp_path):
    seed(db)
    add_video(db, s3, tmp_path, SOURCE)
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    widen(window)
    page = open_detail(admin, window, SOURCE, "2026-10-20 09:30:00")
    wait_until(lambda: admin.mediaModel.canPlay, timeout=10)
    panel = panel_of(page)
    model = admin.coachingModel

    # 寬的版面：面板在影片旁邊（同一列、右邊），標時間註記時影片一直看得到
    media_card = find(page, "mediaCard")
    wait_until(lambda: qml(page, "panelBeside"))
    wait_until(lambda: panel.mapToScene(QPointF(0, 0)).x() > media_card.mapToScene(QPointF(0, 0)).x()
               + media_card.width())
    assert abs(panel.mapToScene(QPointF(0, 0)).y() - media_card.mapToScene(QPointF(0, 0)).y()) < 1
    assert 380 <= panel.width() <= 460
    assert (text(panel, "panelTitle"), text(panel, "panelSubtitle")) == ("Coach Mia", "Mark moments, then set one goal.")
    assert find(panel, "noPanelMoments").isVisible() and not visible(page, "timelinePin")

    # Add Moment at 00:01：說明欄打開、焦點在裡面；Save Moment 馬上出現在清單，進度條下面多一個點
    seek(page, 1500)
    add = find(page, "addMomentButton")
    assert add.property("text") == "Add Moment at 00:01"
    click(window, add)
    wait_until(lambda: find(panel, "momentEditor").isVisible())
    note = find(panel, "momentNote")
    wait_until(lambda: note.property("activeFocus"))
    assert text(panel, "momentEditorLabel") == "Note for 00:01"
    assert not add.property("enabled")  # 說明欄開著：先存或取消
    assert len(visible(page, "timelinePin")) == 1
    type_text(window, "Explained the fee, not the next step")
    click(window, find(panel, "saveMomentButton"))
    wait_until(lambda: not find(panel, "momentEditor").isVisible())
    assert moment_clocks(panel) == ["00:01"]
    assert text(find(panel, "panelMoment"), "panelMomentNote") == "Explained the fee, not the next step"
    assert len(visible(page, "timelinePin")) == 1 and add.property("enabled")
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_marker") == [(0,)]  # 草稿：送出時才寫

    # 第二個：在 00:03 打開說明欄、打了字，沒按 Save 就把影片拉走。說明欄的時間是按下去的那一秒
    seek(page, 3500)
    click(window, add)
    wait_until(lambda: find(panel, "momentNote").property("activeFocus"))
    type_text(window, "Customer asks again what to bring")
    seek(page, 5200)
    assert add.property("text") == "Add Moment at 00:05"
    assert text(panel, "momentEditorLabel") == "Note for 00:03"
    assert text(panel, "attachText") == "2 moments will be attached."

    # 換頁再回來：草稿、打開的說明欄和打的字都還在（存在 coachingModel，每一筆服務一份）
    show(window, "admin_service_page.qml")
    page = open_detail(admin, window, SOURCE, "2026-10-20 09:30:00")
    wait_until(lambda: admin.mediaModel.canPlay, timeout=10)
    panel = panel_of(page)
    assert moment_clocks(panel) == ["00:01"] and find(panel, "momentEditor").isVisible()
    assert qml(panel, "noteField.text") == "Customer asks again what to bring"

    # 欄位空的就按 Send：欄位錯誤，但開著的說明先存成一個時間註記，不會默默丟掉（舊版會，T47 第 1 節）
    click(window, find(panel, "sendGoalButton"))
    assert model.createErrors["goal"] == "Enter the goal the staff member should practise."
    assert model.createErrors["comment"] == "Explain why you set this goal."
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_action") == [(0,)]
    assert not find(panel, "momentEditor").isVisible() and moment_clocks(panel) == ["00:01", "00:03"]

    # 第三個也是開著就送出；這次欄位都填好了
    qml(panel, f'goalField.text = "{GOAL}"')
    qml(panel, f'commentField.text = "{COMMENT}"')
    assert model.createErrors["goal"] == ""  # 改了那一格就拿掉錯誤
    seek(page, 4500)
    click(window, find(page, "addMomentButton"))
    wait_until(lambda: find(panel, "momentNote").property("activeFocus"))
    type_text(window, "Says thanks and leaves")
    assert text(panel, "attachText") == "3 moments will be attached."
    click(window, find(panel, "sendGoalButton"))
    wait_until(lambda: find(panel, "sentBox").isVisible())
    [created] = db.rows("SELECT action_id, staff_account_id, created_by, goal, manager_comment, action_status "
                        "FROM tbl_coaching_action")
    action_id = created[0]
    assert created[1:] == (STAFF_ID, ADMIN_ID, GOAL, COMMENT, "open")
    assert db.rows("SELECT service_id, offset_seconds, note FROM tbl_coaching_marker ORDER BY offset_seconds") == [
        (SOURCE, 1, "Explained the fee, not the next step"), (SOURCE, 3, "Customer asks again what to bring"),
        (SOURCE, 4, "Says thanks and leaves")]
    assert events(db, action_id) == ["created", "marker_added", "marker_added", "marker_added"]
    assert text(panel, "sentTitle") == "Goal sent to Mia"
    assert text(panel, "sentText") == "3 moments attached. Mia will see it in My Actions."
    assert ("success", "Goal sent") not in toast_titles(services)  # 面板上就寫了，不再跳通知
    assert not find(page, "addMomentButton").property("enabled") and not visible(panel, "panelMoment")
    [other] = visible(panel, "otherGoal")  # 這筆服務的目標：小方塊
    assert (text(other, "otherRelation"), text(other, "otherMoments")) == ("Example", "3 moments in this video")

    # Open in Coaching：換到 Coaching、選這一項；時間註記照服務分組，按一下回到那一秒
    click(window, find(panel, "sentOpenButton"))
    wait_until(lambda: qml(window, "currentPage") == "admin_coaching_page.qml")
    coaching_page = qml(window, "mainContentLoader.item")
    wait_until(lambda: model.detailState == "ready" and model.selectedId == action_id)
    detail = qml(coaching_page, "detail")
    assert model.tab == "open"
    [group] = visible(detail, "momentGroup")
    assert text(group, "momentGroupLabel") == "Example · Oct 20"
    assert [qml(chip, "marker.clock") for chip in visible(group, "momentChip")] == ["00:01", "00:03", "00:04"]
    assert text(detail, "detailGoal") == GOAL and text(detail, "detailComment") == COMMENT

    # 服務人員：My Actions 有新的目標、Watch the example 一個時間註記一列
    staff, staff_window = launch(server_home_init, second_engine, STAFF_ID, services)
    staff_page, staff_detail = open_actions(staff, staff_window)
    assert find(staff_detail, "newGoalBanner").isVisible()
    assert text(staff_detail, "newGoalTitle") == "New goal from Alex Wang"
    [card] = visible(staff_detail, "watchCard")
    assert text(card, "watchKicker") == "Watch the example"
    assert text(card, "watchService") == f"Oct 20, 09:30 · {SOURCE}"
    assert moment_clocks(card, "momentRow") == ["00:01", "00:03", "00:04"]
    [item] = visible(staff_page, "actionItem")
    assert text(item, "coachingChipLabel") == "New"


def test_a_moment_opens_the_service_and_seeks_its_video_to_that_second(ui, services, db, s3, tmp_path):
    seed(db)
    action_id = make_action(db)
    call(db, coaching.ADD_MARKER_SQL, (ADMIN_ID, action_id, SOURCE, 4, "Here"))
    add_video(db, s3, tmp_path, SOURCE)
    staff, window = launch(server_home_init, ui, STAFF_ID, services)
    page, detail = open_actions(staff, window)
    [row] = visible(detail, "momentRow")
    assert qml(row, "Accessible.name") == "Play from 00:04: Here"
    click(window, row)
    wait_until(lambda: qml(window, "currentPage") == "server_service_detail_page.qml")
    detail_page = qml(window, "mainContentLoader.item")
    assert qml(window, "pageValue('headerBackText', '')") == "My Actions"
    # 側邊欄標的是 My Actions（和「‹」一致），不是 Services；服務人員的明細頁沒有輔導面板、播放器照舊一列
    assert qml(window, "actionsButton.current") and not qml(window, "servicesButton.current")
    assert not qml(find(detail_page, "coachingCardLoader"), "active") and not find(detail_page, "addMomentButton").isVisible()
    wait_until(lambda: staff.mediaModel.canPlay, timeout=10)
    wait_until(lambda: qml(detail_page, "pendingSeekMs") == -1, timeout=10)   # 影片載入了：跳過去
    wait_until(lambda: 3500 <= qml(detail_page, "playbackMs") <= 4500, timeout=10,
               describe=lambda: f"position {qml(detail_page, 'playbackMs')}")
    qml(detail_page, "goBack()")
    assert qml(window, "currentPage") == "server_actions_page.qml"


# ── 已經有的目標：從 Coaching 按 Watch，面板對準這一項，時間註記加在後續服務自己的影片上 ─────────────

def test_a_moment_added_to_an_existing_goal_shows_on_the_panel_in_coaching_and_in_my_actions(
        ui, second_engine, services, db, s3, tmp_path):
    seed(db)
    action_id = make_action(db)
    call(db, coaching.ADD_MARKER_SQL, (ADMIN_ID, action_id, SOURCE, 135, "Explained the fee"))
    call(db, coaching.LINK_SQL, (ADMIN_ID, action_id, 1, LATE1))
    add_video(db, s3, tmp_path, LATE1)
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    model = admin.coachingModel
    page, detail = open_coaching(admin, window)
    next_step = find(detail, "nextStep")
    assert text(next_step, "nextStepTitle") == "Watch the follow-up, then decide"
    click(window, find(next_step, "watchFollowUpButton"))
    wait_until(lambda: qml(window, "currentPage") == "admin_service_detail_page.qml")
    assert qml(window, "pageValue('headerBackText', '')") == "Coaching"
    assert qml(window, "coachingButton.current") and not qml(window, "servicesButton.current")
    detail_page = qml(window, "mainContentLoader.item")
    wait_until(lambda: admin.mediaModel.serviceId == LATE1 and admin.mediaModel.canPlay, timeout=10)
    panel = panel_of(detail_page)

    # 面板對準這一項：加到哪一項一看就知道，沒有新目標的表單
    assert model.cardFocusId == action_id
    assert find(panel, "focusGoal").isVisible() and text(panel, "focusGoalText") == GOAL
    assert text(panel, "panelSubtitle") == "Mark moments in this follow-up, then review it in Coaching."
    assert not find(panel, "goalForm").isVisible() and not visible(panel, "panelMoment")
    seek(detail_page, 2500)
    click(window, find(detail_page, "addMomentButton"))
    wait_until(lambda: find(panel, "momentNote").property("activeFocus"))
    assert text(panel, "momentEditorLabel") == "Note for 00:02 · adds to this goal"
    type_text(window, "Asked the customer to repeat the next step")
    click(window, find(panel, "saveMomentButton"))
    wait_until(lambda: moment_clocks(panel) == ["00:02"])  # 寫好了就在面板上
    assert not find(panel, "momentEditor").isVisible()
    assert db.rows("SELECT service_id, offset_seconds, note FROM tbl_coaching_marker WHERE removed_at IS NULL "
                   "ORDER BY marker_id") == [(SOURCE, 135, "Explained the fee"),
                                             (LATE1, 2, "Asked the customer to repeat the next step")]
    assert len(visible(detail_page, "timelinePin")) == 1

    # Coaching：Follow-up 那一組有它；Example 的還在
    click(window, find(panel, "focusOpenButton"))
    wait_until(lambda: qml(window, "currentPage") == "admin_coaching_page.qml")
    page = qml(window, "mainContentLoader.item")
    wait_until(lambda: model.detailState == "ready" and model.selectedId == action_id)
    detail = qml(page, "detail")
    groups = visible(detail, "momentGroup")
    assert [text(group, "momentGroupLabel") for group in groups] == ["Example · Oct 20", "Follow-up · Oct 26"]
    assert [qml(chip, "marker.clock") for chip in visible(groups[1], "momentChip")] == ["00:02"]

    # 服務人員：My Actions 的「Later service」那一張有它
    staff, staff_window = launch(server_home_init, second_engine, STAFF_ID, services)
    staff_page, staff_detail = open_actions(staff, staff_window)
    cards = visible(staff_detail, "watchCard")
    assert [text(card, "watchKicker") for card in cards] == ["Watch the example", "Later service"]
    assert moment_clocks(cards[1], "momentRow") == ["00:02"]


# ── 後續服務：還沒有就等；Check Again、重新打開頁面都會重查 → Use as Follow-up → Mark Complete ─────────

def test_follow_up_waits_then_check_again_finds_a_later_service_and_the_goal_is_completed(
        ui, second_engine, services, db):
    seed(db)
    action_id = make_action(db, source=LATE2)  # 最晚的一筆：還沒有更晚、分析完成的
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    model = admin.coachingModel
    page, detail = open_coaching(admin, window)
    next_step = find(detail, "nextStep")
    wait_until(lambda: qml(next_step, "step") == "waiting")
    assert text(next_step, "nextStepTitle") == "Waiting for a later service"
    assert text(next_step, "nextStepText") == ("When Mia records a new service and it's analyzed, "
                                                "you can pick it here.")
    [item] = visible(page, "actionItem")
    assert text(item, "coachingChipLabel") == "Waiting for a Later Service"

    # 之後分析完成了一筆：Check Again 找得到（舊版查一次就停在灰的選單，T47 第 2 節）
    db.add_service(LATER, "2026-10-30 12:00:00", status=3)
    db.add_scores(LATER, customer=80, server=86.5)
    click(window, find(next_step, "checkAgainButton"))
    wait_until(lambda: qml(next_step, "step") == "pick")
    assert text(next_step, "nextStepTitle") == "Pick a later service to check this goal"
    [row] = visible(next_step, "candidateRow")
    assert qml(find(row, "candidateScore"), "Accessible.name") == "Staff 86.5 Good"
    assert text(item, "coachingChipLabel") == "Pick a Follow-up"  # 清單的標籤跟著最新的結果
    click(window, find(row, "useFollowUpButton"))
    wait_until(lambda: qml(next_step, "step") == "review")
    assert action_row(db, action_id)[3:6] == (LATER, None, 2)
    assert text(item, "coachingChipLabel") == "Ready to Review"
    assert [qml(step, "modelData.state") for step in visible(detail, "progressStep")] == [
        "done", "current", "done", "todo"]  # Mia 還沒回應

    # Mark Complete：結論必填，寫在按鈕上面，對話框不關
    click(window, find(next_step, "markCompleteButton"))
    dialog = qml(detail, "dialog")
    wait_until(lambda: qml(dialog, "opened"))
    assert qml(dialog, "title") == "Mark Complete"
    click(window, qml(dialog, "submitButton"))
    assert model.actionError["text"] == "Write what you saw in the follow-up."
    assert find(qml(dialog, "contentItem"), "dialogError").isVisible() and action_row(db, action_id)[0] == "open"
    conclusion = "Explained the next step and asked the customer to repeat it."
    qml(dialog, f'reasonField.text = "{conclusion}"')
    click(window, qml(dialog, "submitButton"))
    wait_until(lambda: not qml(dialog, "visible"))
    assert action_row(db, action_id)[0] == "completed" and action_row(db, action_id)[4] == conclusion
    assert ("success", "Goal completed") in toast_titles(services)
    # 選到的那一項換到 Completed；Next step 是結論
    wait_until(lambda: model.tab == "completed")
    assert qml(next_step, "step") == "completed" and text(next_step, "nextStepTitle") == conclusion
    assert text(next_step, "nextStepText").startswith("Follow-up: Oct 30, 12:00\nCompleted by Alex Wang · ")
    assert (model.completedShare, model.completedOf, model.openCountText) == ("100%", "1 of 1", "0")
    assert not find(detail, "editGoalButton").isVisible() and not find(detail, "composerRow").isVisible()

    # 服務人員：完成的方塊，不能再回應
    staff, staff_window = launch(server_home_init, second_engine, STAFF_ID, services)
    staff_page, staff_detail = open_actions(staff, staff_window)
    assert find(staff_detail, "closedBanner").isVisible()
    assert text(staff_detail, "closedTitle").startswith("Completed ")
    assert text(staff_detail, "conclusionText") == conclusion
    assert not find(staff_detail, "composerRow").isVisible() and not find(staff_detail, "newGoalBanner").isVisible()
    assert refused(db, coaching.RESPOND_SQL, (STAFF_ID, action_id, "Thanks")) == "not_open"


def test_reopening_coaching_checks_the_follow_up_options_again(ui, services, db):
    seed(db)
    make_action(db, source=LATE2)
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    model = admin.coachingModel
    page, detail = open_coaching(admin, window)
    wait_until(lambda: model.followUpState == "empty")
    db.add_service(LATER, "2026-10-30 12:00:00", status=3)  # 之後分析完成了一筆（沒有分數）
    show(window, "admin_service_page.qml")
    page, detail = open_coaching(admin, window)  # 再打開 Coaching：重查，不必按 Check Again
    next_step = find(detail, "nextStep")
    wait_until(lambda: qml(next_step, "step") == "pick")
    assert [option["value"] for option in model.followUpOptions] == [LATER]
    [row] = visible(next_step, "candidateRow")
    assert qml(find(row, "candidateScore"), "Accessible.name") == "No staff score"


# ── 對話：兩端都能寫；自己的在右邊 ───────────────────────────────────────────────

def test_admin_and_staff_talk_in_the_conversation(ui, second_engine, services, db):
    seed(db)
    action_id = make_action(db)
    staff, staff_window = launch(server_home_init, second_engine, STAFF_ID, services)
    staff_page, staff_detail = open_actions(staff, staff_window)
    talk = qml(staff_detail, "conversation")
    composer = find(talk, "composerField")
    assert composer.property("placeholderText") == "Reply to Alex…"
    assert [text(entry, "eventText") for entry in visible(talk, "conversationEvent")][0].startswith(
        "Alex set the goal · ")

    # 空的不送：欄位下面說明
    click(staff_window, find(talk, "sendMessageButton"))
    assert staff.coachingModel.actionError["text"] == "Write a message first."
    assert find(talk, "conversationError").isVisible()
    # 打字、Return 送出：出現在右邊（自己的），欄位清空，不跳通知
    click(staff_window, composer)
    reply = "I was rushed because the queue was long. I'll ask them to repeat the next step."
    type_text(staff_window, reply)
    QTest.keyClick(staff_window, Qt.Key.Key_Return)
    wait_until(lambda: len(visible(talk, "conversationMessage")) == 1)
    [mine] = visible(talk, "conversationMessage")
    assert text(mine, "bubbleText") == reply and text(mine, "bubbleAuthor").startswith("You · ")
    bubble = find(mine, "bubble")
    assert bubble.mapToItem(mine, QPointF(0, 0)).x() + bubble.width() == pytest.approx(mine.width(), abs=1)
    assert composer.property("text") == "" and not find(talk, "conversationError").isVisible()
    assert not [title for title in toast_titles(services) if title[0] == "success"]

    # 管理員：Mia 的訊息在左邊，寫回去（按 Send）
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    page, detail = open_coaching(admin, window)
    admin_talk = qml(detail, "conversation")
    [theirs] = visible(admin_talk, "conversationMessage")
    assert text(theirs, "bubbleAuthor").startswith("Mia Chen · ")
    assert find(theirs, "bubble").mapToItem(theirs, QPointF(0, 0)).x() == 0
    assert text(visible(admin_talk, "conversationEvent")[0], "eventText").startswith("You set the goal · ")
    assert find(admin_talk, "composerField").property("placeholderText") == "Write to Mia…"
    click(window, find(admin_talk, "composerField"))
    type_text(window, "Good plan. Ask them to say it back in their own words.")
    click(window, find(admin_talk, "sendMessageButton"))
    wait_until(lambda: len(visible(admin_talk, "conversationMessage")) == 2)
    assert db.rows("SELECT author_id, author_role, body FROM tbl_coaching_event WHERE event_type = 'response' "
                   "ORDER BY event_id") == [(STAFF_ID, "staff", reply),
                                            (ADMIN_ID, "admin", "Good plan. Ask them to say it back in their own words.")]
    # 管理員的步驟：Mia replied 做完了
    assert [qml(step, "modelData.state") for step in visible(detail, "progressStep")] == [
        "done", "done", "current", "todo"]

    # 服務人員重讀：管理員的訊息在左邊，寫的是管理員的名字
    staff.coachingModel.refresh()
    wait_until(lambda: len(visible(talk, "conversationMessage")) == 2)
    assert text(visible(talk, "conversationMessage")[1], "bubbleAuthor").startswith("Alex Wang · ")
    assert action_row(db, action_id)[0] == "open"


# ── Got It：只代表看過了，不是完成 ─────────────────────────────────────────────────

def test_staff_presses_got_it_on_the_new_goal_banner(ui, second_engine, services, db):
    seed(db)
    action_id = make_action(db)
    staff, window = launch(server_home_init, ui, STAFF_ID, services)
    page, detail = open_actions(staff, window)
    banner = find(detail, "newGoalBanner")
    assert banner.isVisible() and text(detail, "detailFrom").startswith("From Alex Wang · Due ")
    [item] = visible(page, "actionItem")
    assert text(item, "coachingChipLabel") == "New"
    assert [(qml(step, "modelData.label"), qml(step, "modelData.state")) for step in visible(detail, "progressStep")] == [
        ("Goal set", "done"), ("You replied", "current"), ("Later service", "todo"), ("Review", "todo")]
    click(window, find(banner, "gotItButton"))
    wait_until(lambda: not banner.isVisible())
    assert action_row(db, action_id)[0] == "open" and action_row(db, action_id)[6] == 1
    assert events(db, action_id) == ["created", "acknowledged"]
    assert text(visible(detail, "conversationEvent")[-1], "eventText").startswith("You read the goal · ")
    assert text(item, "coachingChipLabel") == "In Progress"
    assert not [title for title in toast_titles(services) if title[0] == "success"]  # 畫面上就看得到

    admin, admin_window = launch(admin_home_init, second_engine, ADMIN_ID, services)
    admin_page, admin_detail = open_coaching(admin, admin_window)
    assert [qml(step, "modelData.label") for step in visible(admin_detail, "progressStep")][1] == "Mia replied"
    assert qml(visible(admin_detail, "progressStep")[1], "modelData.state") == "done"
    assert text(visible(admin_detail, "conversationEvent")[-1], "eventText").startswith("Mia read the goal · ")


# ── Keep Practicing、Edit Goal：舊的值留在歷史，下一次要選更晚的 ──────────────────────────────

def test_keep_practicing_keeps_the_history_and_the_next_follow_up_must_be_later(ui, second_engine, services, db):
    seed(db)
    action_id = make_action(db)
    call(db, coaching.ACKNOWLEDGE_SQL, (STAFF_ID, action_id, 1))
    call(db, coaching.LINK_SQL, (ADMIN_ID, action_id, 1, LATE1))
    call(db, coaching.ADD_MARKER_SQL, (ADMIN_ID, action_id, LATE1, 40, "Explained, but didn't check"))
    old_due = action_row(db, action_id)[2]
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    model = admin.coachingModel
    page, detail = open_coaching(admin, window)
    next_step = find(detail, "nextStep")

    click(window, find(next_step, "keepPracticingButton"))
    dialog = qml(detail, "dialog")
    wait_until(lambda: qml(dialog, "opened"))
    assert qml(dialog, "title") == "Keep Practicing" and qml(dialog, "goalField.text") == GOAL  # 預先填目前的目標
    click(window, qml(dialog, "submitButton"))
    assert model.actionError["text"] == "Write what still needs practice."
    new_goal = "Ask the customer to repeat the next step before they leave."
    due = date.today() + timedelta(days=21)
    reason = "Explained the next step but didn't check the customer understood."
    qml(dialog, f'reasonField.text = "{reason}"')
    qml(dialog, f'goalField.text = "{new_goal}"')
    qml(dialog, f'dueField.text = "{due:%Y-%m-%d}"')
    click(window, qml(dialog, "submitButton"))
    wait_until(lambda: not qml(dialog, "visible"))
    assert action_row(db, action_id) == ("open", new_goal, due, None, None, 3, 0)  # 後續服務拿掉、要再確認
    assert db.rows("SELECT goal_before, goal_after, due_before, due_after, service_id, body FROM tbl_coaching_event "
                   "WHERE event_type = 'adjusted'") == [(GOAL, new_goal, old_due, due, LATE1, reason)]
    assert ("success", "Goal updated") in toast_titles(services)

    # 看過的後續服務和它的時間註記留著；下一次只能選更晚的；步驟從「Mia replied」重新算
    current = model.current
    assert current["goal"] == new_goal and current["followUp"] == {}
    assert [(block["relation"], block["serviceId"]) for block in current["services"]] == [
        ("source", SOURCE), ("reviewed", LATE1)]
    assert [text(group, "momentGroupLabel") for group in visible(detail, "momentGroup")] == ["Reviewed · Oct 26"]
    wait_until(lambda: qml(next_step, "step") == "pick")
    assert [option["value"] for option in model.followUpOptions] == [LATE2]
    assert refused(db, coaching.LINK_SQL, (ADMIN_ID, action_id, 3, LATE1)) == "follow_up"
    assert [qml(step, "modelData.state") for step in visible(detail, "progressStep")] == [
        "done", "current", "todo", "todo"]
    adjusted = [entry for entry in current["conversation"] if entry["text"].startswith("You asked for more practice")]
    assert adjusted[0]["text"] == "You asked for more practice after the Oct 26 service"
    assert adjusted[0]["lines"] == [reason, f"New goal: {new_goal}",
                                    f"Follow up by {coaching.short_day(due, date.today())}"]
    # 員工之前的確認是對舊的目標：畫面上的版本不對就是 changed
    assert refused(db, coaching.ACKNOWLEDGE_SQL, (STAFF_ID, action_id, 1)) == "changed"

    # Edit Goal（標題旁邊）：改目標、寫原因；沒有後續服務時，說明不提後續服務
    click(window, find(detail, "editGoalButton"))
    wait_until(lambda: qml(dialog, "opened") and qml(dialog, "mode") == "edit")
    assert text(qml(dialog, "contentItem"), "dialogExplanation") == (
        "Change the goal or the date, and say why. Mia confirms the new goal again.")
    click(window, qml(dialog, "submitButton"))
    assert model.actionError["text"] == "Write why you're changing the goal."
    qml(dialog, 'reasonField.text = "Clearer wording."')
    qml(dialog, 'goalField.text = "Ask the customer to repeat the next step."')
    click(window, qml(dialog, "submitButton"))
    wait_until(lambda: not qml(dialog, "visible"))
    assert action_row(db, action_id)[1] == "Ask the customer to repeat the next step."
    assert model.current["conversation"][-1]["text"] == "You edited the goal"

    # 服務人員：新的目標又是「New」，要再按 Got It
    staff, staff_window = launch(server_home_init, second_engine, STAFF_ID, services)
    staff_page, staff_detail = open_actions(staff, staff_window)
    [item] = visible(staff_page, "actionItem")
    assert text(item, "coachingChipLabel") == "New"
    assert text(staff_detail, "detailGoal") == "Ask the customer to repeat the next step."
    assert find(staff_detail, "newGoalBanner").isVisible()
    cards = visible(staff_detail, "watchCard")
    assert [text(card, "watchKicker") for card in cards] == ["Watch the example", "Reviewed service"]


# ── 權限：資料庫決定（程序帶呼叫者），不靠畫面 ────────────────────────────────────

LEOS_ACTION = "a0000000-0000-4000-8000-0000000000aa"


def test_staff_can_only_read_and_answer_their_own_actions_and_cannot_change_them(db):
    seed(db)
    mine = make_action(db)
    marker = call(db, coaching.ADD_MARKER_SQL, (ADMIN_ID, mine, SOURCE, 30, "Here"))[0][0]["marker_id"]
    leos = make_action(db, LEOS_ACTION, source=LEO, goal="Greet the customer by name.")
    # 清單、明細、明細頁的面板：只有自己的
    assert [row["action_id"] for row in call(db, coaching.LIST_SQL, (STAFF_ID,))[0]] == [mine]
    assert [row["action_id"] for row in call(db, coaching.LIST_SQL, (OTHER_ID,))[0]] == [leos]
    assert {row["action_id"] for row in call(db, coaching.LIST_SQL, (ADMIN_ID,))[0]} == {mine, leos}
    assert call(db, coaching.LIST_SQL, ("no-such-account",))[0] == []
    assert call(db, coaching.DETAIL_SQL, (OTHER_ID, mine))[:3] == [[], [], []]
    assert call(db, coaching.DETAIL_SQL, ("no-such-account", mine))[:3] == [[], [], []]
    assert call(db, coaching.FOR_SERVICE_SQL, (OTHER_ID, SOURCE))[:2] == [[], []]
    assert call(db, coaching.OPTIONS_SQL, (STAFF_ID, mine))[0] == []
    # 別人的目標：當作不存在
    assert refused(db, coaching.RESPOND_SQL, (OTHER_ID, mine, "Not mine")) == "not_found"
    assert refused(db, coaching.ACKNOWLEDGE_SQL, (OTHER_ID, mine, 1)) == "not_found"
    # 只有管理員能做的：服務人員（就算是自己的目標）一律 forbidden
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
    # 目標、期限、結論、時間註記都沒變，歷史只有建立和加時間註記
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
    open_actions(staff, window)
    model = staff.coachingModel
    opened = []
    model.openServiceRequested.connect(lambda *args: opened.append(args))
    assert [row["actionId"] for row in model.listModel.rows()] == [mine]
    assert not model.openService(mine, LEO, 5)              # 不是這一項的服務
    assert not model.openService(LEOS_ACTION, LEO, -1)      # 別人的目標
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
    # 能選的：同一位員工、比原始服務晚、分析完成的，新的在前（早的、分析中的、Leo 的都不在）
    wait_until(lambda: model.followUpState == "ready")
    assert [option["value"] for option in model.followUpOptions] == [LATE2, LATE1]
    assert [qml(row, "modelData.whenText") for row in visible(find(detail, "nextStep"), "candidateRow")] == [
        "Oct 29, 14:10", "Oct 26, 10:15"]
    for bad in (EARLY, UNANALYZED, LEO, SOURCE, "S20991231000000"):  # 早的、還在分析、別人的、原始服務、不存在
        assert model.linkFollowUp(action_id, bad)
        idle(services)
        assert model.actionError == {"actionId": action_id, "op": "link",
                                     "text": coaching.problem_text("follow_up")}, bad
    assert action_row(db, action_id)[3] is None and events(db, action_id) == ["created"]
    assert find(find(detail, "nextStep"), "linkError").isVisible()
    assert not model.linkFollowUp(action_id, "")  # 還沒選：不送
    assert model.actionError["text"] == "Choose a follow-up service."


def test_a_long_list_of_later_services_shows_the_newest_five_first(ui, services, db):
    seed(db)
    make_action(db)
    for day in range(1, 6):  # 再多 5 筆比原始服務晚、分析完成的：一共 7 筆
        db.add_service(f"S202611{day:02d}090000", f"2026-11-{day:02d} 09:00:00", status=3)
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    page, detail = open_coaching(admin, window)
    next_step = find(detail, "nextStep")
    assert qml(next_step, "step") == "pick" and len(admin.coachingModel.followUpOptions) == 7
    assert [qml(row, "modelData.value") for row in visible(next_step, "candidateRow")] == [
        f"S202611{day:02d}090000" for day in range(5, 0, -1)]
    more = find(next_step, "showAllCandidatesButton")
    assert more.property("text") == "Show All 7"
    click(window, more)
    wait_until(lambda: len(visible(next_step, "candidateRow")) == 7)
    assert not more.isVisible()


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
    assert (model.completedShare, model.completedOf) == ("0%", "0 of 1")


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
    assert ("warning", "This goal changed elsewhere") in toast_titles(services)
    assert action_row(db, action_id)[1:2] == (GOAL,) and action_row(db, action_id)[5] == 2
    [toast] = [toast for toast in toasts(services.notifier) if toast["title"] == "This goal changed elsewhere"]
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
    page, detail = open_actions(staff, window)
    model = staff.coachingModel
    assert model.respond(action_id, "First press")
    assert not model.respond(action_id, "Second press")  # 同一項還在寫：不送
    assert action_id in model.saving
    idle(services)
    assert db.rows("SELECT body FROM tbl_coaching_event WHERE event_type = 'response'") == [("First press",)]
    button = find(detail, "gotItButton")
    qml(button, "clicked()")
    assert qml(button, "busy")  # 忙碌的按鈕擋住第二次（BusyButton）
    assert not model.acknowledge(action_id)
    idle(services)
    assert events(db, action_id).count("acknowledged") == 1

    admin, admin_window = launch(admin_home_init, second_engine, ADMIN_ID, services)
    panel = panel_of(open_detail(admin, admin_window, LATE2, "2026-10-29 14:10:00"))
    # 從 Services 那條路打開的明細：照舊標 Services，面板是新目標
    assert qml(admin_window, "servicesButton.current") and not qml(admin_window, "coachingButton.current")
    assert admin.coachingModel.cardFocusId == "" and find(panel, "goalForm").isVisible()
    qml(panel, f'goalField.text = "{GOAL}"')
    qml(panel, f'commentField.text = "{COMMENT}"')
    assert admin.coachingModel.createGoal()
    assert not admin.coachingModel.createGoal()  # 還在建立：不送第二次
    idle(services)
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_action WHERE source_service_id = %s", (LATE2,)) == [(1,)]


def draft_moment(model, seconds, note):
    """和按 Add Moment、打字、Save Moment 一樣（沒有影片的測試直接呼叫）。"""
    assert model.startMoment(seconds)
    model.updatePending(note)
    assert model.savePending()


def test_a_failure_in_the_middle_of_creating_leaves_nothing(ui, services, db):
    seed(db)
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    model = admin.coachingModel
    panel = panel_of(open_detail(admin, window, SOURCE, "2026-10-20 09:30:00"))
    qml(panel, f'goalField.text = "{GOAL}"')
    qml(panel, f'commentField.text = "{COMMENT}"')
    draft_moment(model, 10, "Fine")
    draft_moment(model, 90000, "Past the end of any recording")  # 第二個時間註記會被程序拒絕（超過一天）
    assert model.createGoal()
    idle(services)
    # 同一個交易：目標、第一個時間註記、歷史都沒有留下
    for table in ("tbl_coaching_action", "tbl_coaching_marker", "tbl_coaching_event"):
        assert db.rows(f"SELECT COUNT(*) FROM {table}") == [(0,)], table
    assert model.createErrors["form"] == coaching.problem_text("invalid")
    assert find(panel, "composerError").isVisible()
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
    panel = panel_of(open_detail(admin, window, SOURCE, "2026-10-20 09:30:00"))
    qml(panel, f'goalField.text = "{GOAL}"')
    qml(panel, f'commentField.text = "{COMMENT}"')
    draft_moment(model, 75, "Here")
    monkeypatch.setenv("DB_PORT", str(unused_port()))  # 資料庫斷了：下一次連線連不上
    assert services.close_db()
    wait_until(lambda: not services.tasks.isBusy(CLOSE_DB_KEY))
    click(window, find(panel, "sendGoalButton"))
    wait_until(lambda: not model.creating)
    assert model.createErrors["form"] == "Can't reach the database. Check your network and try again."
    assert find(panel, "composerError").isVisible() and qml(panel, "goalField.text") == GOAL
    [toast] = [toast for toast in toasts(services.notifier) if toast["title"] == "Goal wasn't sent"]
    assert toast["actionLabel"] == "Retry"
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_action") == [(0,)]

    monkeypatch.setenv("DB_PORT", str(mysql_server["port"]))  # 回來了：Retry 只建一項
    services.notifier.trigger(toast["toastId"])
    wait_until(lambda: len(model.cardActions) == 1)
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_action") == [(1,)]
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_marker") == [(1,)]
    assert find(panel, "sentBox").isVisible()


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
    panel = panel_of(open_detail(admin, window, SOURCE, "2026-10-20 09:30:00"))
    qml(panel, f'goalField.text = "{GOAL}"')
    qml(panel, f'commentField.text = "{COMMENT}"')
    draft_moment(model, 75, "Here")
    assert model.createGoal()
    idle(services)
    [toast] = [toast for toast in toasts(services.notifier) if toast["title"] == "Goal wasn't sent"]
    services.notifier.trigger(toast["toastId"])  # 同一個 action_id：撞到主鍵就是已經寫好了
    wait_until(lambda: len(model.cardActions) == 1)
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_action") == [(1,)]
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_marker") == [(1,)]
    assert find(panel, "sentBox").isVisible() and model.draft["sent"]["count"] == 1


def test_offline_message_can_be_retried_and_is_saved_once(ui, services, db, mysql_server, monkeypatch):
    from myapp.services import CLOSE_DB_KEY
    from tests.integration.helpers import unused_port

    seed(db)
    make_action(db)
    staff, window = launch(server_home_init, ui, STAFF_ID, services)
    page, detail = open_actions(staff, window)
    model = staff.coachingModel
    monkeypatch.setenv("DB_PORT", str(unused_port()))
    assert services.close_db()
    wait_until(lambda: not services.tasks.isBusy(CLOSE_DB_KEY))
    talk = qml(detail, "conversation")
    qml(talk, 'field.text = "Context the admin should know."')
    click(window, find(talk, "sendMessageButton"))
    idle(services)
    assert model.actionError["text"] == "Can't reach the database. Check your network and try again."
    assert qml(talk, "field.text") == "Context the admin should know."  # 打的字留著
    assert find(talk, "conversationError").isVisible()
    [toast] = [toast for toast in toasts(services.notifier) if toast["title"] == "Change wasn't saved"]
    monkeypatch.setenv("DB_PORT", str(mysql_server["port"]))
    services.notifier.trigger(toast["toastId"])
    wait_until(lambda: qml(talk, "field.text") == "")
    assert db.rows("SELECT body FROM tbl_coaching_event WHERE event_type = 'response'") == [
        ("Context the admin should know.",)]
    assert len(visible(talk, "conversationMessage")) == 1


# ── 時間註記：屬於一部影片；拿掉留歷史；換掉後續服務時，它上面的時間註記一起拿掉 ─────────────────

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
    blocks = {block["relation"]: block for block in model.current["services"]}
    assert [(m["serviceId"], m["clock"]) for m in blocks["source"]["markers"]] == [(SOURCE, "01:15")]
    assert [(m["serviceId"], m["clock"]) for m in blocks["follow_up"]["markers"]] == [(LATE1, "01:15")]
    assert refused(db, coaching.ADD_MARKER_SQL, (ADMIN_ID, action_id, LATE2, 5, "")) == "marker_service"

    # 按 Example 那一組的時間註記：打開原始服務、面板對準這一項，可以在那裡拿掉（✕）
    [chip] = [chip for chip in visible(detail, "momentChip") if qml(chip, "serviceId") == SOURCE]
    click(window, chip)
    wait_until(lambda: qml(window, "currentPage") == "admin_service_detail_page.qml")
    detail_page = qml(window, "mainContentLoader.item")
    assert qml(detail_page, "pendingSeekMs") == 75000  # 沒有影片：一直等著，不會跳到別的地方
    panel = panel_of(detail_page)
    assert model.cardFocusId == action_id
    assert text(panel, "panelSubtitle") == "Mark more moments for this goal."
    [row] = visible(panel, "panelMoment")
    click(window, find(row, "panelRemoveMoment"))
    wait_until(lambda: not visible(panel, "panelMoment"))
    assert db.rows("SELECT removed_by FROM tbl_coaching_marker WHERE marker_id = %s", (marker,)) == [(ADMIN_ID,)]
    assert events(db, action_id)[-1] == "marker_removed"
    # 時間註記的加、拿掉不放進對話（在 Moments 裡看得到）
    assert not [entry for entry in model.cardActions if entry["markers"]]

    # 換掉後續服務（Change）：舊的那部影片上的時間註記一起拿掉（各留一列歷史）
    page, detail = open_coaching(admin, window)
    next_step = find(detail, "nextStep")
    click(window, find(next_step, "changeFollowUpButton"))
    wait_until(lambda: qml(next_step, "step") == "pick")
    assert text(next_step, "nextStepTitle") == "Pick a different follow-up"
    rows = visible(next_step, "candidateRow")
    assert [qml(row, "modelData.value") for row in rows] == [LATE2, LATE1]
    assert not find(rows[1], "useFollowUpButton").isVisible()  # 目前的那一筆：寫「Current follow-up」
    click(window, find(rows[0], "useFollowUpButton"))
    wait_until(lambda: model.current.get("followUp", {}).get("serviceId") == LATE2)
    assert qml(next_step, "step") == "review"
    assert db.rows("SELECT COUNT(*) FROM tbl_coaching_marker WHERE removed_at IS NULL") == [(0,)]
    assert events(db, action_id)[-2:] == ["follow_up_linked", "marker_removed"]
    assert model.current["conversation"][-1]["text"] == "You changed the follow-up to the Oct 29 service"


def test_cancelling_keeps_the_goal_and_its_history(ui, second_engine, services, db):
    seed(db)
    action_id = make_action(db)
    admin, window = launch(admin_home_init, ui, ADMIN_ID, services)
    model = admin.coachingModel
    page, detail = open_coaching(admin, window)
    assert find(page, "cancelledTab").isVisible() is False  # 沒有撤回的：沒有這個分頁
    click(window, find(detail, "cancelGoalButton"))
    dialog = qml(detail, "dialog")
    wait_until(lambda: qml(dialog, "opened"))
    assert qml(dialog, "title") == "Cancel Goal"
    qml(dialog, 'reasonField.text = "Set for the wrong staff member."')
    click(window, qml(dialog, "submitButton"))
    wait_until(lambda: not qml(dialog, "visible"))
    assert action_row(db, action_id)[0] == "cancelled" and action_row(db, action_id)[4] == "Set for the wrong staff member."
    assert events(db, action_id) == ["created", "cancelled"]
    assert model.completedShare == "—" and model.completedOf == ""  # 撤回的不算在比例裡
    wait_until(lambda: model.tab == "cancelled")
    assert find(page, "cancelledTab").isVisible() and qml(find(page, "cancelledTab"), "text") == "Cancelled · 1"
    [item] = visible(page, "actionItem")
    assert qml(item, "chipState") == "cancelled"
    assert qml(find(detail, "nextStep"), "step") == "cancelled"
    assert not find(detail, "progressRow").isVisible()
    staff, staff_window = launch(server_home_init, second_engine, STAFF_ID, services)
    staff_page, staff_detail = open_actions(staff, staff_window)
    assert text(staff_detail, "closedTitle").startswith("Cancelled ")
    assert text(staff_detail, "conclusionText") == "Set for the wrong staff member."


# ── 兩個指標、分頁 ─────────────────────────────────────────────────────────────

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


def test_metrics_show_completion_with_follow_up_and_median_review_time_and_tabs_split_the_list(ui, services, db):
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
    assert (model.completedShare, model.completedOf) == ("75%", "3 of 4")
    assert (model.reviewTimeText, model.openCountText, model.overdueCount) == ("9 days", "1", 1)
    assert text(find(page, "completedMetric"), "metricValue") == "75%"
    assert text(find(page, "completedMetric"), "metricNote") == "· 3 of 4"
    assert text(find(page, "openMetric"), "metricNote") == "· 1 overdue"

    # 分頁：Open（過期的那一項，紅字）、Completed、Cancelled（有撤回的才有）
    assert [qml(find(page, name), "text") for name in ("openTab", "completedTab", "cancelledTab")] == [
        "Open · 1", "Completed · 3", "Cancelled · 1"]
    [item] = visible(page, "actionItem")
    assert (qml(item, "actionId")[-2:], text(item, "coachingChipLabel"), qml(item, "dueAlert")) == ("04", "Overdue", True)
    assert text(item, "actionStaff") == "Leo Park" and text(item, "actionDue").startswith("Due ")
    click(window, find(page, "completedTab"))
    wait_until(lambda: model.tab == "completed")
    assert sorted(row["actionId"][-2:] for row in model.listModel.rows()) == ["01", "02", "03"]
    wait_until(lambda: model.selectedId[-2:] in ("01", "02", "03") and model.detailState == "ready")  # 換分頁就選它的第一項
    assert all(row["dueText"].startswith("Done ") for row in model.listModel.rows())
    click(window, find(page, "cancelledTab"))
    wait_until(lambda: model.tab == "cancelled" and model.selectedId[-2:] == "05")
    click(window, find(page, "openTab"))
    wait_until(lambda: model.tab == "open" and model.selectedId[-2:] == "04")


# ── 重新登入、換帳號：資料在資料庫，換人看到的是自己的 ─────────────────────────────────

def test_goals_stay_after_log_out_and_each_account_sees_its_own(engine, db, qml_warnings):
    from pathlib import Path

    from PySide6.QtCore import QEvent, QUrl

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
        shell = log_in("placeholder_staff", "CHANGE-ME-staff-0000")  # 再登入：還在
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
    call(db, coaching.RESPOND_SQL, (ADMIN_ID, first, "Thanks"))
    call(db, coaching.LINK_SQL, (ADMIN_ID, first, 1, LATE1))
    call(db, coaching.ADJUST_SQL, (ADMIN_ID, first, 2, "More practice", "A clearer goal", date.today()))
    second = make_action(db, "a0000000-0000-4000-8000-000000000002", due=date.today() - timedelta(days=1))
    call(db, coaching.LINK_SQL, (ADMIN_ID, second, 1, LATE2))
    call(db, coaching.COMPLETE_SQL, (ADMIN_ID, second, 2, "Did it."))
    third = make_action(db, "a0000000-0000-4000-8000-000000000003")
    call(db, coaching.CANCEL_SQL, (ADMIN_ID, third, 1, "Wrong person"))
    module = admin_home_init if side == "admin" else server_home_init
    home, window = launch(module, ui, ADMIN_ID if side == "admin" else STAFF_ID, services)
    widen(window, 1024, 768)
    page = show(window, "admin_coaching_page.qml" if side == "admin" else "server_actions_page.qml")
    model = home.coachingModel
    wait_until(lambda: model.detailState == "ready")
    for action_id in (second, third, first):
        model.select(action_id)
        wait_until(lambda: model.detailState == "ready" and model.selectedId == action_id)
        idle(services)
    if side == "admin":
        detail = qml(page, "detail")
        for mode in ("complete", "keep", "edit", "cancel"):
            dialog = qml(detail, "dialog")
            qml(dialog, f'ask("{mode}")')
            wait_until(lambda: qml(dialog, "opened"))
            qml(dialog, "close()")
            wait_until(lambda: not qml(dialog, "visible"))
        panel = panel_of(open_detail(home, window, SOURCE, "2026-10-20 09:30:00"))
        assert model.startMoment(3)  # 新目標的說明欄開著
        model.updatePending("Draft")
        assert len(visible(panel, "otherGoal")) == 3
        assert model.focusGoal(first) is False  # 說明欄開著：先存或取消
        model.cancelPending()
        assert model.focusGoal(first)  # 對準一項
        wait_until(lambda: find(panel, "focusGoal").isVisible())
        assert model.startMoment(5)
        model.cancelPending()
        assert model.focusGoal("")
    else:
        # 服務人員的明細頁沒有輔導面板
        detail_page = open_detail(home, window, SOURCE, "2026-10-20 09:30:00")
        assert not qml(find(detail_page, "coachingCardLoader"), "active")
    wait_until(lambda: not any(key.startswith("coaching") for key in services.tasks._running))
