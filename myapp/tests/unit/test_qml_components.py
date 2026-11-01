"""F1 的共用 QML 元件（qml/components/，CONTRACTS §2）：在 offscreen 用小的 harness 視窗測行為。

- harness 用絕對路徑 import 元件資料夾，並把 Theme.animationScale 設成 0：所有 dur* 都是 0，不必等動畫。
- ToastHost 接的是假的 Notifier：只有 CONTRACTS §1 的模型角色、overflowCount、dismiss()、trigger()。
  真的 Notifier（F0）是同時另外寫的，這裡不依賴它。
- 每個測試都要零 QML 警告（engine.warnings，harness fixture 結束時檢查）。等待一律用 wait_until，不用固定的 sleep。
"""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import (QAbstractListModel, QCoreApplication, QEvent, QModelIndex, QObject, QPoint, QPointF,
                            Property, Qt, QUrl, Signal, Slot, qInstallMessageHandler)
from PySide6.QtGui import QColor, QGuiApplication, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from tests.integration.helpers import qml, wait_until

COMPONENTS = Path(__file__).resolve().parents[2] / "src" / "myapp" / "qml" / "components"
ICONS = COMPONENTS / "icons"
PAGES = COMPONENTS.parent / "pages"

HARNESS = """\
import QtQuick
import QtQuick.Controls.Basic
import "{components}"

Window {{
    id: window
    width: 1000
    height: 700
    visible: true
    color: Theme.contentBg

    // 測試不等動畫：animationScale 0 讓所有 dur* 都是 0。motionMode 2（完整）不受這台電腦的系統設定影響
    property real animationScale: 0
    property int motionMode: 2
    Binding {{ target: Theme; property: "animationScale"; value: window.animationScale }}
    Binding {{ target: Theme; property: "motionMode"; value: window.motionMode }}

    TextField {{ id: field; objectName: "field"; x: 20; y: 20; width: 200 }}

{body}
}}
"""


# ── 假的 Notifier ────────────────────────────────────────────────────────────

ROLES = ("toastId", "severity", "title", "detail", "actionLabel", "actionId", "timeoutMs", "done", "total",
         "count", "revision")
USER_ROLE = int(Qt.ItemDataRole.UserRole)


class FakeToastModel(QAbstractListModel):
    """CONTRACTS §1 的模型角色，一個都不多。第 0 列是最新的一則。"""

    def __init__(self):
        super().__init__()
        self.rows = []

    def roleNames(self):
        return {USER_ROLE + i: name.encode() for i, name in enumerate(ROLES)}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or int(role) < USER_ROLE:
            return None
        return self.rows[index.row()][ROLES[int(role) - USER_ROLE]]


class FakeNotifier(QObject):
    """Notifier 的替身：最多顯示 3 則，新的插在第 0 列，其餘排隊；記下 QML 呼叫的 dismiss()、trigger()。"""

    overflowCountChanged = Signal()

    def __init__(self):
        super().__init__()
        self._model = FakeToastModel()
        self.queue = []
        self.dismissed = []
        self.triggered = []
        self._next_id = 1

    @Property(QObject, constant=True)
    def model(self):
        return self._model

    @Property(int, notify=overflowCountChanged)
    def overflowCount(self):
        return len(self.queue)

    @property
    def rows(self):
        """顯示中的通知（第 0 列＝最新）。"""
        return self._model.rows

    def push(self, severity, title, detail="", *, timeoutMs=0, actionLabel="", actionId="", done=0.0, total=0.0):
        row = dict(toastId=self._next_id, severity=severity, title=title, detail=detail, actionLabel=actionLabel,
                   actionId=actionId, timeoutMs=timeoutMs, done=float(done), total=float(total), count=1, revision=0)
        self._next_id += 1
        if len(self._model.rows) < 3:
            self._insert(row)
        else:
            self.queue.append(row)
            self.overflowCountChanged.emit()
        return row["toastId"]

    def update(self, toast_id, **changes):
        """同一個 key 又來一次：就地更新，count、revision 加 1。"""
        i = self._row(toast_id)
        self._model.rows[i].update(changes)
        self._model.rows[i]["count"] += 1
        self._model.rows[i]["revision"] += 1
        index = self._model.index(i)
        self._model.dataChanged.emit(index, index, [])

    @Slot(int)
    def dismiss(self, toast_id):
        self.dismissed.append(toast_id)
        self._remove(toast_id)

    @Slot(int)
    def trigger(self, toast_id):
        self.triggered.append(toast_id)
        self._remove(toast_id)

    def _row(self, toast_id):
        return next((i for i, row in enumerate(self._model.rows) if row["toastId"] == toast_id), None)

    def _insert(self, row):
        self._model.beginInsertRows(QModelIndex(), 0, 0)
        self._model.rows.insert(0, row)
        self._model.endInsertRows()

    def _remove(self, toast_id):
        i = self._row(toast_id)
        if i is None:
            return
        self._model.beginRemoveRows(QModelIndex(), i, i)
        del self._model.rows[i]
        self._model.endRemoveRows()
        if self.queue:  # 空出位置：排隊的補上
            self._insert(self.queue.pop(0))
            self.overflowCountChanged.emit()


# ── harness 與小工具 ─────────────────────────────────────────────────────────

@pytest.fixture
def harness(tmp_path):
    """load(body, **context)：載入 harness 視窗，回傳它。測試結束時關掉視窗、刪掉 engine，並檢查零 QML 警告。"""
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    context_objects = []  # context property 的 Python 物件要活得比 engine 久

    def load(body, **context):
        for name, value in context.items():
            engine.rootContext().setContextProperty(name, value)
            context_objects.append(value)
        path = tmp_path / "Harness.qml"
        path.write_text(HARNESS.format(components=QUrl.fromLocalFile(str(COMPONENTS)).toString(), body=body),
                        encoding="utf-8")
        engine.load(QUrl.fromLocalFile(str(path)))
        assert engine.rootObjects(), warnings
        window = engine.rootObjects()[-1]
        wait_until(window.isExposed)
        # toast 只在視窗作用中時計時（DS-21）。不明確要求的話，哪個視窗作用中要看前一個測試留下什麼，
        # 單獨跑會過、整批跑偶爾不過：所以每個 harness 視窗都先變成作用中，和使用者看著的前景視窗一樣
        window.requestActivate()
        wait_until(window.isActive)
        return window

    yield SimpleNamespace(load=load, warnings=warnings)
    for window in engine.rootObjects():
        window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert warnings == []


