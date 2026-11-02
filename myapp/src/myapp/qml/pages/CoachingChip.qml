import QtQuick
import "../components"

// 改善行動的狀態標籤（PLAN1 W2，T45）：Open、Overdue（進行中、期限已過）、Completed、Cancelled。
// 和服務狀態的標籤（StatusChip）同一個外觀：顏色只表示狀態，旁邊一定有圖示和字（顏色不是唯一的線索）。
// 標籤本身不給螢幕閱讀器（Accessible.ignored）；頁面把 label 寫進那一列的名稱。
Rectangle {
    id: root

    property string actionState: "open"      // open、overdue、completed、cancelled（coaching.chip_state）

    readonly property string label: actionState === "overdue" ? qsTr("Overdue")
                                  : actionState === "completed" ? qsTr("Completed")
                                  : actionState === "cancelled" ? qsTr("Cancelled")
                                  : qsTr("Open")
    readonly property color textColor: actionState === "overdue" ? Theme.warningOnDark
                                     : actionState === "completed" ? Theme.successOnDark
                                     : actionState === "cancelled" ? Theme.textMuted
                                     : Theme.textPrimary

    implicitWidth: content.implicitWidth + 2 * Theme.spaceS
    implicitHeight: Theme.chipHeight
    radius: height / 2
    color: actionState === "overdue" ? Theme.warningTint
         : actionState === "completed" ? Theme.successTint
         : Theme.tile

    Accessible.ignored: true

    Row {
        id: content
        anchors.centerIn: parent
        spacing: Theme.spaceXS

        Image {
            objectName: "coachingChipIcon"
            anchors.verticalCenter: parent.verticalCenter
            width: 14
            height: 14
            sourceSize: Qt.size(14, 14)
            source: root.actionState === "overdue" ? "../components/icons/triangle-alert_warningOnDark.svg"
                  : root.actionState === "completed" ? "../components/icons/circle-check_successOnDark.svg"
                  : root.actionState === "cancelled" ? "../components/icons/circle-slash_textMuted.svg"
                  : "../components/icons/circle-dashed_textPrimary.svg"
        }

        Text {
            objectName: "coachingChipLabel"
            anchors.verticalCenter: parent.verticalCenter
            text: root.label
            color: root.textColor
            font.pixelSize: Theme.fontCaption
            font.weight: Font.DemiBold
        }
    }
}
