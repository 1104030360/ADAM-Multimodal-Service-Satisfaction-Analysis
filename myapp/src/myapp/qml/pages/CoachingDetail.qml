pragma ComponentBehavior: Bound
import QtQuick
// 自訂了按鈕的 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 一項改善行動的明細（PLAN1 W2，T45）：Coaching 頁（管理員）和 My Actions（服務人員）右邊的卡片內容。
// - 目標（員工要練的一句話）和主管評語（為什麼訂它）。
// - 服務：原始服務（作為例子）、後續服務、調整時看過的後續服務；每一筆有 Open Service 和它自己影片上的時間註記，
//   按註記換到那筆服務的明細、影片載入後跳到那一秒（coachingModel.openService，外殼的 openServiceDetailAt）。
//   時間註記只屬於那一部影片：原始服務的 02:15 和後續服務的 02:15 不是同一秒。
// - 管理員（進行中）：連結後續服務（同一位員工、分析完成、比原始服務晚；選單由資料庫列出），完成、調整、撤回
//   （CoachingDialog.qml）。完成要有後續服務。拿掉時間註記在這裡也可以；加註記要在服務的明細頁（要有播放器）。
// - 服務人員（進行中）：Acknowledge（只代表已回應，不是完成），回應（補充情境、不同看法）。
// - 結案的：結論、誰、幾天。最下面是歷史（誰、身分、時間、做了什麼、雙方的回應）。
// 文字都在 Python（coaching.detail_map）產生；這裡只排版、把按鈕接到 coachingModel。
ColumnLayout {
    id: panel

    property var vm: null
    property bool admin: false

    readonly property var current: vm ? vm.current : ({})
    readonly property bool hasCurrent: current.actionId !== undefined
    readonly property string actionId: hasCurrent ? current.actionId : ""
    readonly property bool isOpen: hasCurrent && current.isOpen
    readonly property bool saving: hasCurrent && vm !== null && vm.saving.indexOf(actionId) >= 0
    readonly property var lastError: vm ? vm.actionError : ({})
    readonly property string errorOp: lastError.actionId === actionId ? lastError.op : ""
    readonly property var followUp: hasCurrent ? current.followUp : ({})
    readonly property bool hasFollowUp: followUp.serviceId !== undefined
    property string chosenFollowUp: ""
    property bool changingFollowUp: false
    readonly property alias responseField: responseArea       // 測試用
    readonly property alias dialog: conclude                   // 測試用

    spacing: Theme.spaceL

    function openService(serviceId, seconds) {
        if (vm && hasCurrent)
            vm.openService(actionId, serviceId, seconds)
    }

    function withPlaceholder(options, placeholder) {
        return [{ value: "", text: placeholder }].concat(options)
    }

    // 管理員、進行中：讀能當後續服務的服務（換一項時讀一次；連結、調整之後 coachingModel 自己重讀）
    function ensureOptions() {
        if (vm && admin && isOpen && actionId !== "" && vm.followUpFor !== actionId)
            vm.loadFollowUps(actionId)
    }

    onActionIdChanged: {
        chosenFollowUp = ""
        changingFollowUp = false
        responseArea.text = ""
        ensureOptions()
    }
    onIsOpenChanged: ensureOptions()
    Component.onCompleted: ensureOptions()

    Connections {
        target: panel.vm
        function onSaved(actionId, op) {
            if (actionId !== panel.actionId)
                return
            if (op === "respond")
                responseArea.text = ""
            if (op === "link") {
                panel.changingFollowUp = false
                panel.chosenFollowUp = ""
            }
        }
    }

    // ── 小元件

    // 區塊的小標題（大寫、淡色），和明細頁分數列的 CUSTOMER、STAFF 一樣
    component SectionLabel: Text {
        Layout.fillWidth: true
        color: Theme.textFaint
        font.pixelSize: Theme.fontSmall
        font.weight: Font.DemiBold
        font.capitalization: Font.AllUppercase
        font.letterSpacing: 1
        Accessible.role: Accessible.Heading
        Accessible.name: text
    }

    component Divider: Rectangle {
        Layout.fillWidth: true
        implicitHeight: 1
        color: Theme.cardBorder
    }

    // 一個時間註記：按一下換到那筆服務、跳到那一秒；管理員（進行中）可以拿掉
    component MarkerChip: Row {
        id: chip
        required property var marker        // {markerId, serviceId, seconds, clock, note, editable}
        required property string serviceId
        spacing: 2

        Button {
            id: jump
            objectName: "markerButton"
            implicitHeight: Theme.controlHeight
            leftPadding: 12
            rightPadding: 14
            focusPolicy: Qt.StrongFocus
            text: chip.marker.note !== "" ? qsTr("%1 · %2").arg(chip.marker.clock).arg(chip.marker.note)
                                          : chip.marker.clock
            Accessible.name: qsTr("Play %1 from %2").arg(chip.serviceId).arg(chip.marker.clock)
            Accessible.description: chip.marker.note
            Keys.onReturnPressed: click()
            Keys.onEnterPressed: click()
            onClicked: panel.openService(chip.serviceId, chip.marker.seconds)

            contentItem: Row {
                spacing: 6
                Image {
                    anchors.verticalCenter: parent.verticalCenter
                    width: Theme.iconS
                    height: Theme.iconS
                    sourceSize: Qt.size(Theme.iconS, Theme.iconS)
                    source: "../components/icons/map-pin_iconNeutral.svg"
                    Accessible.ignored: true
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.min(implicitWidth, 360)
                    text: jump.text
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontCaption
                    font.features: { "tnum": 1 }
                    elide: Text.ElideRight
                }
            }

            background: Rectangle {
                radius: 12
                color: jump.down ? Theme.actionSecondaryPressed : jump.hovered ? Theme.actionSecondaryHover : Theme.tile
                border.width: 1
                border.color: Theme.tileBorder

                Rectangle {     // 鍵盤焦點框（DS-30）
                    visible: jump.visualFocus
                    anchors.fill: parent
                    anchors.margins: -4
                    radius: parent.radius + 4
                    color: "transparent"
                    border.color: Theme.focusOnDark
                    border.width: 2
                }
            }
        }

        Button {
            id: remove
            objectName: "removeMarkerButton"
            visible: panel.admin && chip.marker.editable
            implicitWidth: Theme.controlHeight
            implicitHeight: Theme.controlHeight
            focusPolicy: Qt.StrongFocus
            enabled: !panel.saving
            text: qsTr("Remove the marker at %1").arg(chip.marker.clock)
            Accessible.name: text
            onClicked: {
                if (panel.vm)
                    panel.vm.removeMarker(panel.actionId, chip.marker.markerId)
            }

            contentItem: Item {
                Image {
                    anchors.centerIn: parent
                    width: Theme.iconS
                    height: Theme.iconS
                    sourceSize: Qt.size(Theme.iconS, Theme.iconS)
                    source: "../components/icons/x_chromeIcon.svg"
                    opacity: remove.enabled ? 1 : 0.4
                    Accessible.ignored: true
                }
            }
            background: Rectangle {
                radius: width / 2
                color: remove.down ? Theme.actionSecondaryPressed : remove.hovered ? Theme.actionSecondaryHover
                                                                                   : "transparent"
                Rectangle {
                    visible: remove.visualFocus
                    anchors.fill: parent
                    anchors.margins: -2
                    radius: width / 2
                    color: "transparent"
                    border.color: Theme.focusOnDark
                    border.width: 2
                }
            }
        }
    }

    // 一筆服務：標題（Original service、Follow-up…）、服務 ID 和時間、Open Service、它影片上的時間註記
    component ServiceBlock: ColumnLayout {
        id: block
        property string label: ""
        property var service: ({})           // {serviceId, startTime, label, markers}
        property string note: ""             // 標題下面的一句（調整的原因）
        default property alias extra: trailing.data
        readonly property bool hasService: service.serviceId !== undefined
        Layout.fillWidth: true
        spacing: 10

        SectionLabel { text: block.label }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceM
            visible: block.hasService

            Text {
                objectName: "serviceLabel"
                Layout.fillWidth: true
                text: block.hasService ? block.service.label : ""
                color: Theme.textPrimary
                font.pixelSize: Theme.fontBody
                font.weight: Font.DemiBold
                font.features: { "tnum": 1 }
                elide: Text.ElideRight
            }
            BusyButton {
                objectName: "openServiceButton"
                text: qsTr("Open Service")
                font.pixelSize: Theme.fontCaption
                Accessible.name: qsTr("Open %1").arg(block.hasService ? block.service.serviceId : "")
                onClicked: panel.openService(block.service.serviceId, -1)
            }
        }

        Text {
            Layout.fillWidth: true
            visible: text !== ""
            text: block.note
            color: Theme.textSecondary
            font.pixelSize: Theme.fontCaption
            wrapMode: Text.Wrap
        }

        Flow {
            Layout.fillWidth: true
            visible: block.hasService && block.service.markers.length > 0
            spacing: 8
            Repeater {
                model: block.hasService ? block.service.markers : []
                delegate: MarkerChip {
                    required property var modelData
                    marker: modelData
                    // 換成沒有服務的那一刻（調整之後的後續服務），舊的 delegate 還在：給空字串，不是 undefined
                    serviceId: block.hasService ? block.service.serviceId : ""
                }
            }
        }

        ColumnLayout {
            id: trailing
            Layout.fillWidth: true
            spacing: 10
        }
    }

    // ── 版面

    // 狀態、期限、（管理員）員工；誰訂的
    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.spaceS
        visible: panel.hasCurrent

        CoachingChip {
            id: stateChip
            objectName: "detailChip"
            actionState: panel.hasCurrent ? panel.current.chipState : "open"
        }
        Text {
            objectName: "detailDue"
            text: panel.hasCurrent ? panel.current.dueText : ""
            color: panel.hasCurrent && panel.current.chipState === "overdue" ? Theme.warningOnDark : Theme.textSecondary
            font.pixelSize: Theme.fontCaption
            font.weight: Font.DemiBold
        }
        Text {
            Layout.fillWidth: true
            text: !panel.hasCurrent ? "" : panel.admin ? qsTr("%1 · %2").arg(panel.current.staffName)
                                                                         .arg(panel.current.setText)
                                                       : panel.current.setText
            color: Theme.textMuted
            font.pixelSize: Theme.fontCaption
            horizontalAlignment: Text.AlignRight
            elide: Text.ElideLeft
        }
    }

    // 目標：員工要練的一句話（最大的字）
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 6
        visible: panel.hasCurrent

        SectionLabel { text: qsTr("Goal") }
        Text {
            objectName: "detailGoal"
            Layout.fillWidth: true
            text: panel.hasCurrent ? panel.current.goal : ""
            color: Theme.textTitle
            font.pixelSize: Theme.fontTitleSmall
            font.weight: Font.Bold
            wrapMode: Text.Wrap
            Accessible.role: Accessible.Heading
            Accessible.name: qsTr("Goal: %1").arg(text)
        }
    }

    // 主管評語：為什麼訂這個目標
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 6
        visible: panel.hasCurrent

        SectionLabel { text: qsTr("Why this goal") }
        Text {
            objectName: "detailComment"
            Layout.fillWidth: true
            text: panel.hasCurrent ? panel.current.comment : ""
            color: Theme.textPrimary
            font.pixelSize: Theme.fontBody
            wrapMode: Text.Wrap
            lineHeight: 1.2
        }
    }

    Divider { visible: panel.hasCurrent }

    // 原始服務（作為例子）
    ServiceBlock {
        objectName: "sourceBlock"
        visible: panel.hasCurrent
        label: qsTr("Original service")
        service: panel.hasCurrent ? panel.current.source : ({})

        Text {
            Layout.fillWidth: true
            visible: panel.admin && panel.isOpen
            text: qsTr("To add a marker, open the service and pause its video at that moment.")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
            wrapMode: Text.Wrap
        }
    }

    // 後續服務：連好的那一筆，或（管理員）選一筆來連；服務人員看到一句說明
    ServiceBlock {
        id: followUpBlock
        objectName: "followUpBlock"
        visible: panel.hasCurrent && (panel.hasFollowUp || panel.isOpen)
        label: qsTr("Follow-up service")
        service: panel.followUp

        Text {
            objectName: "followUpNote"
            Layout.fillWidth: true
            visible: !panel.hasFollowUp && panel.isOpen
            text: panel.admin ? qsTr("Choose a later service by %1 that has finished analysis. Watch it, then complete or adjust the goal.")
                                .arg(panel.hasCurrent ? panel.current.staffName : "")
                              : qsTr("Your admin will choose one of your later services to check this goal.")
            color: Theme.textMuted
            font.pixelSize: Theme.fontCaption
            wrapMode: Text.Wrap
        }

        BusyButton {
            objectName: "changeFollowUpButton"
            visible: panel.admin && panel.isOpen && panel.hasFollowUp && !panel.changingFollowUp
            text: qsTr("Change Follow-up")
            font.pixelSize: Theme.fontCaption
            onClicked: panel.changingFollowUp = true
        }

        RowLayout {
            objectName: "linkRow"
            Layout.fillWidth: true
            visible: panel.admin && panel.isOpen && (!panel.hasFollowUp || panel.changingFollowUp)
            spacing: Theme.spaceS

            HomeSelect {
                id: followUpSelect
                objectName: "followUpSelect"
                Layout.fillWidth: true
                Layout.maximumWidth: 420
                popupMaxHeight: 320
                enabled: panel.vm !== null && panel.vm.followUpFor === panel.actionId
                         && panel.vm.followUpState === "ready"
                options: panel.withPlaceholder(panel.vm && panel.vm.followUpFor === panel.actionId
                                               ? panel.vm.followUpOptions : [],
                                               panel.vm && panel.vm.followUpState === "loading" ? qsTr("Loading…")
                                               : panel.vm && panel.vm.followUpState === "empty"
                                               ? qsTr("No later analyzed services yet")
                                               : panel.vm && panel.vm.followUpState === "error"
                                               ? qsTr("Couldn't load services") : qsTr("Choose a later service"))
                value: panel.chosenFollowUp
                Accessible.name: qsTr("Follow-up service")
                onPicked: (value) => {
                    panel.chosenFollowUp = value
                    if (panel.vm)
                        panel.vm.clearActionError()
                }
            }
            BusyButton {
                objectName: "linkFollowUpButton"
                variant: "primary"
                text: panel.hasFollowUp ? qsTr("Use This Service") : qsTr("Link Follow-up")
                busyText: qsTr("Linking…")
                busy: panel.saving
                font.pixelSize: Theme.fontCaption
                onClicked: {
                    if (!busy && panel.vm)
                        panel.vm.linkFollowUp(panel.actionId, panel.chosenFollowUp)
                }
            }
            BusyButton {
                visible: panel.changingFollowUp
                text: qsTr("Keep Current")
                font.pixelSize: Theme.fontCaption
                onClicked: panel.changingFollowUp = false
            }
            BusyButton {
                objectName: "reloadFollowUpsButton"
                visible: panel.vm !== null && panel.vm.followUpState === "error"
                text: qsTr("Retry")
                font.pixelSize: Theme.fontCaption
                onClicked: panel.vm.loadFollowUps(panel.actionId)
            }
        }
    }

    // 調整時看過的後續服務（新的在前）：各自的時間註記留著，員工看得到為什麼還要再練
    Repeater {
        model: panel.hasCurrent ? panel.current.reviewed : []
        delegate: ServiceBlock {
            required property var modelData
            objectName: "reviewedBlock"
            label: qsTr("Reviewed follow-up · adjusted %1").arg(modelData.whenText)
            service: modelData
            note: modelData.reason
        }
    }

    // 結案：結論、誰、幾天
    Rectangle {
        objectName: "conclusionBox"
        Layout.fillWidth: true
        visible: panel.hasCurrent && !panel.isOpen
        implicitHeight: conclusionColumn.implicitHeight + 2 * Theme.spaceM
        radius: 12
        color: panel.hasCurrent && panel.current.status === "completed" ? Theme.successTint : Theme.tile

        ColumnLayout {
            id: conclusionColumn
            x: Theme.spaceM
            y: Theme.spaceM
            width: parent.width - 2 * Theme.spaceM
            spacing: 6

            Text {
                objectName: "concludedText"
                Layout.fillWidth: true
                text: panel.hasCurrent ? panel.current.concludedText : ""
                color: panel.hasCurrent && panel.current.status === "completed" ? Theme.successOnDark : Theme.textSecondary
                font.pixelSize: Theme.fontCaption
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
            }
            Text {
                objectName: "conclusionText"
                Layout.fillWidth: true
                text: panel.hasCurrent ? panel.current.conclusion : ""
                color: Theme.textPrimary
                font.pixelSize: Theme.fontBody
                wrapMode: Text.Wrap
            }
        }
    }

    // 管理員：完成（要有後續服務）、調整、撤回
    ColumnLayout {
        objectName: "adminActions"
        Layout.fillWidth: true
        visible: panel.admin && panel.isOpen
        spacing: 8

        Divider {}
        Flow {
            Layout.fillWidth: true
            Layout.topMargin: 8
            spacing: Theme.spaceS

            BusyButton {
                objectName: "completeButton"
                variant: "primary"
                text: qsTr("Complete")
                enabled: panel.hasCurrent && panel.current.canComplete
                onClicked: conclude.ask("complete")
            }
            BusyButton {
                objectName: "adjustButton"
                text: qsTr("Adjust Goal")
                onClicked: conclude.ask("adjust")
            }
            BusyButton {
                objectName: "cancelGoalButton"
                variant: "danger"
                text: qsTr("Cancel Goal")
                onClicked: conclude.ask("cancel")
            }
        }
        Text {
            objectName: "completeHint"
            Layout.fillWidth: true
            visible: !panel.hasFollowUp
            text: qsTr("Link a follow-up service before you complete this action. An acknowledgement or a higher AI score doesn't complete it.")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
            wrapMode: Text.Wrap
        }
    }

    // 服務人員：確認看過（只代表已回應）
    RowLayout {
        objectName: "acknowledgeRow"
        Layout.fillWidth: true
        visible: !panel.admin && panel.isOpen
        spacing: Theme.spaceM

        Text {
            objectName: "acknowledgeText"
            Layout.fillWidth: true
            text: !panel.hasCurrent ? ""
                : panel.current.acknowledged ? qsTr("%1. Your admin can see that you've read this goal.")
                                               .arg(panel.current.acknowledgedText)
                : qsTr("Let your admin know you've read this goal. This doesn't mark it complete.")
            color: Theme.textMuted
            font.pixelSize: Theme.fontCaption
            wrapMode: Text.Wrap
        }
        BusyButton {
            objectName: "acknowledgeButton"
            visible: panel.hasCurrent && panel.current.canAcknowledge
            variant: "primary"
            text: qsTr("Acknowledge")
            busyText: qsTr("Sending…")
            busy: panel.saving
            onClicked: {
                if (!busy && panel.vm)
                    panel.vm.acknowledge(panel.actionId)
            }
        }
    }

    // 回應（進行中）：服務人員補充情境、回應、提出不同看法；管理員回覆
    ColumnLayout {
        objectName: "respondSection"
        Layout.fillWidth: true
        visible: panel.isOpen
        spacing: 8

        SectionLabel { text: panel.admin ? qsTr("Reply") : qsTr("Your response") }
        CoachingTextArea {
            id: responseArea
            objectName: "responseArea"
            Layout.fillWidth: true
            minimumLines: 2
            placeholderText: panel.admin ? qsTr("Reply to %1.").arg(panel.hasCurrent ? panel.current.staffName : "")
                                         : qsTr("Add context, respond, or share a different view.")
            invalid: panel.errorOp === "respond"
            errorText: invalid ? panel.lastError.text : ""
            Accessible.name: panel.admin ? qsTr("Reply") : qsTr("Your response")
            onTextChanged: {
                if (panel.errorOp === "respond" && panel.vm)
                    panel.vm.clearActionError()
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceM

            FieldError {
                Layout.fillWidth: true
                text: panel.errorOp === "respond" || panel.errorOp === "acknowledge" || panel.errorOp === "link"
                      || panel.errorOp === "unmark" ? panel.lastError.text : ""
            }
            BusyButton {
                objectName: "sendResponseButton"
                text: panel.admin ? qsTr("Send Reply") : qsTr("Send Response")
                busyText: qsTr("Sending…")
                busy: panel.saving
                onClicked: {
                    if (!busy && panel.vm)
                        panel.vm.respond(panel.actionId, responseArea.text)
                }
            }
        }
    }

    Divider { visible: panel.hasCurrent }

    // 歷史：舊的在前；回應和評語是一段文字，其他是一句話
    ColumnLayout {
        objectName: "historySection"
        Layout.fillWidth: true
        visible: panel.hasCurrent
        spacing: Theme.spaceM

        SectionLabel { text: qsTr("History") }

        Repeater {
            objectName: "historyRepeater"
            model: panel.hasCurrent ? panel.current.history : []
            delegate: ColumnLayout {
                id: entry
                required property var modelData
                objectName: "historyEntry"
                Layout.fillWidth: true
                spacing: 4
                Accessible.role: Accessible.StaticText
                Accessible.name: modelData.title + ", " + modelData.whenText
                                 + (modelData.body !== "" ? ". " + modelData.body : "")

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    Rectangle {     // 誰寫的：員工、管理員兩種（顏色不是唯一的線索，旁邊有字）
                        implicitWidth: roleText.implicitWidth + 12
                        implicitHeight: 20
                        radius: 10
                        color: entry.modelData.role === "staff" ? Theme.infoTint : Theme.tile
                        Text {
                            id: roleText
                            anchors.centerIn: parent
                            text: entry.modelData.roleText
                            color: entry.modelData.role === "staff" ? Theme.infoOnDark : Theme.textSecondary
                            font.pixelSize: Theme.fontLabel
                            font.weight: Font.DemiBold
                        }
                    }
                    Text {
                        objectName: "historyTitle"
                        Layout.fillWidth: true
                        text: entry.modelData.title
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontCaption
                        font.weight: Font.DemiBold
                        wrapMode: Text.Wrap
                    }
                    Text {
                        text: entry.modelData.whenText
                        color: Theme.textFaint
                        font.pixelSize: Theme.fontSmall
                        font.features: { "tnum": 1 }
                    }
                }
                Repeater {
                    model: entry.modelData.changes
                    delegate: Text {
                        required property string modelData
                        Layout.fillWidth: true
                        Layout.leftMargin: 4
                        text: modelData
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontSmall
                        wrapMode: Text.Wrap
                    }
                }
                Rectangle {     // 回應、評語、原因、結論的內容
                    Layout.fillWidth: true
                    visible: entry.modelData.body !== ""
                    implicitHeight: bodyText.implicitHeight + 2 * Theme.spaceS
                    radius: 10
                    color: Theme.tile
                    Text {
                        id: bodyText
                        objectName: "historyBody"
                        x: 12
                        y: Theme.spaceS
                        width: parent.width - 24
                        text: entry.modelData.body
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontCaption
                        wrapMode: Text.Wrap
                    }
                }
            }
        }
    }

    // 完成、調整、撤回的表單（只有管理員）
    CoachingDialog {
        id: conclude
        vm: panel.vm
        current: panel.current
    }
}