def find(root, name):
    """用 objectName 找物件（包括 Popup 裡的）。"""
    found = root.findChild(QObject, name)
    assert found is not None, name
    return found


def center_of(item):
    return item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()


def click(window, item):
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center_of(item))


def value(obj, expression):
    """qml() 的結果；JS 陣列、物件轉成 Python 的 list、dict。"""
    result = qml(obj, expression)
    return result.toVariant() if hasattr(result, "toVariant") else result


def theme(window, token):
    return qml(window, f"Theme.{token}")


def toasts(window):
    """ListView 已經建立的 toast，依模型順序（第 0 列＝最新）。"""
    view = find(window, "toastList")
    return [qml(view, f"itemAtIndex({i})") for i in range(qml(view, "count"))]


def timer_state(window):
    """toast 的計時器停著的原因（paused＝hover、焦點在 toast 裡，或視窗不是作用中）：給 wait_until 的失敗訊息用。"""
    shown = [toast for toast in toasts(window) if toast is not None]
    return (f"視窗作用中 {window.isActive()}，focus window {QGuiApplication.focusWindow()}，"
            f"toast（標題, paused）{[(toast.property('title'), toast.property('paused')) for toast in shown]}")


def toast_titled(window, title):
    def matching():
        return [toast for toast in toasts(window) if toast is not None and toast.property("title") == title]
    wait_until(lambda: len(matching()) == 1)
    return matching()[0]


def wait_settled(window, toast):
    """等 toast 排進堆疊（新增、補位的轉場跑完）。用它的座標點擊或移動滑鼠之前要先等。"""
    def inside_the_stack():
        top = toast.mapToScene(QPointF(0, 0)).y()
        host_top, host_height = qml(window, "host.y"), qml(window, "host.height")
        return host_height > 0 and host_top <= top and top + toast.height() <= host_top + host_height
    wait_until(inside_the_stack)


def announced(toast):
    return value(toast, "announced.map(entry => (entry.assertive ? 'assertive: ' : 'polite: ') + entry.text)")


TOAST_HOST = """
    ToastHost {
        id: host
        objectName: "host"
        center: notifier
        x: window.width - width - 24
        y: window.height - height - 24
    }
"""


# ── ToastHost／Toast ─────────────────────────────────────────────────────────

def test_toast_host_shows_up_to_three_with_the_newest_at_the_bottom(harness):
    notifier = FakeNotifier()
    window = harness.load(TOAST_HOST, notifier=notifier)
    for title in ("First", "Second", "Third", "Fourth"):
        notifier.push("error", title)  # 都是常駐：第 4 則排隊

    wait_until(lambda: [toast and toast.property("title") for toast in toasts(window)] == ["Third", "Second", "First"])
    assert qml(window, "host.count") == 3

    def tops():
        return [toast.mapToScene(QPointF(0, 0)).y() for toast in toasts(window)]

    def bottom_of_newest():
        newest = toasts(window)[0]
        return newest.mapToScene(QPointF(0, newest.height())).y()

    wait_until(lambda: tops()[0] > tops()[1] > tops()[2])  # 新的（第 0 列）在最下面，舊的往上推
    wait_until(lambda: bottom_of_newest() == window.height() - 24)  # 貼著右下角，距邊 24
    assert toasts(window)[0].width() == 360
    overflow = find(window, "toastOverflow")
    assert overflow.isVisible()
    assert find(overflow, "toastOverflowText").property("text") == "1 more notification"


def test_toast_host_overflow_line_counts_queued_toasts(harness):
    notifier = FakeNotifier()
    window = harness.load(TOAST_HOST, notifier=notifier)
    overflow = find(window, "toastOverflow")
    first = notifier.push("error", "One")
    for title in ("Two", "Three", "Four", "Five"):
        notifier.push("error", title)
    wait_until(lambda: overflow.isVisible())
    assert find(overflow, "toastOverflowText").property("text") == "2 more notifications"

    notifier.dismiss(first)  # 空出一個位置：排隊的補上一則
    wait_until(lambda: find(overflow, "toastOverflowText").property("text") == "1 more notification")
    wait_until(lambda: [toast and toast.property("title") for toast in toasts(window)] == ["Four", "Three", "Two"])
    notifier.dismiss(notifier.rows[0]["toastId"])
    wait_until(lambda: not overflow.isVisible())


def test_toast_auto_dismisses_after_its_timeout_but_sticky_toasts_stay(harness):
    notifier = FakeNotifier()
    window = harness.load(TOAST_HOST, notifier=notifier)
    sticky = notifier.push("error", "S20261031093000 wasn't saved", timeoutMs=0)
    quick = notifier.push("success", "Analysis complete", timeoutMs=50)

    # 2026-11-01 整批測試（整合測試先跑）時失敗過一次，之後重跑 4 次都沒有重現：失敗時印出計時器為什麼停著
    wait_until(lambda: quick in notifier.dismissed, describe=lambda: timer_state(window))
    assert sticky not in notifier.dismissed
    wait_until(lambda: len(toasts(window)) == 1)
    remaining = toasts(window)[0]
    assert remaining.property("toastId") == sticky
    assert not find(remaining, "lifeTimer").property("running")


def test_toast_timer_pauses_on_hover_focus_and_inactive_window(harness):
    notifier = FakeNotifier()
    window = harness.load(TOAST_HOST, notifier=notifier)
    field = find(window, "field")
    field.forceActiveFocus()
    notifier.push("info", "Sent for analysis", timeoutMs=60_000)
    toast = toast_titled(window, "Sent for analysis")
    timer = find(toast, "lifeTimer")
    assert timer.property("running")
    wait_settled(window, toast)

    QTest.mouseMove(window, center_of(toast))  # 滑鼠停在上面
    wait_until(lambda: toast.property("paused"))
    assert not timer.property("running")
    QTest.mouseMove(window, QPoint(500, 300))
    wait_until(lambda: timer.property("running"))

    find(toast, "toastClose").forceActiveFocus()  # 鍵盤焦點在裡面（例如 F6 跳過來）
    wait_until(lambda: not timer.property("running"))
    field.forceActiveFocus()
    wait_until(lambda: timer.property("running"))

    other = QWindow()  # 使用者切到別的視窗（例如錄影視窗）
    other.resize(100, 100)
    other.show()
    other.requestActivate()
    try:
        wait_until(lambda: not window.isActive())
        wait_until(lambda: not timer.property("running"))
        window.requestActivate()
        wait_until(lambda: timer.property("running"))
    finally:
        other.destroy()
    assert notifier.dismissed == []


