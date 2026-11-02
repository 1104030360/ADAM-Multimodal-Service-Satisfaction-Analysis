import QtQuick
import "../components"

// 設定頁（管理員端）。畫面和行為在 components/SettingsView.qml，兩端共用（UX-46）
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
    }
}
