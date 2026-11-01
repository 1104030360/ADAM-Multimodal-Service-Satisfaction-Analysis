"""登入之後的一段使用（new-feature N1）：Log Out、Quit 都先問，⌘Q 也走同一條路；登出時收掉這個帳號的東西。

兩端的 home（ServerHome、AdminHome）各有一個 ShellSession（home.session），launch() 載入外殼之後呼叫 attach()。

- **⌘Q**：QML 外殼的 onClosing 收不到（T20）。外殼開著的時候向 QuitGuard 登記，擋下來之後發 quitRequested，
  外殼的 Quit 按鈕照按下 Quit 的方式問（「Quit ADAM?」；上傳中是原本的「Quit while uploading?」）。
  使用者選了 Quit，外殼先呼叫 allowQuit() 拿掉登記，Qt.quit() 才不會又被擋下來。
- **登出**（end()）：取消這個帳號全部的背景工作、清掉通知，發 loggedOut（MainWindow 讓登入視窗再出現），
  再刪掉外殼視窗。視窗的 destroyed 放開 home（launch() 的存活規則不變）；這裡再把 launch() 註冊的 context property
  設成 null，下一個帳號登入時，只有另一端才有的名稱（deviceFinder、serviceModel、accountManager…）不會還指著舊的物件。
  清掉的時間點在視窗刪掉之後：外殼和頁面還在的時候清，綁定會讀到 null，印出一堆警告。
"""
import logging

from PySide6.QtCore import QObject, Signal, Slot

from .record_widget import QuitGuard

logger = logging.getLogger(__name__)


class ShellSession(QObject):
    """一個外殼開著的這段時間。parent 是 home，services 是 AppServices。"""

    quitRequested = Signal()    # ⌘Q（或 Dock 的「結束」）被 QuitGuard 擋下來了：外殼的 Quit 按鈕問使用者
    loggedOut = Signal()        # 登出了，外殼正要關掉：MainWindow 讓登入視窗再出現

    def __init__(self, services, parent):
        super().__init__(parent)
        self.services = services
        self._context = None
        self._registered = {}   # launch() 註冊的 context property：名稱 → 物件
        self._window = None
        self._ended = False

    @property
    def ended(self):
        """已經登出了（end() 呼叫過）。"""
        return self._ended

    def attach(self, context, registered, window):
        """launch() 載入外殼之後呼叫：開始攔 ⌘Q；記下註冊了哪些 context property，登出時清掉。"""
        self._context = context
        self._registered = dict(registered)
        self._window = window
        window.destroyed.connect(self._window_destroyed)
        QuitGuard.instance().set_blocking(self, True)

    def quit_blocked(self):
        """QuitGuard 擋下 QEvent.Quit 之後，在下一輪事件呼叫（不在 AppKit 的回呼裡開對話框，T20）。"""
        self.quitRequested.emit()

    @Slot()
    def allowQuit(self):
        """使用者選了 Quit（或要登出了）：之後的 QEvent.Quit 不再攔。"""
        QuitGuard.instance().set_blocking(self, False)

    def stop(self):
        """外殼關掉了（視窗被刪掉，例如結束程式、測試收掉 engine）：不再攔 ⌘Q。"""
        self.allowQuit()

    def end(self):
        """登出（外殼問過了，home 也停掉了自己的東西）。回傳 False 表示已經登出過。

        順序：取消背景工作 → 清掉通知 → 登入視窗出現 → 外殼藏起來、刪掉。登入視窗先出現，app 一直有看得到的視窗，
        不會因為「最後一個視窗關了」就結束。
        """
        if self._ended:
            return False
        self._ended = True
        self.allowQuit()
        # 同時只有一個外殼：在跑的工作（查詢、下載、上傳、送出分析、監看）都是這個帳號的。結果不再交回
        self.services.tasks.cancelAll()
        # 下一個帳號不該看到這個帳號的通知（例如 Analysis complete 的 View Results）。沒存完的錄影記在本機，
        # 同一個帳號下次登入時再跳出來（T26）
        self.services.notifier.clear()
        self.loggedOut.emit()
        window, self._window = self._window, None
        if window is not None:
            window.hide()
            window.deleteLater()    # 不能當場刪：登出是從這個視窗的 QML（按鈕的 handler）呼叫的
        # 登入視窗開著的時候，不留這個帳號的偏好（S1、S2）：還沒寫入的音量先寫進它自己的 key，其他回到預設。
        # 外殼已經藏起來了：它的視窗模式變化不會記到別的帳號（UiPrefs 只記現在這個帳號、還開著的視窗）
        self.services.ui_prefs.useAccount("")
        return True

    @Slot()
    def _window_destroyed(self):
        """外殼的視窗刪掉了。登出的話，把這個外殼註冊的 context property 設成 null（已經被新的外殼換掉的不動）。"""
        registered, self._registered = self._registered, {}
        if not self._ended or self._context is None:
            return  # 結束程式、測試收掉 engine：用不到了，不必清
        for name, obj in registered.items():
            if self._context.contextProperty(name) is obj:
                self._context.setContextProperty(name, None)
        logger.info("logged out; cleared %d context properties", len(registered))
