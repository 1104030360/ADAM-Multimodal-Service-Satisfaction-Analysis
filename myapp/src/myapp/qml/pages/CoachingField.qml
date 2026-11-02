import QtQuick
// 自訂了 background，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import "../components"

// 輔導頁面的一行輸入框（PLAN1 W2，T45）：目標、時間註記的說明。和設定頁的輸入框同一個樣子：高 44、圓角 12、
// 框線 fieldBorder（和卡片 3.1:1），焦點時 2 px 的 focusOnDark，有錯（invalid）時 2 px 的 errorOnDark（DS-27）。
// 欄位上面的標籤由頁面放；placeholder 不能當標籤用，頁面要設 Accessible.name（DS-31）。
TextField {
    id: field

    property bool invalid: false
    property string errorText: ""

    implicitHeight: Theme.controlHeight
    leftPadding: 14
    rightPadding: 14
    color: Theme.textPrimary
    placeholderTextColor: Theme.textFaint
    selectionColor: Theme.chromeSelection
    selectedTextColor: Theme.textOnDark
    font.pixelSize: Theme.fontInput
    selectByMouse: true
    Accessible.description: invalid ? errorText : ""

    background: Rectangle {
        implicitWidth: 200
        radius: 12
        color: Theme.field
        border.width: field.invalid || field.activeFocus ? 2 : 1
        border.color: field.invalid ? Theme.errorOnDark : field.activeFocus ? Theme.focusOnDark : Theme.fieldBorder
    }
}
