import QtQuick
// 自訂了 background，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import "../components"

// 輔導頁面的多行輸入框（PLAN1 W2，T45；T47）：輔導面板的目標和為什麼、結論、調整和撤回的原因。外觀和 CoachingField 一樣，
// 至少 minimumLines 行高，字多時跟著長高（不在框裡捲動：整頁會捲）。Tab 移到下一個控制項，不輸入 Tab 字元。
TextArea {
    id: area

    property bool invalid: false
    property string errorText: ""
    property int minimumLines: 3

    implicitHeight: Math.max(contentHeight + topPadding + bottomPadding,
                             minimumLines * 20 + topPadding + bottomPadding)
    topPadding: 11
    bottomPadding: 11
    leftPadding: 14
    rightPadding: 14
    wrapMode: TextEdit.Wrap
    color: Theme.textPrimary
    placeholderTextColor: Theme.textFaint
    selectionColor: Theme.chromeSelection
    selectedTextColor: Theme.textOnDark
    font.pixelSize: Theme.fontInput
    selectByMouse: true
    Accessible.description: invalid ? errorText : ""
    Keys.onTabPressed: (event) => {
        const next = area.nextItemInFocusChain(true)
        if (next)
            next.forceActiveFocus(Qt.TabFocusReason)
        event.accepted = true
    }
    Keys.onBacktabPressed: (event) => {
        const previous = area.nextItemInFocusChain(false)
        if (previous)
            previous.forceActiveFocus(Qt.BacktabFocusReason)
        event.accepted = true
    }

    background: Rectangle {
        implicitWidth: 200
        radius: 12
        color: Theme.field
        border.width: area.invalid || area.activeFocus ? 2 : 1
        border.color: area.invalid ? Theme.errorOnDark : area.activeFocus ? Theme.focusOnDark : Theme.fieldBorder
    }
}
