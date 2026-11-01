import QtQuick

// 設定頁的 Account 區塊（N3 帳號照片、S6 顯示名稱、登入帳號、密碼）。
// 還沒做：不顯示，也不佔位置（new-feature PLAN 13.1：尚未實作的功能不顯示空白區塊）。
// 做的時候換成 SettingsSection { title: qsTr("Account") ... }，和 SettingsGeneral.qml 一樣。
Item {
    property var window: null       // 外殼的視窗（頁面的 setWindow）

    visible: false
}
