pragma ComponentBehavior: Bound
import QtQuick
// 自訂了按鈕的 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 管理員明細頁的輔導卡（PLAN1 W2，T45）：放在影片下面，看影片時就能寫。
// - 和這筆服務有關的改善行動（從這筆訂的、這筆是後續服務、調整時看過這筆）：狀態、目標、這部影片上的時間註記
//   （按一下跳到那一秒，進行中的可以拿掉），進行中的可以在影片目前的位置加一個註記，「Open in Coaching」換到清單頁。
// - New Goal：展開表單（PLAN 13.1：需要時才展開）：目標、為什麼（評語）、追蹤期限、這部影片上的時間註記（選用）。
//   草稿存在 coachingModel（每一筆服務各一份）：換頁再回來還在，Create Goal 或 Discard 才清掉。
//   Create Goal 在一個交易裡寫行動和註記；員工是這筆服務的服務人員。
// - 時間註記從這筆服務自己的開頭算（影片的位置），只屬於這一部影片。影片還不能播時不能加。
// 外面（ServiceDetailView）給 positionMs、canMark，接 seekRequested。
Card {
    id: card

    property var vm: null
    property string serviceId: ""
    property string staffName: ""
    property real positionMs: 0             // 播放器現在的位置
    property bool canMark: false            // 影片可以播：才能在「目前的位置」加註記

    readonly property int seconds: Math.max(0, Math.floor(positionMs / 1000))
    readonly property string clockText: clock(seconds)
    readonly property var actions: vm ? vm.cardActions : []
    readonly property var draft: vm ? vm.draft : ({})
    readonly property bool composing: draft.open === true
    readonly property var errors: vm ? vm.createErrors : ({})
    readonly property var lastError: vm ? vm.actionError : ({})
    readonly property string cardState: vm ? vm.cardState : "idle"
    // 欄位（測試從外面操作用）
    readonly property alias goalField: goalInput
    readonly property alias commentField: commentInput
    readonly property alias dueField: dueInput
    readonly property alias draftNoteField: draftNote

    signal seekRequested(real ms)

    topPadding: 18
    bottomPadding: 18
    Accessible.role: Accessible.Grouping
    Accessible.name: qsTr("Coaching for this service")

    function clock(total) {
        const hours = Math.floor(total / 3600)
        const minutes = Math.floor(total / 60) % 60
        const rest = total % 60
        const mmss = String(minutes).padStart(2, "0") + ":" + String(rest).padStart(2, "0")
        return hours > 0 ? hours + ":" + mmss : mmss
    }

    // 草稿換了（換一筆服務、Create Goal、Discard）：欄位照草稿重填。打字只往草稿寫，不從草稿讀回來（游標不會跳）
    function loadDraft() {
        goalInput.text = draft.goal !== undefined ? draft.goal : ""
        commentInput.text = draft.comment !== undefined ? draft.comment : ""
        dueInput.text = draft.due !== undefined ? draft.due : ""
        draftNote.text = ""
    }

    function startGoal() {
        if (!vm)
            return
        vm.setDraftOpen(true)
        goalInput.forceActiveFocus(Qt.TabFocusReason)
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

    component FieldLabel: Text {
        Layout.fillWidth: true
        color: Theme.textSecondary
        font.pixelSize: Theme.fontCaption
        font.weight: Font.DemiBold
    }

    // 一個時間註記：按一下跳到這一秒（同一部影片）；可以拿掉的有一個 ×
    component MarkerChip: Row {
        id: chip
        required property string clockText
        required property string note
        required property int seconds
        property bool removable: false
        signal removeRequested()
        spacing: 2

        Button {
            id: jump
            objectName: "cardMarkerButton"
            implicitHeight: Theme.controlHeight
            leftPadding: 12
            rightPadding: 14
            focusPolicy: Qt.StrongFocus
            text: chip.note !== "" ? qsTr("%1 · %2").arg(chip.clockText).arg(chip.note) : chip.clockText
            Accessible.name: qsTr("Play from %1").arg(chip.clockText)
            Accessible.description: chip.note
            Keys.onReturnPressed: click()
            Keys.onEnterPressed: click()
            onClicked: card.seekRequested(chip.seconds * 1000)

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
                    width: Math.min(implicitWidth, 320)
                    text: jump.text
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontCaption
                    font.features: { "tnum": 1 }
                    elide: Text.ElideRight
                }
            }
            background: Rectangle {
                radius: 12
                color: jump.down ? Theme.actionSecondaryPressed : jump.hovered ? Theme.actionSecondaryHover : Theme.card
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
            objectName: "cardRemoveMarkerButton"
            visible: chip.removable
            implicitWidth: Theme.controlHeight
            implicitHeight: Theme.controlHeight
            focusPolicy: Qt.StrongFocus
            text: qsTr("Remove the marker at %1").arg(chip.clockText)
            Accessible.name: text
            onClicked: chip.removeRequested()

            contentItem: Item {
                Image {
                    anchors.centerIn: parent
                    width: Theme.iconS
                    height: Theme.iconS
                    sourceSize: Qt.size(Theme.iconS, Theme.iconS)
                    source: "../components/icons/x_chromeIcon.svg"
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

    // 和這筆服務有關的一項行動
    component ActionRow: Rectangle {
        id: row
        required property var modelData     // coaching.card_items 的一項
        readonly property string actionId: modelData.actionId
        readonly property bool saving: card.vm !== null && card.vm.saving.indexOf(actionId) >= 0
        readonly property string errorText: card.lastError.actionId === actionId
                                            && (card.lastError.op === "marker" || card.lastError.op === "unmark")
                                            ? card.lastError.text : ""

        objectName: "cardActionRow"
        Layout.fillWidth: true
        implicitHeight: rowColumn.implicitHeight + 2 * Theme.spaceM
        radius: 12
        color: Theme.tile
        border.width: 1
        border.color: Theme.tileBorder
        Accessible.role: Accessible.Grouping
        Accessible.name: qsTr("%1: %2").arg(modelData.relationText).arg(modelData.goal)

        Connections {
            target: card.vm
            function onSaved(actionId, op) {
                if (actionId === row.actionId && op === "marker")
                    rowNote.text = ""
            }
        }

        ColumnLayout {
            id: rowColumn
            x: Theme.spaceM
            y: Theme.spaceM
            width: parent.width - 2 * Theme.spaceM
            spacing: 10

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceS

                CoachingChip { actionState: row.modelData.chipState }
                Text {
                    objectName: "cardActionMeta"
                    Layout.fillWidth: true
                    text: [row.modelData.relationText, row.modelData.staffName, row.modelData.dueText]
                          .filter(part => part !== "").join(" · ")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                    elide: Text.ElideRight
                }
                BusyButton {
                    objectName: "openInCoachingButton"
                    text: qsTr("Open in Coaching")
                    font.pixelSize: Theme.fontCaption
                    Accessible.name: qsTr("Open %1 in Coaching").arg(row.modelData.goal)
                    onClicked: {
                        if (card.vm)
                            card.vm.requestOpenAction(row.actionId)
                    }
                }
            }

            Text {
                objectName: "cardActionGoal"
                Layout.fillWidth: true
                text: row.modelData.goal
                color: Theme.textTitle
                font.pixelSize: Theme.fontBody
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
            }

            Flow {
                Layout.fillWidth: true
                visible: row.modelData.markers.length > 0
                spacing: 8
                Repeater {
                    model: row.modelData.markers
                    delegate: MarkerChip {
                        required property var modelData
                        clockText: modelData.clock
                        note: modelData.note
                        seconds: modelData.seconds
                        removable: modelData.editable
                        onRemoveRequested: {
                            if (card.vm)
                                card.vm.removeMarker(row.actionId, modelData.markerId)
                        }
                    }
                }
            }

            // 進行中、這筆是原始或後續服務：在影片目前的位置加一個註記
            RowLayout {
                objectName: "cardMarkerRow"
                Layout.fillWidth: true
                visible: row.modelData.canMark
                spacing: Theme.spaceS

                CoachingField {
                    id: rowNote
                    objectName: "cardMarkerNote"
                    Layout.fillWidth: true
                    maximumLength: 300
                    placeholderText: qsTr("Note for this moment (optional)")
                    Accessible.name: qsTr("Note for the marker")
                }
                BusyButton {
                    objectName: "cardAddMarkerButton"
                    text: qsTr("Add Marker at %1").arg(card.clockText)
                    iconSource: "../components/icons/map-pin_actionSecondaryText.svg"
                    font.pixelSize: Theme.fontCaption
                    enabled: card.canMark
                    busy: row.saving
                    onClicked: {
                        if (!busy && card.vm)
                            card.vm.addMarker(row.actionId, card.serviceId, card.seconds, rowNote.text)
                    }
                }
            }
            Text {
                Layout.fillWidth: true
                visible: row.modelData.canMark && !card.canMark
                text: qsTr("You can add markers once the video is ready to play.")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
                wrapMode: Text.Wrap
            }

            FieldError {
                Layout.fillWidth: true
                visible: hasError
                text: row.errorText
            }
        }
    }

    ColumnLayout {
        width: parent ? parent.width : 0
        spacing: Theme.spaceM

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceM

            Image {
                Layout.alignment: Qt.AlignVCenter
                Layout.preferredWidth: Theme.iconM
                Layout.preferredHeight: Theme.iconM
                sourceSize: Qt.size(Theme.iconM, Theme.iconM)
                source: "../components/icons/target_iconNeutral.svg"
                Accessible.ignored: true
            }
            Column {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                spacing: 4

                Text {
                    text: qsTr("Coaching")
                    color: Theme.textTitle
                    font.pixelSize: Theme.fontBody
                    font.weight: Font.Bold
                    Accessible.role: Accessible.Heading
                    Accessible.name: text
                }
                Text {
                    objectName: "cardNote"
                    width: parent.width
                    text: card.cardState === "error" ? qsTr("Couldn't load the goals for this service.")
                        : card.actions.length === 0 ? qsTr("No goals use this service yet. Watch it, then set one improvement goal for %1.")
                                                      .arg(card.staffName !== "" ? card.staffName : qsTr("this staff member"))
                        : qsTr("Goals that use this service. Markers point to moments in this video.")
                    color: card.cardState === "error" ? Theme.errorOnDark : Theme.textMuted
                    font.pixelSize: Theme.fontCaption
                    wrapMode: Text.Wrap
                }
            }
            BusyButton {
                objectName: "reloadCardButton"
                visible: card.cardState === "error"
                text: qsTr("Retry")
                onClicked: card.vm.reloadCard()
            }
            BusyButton {
                objectName: "newGoalButton"
                visible: !card.composing
                variant: "primary"
                text: qsTr("New Goal")
                iconSource: "../components/icons/plus_actionPrimaryText.svg"
                onClicked: card.startGoal()
            }
        }

        Text {
            objectName: "cardLoading"
            visible: card.cardState === "loading" && loadingWait.elapsed
            text: qsTr("Checking for goals…")
            color: Theme.textMuted
            font.pixelSize: Theme.fontCaption
        }

        Repeater {
            objectName: "cardActions"
            model: card.actions
            delegate: ActionRow {}
        }

        // ── 新的目標（草稿）
        ColumnLayout {
            objectName: "goalComposer"
            Layout.fillWidth: true
            visible: card.composing
            spacing: 8

            Rectangle {
                Layout.fillWidth: true
                Layout.bottomMargin: 8
                implicitHeight: 1
                color: Theme.cardBorder
            }

            Text {
                Layout.fillWidth: true
                text: qsTr("New goal for %1").arg(card.staffName !== "" ? card.staffName : qsTr("this staff member"))
                color: Theme.textTitle
                font.pixelSize: Theme.fontBody
                font.weight: Font.Bold
                Accessible.role: Accessible.Heading
                Accessible.name: text
            }
            Text {
                Layout.fillWidth: true
                text: qsTr("One goal the staff member can practise in later services. They see it in My Actions with your comment and this service as the example.")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
                wrapMode: Text.Wrap
            }

            FieldLabel { text: qsTr("Goal") }
            CoachingField {
                id: goalInput
                objectName: "goalInput"
                Layout.fillWidth: true
                maximumLength: 300
                placeholderText: qsTr("For example: Explain the next step before the service ends and check the customer understood.")
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

            FieldLabel { text: qsTr("Why this goal") }
            CoachingTextArea {
                id: commentInput
                objectName: "commentInput"
                Layout.fillWidth: true
                minimumLines: 3
                placeholderText: qsTr("What did you see in this service? The staff member reads this with the goal.")
                invalid: card.errors.comment !== undefined && card.errors.comment !== ""
                errorText: invalid ? card.errors.comment : ""
                Accessible.name: qsTr("Why this goal")
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

            FieldLabel { text: qsTr("Follow up by") }
            DateField {
                id: dueInput
                objectName: "dueInput"
                invalid: card.errors.due !== undefined && card.errors.due !== ""
                errorText: invalid ? card.errors.due : ""
                Accessible.name: qsTr("Follow up by")
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

            FieldLabel { text: qsTr("Moments in this video (optional)") }
            Flow {
                objectName: "draftMarkers"
                Layout.fillWidth: true
                visible: card.draft.markers !== undefined && card.draft.markers.length > 0
                spacing: 8
                Repeater {
                    model: card.draft.markers !== undefined ? card.draft.markers : []
                    delegate: MarkerChip {
                        required property var modelData
                        required property int index
                        clockText: modelData.clock
                        note: modelData.note
                        seconds: modelData.seconds
                        removable: true
                        onRemoveRequested: {
                            if (card.vm)
                                card.vm.removeDraftMarker(index)
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spaceS

                CoachingField {
                    id: draftNote
                    objectName: "draftNote"
                    Layout.fillWidth: true
                    maximumLength: 300
                    placeholderText: qsTr("Note for this moment (optional)")
                    Accessible.name: qsTr("Note for the marker")
                }
                BusyButton {
                    objectName: "addDraftMarkerButton"
                    text: qsTr("Add Marker at %1").arg(card.clockText)
                    iconSource: "../components/icons/map-pin_actionSecondaryText.svg"
                    font.pixelSize: Theme.fontCaption
                    enabled: card.canMark
                    onClicked: {
                        if (card.vm && card.vm.addDraftMarker(card.seconds, draftNote.text))
                            draftNote.text = ""
                    }
                }
            }
            Text {
                Layout.fillWidth: true
                visible: !card.canMark
                text: qsTr("You can add markers once the video is ready to play.")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
                wrapMode: Text.Wrap
            }

            // 存不進去（例如連不上資料庫）：寫在按鈕上面，草稿留著
            FieldError {
                objectName: "composerError"
                Layout.fillWidth: true
                visible: hasError
                text: card.errors.form !== undefined ? card.errors.form : ""
            }

            RowLayout {
                Layout.alignment: Qt.AlignRight
                Layout.topMargin: 8
                spacing: 12

                BusyButton {
                    objectName: "discardGoalButton"
                    text: qsTr("Discard")
                    onClicked: {
                        if (card.vm)
                            card.vm.discardDraft()
                    }
                }
                BusyButton {
                    objectName: "createGoalButton"
                    variant: "primary"
                    text: qsTr("Create Goal")
                    busyText: qsTr("Creating…")
                    busy: card.vm !== null && card.vm.creating
                    onClicked: {
                        if (!busy && card.vm)
                            card.vm.createGoal()
                    }
                }
            }
        }
    }
}