def test_toast_close_and_action_call_dismiss_and_trigger_without_taking_focus(harness):
    notifier = FakeNotifier()
    window = harness.load(TOAST_HOST, notifier=notifier)
    field = find(window, "field")
    field.forceActiveFocus()
    saved = notifier.push("success", "Saved S20261031093000", "Video and audio uploaded.",
                          actionLabel="Analyze", actionId="analyze:S20261031093000")
    failed = notifier.push("error", "Analysis failed", "S20261031101500 couldn't be analyzed. You can send it again.",
                           actionLabel="Retry", actionId="retry-analyze:S20261031101500")

    # 留著 toast 的參照：它被回收時，PySide 會讓從它找到的子物件失效
    saved_toast = toast_titled(window, "Saved S20261031093000")
    wait_settled(window, saved_toast)
    action = find(saved_toast, "toastAction")
    assert action.property("text") == "Analyze"
    click(window, action)
    wait_until(lambda: notifier.triggered == [saved])
    assert field.hasActiveFocus()  # 按通知上的按鈕不會把焦點搶走

    failed_toast = toast_titled(window, "Analysis failed")
    wait_settled(window, failed_toast)
    close = find(failed_toast, "toastClose")
    assert qml(close, "Accessible.name") == "Close notification"
    click(window, close)
    wait_until(lambda: notifier.dismissed == [failed])
    assert field.hasActiveFocus()
    assert notifier.triggered == [saved]


def test_escape_closes_the_focused_toast(harness):
    notifier = FakeNotifier()
    window = harness.load(TOAST_HOST, notifier=notifier)
    toast_id = notifier.push("warning", "Can't refresh analysis status", "Can't reach the database. Retrying in 15 seconds.")
    toast = toast_titled(window, "Can't refresh analysis status")
    find(toast, "toastClose").forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Escape)
    wait_until(lambda: notifier.dismissed == [toast_id])


def test_new_toast_does_not_take_focus(harness):
    notifier = FakeNotifier()
    window = harness.load(TOAST_HOST, notifier=notifier)
    field = find(window, "field")
    field.forceActiveFocus()
    notifier.push("error", "Couldn't send for analysis", "Can't reach the analysis service.", actionLabel="Retry")
    toast_titled(window, "Couldn't send for analysis")
    assert field.hasActiveFocus()


@pytest.mark.parametrize("severity, icon, assertive", [
    ("success", "circle-check_successOnDark.svg", False),
    ("info", "info_infoOnDark.svg", False),
    ("warning", "triangle-alert_warningOnDark.svg", False),
    ("error", "circle-alert_errorOnDark.svg", True),
])
def test_toast_icon_role_and_announcement_follow_severity(harness, severity, icon, assertive):
    notifier = FakeNotifier()
    window = harness.load(TOAST_HOST, notifier=notifier)
    notifier.push(severity, "Analysis complete", "Scores and charts for S20261031093000 are ready.")
    toast = toast_titled(window, "Analysis complete")

    assert find(toast, "toastIcon").property("source").fileName() == icon
    role = "Accessible.AlertMessage" if assertive else "Accessible.Notification"
    assert qml(toast, "Accessible.role") == qml(toast, role)
    assert qml(toast, "Accessible.name") == "Analysis complete"
    priority = "assertive" if assertive else "polite"
    assert announced(toast) == [f"{priority}: Analysis complete. Scores and charts for S20261031093000 are ready."]
    assert find(toast, "toastTitle").property("text") == "Analysis complete"
    assert not find(toast, "toastProgress").isVisible()
    assert not find(toast, "toastAction").isVisible()


def test_progress_toast_shows_amounts_and_is_announced_only_at_start_and_end(harness):
    notifier = FakeNotifier()
    window = harness.load(TOAST_HOST, notifier=notifier)
    toast_id = notifier.push("progress", "Uploading S20261031093000", "File 2 of 3", done=58.6e6, total=124.0e6)
    toast = toast_titled(window, "Uploading S20261031093000")
    body = find(toast, "toastBody")
    progress = find(toast, "toastProgress")
    assert body.property("text") == "58.6 / 124.0 MB · 47% · File 2 of 3"
    assert progress.isVisible() and progress.property("determinate")
    assert find(toast, "toastIcon").property("source").fileName() == "upload_chromeIcon.svg"
    assert announced(toast) == ["polite: Uploading S20261031093000"]  # 開始

    notifier.update(toast_id, done=124.0e6, detail="File 3 of 3")  # 進度更新：不唸
    refresh = find(toast, "refreshTimer")
    wait_until(lambda: body.property("text") == "124.0 / 124.0 MB · 100% · File 3 of 3"
               and not refresh.property("running"))
    assert announced(toast) == ["polite: Uploading S20261031093000"]

    # 同一個 key 就地變成成功（結束）：唸一次，照新的停留時間自動消失
    notifier.update(toast_id, severity="success", title="Saved S20261031093000",
                    detail="Video and audio uploaded.", timeoutMs=50)
    wait_until(lambda: len(announced(toast)) == 2)
    assert announced(toast)[1] == "polite: Saved S20261031093000. Video and audio uploaded."
    assert not progress.isVisible()
    wait_until(lambda: toast_id in notifier.dismissed)


def test_progress_toast_without_total_is_indeterminate(harness):
    notifier = FakeNotifier()
    window = harness.load(TOAST_HOST, notifier=notifier)
    notifier.push("progress", "Saving recording…")
    toast = toast_titled(window, "Saving recording…")
    progress = find(toast, "toastProgress")
    assert progress.isVisible() and not progress.property("determinate")
    assert not find(toast, "toastBody").isVisible()  # 沒有總量、沒有 detail：只有標題和條


