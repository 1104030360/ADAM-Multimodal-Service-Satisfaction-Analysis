import QtQuick

// 設定頁的 Help 區塊（S3 版本、連線檢查、診斷摘要；S5 本機儲存）。
// 還沒做：不顯示，也不佔位置（new-feature PLAN 13.1：尚未實作的功能不顯示空白區塊）。
// 做的時候換成 SettingsSection { title: qsTr("Help") ... }，和 SettingsGeneral.qml 一樣。
Item {
    property var window: null       // 外殼的視窗（頁面的 setWindow）

    visible: false
}
