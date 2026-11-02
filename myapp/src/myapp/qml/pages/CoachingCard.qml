pragma ComponentBehavior: Bound
import QtQuick
// 自訂了時間註記按鈕的 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 管理員明細頁的輔導面板（PLAN1 W2；T47 依設計稿「Coaching, redesigned」改版）。內容區夠寬時放在影片旁邊、
// 窄的時候在下面（ServiceDetailView 排版），一邊看影片一邊做。一次只做一件事：
// - 新目標（「Coach Mia」「Mark moments, then set one goal.」）：播放器上的 Add Moment at 02:41 打開說明欄，時間是按下去的
//   那一秒；Save Moment 馬上出現在 Moments（播放器的進度條下也有一個點）。Goal、Why、Follow up by，Send Goal to Mia，
//   下面寫「2 moments will be attached.」。送出時說明欄還開著，先存成一個時間註記；舊版的說明欄和 Create Goal 是分開的，
//   打了字、沒按 Add Marker 就送出，那個時間註記會不見、也沒有說明（使用者回報的問題，T47 第 1 節）。
// - 送出之後：Goal sent to Mia、幾個時間註記、Open in Coaching（換到 Coaching、選這一項）、Set Another Goal。
// - 對準一項（從 Coaching 打開，或按 Mark Moments）：那一項的目標、這部影片上它的時間註記；Save Moment 直接存進資料庫，
//   成功了說明欄才關、失敗時字留著。後續服務的時間註記要在它自己的影片上加（PLAN1），所以要說清楚加到哪一項。
// - 和這筆服務有關的其他目標（原始服務、後續服務、看過的）：一項一個小方塊，Mark Moments、Open in Coaching。
// 草稿、打開的說明欄都存在 coachingModel（每一筆服務一份），換頁再回來還在。
// 外面給 positionMs、canMark（影片能播），接 seekRequested、revealRequested；播放器上的按鈕呼叫 startMoment()。
Card {
    id: card

    property var vm: null
    property string serviceId: ""
    property string staffName: ""
    property real positionMs: 0             // 播放器現在的位置
    property bool canMark: false            // 影片可以播：才能在「目前的位置」加時間註記

    readonly property int seconds: Math.max(0, Math.floor(positionMs / 1000))
    readonly property string staffFirst: staffName.split(" ")[0]
    readonly property var actions: vm ? vm.cardActions : []
    readonly property var draft: vm ? vm.draft : ({})
    readonly property var pending: draft.pending !== undefined ? draft.pending : ({ open: false })
    readonly property var sent: draft.sent !== undefined ? draft.sent : ({})
    readonly property string cardState: vm ? vm.cardState : "idle"
    readonly property string focusId: vm ? vm.cardFocusId : ""
    readonly property var focusAction: focusId === "" ? null : actions.find(item => item.actionId === focusId) || null
    // new：新目標；sent：剛送出；goal：對準一項
    readonly property string mode: focusAction !== null ? "goal" : sent.actionId !== undefined ? "sent" : "new"
    readonly property var moments: mode === "goal" ? focusAction.markers
                                 : mode === "new" && draft.markers !== undefined ? draft.markers : []
    readonly property var others: actions.filter(item => focusAction === null || item.actionId !== focusAction.actionId)
    readonly property bool adding: pending.open === true
    readonly property bool addEnabled: canMark && !adding && cardState === "ready" && mode !== "sent"
    // 播放器的進度條下面畫的點：這裡列出的時間註記，加上正在寫的那一個
    readonly property var pinSeconds: moments.map(moment => moment.seconds).concat(adding ? [pending.seconds] : [])
    readonly property int attachCount: (mode === "new" ? moments.length : 0) + (adding && mode === "new" ? 1 : 0)
    readonly property var errors: vm ? vm.createErrors : ({})
    readonly property var lastError: vm ? vm.actionError : ({})
    readonly property string targetId: mode === "goal" ? focusAction.actionId : ""
    readonly property bool savingMoment: mode === "goal" && vm !== null && vm.saving.indexOf(targetId) >= 0
    readonly property string momentError: lastError.actionId === targetId
                                          && (lastError.op === "marker" || lastError.op === "unmark") ? lastError.text : ""
    // 欄位（測試從外面操作用）
    readonly property alias goalField: goalInput
    readonly property alias commentField: commentInput
    readonly property alias dueField: dueInput
    readonly property alias noteField: noteInput

    signal seekRequested(real ms)
    signal revealRequested(var item)

    topPadding: 22
    bottomPadding: Theme.spaceXL
    leftPadding: Theme.spaceXL
    rightPadding: Theme.spaceXL
    Accessible.role: Accessible.Grouping
    Accessible.name: qsTr("Coach %1").arg(staffFirst)

    function clock(total) {
        const hours = Math.floor(total / 3600)
        const minutes = Math.floor(total / 60) % 60
        const rest = total % 60
        const mmss = String(minutes).padStart(2, "0") + ":" + String(rest).padStart(2, "0")
        return hours > 0 ? hours + ":" + mmss : mmss
    }

    // Add Moment（播放器上的按鈕）：在現在的位置打開說明欄，焦點進去，窄的版面捲到看得到
    function startMoment() {
        if (!vm || !addEnabled || !vm.startMoment(seconds))
            return false
        noteInput.text = ""
        noteInput.forceActiveFocus(Qt.TabFocusReason)
        revealLater.restart()
        return true
    }

    // 說明欄排好版面之後才捲（剛出現的那一刻，它在 ColumnLayout 裡的位置還沒算好）
    Timer {
        id: revealLater
        interval: 30
        onTriggered: card.revealRequested(noteBox)
    }

    function saveMoment() {
        if (vm && !savingMoment)
            vm.savePending()
    }

    function cancelMoment() {
        if (vm)
            vm.cancelPending()
    }

    // 草稿整份換了（換一筆服務、送出了、Set Another Goal）：照草稿重填欄位。打字只寫回草稿，不會跳
    function loadDraft() {
        goalInput.text = draft.goal !== undefined ? draft.goal : ""
        commentInput.text = draft.comment !== undefined ? draft.comment : ""
        dueInput.text = draft.due !== undefined ? draft.due : ""
        noteInput.text = pending.open ? pending.note : ""
    }

    Component.onCompleted: {
        if (!vm)
            return
        vm.watchService(serviceId)
        vm.pageOpened("card")
        loadDraft()
    }
    Component.onDestruction: {
        if (vm)
            vm.pageClosed("card")
    }
    onServiceIdChanged: {
        if (vm)
            vm.watchService(serviceId)
    }
    onCardStateChanged: {
        if (cardState !== "loading")
            loadingWait.elapsed = false
    }

    Connections {
        target: card.vm
        function onDraftReset() {
            card.loadDraft()
        }
    }

    // active 持續超過 busyDelay 才成立（DS-25）
    Timer {
        id: loadingWait
        property bool elapsed: false
        interval: Theme.busyDelay
        running: card.cardState === "loading" && !elapsed
        onTriggered: elapsed = true
    }

    // ── 小元件

    component SectionLabel: Text {
        color: Theme.textFaint
        font.pixelSize: Theme.fontSmall
        font.weight: Font.Bold
        font.capitalization: Font.AllUppercase
        font.letterSpacing: 1
        Accessible.role: Accessible.Heading
        Accessible.name: text
    }

    component FieldLabel: Text {
        Layout.fillWidth: true
        color: Theme.textSecondary
        font.pixelSize: Theme.fontSmall
        font.weight: Font.DemiBold
    }

    component Divider: Rectangle {
        Layout.fillWidth: true
        implicitHeight: 1
        color: Theme.cardBorder
    }

    // 一個時間註記：時間（按一下影片跳到那一秒）、說明、拿掉
    component MomentRow: Rectangle {
        id: row
        required property var modelData
        required property int index
        readonly property bool removable: card.mode === "new" || modelData.editable === true
        objectName: "panelMoment"
        Layout.fillWidth: true
        implicitHeight: Math.max(Theme.controlHeight, note.implicitHeight) + 8
        radius: 12
        color: Theme.tile
        border.width: 1
        border.color: Theme.tileBorder

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 6
            anchors.rightMargin: 4
            spacing: 6

            Button {
                id: jump
                objectName: "panelMomentTime"
                implicitHeight: Theme.controlHeight
                leftPadding: 4
                rightPadding: 4
                focusPolicy: Qt.StrongFocus
                text: qsTr("Play from %1").arg(row.modelData.clock)
                Accessible.name: text
                Keys.onReturnPressed: click()
                Keys.onEnterPressed: click()
                onClicked: card.seekRequested(row.modelData.seconds * 1000)

                contentItem: CoachingTime {
                    clock: row.modelData.clock
                    pillHeight: 30
                }
                background: Rectangle {
                    radius: height / 2
                    color: jump.down ? Theme.actionSecondaryPressed : jump.hovered ? Theme.actionSecondaryHover
                                                                                   : "transparent"
                    Rectangle {     // 鍵盤焦點框（DS-30）
                        visible: jump.visualFocus
                        anchors.fill: parent
                        anchors.margins: -2
                        radius: height / 2
                        color: "transparent"
                        border.color: Theme.focusOnDark
                        border.width: 2
                    }
                }
            }
            Text {
                id: note
                objectName: "panelMomentNote"
                Layout.fillWidth: true
                text: row.modelData.note
                color: Theme.textPrimary
                font.pixelSize: Theme.fontCaption
                wrapMode: Text.Wrap
            }
            Button {
                id: remove
                objectName: "panelRemoveMoment"
                visible: row.removable
                implicitWidth: Theme.controlHeight
                implicitHeight: Theme.controlHeight
                focusPolicy: Qt.StrongFocus
                enabled: !card.adding && !card.savingMoment
                text: qsTr("Remove the moment at %1").arg(row.modelData.clock)
                Accessible.name: text
                onClicked: {
                    if (!card.vm)
                        return
                    if (card.mode === "goal")
                        card.vm.removeMarker(card.targetId, row.modelData.markerId)
                    else
                        card.vm.removeDraftMarker(row.index)
                }

                contentItem: Item {
                    Image {
                        anchors.centerIn: parent
                        width: Theme.iconS
                        height: Theme.iconS
                        sourceSize: Qt.size(Theme.iconS, Theme.iconS)
                        source: "../components/icons/x_textFaint.svg"
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
    }

    // ── 版面

    ColumnLayout {
        width: parent ? parent.width : 0
        spacing: 18

        // 標題：Coach Mia、一句話說這一步做什麼
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4

            Text {
                objectName: "panelTitle"
                Layout.fillWidth: true
                text: card.staffFirst !== "" ? qsTr("Coach %1").arg(card.staffFirst) : qsTr("Coaching")
                color: Theme.textTitle
                font.pixelSize: Theme.fontTitleSmall
                font.weight: Font.Bold
                elide: Text.ElideRight
                Accessible.role: Accessible.Heading
                Accessible.name: text
            }
            Text {
                objectName: "panelSubtitle"
                Layout.fillWidth: true
                text: card.mode !== "goal" ? qsTr("Mark moments, then set one goal.")
                    : card.focusAction.relation === "follow_up"
                      ? qsTr("Mark moments in this follow-up, then review it in Coaching.")
                      : qsTr("Mark more moments for this goal.")
                color: Theme.textMuted
                font.pixelSize: Theme.fontCaption
                wrapMode: Text.Wrap
            }
        }

        Text {
            objectName: "panelLoading"
            visible: card.cardState === "loading" && loadingWait.elapsed
            text: qsTr("Checking for goals on this service…")
            color: Theme.textMuted
            font.pixelSize: Theme.fontCaption
        }
        RowLayout {
            Layout.fillWidth: true
            visible: card.cardState === "error"
            spacing: Theme.spaceS
            FieldError {
                Layout.fillWidth: true
                text: card.cardState === "error" ? qsTr("Couldn't load the goals for this service.") : ""
            }
            BusyButton {
                objectName: "reloadCardButton"
                text: qsTr("Retry")
                font.pixelSize: Theme.fontCaption
                onClicked: {
                    if (card.vm)
                        card.vm.reloadCard()
                }
            }
        }

        // ── 對準的那一項：加到哪裡一看就知道
        Rectangle {
            objectName: "focusGoal"
            Layout.fillWidth: true
            visible: card.mode === "goal"
            implicitHeight: focusColumn.implicitHeight + 28
            radius: Theme.radiusTile
            color: Theme.panelInset
            border.width: 1
            border.color: Theme.infoOnDark
            Accessible.role: Accessible.Grouping
            Accessible.name: focusKicker.text + ": " + focusGoalText.text

            ColumnLayout {
                id: focusColumn
                x: 16
                y: 14
                width: parent.width - 32
                spacing: 6

                Text {
                    id: focusKicker
                    Layout.fillWidth: true
                    text: card.mode === "goal" && card.focusAction.relation === "follow_up"
                          ? qsTr("Follow-up for this goal") : qsTr("Example for this goal")
                    color: Theme.infoOnDark
                    font.pixelSize: Theme.fontLabel
                    font.weight: Font.Bold
                    font.capitalization: Font.AllUppercase
                    font.letterSpacing: 1
                }
                Text {
                    id: focusGoalText
                    objectName: "focusGoalText"
                    Layout.fillWidth: true
                    text: card.mode === "goal" ? card.focusAction.goal : ""
                    color: Theme.textTitle
                    font.pixelSize: Theme.fontInput
                    font.weight: Font.DemiBold
                    wrapMode: Text.Wrap
                }
                RowLayout {
                    spacing: 8
                    CoachingChip { actionState: card.mode === "goal" ? card.focusAction.chipState : "open" }
                    Text {
                        text: card.mode === "goal" ? card.focusAction.dueText : ""
                        color: Theme.textFaint
                        font.pixelSize: Theme.fontSmall
                        font.features: { "tnum": 1 }
                    }
                }
            }
        }

        // ── Moments：這裡列出的、加的都在這部影片上
        ColumnLayout {
            objectName: "panelMoments"
            Layout.fillWidth: true
            visible: card.mode !== "sent"
            spacing: 10

            Row {
                spacing: 8
                SectionLabel { text: qsTr("Moments") }
                Text {
                    objectName: "momentCount"
                    text: card.moments.length
                    color: Theme.textFaint
                    font.pixelSize: Theme.fontSmall
                    font.features: { "tnum": 1 }
                }
            }

            Repeater {
                model: card.mode !== "sent" ? card.moments : []
                delegate: MomentRow {}
            }

            // 還沒有：說明怎麼加（只有空的時候才有說明）
            Rectangle {
                objectName: "noPanelMoments"
                Layout.fillWidth: true
                visible: card.moments.length === 0 && !card.adding
                implicitHeight: emptyText.implicitHeight + 24
                radius: 12
                color: Theme.panelInset
                border.width: 1
                border.color: Theme.actionSecondaryBorder

                Text {
                    id: emptyText
                    x: 14
                    y: 12
                    width: parent.width - 28
                    text: card.canMark ? qsTr("Pause the video, then press Add Moment.")
                                       : qsTr("You can add moments once the video is ready to play.")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontCaption
                    wrapMode: Text.Wrap
                }
            }

            // 說明欄：Add Moment 打開，時間是按下去的那一秒
            Rectangle {
                id: noteBox
                objectName: "momentEditor"
                Layout.fillWidth: true
                visible: card.adding
                implicitHeight: noteColumn.implicitHeight + 24
                radius: 12
                color: Theme.tile
                border.width: 1
                border.color: Theme.fieldBorder

                ColumnLayout {
                    id: noteColumn
                    x: 12
                    y: 12
                    width: parent.width - 24
                    spacing: 10

                    FieldLabel {
                        objectName: "momentEditorLabel"
                        text: card.mode === "goal" ? qsTr("Note for %1 · adds to this goal").arg(card.pending.clock || "")
                                                   : qsTr("Note for %1").arg(card.pending.clock || "")
                        font.features: { "tnum": 1 }
                        wrapMode: Text.Wrap
                    }
                    CoachingField {
                        id: noteInput
                        objectName: "momentNote"
                        Layout.fillWidth: true
                        maximumLength: 300
                        placeholderText: qsTr("What happens here? (optional)")
                        invalid: card.momentError !== ""
                        errorText: card.momentError
                        Accessible.name: qsTr("Note for the moment at %1").arg(card.pending.clock || "")
                        onTextChanged: {
                            if (card.vm && card.adding)
                                card.vm.updatePending(text)
                        }
                        onAccepted: card.saveMoment()
                        Keys.onEscapePressed: card.cancelMoment()
                    }
                    FieldError {
                        objectName: "momentError"
                        Layout.fillWidth: true
                        visible: hasError
                        text: card.adding ? card.momentError : ""
                    }
                    RowLayout {
                        Layout.alignment: Qt.AlignRight
                        spacing: 8

                        BusyButton {
                            objectName: "cancelMomentButton"
                            text: qsTr("Cancel")
                            font.pixelSize: Theme.fontCaption
                            onClicked: card.cancelMoment()
                        }
                        BusyButton {
                            objectName: "saveMomentButton"
                            variant: "primary"
                            text: qsTr("Save Moment")
                            busyText: qsTr("Saving…")
                            font.pixelSize: Theme.fontCaption
                            busy: card.savingMoment
                            onClicked: card.saveMoment()
                        }
                    }
                }
            }

            // 拿掉已經存好的時間註記失敗（例如連不上）
            FieldError {
                objectName: "removeMomentError"
                Layout.fillWidth: true
                visible: hasError
                text: !card.adding && card.mode === "goal" ? card.momentError : ""
            }
        }

        // ── 新目標：目標、為什麼、期限、送出
        Divider { visible: card.mode === "new" }

        ColumnLayout {
            objectName: "goalForm"
            Layout.fillWidth: true
            visible: card.mode === "new"
            spacing: 6

            SectionLabel {
                Layout.fillWidth: true
                text: qsTr("Goal")
            }
            CoachingTextArea {
                id: goalInput
                objectName: "goalInput"
                Layout.fillWidth: true
                minimumLines: 2
                placeholderText: qsTr("For example: Explain the next step before the service ends.")
                invalid: card.errors.goal !== undefined && card.errors.goal !== ""
                errorText: invalid ? card.errors.goal : ""
                Accessible.name: qsTr("Goal")
                onTextChanged: {
                    if (card.vm)
                        card.vm.updateDraft("goal", text)
                }
            }
            FieldError {
                Layout.fillWidth: true
                visible: hasError
                text: card.errors.goal !== undefined ? card.errors.goal : ""
            }

            FieldLabel {
                Layout.topMargin: 8
                text: qsTr("Why")
            }
            CoachingTextArea {
                id: commentInput
                objectName: "commentInput"
                Layout.fillWidth: true
                minimumLines: 2
                placeholderText: qsTr("What did you see? %1 reads this with the goal.").arg(card.staffFirst)
                invalid: card.errors.comment !== undefined && card.errors.comment !== ""
                errorText: invalid ? card.errors.comment : ""
                Accessible.name: qsTr("Why")
                onTextChanged: {
                    if (card.vm)
                        card.vm.updateDraft("comment", text)
                }
            }
            FieldError {
                Layout.fillWidth: true
                visible: hasError
                text: card.errors.comment !== undefined ? card.errors.comment : ""
            }

            FieldLabel {
                Layout.topMargin: 8
                text: qsTr("Follow up by")
            }
            DateField {
                id: dueInput
                objectName: "dueInput"
                Layout.fillWidth: true
                invalid: card.errors.due !== undefined && card.errors.due !== ""
                errorText: invalid ? card.errors.due : ""
                accessibleName: qsTr("Follow up by")
                onTextChanged: {
                    if (card.vm)
                        card.vm.updateDraft("due", text)
                }
            }
            FieldError {
                Layout.fillWidth: true
                visible: hasError
                text: card.errors.due !== undefined ? card.errors.due : ""
            }

            // 寫不進去（例如連不上資料庫）：寫在按鈕上面，草稿留著
            FieldError {
                objectName: "composerError"
                Layout.fillWidth: true
                visible: hasError
                text: card.errors.form !== undefined ? card.errors.form : ""
            }

            BusyButton {
                objectName: "sendGoalButton"
                Layout.fillWidth: true
                Layout.topMargin: 10
                variant: "primary"
                large: true
                text: card.staffFirst !== "" ? qsTr("Send Goal to %1").arg(card.staffFirst) : qsTr("Send Goal")
                busyText: qsTr("Sending…")
                iconSource: "../components/icons/send_actionPrimaryText.svg"
                busy: card.vm !== null && card.vm.creating
                onClicked: {
                    if (!busy && card.vm)
                        card.vm.createGoal()
                }
            }
            Text {
                objectName: "attachText"
                Layout.fillWidth: true
                text: card.attachCount === 0 ? qsTr("No moments attached.")
                    : card.attachCount === 1 ? qsTr("1 moment will be attached.")
                    : qsTr("%1 moments will be attached.").arg(card.attachCount)
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
                horizontalAlignment: Text.AlignHCenter
            }
        }

        // ── 送出之後
        Rectangle {
            objectName: "sentBox"
            Layout.fillWidth: true
            visible: card.mode === "sent"
            implicitHeight: sentColumn.implicitHeight + 36
            radius: Theme.radiusTile
            color: Theme.successTint
            border.width: 1
            border.color: Theme.successBorder
            Accessible.role: Accessible.AlertMessage
            Accessible.name: sentTitle.text + ". " + sentText.text

            ColumnLayout {
                id: sentColumn
                x: 18
                y: 18
                width: parent.width - 36
                spacing: 12

                Row {
                    spacing: 8
                    Image {
                        anchors.verticalCenter: parent.verticalCenter
                        width: Theme.iconM
                        height: Theme.iconM
                        sourceSize: Qt.size(Theme.iconM, Theme.iconM)
                        source: "../components/icons/check_successOnDark.svg"
                        Accessible.ignored: true
                    }
                    Text {
                        id: sentTitle
                        objectName: "sentTitle"
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("Goal sent to %1").arg(card.staffFirst)
                        color: Theme.successOnDark
                        font.pixelSize: Theme.fontBody
                        font.weight: Font.Bold
                    }
                }
                Text {
                    id: sentText
                    objectName: "sentText"
                    Layout.fillWidth: true
                    text: (card.sent.count === 0 ? qsTr("No moments attached.")
                         : card.sent.count === 1 ? qsTr("1 moment attached.")
                         : qsTr("%1 moments attached.").arg(card.sent.count))
                          + " " + qsTr("%1 will see it in My Actions.").arg(card.staffFirst)
                    color: Theme.iconNeutral
                    font.pixelSize: Theme.fontCaption
                    wrapMode: Text.Wrap
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: 8
                    BusyButton {
                        objectName: "sentOpenButton"
                        variant: "primary"
                        text: qsTr("Open in Coaching")
                        iconSource: "../components/icons/arrow-right_actionPrimaryText.svg"
                        font.pixelSize: Theme.fontCaption
                        onClicked: {
                            if (card.vm)
                                card.vm.requestOpenAction(card.sent.actionId)
                        }
                    }
                    BusyButton {
                        objectName: "setAnotherGoalButton"
                        text: qsTr("Set Another Goal")
                        font.pixelSize: Theme.fontCaption
                        onClicked: {
                            if (card.vm)
                                card.vm.newGoal()
                        }
                    }
                }
            }
        }

        // ── 對準一項時：回到 Coaching、改成訂新的目標
        Flow {
            objectName: "focusActions"
            Layout.fillWidth: true
            visible: card.mode === "goal"
            spacing: 8

            BusyButton {
                objectName: "focusOpenButton"
                variant: "primary"
                text: qsTr("Open in Coaching")
                iconSource: "../components/icons/arrow-right_actionPrimaryText.svg"
                font.pixelSize: Theme.fontCaption
                onClicked: {
                    if (card.vm)
                        card.vm.requestOpenAction(card.targetId)
                }
            }
            BusyButton {
                objectName: "newGoalButton"
                enabled: !card.adding
                text: qsTr("Set a New Goal")
                font.pixelSize: Theme.fontCaption
                onClicked: {
                    if (card.vm)
                        card.vm.focusGoal("")
                }
            }
        }

        // ── 和這筆服務有關的其他目標（小方塊）
        Divider { visible: card.others.length > 0 }

        ColumnLayout {
            objectName: "otherGoals"
            Layout.fillWidth: true
            visible: card.others.length > 0
            spacing: 8

            SectionLabel {
                Layout.fillWidth: true
                text: card.mode === "goal" ? qsTr("Other goals on this service") : qsTr("Goals on this service")
            }

            Repeater {
                model: card.others
                delegate: Rectangle {
                    id: other
                    required property var modelData
                    objectName: "otherGoal"
                    Layout.fillWidth: true
                    implicitHeight: otherColumn.implicitHeight + 24
                    radius: 12
                    color: Theme.tile
                    border.width: 1
                    border.color: Theme.tileBorder
                    Accessible.role: Accessible.Grouping
                    Accessible.name: qsTr("%1: %2").arg(modelData.relationText).arg(modelData.goal)

                    ColumnLayout {
                        id: otherColumn
                        x: 12
                        y: 12
                        width: parent.width - 24
                        spacing: 8

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Text {
                                objectName: "otherRelation"
                                text: other.modelData.relationText
                                color: Theme.textFaint
                                font.pixelSize: Theme.fontLabel
                                font.weight: Font.Bold
                                font.capitalization: Font.AllUppercase
                                font.letterSpacing: 1
                            }
                            CoachingChip { actionState: other.modelData.chipState }
                            Text {
                                Layout.fillWidth: true
                                text: other.modelData.dueText
                                color: Theme.textFaint
                                font.pixelSize: Theme.fontSmall
                                font.features: { "tnum": 1 }
                                elide: Text.ElideRight
                            }
                        }
                        Text {
                            objectName: "otherGoalText"
                            Layout.fillWidth: true
                            text: other.modelData.goal
                            color: Theme.textPrimary
                            font.pixelSize: Theme.fontCaption
                            font.weight: Font.DemiBold
                            wrapMode: Text.Wrap
                            maximumLineCount: 2
                            elide: Text.ElideRight
                        }
                        Text {
                            objectName: "otherMoments"
                            Layout.fillWidth: true
                            text: other.modelData.markers.length === 0 ? qsTr("No moments in this video")
                                : other.modelData.markers.length === 1 ? qsTr("1 moment in this video")
                                : qsTr("%1 moments in this video").arg(other.modelData.markers.length)
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSmall
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: 8
                            BusyButton {
                                objectName: "markMomentsButton"
                                visible: other.modelData.canMark
                                enabled: !card.adding
                                text: qsTr("Mark Moments")
                                font.pixelSize: Theme.fontCaption
                                Accessible.name: qsTr("Mark moments for %1").arg(other.modelData.goal)
                                onClicked: {
                                    if (card.vm)
                                        card.vm.focusGoal(other.modelData.actionId)
                                }
                            }
                            BusyButton {
                                objectName: "openInCoachingButton"
                                text: qsTr("Open in Coaching")
                                font.pixelSize: Theme.fontCaption
                                Accessible.name: qsTr("Open %1 in Coaching").arg(other.modelData.goal)
                                onClicked: {
                                    if (card.vm)
                                        card.vm.requestOpenAction(other.modelData.actionId)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