def test_repeated_error_with_the_same_key_is_announced_again(harness):
    notifier = FakeNotifier()
    window = harness.load(TOAST_HOST, notifier=notifier)
    toast_id = notifier.push("error", "Account wasn't saved", "Can't reach the database.")
    toast = toast_titled(window, "Account wasn't saved")
    notifier.update(toast_id)
    wait_until(lambda: len(announced(toast)) == 2)
    assert announced(toast) == ["assertive: Account wasn't saved. Can't reach the database."] * 2
    assert toast.property("count") == 2


def test_toast_host_takes_no_space_when_empty_and_tolerates_no_notifier(harness):
    notifier = FakeNotifier()
    window = harness.load(TOAST_HOST + "\n    ToastHost { id: idle }\n", notifier=notifier)
    assert qml(window, "host.height") == 0 and qml(window, "host.occupiedHeight") == 0
    assert qml(window, "host.visible")  # 一直開著（ENG-15）
    toast_id = notifier.push("info", "Sent for analysis")
    wait_until(lambda: qml(window, "host.height") > 0)
    assert qml(window, "host.occupiedHeight") == qml(window, "host.height") + 24
    notifier.dismiss(toast_id)
    wait_until(lambda: qml(window, "host.height") == 0)
    assert qml(window, "idle.count") == 0 and qml(window, "idle.overflowCount") == 0


CONFIRM_DIALOG = """
    ConfirmDialog {
        id: dialog
        objectName: "dialog"
        title: "Quit while uploading?"
        body: "S20261031093000 is still uploading (58.6 / 124.0 MB). If you quit now, this service won't be saved."
        confirmText: "Quit Anyway"
        cancelText: "Keep Uploading"
        property int confirmedCount: 0
        property int cancelledCount: 0
        onConfirmed: confirmedCount++
        onCancelled: cancelledCount++
    }
"""


def open_dialog(window):
    qml(window, "dialog.open()")
    wait_until(lambda: qml(window, "dialog.opened"))


def test_toasts_stay_above_and_clickable_over_a_modal_dialog(harness):
    notifier = FakeNotifier()
    window = harness.load(TOAST_HOST + CONFIRM_DIALOG, notifier=notifier)
    open_dialog(window)
    toast_id = notifier.push("error", "S20261031093000 wasn't saved")
    toast = toast_titled(window, "S20261031093000 wasn't saved")
    wait_settled(window, toast)
    close = find(toast, "toastClose")
    assert qml(window, "host.z") == theme(window, "zToast") > qml(window, "dialog.z") == theme(window, "zDialog")

    click(window, close)
    wait_until(lambda: notifier.dismissed == [toast_id])
    assert qml(window, "dialog.opened")
    assert qml(window, "dialog.confirmedCount") == qml(window, "dialog.cancelledCount") == 0


# ── StatusChip ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status, failed, label, background, text, icon", [
    (1, False, "Not Analyzed", "surfaceMuted", "textPrimary", "circle-dashed_textPrimary.svg"),
    (2, False, "Analyzing", "infoTint", "infoOnLight", "clock_infoOnLight.svg"),
    (3, False, "Completed", "successTint", "successOnLight", "circle-check_successOnLight.svg"),
    (1, True, "Failed", "errorTint", "errorOnLight", "circle-x_errorOnLight.svg"),
    (2, True, "Analyzing", "infoTint", "infoOnLight", "clock_infoOnLight.svg"),  # 重新送出後跟著資料庫
    (3, True, "Completed", "successTint", "successOnLight", "circle-check_successOnLight.svg"),
], ids=["1", "2", "3", "failed", "failed-then-resent", "failed-then-completed"])
def test_status_chip_label_colors_and_icon(harness, status, failed, label, background, text, icon):
    window = harness.load(f"""
    StatusChip {{ id: chip; objectName: "chip"; x: 300; y: 100; status: {status}; failed: {str(failed).lower()} }}
""")
    chip = find(window, "chip")
    assert chip.property("label") == label
    assert find(chip, "chipLabel").property("text") == label
    assert qml(chip, "color") == theme(window, background)
    assert qml(find(chip, "chipLabel"), "color") == theme(window, text)
    assert find(chip, "chipIcon").property("source").fileName() == icon
    assert chip.height() == 24 and qml(chip, "radius") == 12
    assert qml(chip, "Accessible.ignored")  # 狀態寫進那一列的名稱（DS-22）


def test_status_chip_follows_status_changes(harness):
    window = harness.load('    StatusChip { id: chip; objectName: "chip"; x: 300; y: 100 }\n')
    chip = find(window, "chip")
    assert chip.property("label") == "Not Analyzed"
    chip.setProperty("status", 2)
    assert find(chip, "chipLabel").property("text") == "Analyzing"
    chip.setProperty("status", 1)
    chip.setProperty("failed", True)
    assert find(chip, "chipLabel").property("text") == "Failed"


# ── TransferProgress ─────────────────────────────────────────────────────────

def test_transfer_progress_shows_megabytes_and_percent(harness):
    window = harness.load("""
    TransferProgress {
        id: progress
        objectName: "progress"
        x: 300; y: 100; width: 400
        label: "Downloading customer video"
        done: 58.6e6
        total: 124.0e6
    }
""")
    progress = find(window, "progress")
    assert find(progress, "progressLabel").property("text") == "Downloading customer video"
    assert find(progress, "progressAmount").property("text") == "58.6 / 124.0 MB · 47%"
    bar = find(progress, "progressBar")
    assert not bar.property("indeterminate")
    assert bar.property("position") == pytest.approx(58.6 / 124.0)
    assert qml(bar, "Accessible.name") == "Downloading customer video"

    progress.setProperty("done", 130e6)  # 多報了也不超過 100%
    assert find(progress, "progressAmount").property("text") == "124.0 / 124.0 MB · 100%"
    assert bar.property("position") == 1


