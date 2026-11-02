pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 background，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 完成、繼續練、改目標、撤回一項改善目標（管理員，PLAN1 W2，T45；T47 依設計稿改名）：Coaching 頁明細的
// Mark Complete、Keep Practicing（Next step 卡片）、Edit Goal、Cancel Goal（明細最上面）才打開。
// - complete：寫看了後續服務之後的結論（必填）。沒有後續服務時沒有這個按鈕；資料庫再擋一次。
// - keep：還要練什麼（必填）、目標（預先填目前的）、新的期限（預設兩週後）。看過的後續服務和它的時間註記留在歷史，
//   下一次要選更晚的一筆。員工要再按一次 Got It。
// - edit：改目標或期限、寫原因（必填）。和 keep 是同一個程序（sp_coaching_adjust）：連著的後續服務也會移到歷史。
// - cancel：原因（必填）。目標和歷史都留著，狀態是 Cancelled。安全的選項（Keep Goal）是預設（DS-28）。
// 檢查和寫入都在 coachingModel（db lane）；寫好了（saved）就關掉，失敗的原因寫在按鈕上面，填的字留著。
Dialog {
    id: root

    property var vm: null
    property var current: ({})
    property string mode: "complete"        // complete、keep、edit、cancel

    readonly property string actionId: current.actionId !== undefined ? current.actionId : ""
    readonly property string op: mode === "keep" || mode === "edit" ? "adjust" : mode
    readonly property bool saving: vm !== null && vm.saving.indexOf(actionId) >= 0
    readonly property var lastError: vm ? vm.actionError : ({})
    readonly property string errorText: lastError.actionId === actionId && lastError.op === op ? lastError.text : ""
    readonly property string staffFirst: current.staffFirst !== undefined ? current.staffFirst : ""
    readonly property bool hasFollowUp: current.followUp !== undefined && current.followUp.serviceId !== undefined
    readonly property string followUpLabel: hasFollowUp ? current.followUp.whenText : ""
    // 欄位（測試從外面操作用）
    readonly property alias reasonField: reasonArea
    readonly property alias goalField: goalInput
    readonly property alias dueField: dueInput
    readonly property alias submitButton: submit

    function ask(which) {
        mode = which
        reasonArea.text = ""
        goalInput.text = current.goal !== undefined ? current.goal : ""
        dueInput.text = vm ? vm.defaultDue() : ""
        if (vm)
            vm.clearActionError()
        open()
    }

    function send() {
        if (!vm || saving)
            return
        if (mode === "complete")
            vm.complete(actionId, reasonArea.text)
        else if (mode === "keep")
            vm.keepPracticing(actionId, reasonArea.text, goalInput.text, dueInput.text)
        else if (mode === "edit")
            vm.adjust(actionId, reasonArea.text, goalInput.text, dueInput.text)
        else
            vm.cancelAction(actionId, reasonArea.text)
    }

    title: mode === "complete" ? qsTr("Mark Complete") : mode === "keep" ? qsTr("Keep Practicing")
         : mode === "edit" ? qsTr("Edit Goal") : qsTr("Cancel Goal")
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    z: Theme.zDialog
    anchors.centerIn: Overlay.overlay
    width: Math.min(560, Overlay.overlay ? Overlay.overlay.width - 2 * Theme.spaceXL : 560)
    padding: Theme.spaceXL
    header: null
    footer: null

    Connections {
        target: root.vm
        function onSaved(actionId, op) {
            if (actionId === root.actionId && op === root.op)
                root.close()
        }
    }

    background: Rectangle {
        color: Theme.card
        radius: Theme.radiusCard
        border.color: Theme.cardBorder
        border.width: 1
    }

    Overlay.modal: Rectangle {
        color: Theme.scrim
    }

    component FieldLabel: Text {
        color: Theme.textSecondary
        font.pixelSize: Theme.fontCaption
        font.weight: Font.DemiBold
    }

    contentItem: ColumnLayout {
        spacing: Theme.spaceM

        Text {
            objectName: "dialogTitle"
            Layout.fillWidth: true
            text: root.title
            color: Theme.textPrimary
            font.pixelSize: Theme.fontTitleSmall
            font.weight: Font.Bold
            Accessible.role: Accessible.Heading
            Accessible.name: text
        }

        Text {
            objectName: "dialogExplanation"
            Layout.fillWidth: true
            text: {
                switch (root.mode) {
                case "complete":
                    return qsTr("You watched the follow-up from %1. Write what you saw. %2 can read it in My Actions.")
                           .arg(root.followUpLabel).arg(root.staffFirst)
                case "keep":
                    return qsTr("Say what still needs practice and set the next date. The service you watched stays in the history, and the next follow-up must be a later service.")
                case "edit":
                    return root.hasFollowUp
                           ? qsTr("Change the goal or the date, and say why. %1 confirms the new goal again. The linked follow-up moves to the history.")
                             .arg(root.staffFirst)
                           : qsTr("Change the goal or the date, and say why. %1 confirms the new goal again.").arg(root.staffFirst)
                }
                return qsTr("Use this when the goal was set by mistake or no longer applies. The goal and its history stay, and %1 sees it as cancelled.")
                       .arg(root.staffFirst)
            }
            color: Theme.textMuted
            font.pixelSize: Theme.fontCaption
            wrapMode: Text.Wrap
        }

        FieldLabel {
            text: root.mode === "complete" ? qsTr("Conclusion") : root.mode === "keep" ? qsTr("What still needs practice")
                : root.mode === "edit" ? qsTr("Why change it") : qsTr("Why cancel the goal")
        }
        CoachingTextArea {
            id: reasonArea
            objectName: "reasonArea"
            Layout.fillWidth: true
            minimumLines: 3
            focus: true
            placeholderText: root.mode === "complete" ? qsTr("For example: Explained the next step and checked the customer understood.")
                                                      : ""
            Accessible.name: root.mode === "complete" ? qsTr("Conclusion") : qsTr("Reason")
            onTextChanged: {
                if (root.errorText !== "" && root.vm)
                    root.vm.clearActionError()
            }
        }

        FieldLabel {
            visible: root.mode === "keep" || root.mode === "edit"
            text: qsTr("Goal")
        }
        CoachingTextArea {
            id: goalInput
            objectName: "goalInput"
            Layout.fillWidth: true
            visible: root.mode === "keep" || root.mode === "edit"
            minimumLines: 2
            Accessible.name: qsTr("Goal")
        }

        FieldLabel {
            visible: root.mode === "keep" || root.mode === "edit"
            text: qsTr("Follow up by")
        }
        DateField {
            id: dueInput
            objectName: "dueInput"
            visible: root.mode === "keep" || root.mode === "edit"
            popupZ: Theme.zDialogOverlay   // 月曆疊在對話框上面，不然點不到（T43 第 5 節）
            Accessible.name: qsTr("Follow up by")
        }

        // 失敗的原因（例如別人剛改過、連不上資料庫）：填的字留著
        FieldError {
            objectName: "dialogError"
            Layout.fillWidth: true
            visible: hasError
            text: root.errorText
        }

        RowLayout {
            Layout.alignment: Qt.AlignRight
            Layout.topMargin: 8
            spacing: 12

            BusyButton {
                objectName: "closeDialogButton"
                variant: root.mode === "cancel" ? "primary" : "secondary"
                text: root.mode === "cancel" ? qsTr("Keep Goal") : qsTr("Close")
                onClicked: root.close()
            }

            BusyButton {
                id: submit
                objectName: "submitDialogButton"
                variant: root.mode === "cancel" ? "danger" : "primary"
                text: root.mode === "edit" ? qsTr("Save Goal") : root.title
                busyText: qsTr("Saving…")
                busy: root.saving
                onClicked: root.send()
            }
        }
    }
}
