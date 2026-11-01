"""管理員端（步驟 2-4、4-1～4-5；前端重建 F2）：首頁、趨勢、排名、搜尋、帳號管理接真的 MySQL 8.4。

資料庫的工作都在 db lane（services fixture 的 TaskRunner＋DbLane），no_network_on_gui_thread 確認 GUI 執行緒不碰網路。
結果放在屬性裡，等待一律用 wait_until。畫面上的回饋（toast、欄位錯誤、空狀態）在 test_admin_feedback.py。
"""
import calendar
from datetime import date

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from myapp.init import admin_home_init
from myapp.init.admin_home_init import AdminHome, RankingModel, ServiceManager, ServiceScoreManager, TrendDataManager
from myapp.passwords import verify_password
from tests.integration.helpers import ADMIN_ID, STAFF_ID, STAFF_NAME, qml, table_row, wait_until

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def gui_thread_stays_off_the_network(db, no_network_on_gui_thread):
    """db fixture 自己的連線（放測試資料用）開好之後，才開始禁止 GUI 執行緒碰網路。"""


def loaded(section):
    """等這個區塊載入完：不是 loading（或還沒搜尋過的 idle），也不在重新整理。"""
    wait_until(lambda: section.state not in ("loading", "idle") and not section.refreshing)


def freeze_lane_clock(services, moment):
    """讓 db lane 那條連線的 NOW()、CURDATE() 停在 moment（MySQL 的 SET TIMESTAMP，只影響這條連線）。"""
    done = []

    def work(token, report):
        with services.db.cursor() as cursor:
            cursor.execute("SET TIMESTAMP = UNIX_TIMESTAMP(%s)", (moment,))

    services.tasks.submit("db", "test:clock", work, on_ok=done.append)
    wait_until(lambda: done)


def test_trend_has_points_only_on_days_with_scores(db, services):
    db.add_service("S20261001100000", "2026-10-01 10:00:00")
    db.add_scores("S20261001100000", customer=80, server=70)
    db.add_service("S20261031233000", "2026-10-31 23:30:00")  # 月底最後一天的晚上
    db.add_scores("S20261031233000", customer=-1, server=90)  # 顧客分數沒有資料
    db.add_service("S20261031120000", "2026-10-31 12:00:00", status=2)  # 分數寫好了、但還沒完成：不算
    db.add_scores("S20261031120000", customer=0, server=0)
    db.add_service("S20261101000000", "2026-11-01 00:00:00")  # 下個月：不算
    db.add_scores("S20261101000000", customer=10, server=10)

    manager = TrendDataManager(services)
    assert manager.load(10, 2026)
    loaded(manager)
    staff = {point["x"]: point["y"] for point in manager.staffPoints}
    customer = {point["x"]: point["y"] for point in manager.customerPoints}
    assert staff == {1: 70.0, 31: 90.0}  # 沒有資料的日子沒有點，不是 0（UX-30）
    assert customer == {1: 80.0}  # 31 日的顧客分數是 -1：那天沒有顧客的點
    assert (manager.state, manager.daysInMonth) == ("ready", 31)


def test_admin_dashboard_averages_all_staff_and_skips_missing_scores(db, services):
    db.add_service("S20261030090000", "2026-10-30 09:00:00")
    db.add_scores("S20261030090000", customer=80, server=70, customer_text=-1)
    db.add_service("S20261030100000", "2026-10-30 10:00:00", account_id=ADMIN_ID)  # 所有人的服務都算
    db.add_scores("S20261030100000", customer=60, server=90)
    db.add_service("S20261027100000", "2026-10-27 10:00:00")  # 3 天前：只算進本週
    db.add_scores("S20261027100000", customer=100, server=100)
    db.add_service("S20261030110000", "2026-10-30 11:00:00", status=2)  # 還在分析：不算
    db.add_scores("S20261030110000", customer=0, server=0)

    freeze_lane_clock(services, "2026-10-30 18:00:00")
    manager = ServiceScoreManager(services)
    manager.refresh()
    loaded(manager)
    assert (manager.dailyCount, manager.weeklyCount) == (2, 3)
    assert manager.dailyCustomerScores == {"total": 70.0, "text": 60.0, "audio": 70.0, "facial": 70.0}
    assert manager.weeklyStaffScores["total"] == pytest.approx((70 + 90 + 100) / 3)
    assert (manager.dailyState, manager.weeklyState) == ("ready", "ready")


def test_ranking_averages_each_staff_member_in_sql(db, services):
    db.add_service("S20261030090000", "2026-10-30 09:00:00")
    db.add_scores("S20261030090000", customer=80, server=-1)
    db.add_service("S20261030100000", "2026-10-30 10:00:00")
    db.add_scores("S20261030100000", customer=60, server=90)
    db.add_service("S20261030110000", "2026-10-30 11:00:00", status=2)  # 還沒分析完：不算
    db.add_scores("S20261030110000", customer=0, server=0)

    model = RankingModel(services)
    model.refresh()
    loaded(model)
    assert model.topCustomer == [{"name": STAFF_NAME, "score": 70.0}]
    assert model.topStaff == [{"name": STAFF_NAME, "score": 90.0}]  # -1 不算進平均
    assert (model.state, model.rankedCount) == ("ready", 1)