def test_transfer_progress_without_total_is_indeterminate_and_still_when_motion_is_reduced(harness):
    window = harness.load("""
    TransferProgress { id: progress; objectName: "progress"; x: 300; y: 100; width: 400; label: "Saving recording…" }
""")
    progress = find(window, "progress")
    bar = find(progress, "progressBar")
    segment = find(progress, "indeterminateSegment")
    assert bar.property("indeterminate") and not progress.property("determinate")
    assert find(progress, "progressAmount").property("text") == ""
    assert find(progress, "progressLabel").property("text") == "Saving recording…"
    assert not segment.isVisible()  # animationScale 0：靜止的軌道加文字

    window.setProperty("animationScale", 1)  # 完整動態：色塊來回移動
    wait_until(segment.isVisible)
    window.setProperty("motionMode", 1)  # 減少動態：又變回靜止的軌道
    wait_until(lambda: not segment.isVisible())


# ── BusyButton ───────────────────────────────────────────────────────────────

BUSY_BUTTON = """
    BusyButton {{
        id: button
        objectName: "button"
        x: 300; y: 200
        variant: "{variant}"
        text: "Analyze"
        busyText: "Sending…"
        busyDelay: {delay}
        busyMinShow: {min_show}
        property int clicks: 0
        onClicked: {{ clicks++; busy = true }}
    }}
"""


def test_busy_button_blocks_repeat_clicks_at_once_and_keeps_focus(harness):
    window = harness.load(BUSY_BUTTON.format(variant="secondary", delay=60_000, min_show=500))
    button = find(window, "button")
    width = button.width()

    click(window, button)
    wait_until(lambda: button.property("clicks") == 1)
    assert button.property("busy") and not button.property("busyShown")
    assert find(button, "busyLabel").property("text") == "Analyze"  # 還沒過 busyDelay：字不變
    assert qml(button, "background.color") == theme(window, "surfaceMuted")  # 只換成按下的底色

    click(window, button)  # 第二次點擊、Space、Enter 都沒有作用
    button.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Space)
    QTest.keyClick(window, Qt.Key.Key_Return)
    QCoreApplication.processEvents()
    assert button.property("clicks") == 1
    assert button.hasActiveFocus() and button.isEnabled()  # 沒有停用：焦點留在按鈕上
    assert button.width() == width

    button.setProperty("busy", False)  # 工作結束：又可以按
    click(window, button)
    wait_until(lambda: button.property("clicks") == 2)


def test_busy_button_shows_busy_text_and_spinner_only_after_the_delay(harness):
    window = harness.load(BUSY_BUTTON.format(variant="secondary", delay=50, min_show=500))
    button = find(window, "button")
    width = button.width()
    spinner = find(button, "busySpinner")

    button.setProperty("busy", True)
    assert not button.property("busyShown") and not spinner.isVisible()
    assert find(button, "busyShowTimer").property("running")
    wait_until(lambda: button.property("busyShown"))
    assert find(button, "busyLabel").property("text") == "Sending…"
    assert spinner.isVisible()
    assert spinner.property("source").fileName() == "loader-circle_textSecondary.svg"
    assert button.width() == width  # 寬度取兩種文字中較寬的，換字時不變
    assert qml(button, "Accessible.name") == "Sending…"
    assert qml(button, "Accessible.description") == "Busy"  # DS-24、DS-51 的用詞
    assert qml(button, "background.color") == theme(window, "surfaceMuted")
    assert qml(button, "background.border.color") == theme(window, "divider")
    assert qml(find(button, "busyLabel"), "color") == theme(window, "textSecondary")


def test_busy_button_keeps_the_busy_look_for_at_least_min_show(harness):
    window = harness.load(BUSY_BUTTON.format(variant="primary", delay=0, min_show=60_000))
    button = find(window, "button")
    button.setProperty("busy", True)
    wait_until(lambda: button.property("busyShown"))

    button.setProperty("busy", False)  # 剛顯示就結束：還要維持 busyMinShow，避免閃一下
    QCoreApplication.processEvents()
    assert button.property("busyShown")
    assert find(button, "busyMinShowTimer").property("running")
    assert find(button, "busyLabel").property("text") == "Sending…"


def test_busy_button_restores_after_min_show(harness):
    window = harness.load(BUSY_BUTTON.format(variant="primary", delay=0, min_show=30))
    button = find(window, "button")
    button.setProperty("busy", True)
    wait_until(lambda: button.property("busyShown"))
    button.setProperty("busy", False)
    wait_until(lambda: not button.property("busyShown"))
    assert find(button, "busyLabel").property("text") == "Analyze"
    assert not find(button, "busySpinner").isVisible()
    assert qml(button, "background.color") == theme(window, "actionPrimary")


def test_busy_button_does_not_flash_when_the_work_ends_before_the_delay(harness):
    window = harness.load(BUSY_BUTTON.format(variant="secondary", delay=60_000, min_show=500))
    button = find(window, "button")
    button.setProperty("busy", True)
    button.setProperty("busy", False)
    assert not button.property("busyShown")
    assert not find(button, "busyShowTimer").property("running")
    assert qml(button, "background.color") == theme(window, "actionSecondaryBg")


@pytest.mark.parametrize("variant, normal, waiting, busy, busy_text, spinner", [
    ("primary", "actionPrimary", "actionPrimaryPressed", "actionPrimaryPressed", "textOnDark",
     "loader-circle_textOnDark.svg"),
    ("secondary", "actionSecondaryBg", "surfaceMuted", "surfaceMuted", "textSecondary",
     "loader-circle_textSecondary.svg"),
    ("danger", "actionSecondaryBg", "errorTint", "surfaceMuted", "textSecondary", "loader-circle_textSecondary.svg"),
    ("chrome", "chromeIcon", "chromeButtonPressed", "chromeButtonPressed", "chromeOnLight",
     "loader-circle_chromeOnLight.svg"),
])
def test_busy_button_colors_per_variant(harness, variant, normal, waiting, busy, busy_text, spinner):
    window = harness.load(BUSY_BUTTON.format(variant=variant, delay=40, min_show=0))
    button = find(window, "button")
    assert qml(button, "background.color") == theme(window, normal)
    button.setProperty("busy", True)
    assert qml(button, "background.color") == theme(window, waiting)  # 等待中：按下的底色
    wait_until(lambda: button.property("busyShown"))
    assert qml(button, "background.color") == theme(window, busy)
    assert qml(find(button, "busyLabel"), "color") == theme(window, busy_text)
    assert find(button, "busySpinner").property("source").fileName() == spinner


