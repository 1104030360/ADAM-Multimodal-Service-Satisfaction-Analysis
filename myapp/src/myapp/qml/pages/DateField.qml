pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 background，要用 Basic 樣式（T13）。在這個檔案裡 import，用它的頁面不必換掉自己的樣式
import QtQuick.Controls.Basic
import "../components"

// 日期欄：兩端的 Services 頁共用（UX-46），長得一樣、錯誤的樣子也一樣（設計稿 StaffServices、AdminServices）。
// - 高 44、圓角 12，左邊是月曆圖示，placeholder「YYYY-MM-DD」，等寬數字。框線是 fieldBorder（和卡片 3.1:1），
//   取得焦點時 2 px 的 focusOnDark，有錯（invalid）時 2 px 的 errorOnDark（DS-27）。
// - 錯誤的文字由頁面放在欄位下方的 FieldError；這裡把它寫進 Accessible.description（errorText）。
// - 欄位上面的標籤（From、To）由頁面放；placeholder 不能當標籤用，頁面要給 accessibleName（DS-31）。
TextField {
    id: field

    property bool invalid: false
    property string accessibleName: ""
    property string errorText: ""

    implicitWidth: 220
    implicitHeight: Theme.controlHeight
    leftPadding: 42             // 月曆圖示 18 + 左右的空白
    rightPadding: 14
    font.pixelSize: Theme.fontInput
    font.features: { "tnum": 1 }
    color: Theme.textPrimary
    placeholderText: qsTr("YYYY-MM-DD")
    placeholderTextColor: Theme.textFaint
    selectionColor: Theme.chromeSelection
    selectedTextColor: Theme.textTitle
    inputMethodHints: Qt.ImhPreferNumbers
    selectByMouse: true
    Accessible.name: accessibleName
    Accessible.description: invalid ? errorText : ""

    background: Rectangle {
        radius: 12
        color: Theme.field
        border.width: field.activeFocus || field.invalid ? 2 : 1
        border.color: field.invalid ? Theme.errorOnDark : field.activeFocus ? Theme.focusOnDark : Theme.fieldBorder

        Image {
            x: 14
            anchors.verticalCenter: parent.verticalCenter
            width: 18
            height: 18
            sourceSize: Qt.size(18, 18)
            source: "../components/icons/calendar_iconNeutral.svg"
            Accessible.ignored: true
        }
    }
}
