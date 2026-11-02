import QtQuick
import "../components"

// 設定頁（服務人員端）。畫面和行為在 components/SettingsView.qml，兩端共用（UX-46）
Item {
    id: page

    property var window: null

    function setWindow(win) {
        window = win
    }

    SettingsView {
        id: settings
        objectName: "settingsView"
        anchors.fill: parent
        window: page.window
        prefs: uiPrefs
        notificationsAvailable: true    // 服務人員端才有分析完成的通知（S2）
        devices: typeof deviceFinder !== "undefined" && deviceFinder ? deviceFinder.setup : null  // 設備設定（T41）
    }
}