def test_busy_button_disabled_and_large_look(harness):
    window = harness.load("""
    BusyButton { id: regular; objectName: "regular"; x: 300; y: 100; text: "View Results" }
    BusyButton { id: large; objectName: "large"; x: 300; y: 200; variant: "primary"; large: true; text: "Add Account" }
    BusyButton { id: off; objectName: "off"; x: 300; y: 300; variant: "primary"; enabled: false; text: "Add Account" }
""")
    regular, large, off = find(window, "regular"), find(window, "large"), find(window, "off")
    assert regular.height() == 40 and qml(regular, "background.radius") == 5 and qml(regular, "font.pixelSize") == 16
    assert large.height() == 50 and qml(large, "background.radius") == 10 and qml(large, "font.pixelSize") == 18
    assert qml(off, "background.color") == theme(window, "actionDisabledBg")
    assert qml(find(off, "busyLabel"), "color") == theme(window, "actionDisabledText")


# ── EmptyState、InlineError、FieldError ─────────────────────────────────────

def test_empty_state_action_and_announcement(harness):
    window = harness.load("""
    EmptyState {
        id: empty
        objectName: "empty"
        x: 300; y: 100; width: 400
        title: "No completed analyses yet"
        description: "Services you analyze appear here when they're done."
        actionText: "View Not Analyzed"
        property int triggered: 0
        onActionTriggered: triggered++
    }
    EmptyState { id: plain; objectName: "plain"; x: 300; y: 400; width: 400; title: "No reports yet" }
""")
    empty, plain = find(window, "empty"), find(window, "plain")
    assert find(empty, "emptyTitle").property("text") == "No completed analyses yet"
    assert find(empty, "emptyDescription").property("text") == "Services you analyze appear here when they're done."
    click(window, find(empty, "emptyAction"))
    wait_until(lambda: empty.property("triggered") == 1)
    assert value(empty, "announced") == ["No completed analyses yet"]

    assert not find(plain, "emptyAction").isVisible()  # 沒有 actionText 就沒有按鈕
    assert not find(plain, "emptyDescription").isVisible()
    plain.setProperty("visible", False)
    plain.setProperty("title", "No services from 2026-10-01 to 2026-10-31")
    plain.setProperty("visible", True)  # 再出現：唸一次新的標題
    assert value(plain, "announced") == ["No reports yet", "No services from 2026-10-01 to 2026-10-31"]


INLINE_ERROR = """
    InlineError {
        id: error
        objectName: "error"
        x: 300; y: 100; width: 480
        title: "Video failed to load"
        detail: "The download of S20261031090500_cam0.avi was interrupted."
        technicalDetail: "NoSuchKey: video/S20261031090500_cam0.avi"
        property int retries: 0
        onRetry: retries++
    }
    InlineError { id: bare; objectName: "bare"; x: 300; y: 450; width: 480; title: "Scores failed to load"; retryText: "" }
"""


def test_inline_error_retry_details_and_repeated_failure_hint(harness):
    window = harness.load(INLINE_ERROR)
    error = find(window, "error")
    assert qml(error, "Accessible.role") == qml(error, "Accessible.AlertMessage")
    assert value(error, "announced") == ["Video failed to load. The download of S20261031090500_cam0.avi was interrupted."]

    retry = find(error, "retryButton")
    assert retry.property("text") == "Retry"
    click(window, retry)
    wait_until(lambda: error.property("retries") == 1)
    error.setProperty("busy", True)  # 重試中：Retry 按鈕忙碌
    assert retry.property("busy")

    technical = find(error, "errorTechnical")
    details = find(error, "detailsButton")
    assert details.property("text") == "Details" and not technical.isVisible()
    click(window, details)
    wait_until(technical.isVisible)
    assert technical.property("text") == "NoSuchKey: video/S20261031090500_cam0.avi"
    assert qml(details, "Accessible.expanded")

    hint = find(error, "errorHint")
    error.setProperty("failureCount", 2)
    assert not hint.isVisible()
    error.setProperty("failureCount", 3)  # 同一個區塊連續失敗 3 次：多一行建議，並再唸一次
    assert hint.isVisible()
    assert hint.property("text") == "If it still fails, check your network or contact your administrator."  # DS-52
    assert len(value(error, "announced")) == 3

    bare = find(window, "bare")
    assert not find(bare, "retryButton").isVisible() and not find(bare, "detailsButton").isVisible()


def test_field_error_reserves_one_line_and_wraps_long_text(harness):
    window = harness.load("""
    FieldError { id: empty; objectName: "empty"; x: 300; y: 100; width: 300 }
    FieldError { id: oneLine; objectName: "oneLine"; x: 300; y: 200; width: 300; text: "Enter a password." }
    FieldError { id: long; objectName: "long"; x: 300; y: 300; width: 160
                 text: "Username staff07 is already taken. Choose another one." }
""")
    empty, one_line, long = find(window, "empty"), find(window, "oneLine"), find(window, "long")
    assert empty.height() == one_line.height() == 20  # 沒有錯誤也先留一行，錯誤出現時版面不跳
    assert long.height() > one_line.height()
    assert not find(empty, "fieldErrorIcon").isVisible() and find(one_line, "fieldErrorIcon").isVisible()
    assert qml(empty, "Accessible.ignored") and not qml(one_line, "Accessible.ignored")
    assert qml(find(one_line, "fieldErrorText"), "color") == theme(window, "errorOnLight")
    assert value(one_line, "announced") == ["Enter a password."]

    empty.setProperty("text", "Enter a username.")
    assert empty.height() == 20
    assert value(empty, "announced") == ["Enter a username."]


# ── ConfirmDialog ────────────────────────────────────────────────────────────

