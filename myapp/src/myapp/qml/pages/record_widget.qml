pragma ComponentBehavior: Bound
import QtQuick
// 下拉選單、捲軸自訂了 background、contentItem、popup，要用 Basic 樣式（T13）：原生樣式不能自訂，深色也做不出來
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 錄影頁（新版介面 U2，PLAN 6-2，設計稿 StaffRecord）：選好裝置，打開錄影視窗；下面列出背景存檔。
// - 頁面標題和說明在外殼的頁首，這一頁不畫標題。
// - Devices 卡片：三張裝置卡（顧客鏡頭、員工鏡頭、麥克風），各有一個下拉選單和一句「對著誰」。
//   打開這一頁才探測裝置（UX-13、ENG-06），結果留著；Scan Again 重新探測。探測中、裝置不夠（空狀態）、
//   探測失敗（區塊錯誤）都畫在卡片裡。員工鏡頭預設選第二支；兩支相同時欄位框變紅、下面說明，Open Recorder 停用。
// - 一開始的選擇來自設定頁存的裝置設定（deviceFinder.startSelection，T41），這一頁照舊可以換、可以重新探測。
//   存的設定對不上（裝置拔插過、認不出身分、存的裝置沒接）時，卡片上說明哪裡不同，按 Confirm Devices 才能開錄影視窗：
//   不會默默換成另一支鏡頭。沒有相機權限時，空狀態說明去哪裡打開。
// - Open Recorder 打開錄影視窗（QtWidgets 的 RecordWidget，PLAN D5）。同時只開一個：錄影中再按是帶回最前面。
// - Saving 卡片（UD9、T29）：deviceFinder.saves 的每一段存檔。上傳中有第幾個檔案、MB、百分比和進度條；
//   存好的有 Analyze，沒存好的有原因和 Retry，都和通知上的同一個動作。沒有任何存檔時不顯示這張卡片。
// - 內容比內容區高時自己捲動（外殼的約定）；用 Tab 移到看不到的按鈕時捲過去。下面多留通知的高度。
Item {
    id: recordPage

    readonly property var finder: typeof deviceFinder !== "undefined" ? deviceFinder : null
    readonly property var saves: finder ? finder.saves : null
    readonly property string status: finder ? finder.status : "idle"
    readonly property int cameraCount: finder ? finder.cameras.length : 0
    readonly property int microphoneCount: finder ? finder.microphones.length : 0
    readonly property bool enoughDevices: cameraCount >= 2 && microphoneCount >= 1
    readonly property bool formShown: status === "ready" && enoughDevices
    readonly property bool sameCamera: cam0ComboBox.currentIndex === cam1ComboBox.currentIndex
    readonly property bool recording: finder ? finder.recording : false
    // 存的裝置設定（T41）：changed、missing 時要先確認角色。rolesConfirmed 是這一次的確認，清單或設定一變就重來
    readonly property string setupState: finder ? finder.setupState : "none"
    property bool rolesConfirmed: false
    readonly property bool needsConfirmation: formShown && !rolesConfirmed
                                              && (setupState === "changed" || setupState === "missing")
    readonly property string cameraAccess: finder ? finder.cameraAccess : "unknown"
    readonly property bool cameraAccessOff: cameraAccess === "denied" || cameraAccess === "restricted"
    // 通知疊在內容區的右下角：捲到底時，最後一列可以捲到通知上面（和分析頁的清單一樣）
    readonly property int toastClearance: typeof toastHost !== "undefined" && toastHost ? toastHost.occupiedHeight : 0

    // 一開始的選擇：存的裝置設定對到這次清單的位置；沒存過（或那個角色找不到）時照舊選前兩支鏡頭、第一支麥克風
    function resetSelection() {
        const start = finder ? finder.startSelection : null
        cam0ComboBox.currentIndex = start && start.customer >= 0 ? start.customer : 0
        cam1ComboBox.currentIndex = start && start.staff >= 0 ? start.staff : (cameraCount > 1 ? 1 : 0)  // 兩支不同的鏡頭
        micComboBox.currentIndex = start && start.microphone >= 0 ? start.microphone : 0
        rolesConfirmed = false
    }

    function roleName(role) {
        return role === "customer" ? qsTr("customer camera") : role === "staff" ? qsTr("staff camera") : qsTr("microphone")
    }

    // 存的裝置設定對不上時的說明（T41）：哪一個沒接，或認不出哪支是哪支
    function setupNoticeText() {
        if (!finder)
            return ""
        const saved = finder.savedSetup
        const lines = finder.setupMissing.map(role => qsTr("The saved %1, %2, isn't connected.")
                                              .arg(roleName(role)).arg(saved[role] || ""))
        if (lines.length === 0)
            lines.push(qsTr("The connected devices changed since the device setup was saved, so the app can't tell which device is which."))
        lines.push(qsTr("Check the choices below, then confirm."))
        return lines.join(" ")
    }

    // 「Found 2 cameras and 1 microphone.」
    function foundText() {
        const cameras = cameraCount === 1 ? qsTr("1 camera") : qsTr("%1 cameras").arg(cameraCount)
        const microphones = microphoneCount === 1 ? qsTr("1 microphone") : qsTr("%1 microphones").arg(microphoneCount)
        return qsTr("Found %1 and %2.").arg(cameras).arg(microphones)
    }

    // 用鍵盤移到捲出畫面的項目時，捲到看得到（上下各留 Theme.spaceM）
    function ensureVisible(item) {
        const top = item.mapToItem(flick.contentItem, 0, 0).y - Theme.spaceM
        const bottom = top + item.height + 2 * Theme.spaceM
        if (top < flick.contentY)
            flick.contentY = Math.max(0, top)
        else if (bottom > flick.contentY + flick.height)
            flick.contentY = Math.min(flick.contentHeight - flick.height, bottom - flick.height)
    }

    Component.onCompleted: {
        resetSelection()
        if (finder)
            finder.ensureScanned()
    }

    Connections {
        target: recordPage.finder
        function onCamerasChanged() { recordPage.resetSelection() }
        function onMicrophonesChanged() {
            const start = recordPage.finder.startSelection
            micComboBox.currentIndex = start && start.microphone >= 0 ? start.microphone : 0
        }
        function onSetupChanged() { recordPage.resetSelection() }
    }

    // ── 卡片的標題和一句說明
    component CardTitle: ColumnLayout {
        id: cardTitle
        property string title: ""
        property string description: ""
        spacing: 4

        Text {
            text: cardTitle.title
            color: Theme.textTitle
            font.pixelSize: Theme.fontTitleSmall
            font.weight: Font.Bold
            Accessible.role: Accessible.Heading
            Accessible.name: text
        }

        Text {
            Layout.fillWidth: true
            visible: text !== ""
            text: cardTitle.description
            color: Theme.textMuted
            font.pixelSize: Theme.fontCaption
            wrapMode: Text.Wrap
        }
    }

    // ── 一張裝置卡：圖示和名稱、下拉選單（放在 field 裡）、一句「對著誰」；有錯誤時換成欄位錯誤
    component DeviceTile: Rectangle {
        id: tile

        property url iconSource
        property string label: ""
        property string hint: ""
        property string error: ""
        property string errorName: ""           // FieldError 的 objectName（測試找得到）
        property string reservedError: ""       // 錯誤出現時會顯示的句子：先留好高度，版面不跳（DS-27）
        default property alias field: fieldSlot.data

        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.preferredWidth: 260
        implicitHeight: tileColumn.implicitHeight + 2 * 18
        color: Theme.tile
        border.color: Theme.tileBorder
        border.width: 1
        radius: Theme.radiusTile

        ColumnLayout {
            id: tileColumn
            x: 18
            y: 18
            width: parent.width - 2 * 18
            spacing: 12

            RowLayout {
                spacing: 10

                Image {
                    Layout.preferredWidth: Theme.iconM
                    Layout.preferredHeight: Theme.iconM
                    sourceSize: Qt.size(Theme.iconM, Theme.iconM)
                    source: tile.iconSource
                    Accessible.ignored: true
                }

                Text {
                    text: tile.label
                    color: Theme.textTitle
                    font.pixelSize: Theme.fontInput
                    font.weight: Font.DemiBold
                    Accessible.ignored: true    // 名稱在下拉選單的 Accessible.name
                }
            }

            Item {
                id: fieldSlot
                Layout.fillWidth: true
                implicitHeight: Theme.controlHeight
            }

            Item {
                Layout.fillWidth: true
                implicitHeight: Math.max(hintText.implicitHeight, fieldError.implicitHeight,
                                         tile.reservedError !== "" ? reserve.implicitHeight : 0)

                Text {
                    id: hintText
                    width: parent.width
                    visible: tile.error === ""
                    text: tile.hint
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                    wrapMode: Text.Wrap
                }

                FieldError {
                    id: fieldError
                    objectName: tile.errorName
                    width: parent.width
                    visible: tile.error !== ""
                    text: tile.error
                }

                Text {      // 量錯誤那一句排起來有多高；不顯示
                    id: reserve
                    visible: false
                    x: 20
                    width: parent.width - x
                    text: tile.reservedError
                    font.pixelSize: Theme.fontCaption
                    lineHeight: 20
                    lineHeightMode: Text.FixedHeight
                    wrapMode: Text.Wrap
                    Accessible.ignored: true
                }
            }
        }
    }

    Flickable {
        id: flick
        objectName: "recordScroll"
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        bottomMargin: recordPage.toastClearance
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}

        ColumnLayout {
            id: column
            width: flick.width
            spacing: Theme.sectionGap

            // ── Devices
            Card {
                objectName: "devicesCard"
                Layout.fillWidth: true

                ColumnLayout {
                    width: parent.width
                    spacing: Theme.spaceL

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spaceM

                        CardTitle {
                            Layout.fillWidth: true
                            title: qsTr("Devices")
                            description: recordPage.formShown ? recordPage.foundText() : ""
                        }

                        BusyButton {
                            objectName: "rescanButton"
                            Layout.alignment: Qt.AlignTop
                            visible: recordPage.formShown     // 其他狀態的 Scan Again 在卡片裡
                            text: qsTr("Scan Again")
                            iconSource: "../components/icons/rotate-cw_actionSecondaryText.svg"
                            onClicked: recordPage.finder.rescan()
                        }
                    }

                    // 探測中
                    Column {
                        objectName: "scanningState"
                        Layout.fillWidth: true
                        Layout.topMargin: Theme.spaceL
                        Layout.bottomMargin: Theme.spaceL
                        spacing: Theme.spaceS
                        visible: recordPage.status === "idle" || recordPage.status === "scanning"
                        Accessible.role: Accessible.StaticText
                        Accessible.name: scanningText.text

                        Image {
                            id: spinner
                            anchors.horizontalCenter: parent.horizontalCenter
                            width: Theme.iconM
                            height: Theme.iconM
                            sourceSize: Qt.size(Theme.iconM, Theme.iconM)
                            source: "../components/icons/loader-circle_textSecondary.svg"
                            Accessible.ignored: true

                            RotationAnimator on rotation {
                                from: 0
                                to: 360
                                duration: Theme.durSpin
                                loops: Animation.Infinite
                                running: spinner.visible && Theme.durSpin > 0
                            }
                        }

                        Text {
                            id: scanningText
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: qsTr("Looking for cameras and microphones…")
                            color: Theme.textSecondary
                            font.pixelSize: Theme.fontBody
                        }
                    }

                    // 探測失敗（例如音訊系統初始化失敗）
                    InlineError {
                        objectName: "scanError"
                        Layout.fillWidth: true
                        Layout.bottomMargin: Theme.spaceS
                        visible: recordPage.status === "error"
                        title: qsTr("Couldn't check cameras and microphones")
                        detail: qsTr("Check that they're connected, then try again.")
                        technicalDetail: recordPage.finder ? recordPage.finder.errorTechnical : ""
                        retryText: qsTr("Scan Again")
                        onRetry: recordPage.finder.rescan()
                    }

                    // 裝置不夠，錄不了影
                    EmptyState {
                        objectName: "devicesEmpty"
                        Layout.fillWidth: true
                        Layout.bottomMargin: Theme.spaceS
                        visible: recordPage.status === "ready" && !recordPage.enoughDevices
                        // 一支鏡頭都沒有時，分得出是沒有權限、還沒允許，還是沒接（T41）
                        title: recordPage.cameraCount === 0 && recordPage.cameraAccessOff ? qsTr("Camera access is off")
                             : recordPage.cameraCount === 0 ? qsTr("No cameras found")
                             : recordPage.cameraCount === 1 ? qsTr("Only one camera found")
                             : qsTr("No microphone found")
                        description: recordPage.cameraCount === 0 && recordPage.cameraAccessOff
                                     ? qsTr("Turn on Camera for this app in System Settings > Privacy & Security > Camera, then scan again.")
                                   : recordPage.cameraCount === 0 && recordPage.cameraAccess === "undetermined"
                                     ? qsTr("Allow camera access when macOS asks, then scan again.")
                                   : recordPage.cameraCount === 0 ? qsTr("Connect the customer and staff cameras, then scan again.")
                                   : recordPage.cameraCount === 1 ? qsTr("Recording needs a customer camera and a staff camera. Connect another camera, then scan again.")
                                   : qsTr("Connect a microphone, then scan again.")
                        actionText: qsTr("Scan Again")
                        onActionTriggered: recordPage.finder.rescan()
                    }

                    ColumnLayout {
                        objectName: "deviceForm"
                        Layout.fillWidth: true
                        spacing: Theme.spaceL
                        visible: recordPage.formShown

                        // 存的裝置設定對不上（T41）：說明哪裡不同，確認之後才能開錄影視窗
                        Rectangle {
                            objectName: "setupNotice"
                            Layout.fillWidth: true
                            visible: recordPage.needsConfirmation
                            implicitHeight: noticeRow.implicitHeight + 2 * Theme.spaceM
                            radius: Theme.radiusTile
                            color: Theme.warningTint
                            border.color: Theme.tileBorder
                            border.width: 1
                            Accessible.role: Accessible.AlertMessage
                            Accessible.name: noticeTitle.text
                            Accessible.description: noticeText.text

                            RowLayout {
                                id: noticeRow
                                x: Theme.spaceM
                                y: Theme.spaceM
                                width: parent.width - 2 * Theme.spaceM
                                spacing: Theme.spaceM

                                Image {
                                    Layout.alignment: Qt.AlignTop
                                    Layout.preferredWidth: Theme.iconM
                                    Layout.preferredHeight: Theme.iconM
                                    sourceSize: Qt.size(Theme.iconM, Theme.iconM)
                                    source: "../components/icons/triangle-alert_warningOnDark.svg"
                                    Accessible.ignored: true
                                }

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 4

                                    Text {
                                        id: noticeTitle
                                        text: qsTr("Check the devices")
                                        color: Theme.textTitle
                                        font.pixelSize: Theme.fontInput
                                        font.weight: Font.DemiBold
                                        Accessible.ignored: true
                                    }

                                    Text {
                                        id: noticeText
                                        objectName: "setupNoticeText"
                                        Layout.fillWidth: true
                                        text: recordPage.setupNoticeText()
                                        color: Theme.textSecondary
                                        font.pixelSize: Theme.fontCaption
                                        wrapMode: Text.Wrap
                                        Accessible.ignored: true
                                    }
                                }

                                BusyButton {
                                    id: confirmDevicesButton
                                    objectName: "confirmDevicesButton"
                                    Layout.alignment: Qt.AlignVCenter
                                    text: qsTr("Confirm Devices")
                                    Accessible.description: qsTr("Use the cameras and microphone chosen below for this recording.")
                                    onActiveFocusChanged: if (activeFocus) recordPage.ensureVisible(confirmDevicesButton)
                                    onClicked: {
                                        recordPage.rolesConfirmed = true
                                        cam0ComboBox.forceActiveFocus(Qt.TabFocusReason)    // 按鈕不見了，焦點留在卡片裡
                                    }
                                }
                            }
                        }

                        // 一排最多三張，每張至少 260 寬；窄的時候換行（設計稿的 auto-fit）
                        GridLayout {
                            id: deviceGrid
                            Layout.fillWidth: true
                            columns: Math.max(1, Math.min(3, Math.floor((width + columnSpacing) / (260 + columnSpacing))))
                            columnSpacing: Theme.spaceM
                            rowSpacing: Theme.spaceM

                            DeviceTile {
                                iconSource: "../components/icons/video_iconNeutral.svg"
                                label: qsTr("Customer camera")
                                hint: qsTr("Points at the customer.")

                                DeviceSelect {
                                    id: cam0ComboBox
                                    objectName: "customerCamera"
                                    width: parent.width
                                    model: recordPage.finder ? recordPage.finder.cameras : []
                                    onActiveFocusChanged: if (activeFocus) recordPage.ensureVisible(cam0ComboBox)
                                    Accessible.name: qsTr("Customer camera")
                                    Accessible.description: qsTr("Points at the customer.")
                                }
                            }

                            DeviceTile {
                                id: staffTile
                                iconSource: "../components/icons/video_iconNeutral.svg"
                                label: qsTr("Staff camera")
                                hint: qsTr("Points at you.")
                                errorName: "sameCameraError"
                                // 只有兩支鏡頭時「另一支」只有一個選擇；三支以上換一個說法
                                reservedError: recordPage.cameraCount > 2
                                    ? qsTr("This is the customer camera too. Choose a different camera.")
                                    : qsTr("This is the customer camera too. Choose the other camera.")
                                error: recordPage.sameCamera ? reservedError : ""

                                DeviceSelect {
                                    id: cam1ComboBox
                                    objectName: "staffCamera"
                                    width: parent.width
                                    invalid: recordPage.sameCamera
                                    model: recordPage.finder ? recordPage.finder.cameras : []
                                    onActiveFocusChanged: if (activeFocus) recordPage.ensureVisible(cam1ComboBox)
                                    Accessible.name: qsTr("Staff camera")
                                    Accessible.description: staffTile.error !== "" ? staffTile.error : staffTile.hint
                                }
                            }

                            DeviceTile {
                                iconSource: "../components/icons/mic_iconNeutral.svg"
                                label: qsTr("Microphone")
                                hint: qsTr("Records both voices.")

                                DeviceSelect {
                                    id: micComboBox
                                    objectName: "microphone"
                                    width: parent.width
                                    model: recordPage.finder ? recordPage.finder.microphones : []
                                    onActiveFocusChanged: if (activeFocus) recordPage.ensureVisible(micComboBox)
                                    Accessible.name: qsTr("Microphone")
                                    Accessible.description: qsTr("Records both voices.")
                                }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 18

                            BusyButton {
                                id: openRecorderButton
                                objectName: "openRecorderButton"
                                variant: "primary"
                                iconSource: "../components/icons/video_actionPrimaryText.svg"
                                // 錄影中：按下去是把錄影視窗帶回最前面，不會開第二個
                                text: recordPage.recording ? qsTr("Show Recorder") : qsTr("Open Recorder")
                                // 選項不合法、或存的裝置設定還沒確認時停用，原因寫在欄位下方、上面的說明
                                enabled: !recordPage.sameCamera && !recordPage.needsConfirmation
                                onActiveFocusChanged: if (activeFocus) recordPage.ensureVisible(openRecorderButton)
                                onClicked: recordPage.finder.startRecording(cam0ComboBox.currentIndex,
                                                                            cam1ComboBox.currentIndex,
                                                                            micComboBox.currentIndex)
                            }

                            Text {
                                objectName: "openRecorderHint"
                                Layout.fillWidth: true
                                text: recordPage.recording
                                      ? qsTr("Recording is in progress in the recorder window.")
                                      : recordPage.needsConfirmation
                                      ? qsTr("Confirm the devices above to open the recorder.")
                                      : qsTr("The recorder opens in its own window. Recording starts when you press Start Recording there.")
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSmall
                                wrapMode: Text.Wrap
                            }
                        }
                    }
                }
            }

            // ── Saving：背景存檔（UD9、T29）
            Card {
                objectName: "savingCard"
                Layout.fillWidth: true
                visible: recordPage.saves !== null && recordPage.saves.count > 0
                bottomPadding: 12

                ColumnLayout {
                    width: parent.width
                    spacing: 0

                    CardTitle {
                        Layout.fillWidth: true
                        Layout.bottomMargin: Theme.spaceM
                        title: qsTr("Saving")
                        description: qsTr("Recordings upload in the background. You can record the next one now.")
                    }

                    Repeater {
                        objectName: "savesRepeater"
                        model: recordPage.saves

                        delegate: Item {
                            id: row
                            objectName: "saveRow"

                            required property string serviceId
                            required property string status
                            required property int attempt
                            required property int fileNumber
                            required property int fileCount
                            required property real bytesDone
                            required property real bytesTotal
                            required property int percent
                            required property string reason
                            required property bool canRetry
                            required property bool canAnalyze
                            required property bool analyzeRequested
                            required property string savedAt

                            readonly property bool inFlight: status === "finishing" || status === "uploading"
                                                             || status === "saving"
                            readonly property string statusText: {
                                switch (status) {
                                case "finishing":
                                    return qsTr("Finishing the recording…")
                                case "uploading":
                                    return attempt > 1
                                        ? qsTr("Uploading · file %1 of %2 · attempt %3").arg(fileNumber).arg(fileCount).arg(attempt)
                                        : qsTr("Uploading · file %1 of %2").arg(fileNumber).arg(fileCount)
                                case "saving":
                                    return qsTr("Saving to the database…")
                                case "saved":
                                    return analyzeRequested ? qsTr("Saved at %1 · sent for analysis").arg(savedAt)
                                                            : qsTr("Saved at %1 · not analyzed yet").arg(savedAt)
                                }
                                return canRetry ? qsTr("Wasn't saved") : qsTr("Can't be saved")
                            }
                            readonly property string amountText: status === "uploading"
                                                                 ? Theme.transferText(bytesDone, bytesTotal) : ""

                            Layout.fillWidth: true
                            implicitHeight: rowContent.implicitHeight + 2 * Theme.spaceM

                            Accessible.role: Accessible.ListItem
                            Accessible.name: [serviceId, statusText, status === "failed" ? reason : amountText]
                                .filter(part => part !== "").join(". ")

                            Rectangle {     // 每一列上面一條線
                                width: parent.width
                                height: 1
                                color: Theme.cardBorder
                            }

                            RowLayout {
                                id: rowContent
                                y: Theme.spaceM
                                width: parent.width
                                spacing: Theme.spaceM

                                Image {
                                    Layout.alignment: Qt.AlignVCenter
                                    Layout.preferredWidth: Theme.iconNav
                                    Layout.preferredHeight: Theme.iconNav
                                    sourceSize: Qt.size(Theme.iconNav, Theme.iconNav)
                                    source: row.status === "saved" ? "../components/icons/circle-check_successOnDark.svg"
                                          : row.status === "failed" ? "../components/icons/circle-alert_errorOnDark.svg"
                                          : "../components/icons/arrow-up-from-line_iconNeutral.svg"
                                    Accessible.ignored: true
                                }

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Layout.alignment: Qt.AlignVCenter
                                    spacing: row.inFlight ? 8 : 2

                                    Flow {
                                        Layout.fillWidth: true
                                        spacing: 12

                                        Text {
                                            text: row.serviceId
                                            color: Theme.textTitle
                                            font.pixelSize: Theme.fontCaption
                                            font.weight: Font.DemiBold
                                            font.features: { "tnum": 1 }
                                            Accessible.ignored: true
                                        }

                                        Text {
                                            objectName: "saveStatus"
                                            text: row.statusText
                                            color: row.status === "failed" ? Theme.errorOnDark : Theme.textSecondary
                                            font.pixelSize: Theme.fontCaption
                                            font.weight: row.status === "failed" ? Font.DemiBold : Font.Normal
                                            Accessible.ignored: true
                                        }
                                    }

                                    Text {
                                        objectName: "saveReason"
                                        Layout.fillWidth: true
                                        visible: row.status === "failed" && text !== ""
                                        text: row.reason
                                        color: Theme.textSecondary
                                        font.pixelSize: Theme.fontCaption
                                        wrapMode: Text.Wrap
                                        Accessible.ignored: true
                                    }

                                    // 進度條：收尾中是 0、寫資料庫時是滿的；數值直接設定，不做補間（DS-23）
                                    ProgressBar {
                                        id: progress
                                        objectName: "saveProgress"
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 6
                                        visible: row.inFlight
                                        from: 0
                                        to: 1
                                        value: row.bytesTotal > 0 ? Math.min(row.bytesDone / row.bytesTotal, 1) : row.percent / 100
                                        Accessible.name: qsTr("Upload progress for %1").arg(row.serviceId)

                                        background: Rectangle {
                                            implicitHeight: 6
                                            radius: 3
                                            color: Theme.track
                                        }

                                        contentItem: Item {
                                            implicitHeight: 6

                                            Rectangle {
                                                width: parent.width * progress.position
                                                height: parent.height
                                                radius: 3
                                                color: Theme.progressFill
                                            }
                                        }
                                    }
                                }

                                Text {
                                    objectName: "saveAmount"
                                    Layout.alignment: Qt.AlignVCenter
                                    visible: text !== ""
                                    text: row.amountText
                                    color: Theme.textMuted
                                    font.pixelSize: Theme.fontSmall
                                    font.features: { "tnum": 1 }
                                    Accessible.ignored: true
                                }

                                BusyButton {
                                    id: analyzeButton
                                    objectName: "saveAnalyze"
                                    Layout.alignment: Qt.AlignVCenter
                                    visible: row.canAnalyze      // 送去分析之後就拿掉（那一列寫 sent for analysis）
                                    text: qsTr("Analyze")
                                    Accessible.name: qsTr("Analyze %1").arg(row.serviceId)
                                    onActiveFocusChanged: if (activeFocus) recordPage.ensureVisible(analyzeButton)
                                    onClicked: recordPage.saves.analyze(row.serviceId)
                                }

                                BusyButton {
                                    id: retryButton
                                    objectName: "saveRetry"
                                    Layout.alignment: Qt.AlignVCenter
                                    visible: row.canRetry
                                    variant: "primary"
                                    text: qsTr("Retry")
                                    Accessible.name: qsTr("Retry %1").arg(row.serviceId)
                                    onActiveFocusChanged: if (activeFocus) recordPage.ensureVisible(retryButton)
                                    onClicked: recordPage.saves.retry(row.serviceId)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
