import QtQuick

// 設定頁的內容，服務人員端和管理員端共用（UX-46；新版介面 PLAN 6-7；new-feature PLAN 13.1）。
// 頁面標題和「改了立刻生效」的說明在外殼的頁首。最多四個區塊，由上到下：Account、General、Devices、Help，
// 每個區塊一張卡片、一個檔案（SettingsAccount.qml 等）。還沒做的區塊不顯示；內容比視窗高時整頁捲動。
Item {
    id: root

    property var window: null       // 外殼的視窗（頁面的 setWindow）
    property var prefs: null        // UiPrefs（context property uiPrefs）；程式結束時可能先變成 null
    property bool notificationsAvailable: false // 服務人員端：General 顯示分析完成通知、Dock 跳動的選項（S2）

    Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: sections.height
        boundsBehavior: Flickable.StopAtBounds
        clip: true

        Column {
            id: sections
            objectName: "settingsSections"
            width: Math.min(760, root.width)
            spacing: Theme.sectionGap

            SettingsAccount {
                objectName: "accountSettings"
                width: parent.width
                window: root.window
            }

            SettingsGeneral {
                objectName: "generalSettings"
                width: parent.width
                window: root.window
                prefs: root.prefs
                showNotifications: root.notificationsAvailable
            }

            SettingsDevices {
                objectName: "deviceSettings"
                width: parent.width
                window: root.window
            }

            SettingsHelp {
                objectName: "helpSettings"
                width: parent.width
                window: root.window
            }
        }
    }
}
