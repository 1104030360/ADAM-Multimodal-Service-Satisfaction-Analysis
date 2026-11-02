pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 background，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 完成、調整、撤回一項改善行動（管理員，PLAN1 W2，T45）：Coaching 頁明細的 Complete、Adjust Goal、Cancel Goal 才打開。
// - Complete：寫看了後續服務之後的結論（必填）。沒有後續服務時按鈕本來就停用；資料庫再擋一次。
// - Adjust Goal：原因（必填）、新的目標（預先填目前的）、新的期限（預設兩週後）。舊的值和看過的後續服務留在歷史，
//   下一次要選更晚的一筆。員工要再按一次 Acknowledge。
// - Cancel Goal：原因（必填）。行動和歷史都留著，狀態是 Cancelled。安全的選項（Keep Goal）是預設（DS-28）。
// 檢查和寫入都在 coachingModel（db lane）；寫好了（saved）就關掉，失敗的原因寫在按鈕上面，填的字留著。
Dialog {
    id: root

    property var vm: null
    property var current: ({})
    property string mode: "complete"        // complete、adjust、cancel

    readonly property string actionId: current.actionId !== undefined ? current.actionId : ""
    readonly property bool saving: vm !== null && vm.saving.indexOf(actionId) >= 0
    readonly property var lastError: vm ? vm.actionError : ({})
    readonly property string errorText: lastError.actionId === actionId && lastError.op === mode ? lastError.text : ""
    readonly property string staffName: current.staffName !== undefined ? current.staffName : ""
    readonly property string followUpLabel: current.followUp !== undefined && current.followUp.label !== undefined
                                            ? current.followUp.label : ""
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
        else if (mode === "adjust")
            vm.adjust(actionId, reasonArea.text, goalInput.text, dueInput.text)
        else
            vm.cancelAction(actionId, reasonArea.text)
    }

    title: mode === "complete" ? qsTr("Complete Action") : mode === "adjust" ? qsTr("Adjust Goal") : qsTr("Cancel Goal")
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
            if (actionId === root.actionId && op === root.mode)
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
            text: root.mode === "complete"
                  ? qsTr("You reviewed %1. Write what you saw. %2 can read your conclusion.").arg(root.followUpLabel)
                                                                                              .arg(root.staffName)
                  : root.mode === "adjust"
                  ? qsTr("Set the goal and date for the next try. The old goal and the reviewed follow-up stay in the history, and the next follow-up must be a later service.")
                  : qsTr("Use this when the goal was set by mistake or no longer applies. The goal and its history stay, and %1 sees it as cancelled.")
                    .arg(root.staffName)
            color: Theme.textMuted
            font.pixelSize: Theme.fontCaption
            wrapMode: Text.Wrap
        }

        FieldLabel {
            text: root.mode === "complete" ? qsTr("Conclusion") : root.mode === "adjust" ? qsTr("Why adjust the goal")
                                                                                       : qsTr("Why cancel the goal")
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
            visible: root.mode === "adjust"
            text: qsTr("New goal")
        }
        CoachingField {
            id: goalInput
            objectName: "goalInput"
            Layout.fillWidth: true
            visible: root.mode === "adjust"
            maximumLength: 300
            Accessible.name: qsTr("New goal")
        }

        FieldLabel {
            visible: root.mode === "adjust"
            text: qsTr("Follow up by")
        }
        DateField {
            id: dueInput
            objectName: "dueInput"
            visible: root.mode === "adjust"
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
                text: root.title
                busyText: qsTr("Saving…")
                busy: root.saving
                onClicked: root.send()
            }
        }
    }
}