def test_confirm_dialog_defaults_to_the_safe_choice(harness):
    window = harness.load(CONFIRM_DIALOG)
    field = find(window, "field")
    field.forceActiveFocus()
    cancel, confirm = find(window, "cancelButton"), find(window, "confirmButton")
    assert cancel.property("text") == "Keep Uploading" and confirm.property("text") == "Quit Anyway"
    assert qml(window, "dialog.modal") and qml(window, "dialog.z") == theme(window, "zDialog")
    assert find(window, "dialogTitle").property("text") == "Quit while uploading?"

    open_dialog(window)
    wait_until(cancel.hasActiveFocus)  # 開啟時焦點在安全的按鈕上
    QTest.keyClick(window, Qt.Key.Key_Escape)  # Esc 等於安全的選項
    wait_until(lambda: not qml(window, "dialog.visible"))
    assert (qml(window, "dialog.cancelledCount"), qml(window, "dialog.confirmedCount")) == (1, 0)
    wait_until(field.hasActiveFocus)  # 關閉後焦點回到原本的地方

    open_dialog(window)
    wait_until(cancel.hasActiveFocus)
    QTest.keyClick(window, Qt.Key.Key_Return)  # Enter 也是安全的選項
    wait_until(lambda: not qml(window, "dialog.visible"))
    assert (qml(window, "dialog.cancelledCount"), qml(window, "dialog.confirmedCount")) == (2, 0)

    open_dialog(window)
    click(window, confirm)  # 危險的選項要明確按下去
    wait_until(lambda: not qml(window, "dialog.visible"))
    assert (qml(window, "dialog.cancelledCount"), qml(window, "dialog.confirmedCount")) == (2, 1)


# ── 完整動態 ─────────────────────────────────────────────────────────────────

def test_full_motion_runs_without_warnings_and_ends_in_the_same_state(harness):
    """其他測試都把時間設成 0。這裡用真的時間（animationScale 1）跑一次每一種動態，確認沒有警告、最後的狀態一樣。"""
    notifier = FakeNotifier()
    window = harness.load(TOAST_HOST + CONFIRM_DIALOG + BUSY_BUTTON.format(variant="primary", delay=0, min_show=0) + """
    TransferProgress { id: progress; objectName: "progress"; x: 300; y: 400; width: 300; label: "Saving recording…" }
""", notifier=notifier)
    window.setProperty("animationScale", 1)
    assert theme(window, "durEnter") == 200 and not theme(window, "reduceMotion")

    first = notifier.push("info", "Sent for analysis")  # M-2 進場
    notifier.push("error", "Analysis failed")
    older, newer = toast_titled(window, "Sent for analysis"), toast_titled(window, "Analysis failed")
    wait_until(lambda: older.opacity() == 1 and newer.opacity() == 1)
    wait_settled(window, older)
    wait_settled(window, newer)
    notifier.dismiss(first)  # M-3 退場、M-4 補位
    wait_until(lambda: qml(window, "host.height") == newer.height())
    wait_settled(window, newer)

    open_dialog(window)  # M-6 對話框
    wait_until(lambda: qml(window, "dialog.opacity") == 1 and qml(window, "dialog.scale") == 1)
    QTest.keyClick(window, Qt.Key.Key_Escape)
    wait_until(lambda: not qml(window, "dialog.visible"))

    button = find(window, "button")  # M-7 旋轉圖示、M-1 顏色
    button.setProperty("busy", True)
    wait_until(lambda: find(button, "busySpinner").isVisible())
    button.setProperty("busy", False)
    wait_until(lambda: qml(button, "background.color") == theme(window, "actionPrimary"))

    wait_until(find(window, "indeterminateSegment").isVisible)  # M-8 不定進度條
    assert qml(window, "dialog.cancelledCount") == 1


# ── Theme 與圖示 ─────────────────────────────────────────────────────────────

DURATIONS = ("durFast", "durBase", "durEnter", "durSplash", "durSpin", "durIndeterminate")


def test_theme_durations_follow_reduce_motion_and_animation_scale(harness):
    window = harness.load("")

    def durations():
        return [theme(window, name) for name in DURATIONS]

    window.setProperty("animationScale", 1)
    assert durations() == [100, 150, 200, 400, 1000, 1500]
    window.setProperty("animationScale", 0.5)
    assert durations() == [50, 75, 100, 200, 500, 750]
    window.setProperty("motionMode", 1)  # 減少動態：全部是 0
    assert theme(window, "reduceMotion") and durations() == [0] * 6
    assert [theme(window, name) for name in ("busyDelay", "busyMinShow", "slowAfter")] == [300, 500, 2000]
    window.setProperty("motionMode", 0)  # 跟隨系統
    assert theme(window, "reduceMotion") == theme(window, "systemReduceMotion")
    window.setProperty("motionMode", 2)
    window.setProperty("animationScale", 0)  # 測試用
    assert not theme(window, "reduceMotion") and durations() == [0] * 6
    assert [theme(window, name) for name in ("easeEnter", "easeExit", "easeMove")] == [
        qml(window, "Easing.OutCubic"), qml(window, "Easing.InCubic"), qml(window, "Easing.InOutCubic")]


def test_theme_transfer_text(harness):
    window = harness.load("")
    assert qml(window, "Theme.transferText(58.6e6, 124.0e6)") == "58.6 / 124.0 MB · 47%"
    assert qml(window, "Theme.transferText(124.0e6, 124.0e6)") == "124.0 / 124.0 MB · 100%"
    assert qml(window, "Theme.transferText(123.9e6, 124.0e6)") == "123.9 / 124.0 MB · 99%"  # 還沒傳完不顯示 100%
    assert qml(window, "Theme.transferText(-1, 124.0e6)") == "0.0 / 124.0 MB · 0%"
    assert qml(window, "Theme.transferText(5e6, 0)") == ""  # 不知道總量


@pytest.mark.parametrize("token, expected", [
    ("chromeWindow", "#0B0C0E"), ("sidebarBg", "#333333"), ("chromeBorder", "#596069"), ("chromeIcon", "#E6E8EB"),
    ("contentBg", "#FFFFFF"), ("track", "#E0E0E0"), ("divider", "#D1D1D1"), ("textPrimary", "#333333"),
    ("textSecondary", "#596069"), ("actionPrimary", "#333333"), ("actionDanger", "#C62828"),
    ("successOnLight", "#2E7D32"), ("successTint", "#E8F5E9"), ("successOnDark", "#81C784"),
    ("infoOnLight", "#1565C0"), ("infoTint", "#E3F2FD"), ("infoOnDark", "#90CAF9"),
    ("warningOnLight", "#9C5100"), ("warningTint", "#FFF3E0"), ("warningOnDark", "#FFB74D"),
    ("errorOnLight", "#C62828"), ("errorTint", "#FFEBEE"), ("errorOnDark", "#FF9A90"),
    ("recording", "#E53935"), ("focusOnLight", "#1565C0"), ("focusOnDark", "#E6E8EB"),
])
def test_theme_colors_match_the_design_system(harness, token, expected):
    window = harness.load("")
    assert theme(window, token) == QColor(expected)


