import QtQuick
import "../components"

// 輔導的狀態標籤（PLAN1 W2；T47 依設計稿「Coaching, redesigned」改版）：一塊淡底加字，字就是狀態（顏色不是唯一的線索）。
// - 管理員的清單一次只說下一步（coaching.admin_chip）：Pick a Follow-up（有能選的後續服務）、Waiting for a Later Service
//   （還沒有）、Ready to Review（有後續服務，等你看）、Overdue、Completed、Cancelled；還不知道的是 Open。
// - 服務人員（coaching.staff_chip）：New（還沒按 Got It）、In Progress、Completed、Cancelled。
// 標籤本身不給螢幕閱讀器（Accessible.ignored）；頁面把 label 寫進那一列的說明。
Rectangle {
    id: root

    property string actionState: "open"

    readonly property string label: {
        switch (actionState) {
        case "pick": return qsTr("Pick a Follow-up")
        case "waiting": return qsTr("Waiting for a Later Service")
        case "review": return qsTr("Ready to Review")
        case "overdue": return qsTr("Overdue")
        case "completed": return qsTr("Completed")
        case "cancelled": return qsTr("Cancelled")
        case "new": return qsTr("New")
        case "progress": return qsTr("In Progress")
        }
        return qsTr("Open")
    }
    readonly property color textColor: {
        switch (actionState) {
        case "pick": return Theme.warningOnDark
        case "review":
        case "new": return Theme.infoOnDark
        case "overdue": return Theme.errorOnDark
        case "completed": return Theme.successOnDark
        case "cancelled": return Theme.textMuted
        case "waiting":
        case "progress": return Theme.textSecondary
        }
        return Theme.textPrimary
    }

    implicitWidth: chipText.implicitWidth + 20
    implicitHeight: Theme.chipHeight
    radius: height / 2
    color: {
        switch (actionState) {
        case "pick": return Theme.warningTint
        case "review":
        case "new": return Theme.infoTint
        case "overdue": return Theme.errorTint
        case "completed": return Theme.successTint
        case "waiting":
        case "progress": return Theme.tileSelected
        }
        return Theme.tile
    }

    Accessible.ignored: true

    Text {
        id: chipText
        objectName: "coachingChipLabel"
        anchors.centerIn: parent
        text: root.label
        color: root.textColor
        font.pixelSize: Theme.fontLabel
        font.weight: Font.Bold
    }
}
