pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 background，要用 Basic 樣式（T13）。在這個檔案裡 import，用它的頁面不必換掉自己的樣式
import QtQuick.Controls.Basic
import "../components"

// 日期欄：兩端的服務查詢共用（UX-46），長得一樣、錯誤的樣子也一樣。
// - 280 寬的膠囊，顏色都取自 Theme。取得焦點時框線是 focusOnLight，格式不對（invalid）時是 errorOnLight（DS-27）。
// - 錯誤的文字由頁面放在欄位下方的 FieldError；這裡把它寫進 Accessible.description（errorText）。
// - placeholder 不能當標籤用：頁面要給 accessibleName（DS-31）。
TextField {
    id: field

    property bool invalid: false
    property string accessibleName: ""
    property string errorText: ""

    implicitWidth: 280
    implicitHeight: Theme.controlHeight
    leftPadding: Theme.spaceM
    rightPadding: Theme.spaceM
    font.pixelSize: Theme.fontBody
    color: Theme.textPrimary
    placeholderTextColor: Theme.textSecondary
    selectByMouse: true
    Accessible.name: accessibleName
    Accessible.description: invalid ? errorText : ""

    background: Rectangle {
        radius: height / 2
        color: Theme.contentBg
        border.width: field.activeFocus || field.invalid ? 2 : 1
        border.color: field.invalid ? Theme.errorOnDark : field.activeFocus ? Theme.focusOnDark : Theme.textSecondary
    }
}
