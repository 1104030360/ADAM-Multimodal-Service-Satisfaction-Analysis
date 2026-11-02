pragma ComponentBehavior: Bound
import QtQuick
// 自訂了時間註記按鈕的 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 一項改善目標的明細（PLAN1 W2；T47 依設計稿「Coaching, redesigned」改版）：Coaching 頁（管理員）、My Actions（服務人員）
// 右邊的卡片內容。字少、一次只有一個明顯的下一步：
// - 管理員：員工、「Set Nov 1 · Due Nov 6」、Edit Goal、Cancel Goal；目標（大字）和為什麼；四個步驟（CoachingProgress）；
//   Next step（CoachingNextStep：選後續服務、等、看了之後 Mark Complete 或 Keep Practicing）；Moments：依服務分組
//   （Example · Oct 8、Follow-up · Oct 18），按一下打開那筆服務、影片跳到那一秒；Conversation（CoachingConversation）。
// - 服務人員：還沒按 Got It 時最上面是「New goal from Alex Wang」和 Got It（只代表看過了，不是完成）；完成、撤回時是結論；
//   「From Alex Wang · Due Nov 6」、目標、為什麼；四個步驟；Watch the example（原始服務，一個時間註記一列）、
//   後續服務和看過的服務；Conversation。
// 時間註記只屬於那一部影片：原始服務的 02:15 和後續服務的 02:15 不是同一秒（coachingModel.openService，外殼的
// openServiceDetailAt）。文字都在 Python（coaching.detail_map）產生；這裡只排版、把按鈕接到 coachingModel。
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
    readonly property var services: hasCurrent ? current.services : []
    readonly property var markedServices: services.filter(block => block.markers.length > 0)
    readonly property alias dialog: conclude                   // 測試用
    readonly property alias conversation: talk                 // 測試用
    readonly property alias nextStep: next                     // 測試用

    spacing: Theme.spaceL

    function openService(serviceId, seconds) {
        if (vm && hasCurrent)
            vm.openService(actionId, serviceId, seconds)
    }

    // ── 小元件

    // 區塊的小標題（大寫、淡色），和明細頁分數列的 CUSTOMER、STAFF 一樣
    component SectionLabel: Text {
        Layout.fillWidth: true
        color: Theme.textFaint
        font.pixelSize: Theme.fontSmall
        font.weight: Font.Bold
        font.capitalization: Font.AllUppercase
        font.letterSpacing: 1
        Accessible.role: Accessible.Heading
        Accessible.name: text
    }

    // 管理員的 Moments：一個膠囊，時間加說明；按一下打開那筆服務、跳到那一秒
    component MomentChip: Button {
        id: chip
        required property var marker
        required property string serviceId
        required property string serviceLabel
        objectName: "momentChip"
        implicitHeight: Theme.controlHeight
        leftPadding: 10
        rightPadding: 14
        focusPolicy: Qt.StrongFocus
        text: marker.note
        Accessible.name: qsTr("Play %1 from %2").arg(serviceLabel).arg(marker.clock)
        Accessible.description: marker.note
        Keys.onReturnPressed: click()
        Keys.onEnterPressed: click()
        onClicked: panel.openService(chip.serviceId, chip.marker.seconds)

        contentItem: Row {
            spacing: 8
            CoachingTime {
                anchors.verticalCenter: parent.verticalCenter
                clock: chip.marker.clock
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                visible: text !== ""
                width: Math.min(implicitWidth, 320)
                text: chip.text
                color: Theme.textPrimary
                font.pixelSize: Theme.fontCaption
                elide: Text.ElideRight
            }
        }
        background: Rectangle {
            radius: height / 2
            color: chip.down ? Theme.actionSecondaryPressed : chip.hovered ? Theme.actionSecondaryHover : Theme.tile
            border.width: 1
            border.color: Theme.tileBorder
            Rectangle {     // 鍵盤焦點框（DS-30）
                visible: chip.visualFocus
                anchors.fill: parent
                anchors.margins: -4
                radius: parent.radius + 4
                color: "transparent"
                border.color: Theme.focusOnDark
                border.width: 2
            }
        }
    }

    // ── 管理員：員工、日期、Edit Goal、Cancel Goal。明細窄的時候（小視窗），兩個按鈕排到下一行，不擠出卡片
    GridLayout {
        id: header
        objectName: "detailHeader"
        readonly property bool wide: panel.width >= 480
        Layout.fillWidth: true
        visible: panel.admin && panel.hasCurrent
        columns: wide ? 3 : 2
        columnSpacing: 12
        rowSpacing: 10

        CoachingAvatar {
            Layout.row: 0
            Layout.column: 0
            size: 40
            initials: panel.hasCurrent ? panel.current.initials : ""
        }
        ColumnLayout {
            Layout.row: 0
            Layout.column: 1
            Layout.fillWidth: true
            spacing: 0

            Text {
                objectName: "detailStaff"
                Layout.fillWidth: true
                text: panel.hasCurrent ? panel.current.staffName : ""
                color: Theme.textTitle
                font.pixelSize: Theme.fontBody
                font.weight: Font.Bold
                elide: Text.ElideRight
            }
            Text {
                objectName: "detailSetText"
                Layout.fillWidth: true
                text: panel.hasCurrent ? panel.current.setText : ""
                color: panel.hasCurrent && panel.current.dueAlert ? Theme.errorOnDark : Theme.textMuted
                font.pixelSize: Theme.fontSmall
                font.features: { "tnum": 1 }
                elide: Text.ElideRight
            }
        }
        RowLayout {
            Layout.row: header.wide ? 0 : 1
            Layout.column: header.wide ? 2 : 0
            Layout.columnSpan: header.wide ? 1 : 2
            visible: panel.isOpen
            spacing: 10

            BusyButton {
                objectName: "editGoalButton"
                text: qsTr("Edit Goal")
                font.pixelSize: Theme.fontCaption
                onClicked: conclude.ask("edit")
            }
            BusyButton {
                objectName: "cancelGoalButton"
                variant: "danger"
                text: qsTr("Cancel Goal")
                font.pixelSize: Theme.fontCaption
                onClicked: conclude.ask("cancel")
            }
        }
    }

    // ── 服務人員：新的目標（Got It）
    Rectangle {
        objectName: "newGoalBanner"
        Layout.fillWidth: true
        visible: !panel.admin && panel.hasCurrent && panel.current.canAcknowledge
        implicitHeight: bannerColumn.implicitHeight + 28
        radius: Theme.radiusTile
        color: Theme.infoTint
        border.width: 1
        border.color: Theme.infoBorder
        Accessible.role: Accessible.AlertMessage
        Accessible.name: bannerTitle.text
        Accessible.description: bannerText.text

        ColumnLayout {
            id: bannerColumn
            x: 16
            y: 14
            width: parent.width - 32
            spacing: 8

            RowLayout {
                Layout.fillWidth: true
                spacing: 16

                Rectangle {
                    implicitWidth: 36
                    implicitHeight: 36
                    radius: 18
                    color: Theme.infoTintStrong
                    Image {
                        anchors.centerIn: parent
                        width: 18
                        height: 18
                        sourceSize: Qt.size(18, 18)
                        source: "../components/icons/target_infoOnDark.svg"
                        Accessible.ignored: true
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    Text {
                        id: bannerTitle
                        objectName: "newGoalTitle"
                        Layout.fillWidth: true
                        text: qsTr("New goal from %1").arg(panel.hasCurrent ? panel.current.adminName : "")
                        color: Theme.textTitle
                        font.pixelSize: Theme.fontInput
                        font.weight: Font.Bold
                        wrapMode: Text.Wrap
                    }
                    Text {
                        id: bannerText
                        Layout.fillWidth: true
                        text: panel.services.length > 0 && panel.services[0].markers.length > 0
                              ? qsTr("Watch the moments, then press Got It.")
                              : qsTr("Watch the example, then press Got It.")
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontSmall
                        wrapMode: Text.Wrap
                    }
                }
                BusyButton {
                    objectName: "gotItButton"
                    variant: "primary"
                    text: qsTr("Got It")
                    busyText: qsTr("Sending…")
                    iconSource: "../components/icons/check_actionPrimaryText.svg"
                    busy: panel.saving
                    Accessible.description: qsTr("Tells your admin you read the goal. It doesn't mark it complete.")
                    onClicked: {
                        if (!busy && panel.vm)
                            panel.vm.acknowledge(panel.actionId)
                    }
                }
            }
            FieldError {
                objectName: "gotItError"
                Layout.fillWidth: true
                visible: hasError
                text: panel.lastError.actionId === panel.actionId && panel.lastError.op === "acknowledge"
                      ? panel.lastError.text : ""
            }
        }
    }

    // ── 服務人員：完成、撤回（結論或原因）
    Rectangle {
        id: closedBanner
        objectName: "closedBanner"
        Layout.fillWidth: true
        visible: !panel.admin && panel.hasCurrent && !panel.isOpen
        readonly property bool completed: panel.hasCurrent && panel.current.status === "completed"
        implicitHeight: closedRow.implicitHeight + 28
        radius: Theme.radiusTile
        color: completed ? Theme.successTint : Theme.tile
        border.width: 1
        border.color: completed ? Theme.successBorder : Theme.tileBorder
        Accessible.role: Accessible.StaticText
        Accessible.name: closedTitle.text + ". " + closedText.text

        RowLayout {
            id: closedRow
            x: 16
            y: 14
            width: parent.width - 32
            spacing: 12

            Image {
                Layout.alignment: Qt.AlignTop
                visible: closedBanner.completed
                Layout.preferredWidth: 20
                Layout.preferredHeight: 20
                sourceSize: Qt.size(20, 20)
                source: "../components/icons/check_successOnDark.svg"
                Accessible.ignored: true
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    id: closedTitle
                    objectName: "closedTitle"
                    Layout.fillWidth: true
                    text: !panel.hasCurrent ? ""
                        : panel.current.status === "completed" ? qsTr("Completed %1").arg(panel.current.closedDay)
                        : qsTr("Cancelled %1").arg(panel.current.closedDay)
                    color: closedBanner.completed ? Theme.successOnDark : Theme.textSecondary
                    font.pixelSize: Theme.fontInput
                    font.weight: Font.Bold
                }
                Text {
                    id: closedText
                    objectName: "conclusionText"
                    Layout.fillWidth: true
                    text: panel.hasCurrent ? panel.current.conclusion : ""
                    color: Theme.iconNeutral
                    font.pixelSize: Theme.fontCaption
                    wrapMode: Text.Wrap
                }
            }
        }
    }

    // ── 目標（員工要練的一句話，最大的字）和為什麼
    ColumnLayout {
        Layout.fillWidth: true
        visible: panel.hasCurrent
        spacing: 6

        Text {
            objectName: "detailFrom"
            Layout.fillWidth: true
            visible: !panel.admin
            text: panel.hasCurrent ? panel.current.fromText : ""
            color: panel.hasCurrent && panel.current.dueAlert ? Theme.errorOnDark : Theme.textMuted
            font.pixelSize: Theme.fontSmall
            font.features: { "tnum": 1 }
            wrapMode: Text.Wrap
        }
        Text {
            objectName: "detailGoal"
            Layout.fillWidth: true
            text: panel.hasCurrent ? panel.current.goal : ""
            color: Theme.textTitle
            font.pixelSize: Theme.fontTitle
            font.weight: Font.Bold
            lineHeight: 1.15
            wrapMode: Text.Wrap
            Accessible.role: Accessible.Heading
            Accessible.name: qsTr("Goal: %1").arg(text)
        }
        Text {
            objectName: "detailComment"
            Layout.fillWidth: true
            text: panel.hasCurrent ? panel.current.comment : ""
            color: Theme.textSecondary
            font.pixelSize: Theme.fontInput
            lineHeight: 1.2
            wrapMode: Text.Wrap
        }
    }

    CoachingProgress {
        objectName: "progressRow"
        Layout.fillWidth: true
        visible: panel.hasCurrent && panel.current.status !== "cancelled"
        steps: panel.hasCurrent ? panel.current.steps : []
    }

    // ── 管理員：下一步
    CoachingNextStep {
        id: next
        objectName: "nextStep"
        Layout.fillWidth: true
        visible: panel.admin && panel.hasCurrent
        vm: panel.vm
        current: panel.current
        onCompleteRequested: conclude.ask("complete")
        onKeepRequested: conclude.ask("keep")
    }

    // ── 服務人員：Watch the example（原始服務）、後續服務、看過的服務；一個時間註記一列
    Repeater {
        model: !panel.admin && panel.hasCurrent ? panel.services : []
        delegate: Rectangle {
            id: watchCard
            required property var modelData
            objectName: "watchCard"
            Layout.fillWidth: true
            implicitHeight: watchColumn.implicitHeight + 32
            radius: Theme.radiusTile
            color: Theme.panelInset
            border.width: 1
            border.color: Theme.tileBorder
            Accessible.role: Accessible.Grouping
            Accessible.name: watchKicker.text

            ColumnLayout {
                id: watchColumn
                x: 18
                y: 16
                width: parent.width - 36
                spacing: 12

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 14

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        Text {
                            id: watchKicker
                            objectName: "watchKicker"
                            Layout.fillWidth: true
                            text: watchCard.modelData.relation === "source" ? qsTr("Watch the example")
                                : watchCard.modelData.relation === "follow_up" ? qsTr("Later service")
                                : qsTr("Reviewed service")
                            color: Theme.textFaint
                            font.pixelSize: Theme.fontLabel
                            font.weight: Font.Bold
                            font.capitalization: Font.AllUppercase
                            font.letterSpacing: 1
                        }
                        Text {
                            objectName: "watchService"
                            Layout.fillWidth: true
                            text: qsTr("%1 · %2").arg(watchCard.modelData.whenText).arg(watchCard.modelData.serviceId)
                            color: Theme.textTitle
                            font.pixelSize: Theme.fontInput
                            font.weight: Font.DemiBold
                            font.features: { "tnum": 1 }
                            elide: Text.ElideRight
                        }
                    }
                    BusyButton {
                        objectName: "openServiceButton"
                        text: qsTr("Open Service")
                        iconSource: "../components/icons/play_actionSecondaryText.svg"
                        font.pixelSize: Theme.fontCaption
                        Accessible.name: qsTr("Open %1").arg(watchCard.modelData.serviceId)
                        onClicked: panel.openService(watchCard.modelData.serviceId, -1)
                    }
                }

                // 調整時看過的服務：為什麼還要再練
                Text {
                    Layout.fillWidth: true
                    visible: text !== ""
                    text: watchCard.modelData.reason
                    color: Theme.textSecondary
                    font.pixelSize: Theme.fontCaption
                    wrapMode: Text.Wrap
                }

                Repeater {
                    model: watchCard.modelData.markers
                    delegate: Button {
                        id: momentRow
                        required property var modelData
                        objectName: "momentRow"
                        Layout.fillWidth: true
                        implicitHeight: Math.max(48, noteText.implicitHeight + 20)
                        leftPadding: 12
                        rightPadding: 12
                        focusPolicy: Qt.StrongFocus
                        text: modelData.note
                        Accessible.name: modelData.note !== "" ? qsTr("Play from %1: %2").arg(modelData.clock).arg(modelData.note)
                                                               : qsTr("Play from %1").arg(modelData.clock)
                        Keys.onReturnPressed: click()
                        Keys.onEnterPressed: click()
                        onClicked: panel.openService(watchCard.modelData.serviceId, momentRow.modelData.seconds)

                        contentItem: RowLayout {
                            spacing: 12
                            CoachingTime {
                                clock: momentRow.modelData.clock
                                pillHeight: 28
                            }
                            Text {
                                id: noteText
                                Layout.fillWidth: true
                                text: momentRow.text
                                color: Theme.textPrimary
                                font.pixelSize: Theme.fontInput
                                wrapMode: Text.Wrap
                            }
                        }
                        background: Rectangle {
                            radius: 12
                            color: momentRow.down ? Theme.actionSecondaryPressed
                                 : momentRow.hovered ? Theme.actionSecondaryHover : Theme.tile
                            border.width: 1
                            border.color: Theme.tileBorder
                            Rectangle {     // 鍵盤焦點框（DS-30）
                                visible: momentRow.visualFocus
                                anchors.fill: parent
                                anchors.margins: -4
                                radius: parent.radius + 4
                                color: "transparent"
                                border.color: Theme.focusOnDark
                                border.width: 2
                            }
                        }
                    }
                }
            }
        }
    }

    // ── 管理員：Moments，依服務分組
    ColumnLayout {
        objectName: "momentsSection"
        Layout.fillWidth: true
        visible: panel.admin && panel.hasCurrent
        spacing: 10

        SectionLabel { text: qsTr("Moments") }

        Repeater {
            model: panel.admin ? panel.markedServices : []
            delegate: RowLayout {
                id: group
                required property var modelData
                objectName: "momentGroup"
                Layout.fillWidth: true
                spacing: 8

                Text {
                    objectName: "momentGroupLabel"
                    Layout.preferredWidth: 140
                    Layout.alignment: Qt.AlignTop
                    Layout.topMargin: 12
                    text: group.modelData.label
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                    font.features: { "tnum": 1 }
                    wrapMode: Text.Wrap
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: 8
                    Repeater {
                        model: group.modelData.markers
                        delegate: MomentChip {
                            required property var modelData
                            marker: modelData
                            serviceId: group.modelData.serviceId
                            serviceLabel: group.modelData.label
                        }
                    }
                }
            }
        }

        Text {
            objectName: "noMoments"
            Layout.fillWidth: true
            visible: panel.markedServices.length === 0
            text: qsTr("No moments marked.")
            color: Theme.textMuted
            font.pixelSize: Theme.fontCaption
        }
    }

    // ── 對話（取代舊版的 History）
    CoachingConversation {
        id: talk
        objectName: "conversation"
        Layout.fillWidth: true
        visible: panel.hasCurrent
        vm: panel.vm
        actionId: panel.actionId
        items: panel.hasCurrent ? panel.current.conversation : []
        canWrite: panel.isOpen
        partner: !panel.hasCurrent ? "" : panel.admin ? panel.current.staffName : panel.current.adminName
        placeholder: !panel.hasCurrent ? ""
                   : panel.admin ? qsTr("Write to %1…").arg(panel.current.staffFirst)
                   : qsTr("Reply to %1…").arg(panel.current.adminFirst)
    }

    // 完成、繼續練、改目標、撤回的表單（只有管理員）
    CoachingDialog {
        id: conclude
        vm: panel.vm
        current: panel.current
    }
}