def test_search_filters_by_staff_name_and_covers_whole_end_day(db, services):
    db.add_account("00000000-0000-0000-0000-000000000003", "wang", "王小明")
    db.add_service("S20261031235900", "2026-10-31 23:59:00")  # 結束日的最後一分鐘
    db.add_service("S20261031100000", "2026-10-31 10:00:00", account_id="00000000-0000-0000-0000-000000000003")
    db.add_service("S20261101000000", "2026-11-01 00:00:00")
    manager = ServiceManager(services)
    manager.loadNames()
    wait_until(lambda: manager.namesState == "ready")
    assert sorted(manager.names) == sorted([STAFF_NAME, "王小明"])  # 下拉選單只列服務人員（等級 1）
    assert manager.search(STAFF_NAME, "2026-10-31", "2026-10-31")
    loaded(manager)
    assert [row["service_id"] for row in manager.results] == ["S20261031235900"]
    assert table_row(manager.resultsModel, 0) == {
        "service_id": "S20261031235900", "name": STAFF_NAME, "start_datetime": "2026-10-31 23:59:00"}


@pytest.fixture
def account_results(db, services):
    """管理員（等級 5）的 AccountManager，以及它送出的 (成功與否, 訊息)。"""
    home = AdminHome(ADMIN_ID, services)  # AccountManager 是它的子物件：留著 home，子物件才不會跟著被刪掉
    home.profile.load()
    wait_until(lambda: home.profile.state == "ready")
    manager = home.accountManager
    assert manager.currentLevel == 5
    results = []
    manager.accountAdded.connect(lambda ok, message: results.append((ok, message)))
    manager.accountUpdated.connect(lambda ok, message: results.append((ok, message)))
    yield manager, results
    del home


def test_new_account_is_stored_hashed_and_can_log_in(db, account_results):
    manager, results = account_results
    assert manager.addAccount("new_staff", "S3cret-pass", "新同事", "1", "服務人員")
    wait_until(lambda: results)
    assert results == [(True, "Account added")]
    stored, level, job_title = db.rows("SELECT account_password, account_level, account_occupation_name "
                                       "FROM tbl_account WHERE account_username = 'new_staff'")[0]
    assert stored.startswith("pbkdf2_sha256$")  # 不存明碼
    assert verify_password("S3cret-pass", stored)  # 登入時 sp_get_login 取回雜湊，用同一個函式比對
    assert (level, job_title) == ("1", "服務人員")


def test_updated_password_replaces_old_one(db, account_results):
    manager, results = account_results
    assert manager.updateAccount(STAFF_ID, "placeholder_staff", "New-pass-2026", STAFF_NAME, "1", "服務人員")
    wait_until(lambda: results)
    assert results == [(True, "Account updated")]
    stored = db.rows("SELECT account_password FROM tbl_account WHERE account_id = %s", (STAFF_ID,))[0][0]
    assert verify_password("New-pass-2026", stored)
    assert not verify_password("CHANGE-ME-staff-0000", stored)


def test_duplicate_username_is_reported_on_the_username_field(db, account_results):
    manager, results = account_results
    manager.addAccount("placeholder_staff", "pw", "重複", "1", "服務人員")
    wait_until(lambda: results)
    ok, message = results[0]
    assert not ok
    assert manager.usernameError == message == "Username placeholder_staff is already taken. Choose another one."
    assert db.rows("SELECT COUNT(*) FROM tbl_account")[0][0] == 2


def test_trend_page_starts_at_current_month_and_redraws_without_stacking(engine, db, services):
    today = date.today()
    first, second = today.replace(day=1), today.replace(day=2)
    db.add_service(f"S{first:%Y%m%d}100000", f"{first} 10:00:00")
    db.add_scores(f"S{first:%Y%m%d}100000", customer=80, server=70)
    db.add_service(f"S{second:%Y%m%d}100000", f"{second} 10:00:00")
    db.add_scores(f"S{second:%Y%m%d}100000", customer=60, server=-1)

    home = admin_home_init.launch(engine, ADMIN_ID, services)
    page = qml(engine.rootObjects()[-1], "mainContentLoader.item")  # admin_home_page.qml
    loaded(home.trendDataManager)
    assert qml(page, "yearSelector.currentText") == str(today.year)  # QML 的 new Date() 就是電腦現在的日期
    assert qml(page, "monthSelector.currentIndex") == today.month - 1
    assert qml(page, "valueAxisX.max") == calendar.monthrange(today.year, today.month)[1]  # 那個月實際的天數
    assert (qml(page, "staffDots.count"), qml(page, "customerDots.count")) == (1, 2)
    assert qml(page, "trendLines.length") == 1  # 顧客 1、2 日連成一條線；服務人員只有 1 日，只有點
    assert qml(page, "chartView.count") == 3

    other = 0 if today.month != 1 else 1  # 使用者改選另一個月（一月，或今天就是一月時選二月）
    qml(page, f"monthSelector.currentIndex = {other}; monthSelector.activated({other})")
    wait_until(lambda: home.trendDataManager.month == other + 1)
    loaded(home.trendDataManager)
    assert home.trendDataManager.state == "empty"
    assert qml(page, "chartView.count") == 2  # 重畫，不是疊上去：舊的線段拿掉了，只剩兩組點
    assert (qml(page, "staffDots.count"), qml(page, "customerDots.count")) == (0, 0)


def test_opening_add_account_does_not_reload_home_data(engine, db, services, db_proxy):
    home = admin_home_init.launch(engine, ADMIN_ID, services)
    window = engine.rootObjects()[-1]
    wait_until(window.isExposed)
    for section in (home.profile, home.serviceScoreManager, home.rankingModel, home.trendDataManager):
        loaded(section)
    db_proxy.reset()
    add_button = qml(window, "addAccountButton")  # 側邊欄的 Add Account
    center = add_button.mapToScene(QPointF(add_button.width() / 2, add_button.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
    wait_until(lambda: qml(window, "mainContentLoader.source.toString()").endswith("admin_add_page.qml"))
    wait_until(lambda: qml(window, "mainContentLoader.status === Loader.Ready"))
    assert db_proxy.snapshot()["rt"] == 0  # 以前會把首頁的分數、排名、趨勢再查一次（UX-32）
