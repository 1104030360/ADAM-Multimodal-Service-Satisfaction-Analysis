import QtQuick
// 自訂了 RadioButton 的 indicator、contentItem、background，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic

// 設定頁的一個選項（新版介面 PLAN 6-7）：整塊方塊（52 高）都可以點，左邊是單選鈕的圓圈。
// 選到的方塊底色亮一階、框線變亮，圓圈裡有點（不只靠顏色）。同一組用 ButtonGroup，已經選的那個再按也不會被取消。
// 放在 SettingsGroup 裡，寬度由那一組平分。
RadioButton {
    id: option

    implicitHeight: 52
    leftPadding: Theme.spaceM
    rightPadding: Theme.spaceM
    spacing: 12
    focusPolicy: Qt.StrongFocus
    font.pixelSize: Theme.fontInput
    font.weight: Font.DemiBold

    Accessible.role: Accessible.RadioButton
    Accessible.name: text
    Keys.onReturnPressed: click()
    Keys.onEnterPressed: click()

    indicator: Rectangle {
        x: option.leftPadding
        y: (option.height - height) / 2
        implicitWidth: 18
        implicitHeight: 18
        radius: width / 2
        color: Theme.field
        border.width: 2
        border.color: option.checked ? Theme.actionPrimary : Theme.fieldBorder     // 沒選的圓圈和方塊 3:1 以上

        Rectangle {
            anchors.centerIn: parent
            width: 8
            height: 8
            radius: 4
            visible: option.checked
            color: Theme.actionPrimary
        }
    }

    contentItem: Text {
        leftPadding: option.indicator.width + option.spacing
        verticalAlignment: Text.AlignVCenter
        text: option.text
        font: option.font
        color: Theme.textPrimary
        elide: Text.ElideRight
        Accessible.ignored: true    // 選項自己就叫這個名字
    }

    background: Rectangle {
        radius: 12
        color: option.checked ? Theme.tileSelected : Theme.tile
        border.width: 1
        border.color: option.checked ? Theme.textFaint : option.hovered ? Theme.fieldBorder : Theme.tileBorder

        Rectangle {     // 鍵盤焦點框（DS-30）：2 px、往外 2 px。測試用 background.children[0] 找它
            visible: option.visualFocus
            anchors.fill: parent
            anchors.margins: -4
            radius: parent.radius + 4
            color: "transparent"
            border.color: Theme.focusOnDark
            border.width: 2
        }
    }
}