def test_theme_sizes_and_layers_match_the_design_system(harness):
    window = harness.load("")
    expected = {"fontBody": 16, "fontCaption": 14, "fontTitleSmall": 20, "spaceXS": 5, "spaceS": 10, "spaceM": 16,
                "spaceXL": 24, "radiusS": 5, "radiusM": 10, "controlHeight": 40, "controlHeightL": 50,
                "chipHeight": 24, "iconS": 16, "iconM": 20, "toastWidth": 360, "hitMin": 32,
                "zContent": 0, "zPageOverlay": 10, "zDialog": 100, "zToast": 200}
    assert {name: theme(window, name) for name in expected} == expected


def test_qmldir_declares_theme_as_singleton_and_the_eleven_components():
    lines = (COMPONENTS / "qmldir").read_text(encoding="utf-8").splitlines()
    assert lines == ["singleton Theme 1.0 Theme.qml"] + [
        f"{name} 1.0 {name}.qml" for name in ("Toast", "ToastHost", "StatusChip", "TransferProgress", "BusyButton",
                                               "EmptyState", "InlineError", "FieldError", "ConfirmDialog",
                                               "SidebarButton", "SettingsView")]


def test_component_qml_files_use_crlf():
    for path in sorted(COMPONENTS.glob("*.qml")):
        data = path.read_bytes()
        assert data.count(b"\n") == data.count(b"\r\n") > 0, path.name


ICON_NAME = re.compile(r"^(?P<lucide>[a-z-]+)_(?P<token>[A-Za-z]+)\.svg$")


def test_icons_are_lucide_svgs_precoloured_with_theme_tokens(harness):
    """圖示檔名是「Lucide 名稱_Theme token」，線條顏色要和那個 token 一樣（DS-13）。"""
    window = harness.load("")
    paths = sorted(ICONS.rglob("*.svg"))  # 共用元件的 15 個，加上 nav/ 裡側邊欄的 15 個（T23）
    assert len(paths) == 30
    for path in paths:
        match = ICON_NAME.match(path.name)
        assert match, path.name
        svg = path.read_text(encoding="utf-8")
        assert svg.startswith("<!-- @license lucide-static v1.58.0 - ISC -->"), path.name
        assert "currentColor" not in svg and "<script" not in svg, path.name
        strokes = re.findall(r'stroke="(#[0-9A-F]{6})"', svg)
        assert len(strokes) == 1, path.name
        assert QColor(strokes[0]) == theme(window, match["token"]), path.name


def test_every_icon_is_used_and_every_used_icon_exists():
    used = set()
    for path in COMPONENTS.glob("*.qml"):
        used |= set(re.findall(r"icons/([\w-]+\.svg)", path.read_text(encoding="utf-8")))
    assert used == {path.name for path in ICONS.glob("*.svg")}


def test_every_nav_icon_is_used_and_every_used_nav_icon_exists():
    """側邊欄（SidebarButton）的每個按鈕有亮色（chromeIcon）的一份；會變成目前頁面的按鈕另有深色
    （navItemCurrentText）的一份。Quit 不會是目前頁面，只有亮色（T23 第 4 節）。"""
    names = set()
    for shell in ("server_home.qml", "admin_home.qml"):
        names |= set(re.findall(r'iconName:\s*"([\w-]+)"', (PAGES / shell).read_text(encoding="utf-8")))
    expected = ({f"{name}_chromeIcon.svg" for name in names}
                | {f"{name}_navItemCurrentText.svg" for name in names - {"log-out"}})
    assert expected == {path.name for path in (ICONS / "nav").glob("*.svg")}


def test_icons_render_in_their_colour_without_warnings(harness):
    """Qt 的 SVG 外掛畫得出來，而且沒有任何警告；畫出來的線條就是預先上的顏色。"""
    messages = []
    previous = qInstallMessageHandler(lambda mode, context, message: messages.append((context.category, message)))
    try:
        names = sorted(path.relative_to(ICONS).as_posix() for path in ICONS.rglob("*.svg"))  # 含 nav/
        window = harness.load("""
    Grid {
        x: 0; y: 60
        columns: 8
        spacing: 4
        Repeater {
            objectName: "iconRepeater"
            model: iconNames
            Rectangle {
                required property string modelData
                width: 48; height: 48
                color: "#808080"
                Image {
                    objectName: "image"
                    anchors.fill: parent
                    sourceSize: Qt.size(48, 48)
                    source: iconDir + "/" + parent.modelData
                }
            }
        }
    }
""", iconNames=names, iconDir=QUrl.fromLocalFile(str(ICONS)).toString())
        repeater = find(window, "iconRepeater")
        cells = [qml(repeater, f"itemAt({i})") for i in range(len(names))]  # 留著參照，子物件才不會失效
        images = [find(cell, "image") for cell in cells]
        wait_until(lambda: all(qml(image, "status === Image.Ready") for image in images))
        wait_until(lambda: not window.grabWindow().isNull())
        picture = window.grabWindow()
    finally:
        qInstallMessageHandler(previous)

    assert [(category, message) for category, message in messages if "svg" in f"{category} {message}".lower()] == []
    for name, image in zip(names, images):
        expected = QColor(re.search(r'stroke="(#[0-9A-F]{6})"', (ICONS / name).read_text(encoding="utf-8"))[1])
        top_left = image.mapToScene(QPointF(0, 0)).toPoint()
        close = sum(
            1 for x in range(top_left.x(), top_left.x() + 48) for y in range(top_left.y(), top_left.y() + 48)
            if max(abs(picture.pixelColor(x, y).red() - expected.red()),
                   abs(picture.pixelColor(x, y).green() - expected.green()),
                   abs(picture.pixelColor(x, y).blue() - expected.blue())) <= 16)
        assert close >= 30, (name, close)
