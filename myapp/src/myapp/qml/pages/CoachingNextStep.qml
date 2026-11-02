pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 Change 按鈕的 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 管理員明細的「Next step」（設計稿「Coaching, redesigned」，T47）：一次只有一件事，框的顏色跟著狀態。
// - pick：能選的後續服務一列一筆（時間、服務 ID、員工的分數），Use as Follow-up。
// - waiting：還沒有分析完成、比較晚的服務：說明＋Check Again（重查）。舊版查一次、沒有就停在灰掉的選單，
//   使用者以為壞了（T47 第 2 節）；現在打開這一項、回到這一頁、按 Check Again 都會重查。
// - review：連好的後續服務：Watch（打開它的明細，面板對準這一項，可以在它的影片上加時間註記）、Change（換一筆）；
//   Mark Complete（寫結論）、Keep Practicing（還要練什麼、新的目標和期限）。
// - completed、cancelled：結論或原因、誰、什麼時候。
// 第一次查超過 busyDelay 才寫「Checking for later services…」（DS-25）；查不到是錯誤＋Retry。
Rectangle {
    id: root

    property var vm: null
    property var current: ({})
    property bool changing: false       // 已經有後續服務時按了 Change：列出能換的
    property bool showAll: false        // 能選的很多時（最多 50 筆）先列最新的 5 筆，Show All 才全部列出
    readonly property int shortList: 5

    signal completeRequested()
    signal keepRequested()

    readonly property string actionId: current.actionId !== undefined ? current.actionId : ""
    readonly property bool hasFollowUp: current.followUp !== undefined && current.followUp.serviceId !== undefined
    readonly property string followUpId: hasFollowUp ? current.followUp.serviceId : ""
    readonly property bool optionsHere: vm !== null && vm.followUpFor === actionId && actionId !== ""
    readonly property string optionsState: optionsHere ? vm.followUpState : "loading"
    readonly property var options: optionsHere ? vm.followUpOptions : []
    readonly property bool saving: vm !== null && actionId !== "" && vm.saving.indexOf(actionId) >= 0
    readonly property var lastError: vm ? vm.actionError : ({})
    readonly property string errorText: lastError.actionId === actionId && actionId !== "" && lastError.op === "link"
                                        ? lastError.text : ""
    readonly property string staffFirst: current.staffFirst !== undefined ? current.staffFirst : ""
    property string chosen: ""          // 按了哪一列的 Use as Follow-up（那一個按鈕忙碌）
    readonly property bool wide: width >= 480   // 窄的時候（小視窗）每一列的按鈕排到下一行
    // 現在是哪一步
    readonly property string step: current.status === "completed" ? "completed"
                                 : current.status === "cancelled" ? "cancelled"
                                 : hasFollowUp && !changing ? "review"
                                 : optionsState === "ready" ? "pick"
                                 : optionsState === "empty" ? "waiting"
                                 : optionsState === "error" ? "error" : "loading"
    readonly property color accent: step === "pick" ? Theme.warningOnDark
                                  : step === "review" ? Theme.infoOnDark
                                  : step === "completed" ? Theme.successOnDark : Theme.textMuted

    implicitHeight: column.implicitHeight + 2 * 18
    radius: Theme.radiusTile
    color: Theme.panelInset
    border.width: 1
    border.color: step === "pick" ? Theme.warningOnDark : step === "review" ? Theme.infoOnDark
                : step === "completed" ? Theme.successBorder : Theme.actionSecondaryBorder
    Accessible.role: Accessible.Grouping
    Accessible.name: kicker.text + ": " + title.text

    onActionIdChanged: {
        changing = false
        showAll = false
        chosen = ""
    }
    onStepChanged: {
        if (step !== "loading")
            loadingWait.elapsed = false
    }

    Connections {
        target: root.vm
        function onSaved(actionId, op) {
            if (actionId === root.actionId && op === "link") {
                root.changing = false
                root.chosen = ""
            }
        }
    }

    // 第一次查超過 busyDelay 才說在查（DS-25）
    Timer {
        id: loadingWait
        property bool elapsed: false
        interval: Theme.busyDelay
        running: root.step === "loading" && !elapsed
        onTriggered: elapsed = true
    }

    // 一筆服務：時間（大）、服務 ID（小）
    component ServiceLabel: Column {
        property string whenText: ""
        property string serviceId: ""
        spacing: 0
        Text {
            text: parent.whenText
            color: Theme.textTitle
            font.pixelSize: Theme.fontInput
            font.weight: Font.DemiBold
            font.features: { "tnum": 1 }
        }
        Text {
            text: parent.serviceId
            color: Theme.textFaint
            font.pixelSize: Theme.fontSmall
            font.features: { "tnum": 1 }
        }
    }

    ColumnLayout {
        id: column
        x: 20
        y: 18
        width: parent.width - 40
        spacing: 14

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4

            Text {
                id: kicker
                objectName: "nextStepKicker"
                Layout.fillWidth: true
                text: root.step === "completed" ? qsTr("Completed")
                    : root.step === "cancelled" ? qsTr("Cancelled") : qsTr("Next step")
                color: root.accent
                font.pixelSize: Theme.fontLabel
                font.weight: Font.Bold
                font.capitalization: Font.AllUppercase
                font.letterSpacing: 1
            }
            Text {
                id: title
                objectName: "nextStepTitle"
                Layout.fillWidth: true
                text: {
                    switch (root.step) {
                    case "pick": return root.changing ? qsTr("Pick a different follow-up")
                                                      : qsTr("Pick a later service to check this goal")
                    case "waiting": return qsTr("Waiting for a later service")
                    case "review": return qsTr("Watch the follow-up, then decide")
                    case "error": return qsTr("Couldn't check for later services")
                    case "completed":
                    case "cancelled": return root.current.conclusion !== undefined ? root.current.conclusion : ""
                    }
                    return loadingWait.elapsed ? qsTr("Checking for later services…") : ""
                }
                color: Theme.textTitle
                font.pixelSize: Theme.fontBodyLarge
                font.weight: Font.Bold
                wrapMode: Text.Wrap
                Accessible.role: Accessible.Heading
                Accessible.name: text
            }
            Text {
                objectName: "nextStepText"
                Layout.fillWidth: true
                visible: text !== ""
                text: {
                    switch (root.step) {
                    case "waiting": return qsTr("When %1 records a new service and it's analyzed, you can pick it here.")
                                           .arg(root.staffFirst)
                    case "error": return root.vm ? qsTr("Check your network, then try again.") : ""
                    case "completed": return [qsTr("Follow-up: %1").arg(root.hasFollowUp ? root.current.followUp.whenText : ""),
                                              root.current.concludedText].join("\n")
                    case "cancelled": return root.current.concludedText !== undefined ? root.current.concludedText : ""
                    }
                    return ""
                }
                color: Theme.textMuted
                font.pixelSize: Theme.fontCaption
                lineHeight: 1.2
                wrapMode: Text.Wrap
            }
        }

        // ── pick：能選的後續服務
        ColumnLayout {
            objectName: "candidateList"
            Layout.fillWidth: true
            visible: root.step === "pick"
            spacing: 8

            Repeater {
                model: root.step !== "pick" ? [] : root.showAll ? root.options : root.options.slice(0, root.shortList)
                delegate: Rectangle {
                    id: candidate
                    required property var modelData
                    readonly property bool isCurrent: modelData.value === root.followUpId
                    objectName: "candidateRow"
                    Layout.fillWidth: true
                    implicitHeight: candidateRow.implicitHeight + 16
                    radius: 12
                    color: Theme.tile
                    border.width: 1
                    border.color: Theme.tileBorder
                    Accessible.role: Accessible.Grouping
                    Accessible.name: qsTr("%1, %2").arg(modelData.whenText).arg(modelData.value)

                    // 窄的時候（小視窗）按鈕排到下一行，不擠出卡片
                    GridLayout {
                        id: candidateRow
                        x: 14
                        y: 8
                        width: parent.width - 22
                        columns: root.wide ? 3 : 2
                        columnSpacing: 14
                        rowSpacing: 6

                        ServiceLabel {
                            Layout.row: 0
                            Layout.column: 0
                            Layout.fillWidth: true
                            whenText: candidate.modelData.whenText
                            serviceId: candidate.modelData.value
                        }
                        // 員工這一筆的總分和分數帶（設計稿「Staff 84.0 Fair」）；只是參考，完成要看影片、寫結論
                        Row {
                            objectName: "candidateScore"
                            Layout.row: 0
                            Layout.column: 1
                            spacing: 6
                            Accessible.role: Accessible.StaticText
                            Accessible.name: candidate.modelData.scoreText === "" ? qsTr("No staff score")
                                : qsTr("Staff %1 %2").arg(candidate.modelData.scoreText)
                                                     .arg(Theme.bandName(candidate.modelData.score))

                            Text {
                                anchors.baseline: scoreValue.baseline
                                text: candidate.modelData.scoreText === "" ? qsTr("No staff score") : qsTr("Staff")
                                color: Theme.textSecondary
                                font.pixelSize: Theme.fontCaption
                            }
                            Text {
                                id: scoreValue
                                visible: candidate.modelData.scoreText !== ""
                                text: candidate.modelData.scoreText
                                color: Theme.textTitle
                                font.pixelSize: Theme.fontBody
                                font.weight: Font.Bold
                                font.features: { "tnum": 1 }
                            }
                            Text {
                                anchors.baseline: scoreValue.baseline
                                visible: candidate.modelData.scoreText !== ""
                                text: Theme.bandName(candidate.modelData.score)
                                color: Theme.bandColor(candidate.modelData.score)
                                font.pixelSize: Theme.fontCaption
                                font.weight: Font.DemiBold
                            }
                        }
                        Text {
                            Layout.row: root.wide ? 0 : 1
                            Layout.column: root.wide ? 2 : 0
                            Layout.columnSpan: root.wide ? 1 : 2
                            visible: candidate.isCurrent
                            text: qsTr("Current follow-up")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontCaption
                        }
                        BusyButton {
                            objectName: "useFollowUpButton"
                            Layout.row: root.wide ? 0 : 1
                            Layout.column: root.wide ? 2 : 0
                            Layout.columnSpan: root.wide ? 1 : 2
                            visible: !candidate.isCurrent
                            variant: "primary"
                            text: qsTr("Use as Follow-up")
                            busyText: qsTr("Saving…")
                            font.pixelSize: Theme.fontCaption
                            busy: root.saving && root.chosen === candidate.modelData.value
                            Accessible.name: qsTr("Use %1 as the follow-up").arg(candidate.modelData.value)
                            onClicked: {
                                if (root.saving || !root.vm)
                                    return
                                root.chosen = candidate.modelData.value
                                root.vm.linkFollowUp(root.actionId, candidate.modelData.value)
                            }
                        }
                    }
                }
            }

            Flow {
                readonly property bool more: !root.showAll && root.options.length > root.shortList
                Layout.fillWidth: true
                spacing: 8
                visible: more || root.changing

                BusyButton {
                    objectName: "showAllCandidatesButton"
                    visible: parent.more
                    text: qsTr("Show All %1").arg(root.options.length)
                    font.pixelSize: Theme.fontCaption
                    Accessible.name: qsTr("Show all %1 later services").arg(root.options.length)
                    onClicked: root.showAll = true
                }
                BusyButton {
                    objectName: "keepCurrentButton"
                    visible: root.changing
                    text: qsTr("Keep Current Follow-up")
                    font.pixelSize: Theme.fontCaption
                    onClicked: root.changing = false
                }
            }
        }

        // ── waiting、error：再查一次
        BusyButton {
            objectName: "checkAgainButton"
            visible: root.step === "waiting" || root.step === "error"
            text: root.step === "error" ? qsTr("Retry") : qsTr("Check Again")
            busyText: qsTr("Checking…")
            busy: root.vm !== null && root.vm.followUpBusy
            onClicked: {
                if (!busy && root.vm)
                    root.vm.loadFollowUps(root.actionId)
            }
        }

        // ── review：連好的後續服務、Watch、Change；Mark Complete、Keep Practicing
        Rectangle {
            objectName: "followUpRow"
            Layout.fillWidth: true
            visible: root.step === "review"
            implicitHeight: followUpRow.implicitHeight + 16
            radius: 12
            color: Theme.tile
            border.width: 1
            border.color: Theme.tileBorder

            GridLayout {
                id: followUpRow
                x: 14
                y: 8
                width: parent.width - 22
                columns: root.wide ? 3 : 2
                columnSpacing: 10
                rowSpacing: 6

                ServiceLabel {
                    Layout.row: 0
                    Layout.column: 0
                    Layout.columnSpan: root.wide ? 1 : 2
                    Layout.fillWidth: true
                    whenText: root.hasFollowUp ? root.current.followUp.whenText : ""
                    serviceId: root.followUpId
                }
                BusyButton {
                    objectName: "watchFollowUpButton"
                    Layout.row: root.wide ? 0 : 1
                    Layout.column: root.wide ? 1 : 0
                    text: qsTr("Watch")
                    iconSource: "../components/icons/play_actionSecondaryText.svg"
                    font.pixelSize: Theme.fontCaption
                    Accessible.name: qsTr("Watch %1").arg(root.followUpId)
                    onClicked: {
                        if (root.vm)
                            root.vm.openService(root.actionId, root.followUpId, -1)
                    }
                }
                Button {
                    id: changeButton
                    objectName: "changeFollowUpButton"
                    Layout.row: root.wide ? 0 : 1
                    Layout.column: root.wide ? 2 : 1
                    implicitHeight: Theme.controlHeight
                    leftPadding: 12
                    rightPadding: 12
                    focusPolicy: Qt.StrongFocus
                    text: qsTr("Change")
                    Accessible.name: qsTr("Change the follow-up")
                    Keys.onReturnPressed: click()
                    Keys.onEnterPressed: click()
                    onClicked: {
                        root.changing = true
                        if (root.vm)
                            root.vm.loadFollowUps(root.actionId)
                    }

                    contentItem: Text {
                        text: changeButton.text
                        color: changeButton.hovered ? Theme.textTitle : Theme.iconNeutral
                        font.pixelSize: Theme.fontCaption
                        font.weight: Font.DemiBold
                        font.underline: true
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        radius: height / 2
                        color: changeButton.down ? Theme.actionSecondaryPressed : "transparent"
                        Rectangle {     // 鍵盤焦點框（DS-30）
                            visible: changeButton.visualFocus
                            anchors.fill: parent
                            anchors.margins: -2
                            radius: parent.radius + 2
                            color: "transparent"
                            border.color: Theme.focusOnDark
                            border.width: 2
                        }
                    }
                }
            }
        }

        Flow {
            objectName: "decisionRow"
            Layout.fillWidth: true
            visible: root.step === "review"
            spacing: Theme.spaceS

            BusyButton {
                objectName: "markCompleteButton"
                variant: "primary"
                text: qsTr("Mark Complete")
                iconSource: "../components/icons/check_actionPrimaryText.svg"
                onClicked: root.completeRequested()
            }
            BusyButton {
                objectName: "keepPracticingButton"
                text: qsTr("Keep Practicing")
                onClicked: root.keepRequested()
            }
        }

        FieldError {
            objectName: "linkError"
            Layout.fillWidth: true
            visible: hasError
            text: root.errorText
        }
    }
}
