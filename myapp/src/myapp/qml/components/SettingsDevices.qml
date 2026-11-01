pragma ComponentBehavior: Bound
import QtQuick
// 下拉選單、按鈕自訂了外觀，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts

// 設定頁的 Devices 區塊（new-feature PLAN 13.5 S4；T41）：設備設定與測試。
// devices 是 deviceFinder.setup（myapp/device_setup.py 的 DeviceSetup）。只有會錄影的服務人員端給它；
// 管理員端是 null，整個區塊不顯示、不佔位置（管理員不因為這裡多出錄影的權限）。
// - 平常是摘要：存的顧客鏡頭、員工鏡頭、麥克風，和 Set Up Devices。存的設定對不上現在的裝置時多一句說明。
// - 按 Set Up Devices 才打開鏡頭和麥克風（PLAN 13.1：測試以操作入口呈現）：重新探測，三個下拉選單、
//   兩個角色的預覽、麥克風的音量。Save 存成這台電腦的設定，錄影頁從這組開始選；Cancel 不存。
//   兩個都會放開鏡頭和麥克風，離開設定頁（這個項目被刪掉）也會。
// - 錄影視窗開著、錄影中、檔案還在寫時不打開：按鈕停用，下面寫原因。
// - 狀態都寫成字（DS-27、DS-34）：探測中、探測失敗、沒有權限、裝置不夠、打不開（別的 app 在用）、沒有畫面、
//   同一支鏡頭選了兩次、存的裝置沒接或認不出來（要重新確認）。
SettingsSection {
    id: section

    property var window: null       // 外殼的視窗（頁面的 setWindow）
    property var devices: null      // DeviceSetup；管理員端是 null

    readonly property bool active: devices ? devices.active : false
    readonly property string status: devices ? devices.status : "idle"
    readonly property int cameraCount: devices ? devices.cameras.length : 0
    readonly property int microphoneCount: devices ? devices.microphones.length : 0
    readonly property bool cameraAccessOff: devices !== null
                                            && (devices.cameraAccess === "denied" || devices.cameraAccess === "restricted")
    readonly property bool enoughDevices: cameraCount >= 2 && microphoneCount >= 1
    readonly property bool formShown: active && status === "ready" && enoughDevices && !cameraAccessOff
    readonly property string blockedReason: devices ? devices.blockedReason : ""
    readonly property string setupState: devices ? devices.setupState : "none"
    readonly property bool hasSaved: devices !== null && devices.savedCustomer !== ""

    function roleName(role) {
        return role === "customer" ? qsTr("customer camera") : role === "staff" ? qsTr("staff camera") : qsTr("microphone")
    }

    function savedName(role) {
        if (!devices)
            return ""
        return role === "customer" ? devices.savedCustomer : role === "staff" ? devices.savedStaff : devices.savedMicrophone
    }

    // 存的設定對不上現在的裝置時的說明（錄影頁的同一種情況說法一樣）
    function mismatchText() {
        if (!devices)
            return ""
        const lines = devices.missingRoles.map(role => qsTr("The saved %1, %2, isn't connected.")
                                               .arg(roleName(role)).arg(savedName(role)))
        if (lines.length === 0)
            lines.push(qsTr("The connected devices changed since the device setup was saved, so the app can't tell which device is which."))
        return lines.join(" ")
    }

    function blockedText() {
        switch (blockedReason) {
        case "recording":
            return qsTr("Device Setup is off while recording. The recorder is using the cameras and microphone.")
        case "recorder":
            return qsTr("Close the recorder window first. It's using the cameras.")
        case "finishing":
            return qsTr("Wait for the last recording to finish writing its files.")
        }
        return ""
    }

    function previewText(previewState) {
        switch (previewState) {
        case "starting":
            return qsTr("Starting the camera…")
        case "noPicture":
            return qsTr("No picture from this camera. Check the cable, or choose another camera.")
        case "unavailable":
            return qsTr("Can't open this camera. Another app may be using it, or it was unplugged.")
        case "denied":
            return qsTr("Camera access is off.")
        case "same":
            return qsTr("Choose a different camera to see the staff preview.")
        case "live":
            return ""
        }
        return qsTr("No camera chosen.")
    }

    function microphoneText(micState) {
        switch (micState) {
        case "starting":
            return qsTr("Starting the microphone…")
        case "live":
            return qsTr("Speak to check the level. Nothing is recorded.")
        case "noSignal":
            return qsTr("No sound from this microphone. Check the cable, or choose another microphone.")
        case "unavailable":
            return qsTr("Can't open this microphone. Another app may be using it, or it was unplugged.")
        case "denied":
            return qsTr("Microphone access is off. Turn on Microphone for this app in System Settings > Privacy & Security > Microphone.")
        }
        return qsTr("No microphone chosen.")
    }

    function openSetup() {
        if (devices && devices.openSetup(section)) {
            cancelButton.forceActiveFocus(Qt.OtherFocusReason)     // Set Up Devices 不見了：焦點移到面板裡
            revealTimer.restart()
        }
    }

    // 打開設備設定、預覽出現時，整頁捲到這個區塊：預覽在卡片的下半部，視窗不高時一打開看不到
    function revealSection() {
        let flick = section.parent
        while (flick && flick.contentY === undefined)
            flick = flick.parent
        if (!flick)
            return
        const top = section.mapToItem(flick.contentItem, 0, 0).y - Theme.spaceM
        flick.contentY = Math.max(0, Math.min(top, flick.contentHeight - flick.height))
    }

    onFormShownChanged: if (formShown) revealTimer.restart()

    Timer {     // 等面板長高、整頁的高度重新算好（下一個畫格）之後才捲
        id: revealTimer
        interval: 50
        onTriggered: section.revealSection()
    }

    function closeSetup() {
        if (devices)
            devices.closeSetup()
        setUpButton.forceActiveFocus(Qt.OtherFocusReason)
    }

    function save() {
        if (devices && devices.save())
            setUpButton.forceActiveFocus(Qt.OtherFocusReason)
    }

    visible: devices !== null
    title: qsTr("Devices")

    Component.onDestruction: {
        if (devices)
            devices.closeSetup()    // 離開設定頁：放開鏡頭和麥克風
    }

    // ── 一行說明：圖示和字（警告、成功、原因）
    component Note: RowLayout {
        id: note
        property url iconSource
        property string text: ""
        property color textColor: Theme.textSecondary

        spacing: Theme.spaceS
        visible: text !== ""
        Accessible.role: Accessible.StaticText
        Accessible.name: text

        Image {
            Layout.alignment: Qt.AlignTop
            Layout.topMargin: 1
            Layout.preferredWidth: Theme.iconS
            Layout.preferredHeight: Theme.iconS
            sourceSize: Qt.size(Theme.iconS, Theme.iconS)
            source: note.iconSource
            Accessible.ignored: true
        }

        Text {
            Layout.fillWidth: true
            text: note.text
            color: note.textColor
            font.pixelSize: Theme.fontCaption
            lineHeight: 18
            lineHeightMode: Text.FixedHeight
            wrapMode: Text.Wrap
            Accessible.ignored: true
        }
    }

    // ── 一個鏡頭角色：名稱、下拉選單、一句「對著誰」（同一支鏡頭時換成欄位錯誤）、4:3 的預覽
    component CameraTile: Rectangle {
        id: tile

        property string label: ""
        property string hint: ""
        property string error: ""
        property string errorName: ""
        property string previewState: "off"
        property string frame: ""
        property string previewName: ""     // 預覽給輔助技術的名字
        default property alias field: fieldSlot.data

        readonly property bool showsPicture: previewState === "live" && frame !== ""
        readonly property string message: previewState === "live" && frame === "" ? section.previewText("starting")
                                                                                   : section.previewText(previewState)

        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.preferredWidth: 260
        implicitHeight: tileColumn.implicitHeight + 2 * Theme.spaceM
        color: Theme.tile
        border.color: Theme.tileBorder
        border.width: 1
        radius: Theme.radiusTile

        ColumnLayout {
            id: tileColumn
            x: Theme.spaceM
            y: Theme.spaceM
            width: parent.width - 2 * Theme.spaceM
            spacing: 10

            RowLayout {
                spacing: 10

                Image {
                    Layout.preferredWidth: Theme.iconM
                    Layout.preferredHeight: Theme.iconM
                    sourceSize: Qt.size(Theme.iconM, Theme.iconM)
                    source: "icons/video_iconNeutral.svg"
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

            Item {      // 一句「對著誰」；同一支鏡頭時換成欄位錯誤，先留好兩行的高度，版面不跳（DS-27）
                Layout.fillWidth: true
                implicitHeight: 2 * 20

                Text {
                    width: parent.width
                    visible: tile.error === ""
                    text: tile.hint
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                    wrapMode: Text.Wrap
                }

                FieldError {
                    objectName: tile.errorName
                    width: parent.width
                    visible: tile.error !== ""
                    text: tile.error
                }
            }

            Rectangle {     // 預覽：黑底、4:3，畫面照比例放進去
                objectName: "preview"
                Layout.fillWidth: true
                Layout.preferredHeight: Math.round(width * 3 / 4)
                radius: 12
                color: Theme.contentBg
                border.color: Theme.cardBorder
                border.width: 1
                clip: true
                Accessible.role: Accessible.Graphic
                Accessible.name: tile.previewName
                Accessible.description: tile.message

                Image {
                    objectName: "previewImage"
                    anchors.fill: parent
                    anchors.margins: 1
                    visible: tile.showsPicture
                    source: tile.showsPicture ? tile.frame : ""
                    cache: false
                    asynchronous: false
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                    Accessible.ignored: true
                }

                Column {
                    anchors.centerIn: parent
                    width: parent.width - 2 * Theme.spaceL
                    spacing: Theme.spaceS
                    visible: !tile.showsPicture

                    Image {
                        id: previewIcon
                        anchors.horizontalCenter: parent.horizontalCenter
                        width: Theme.iconM
                        height: Theme.iconM
                        sourceSize: Qt.size(Theme.iconM, Theme.iconM)
                        source: tile.previewState === "starting" || (tile.previewState === "live" && tile.frame === "")
                                ? "icons/loader-circle_textSecondary.svg" : "icons/video-off_iconNeutral.svg"
                        Accessible.ignored: true

                        RotationAnimator on rotation {
                            from: 0
                            to: 360
                            duration: Theme.durSpin
                            loops: Animation.Infinite
                            running: previewIcon.visible && Theme.durSpin > 0
                                     && previewIcon.source.toString().indexOf("loader-circle") >= 0
                        }
                    }

                    Text {
                        objectName: "previewMessage"
                        width: parent.width
                        text: tile.message
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontCaption
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap
                        Accessible.ignored: true
                    }
                }
            }
        }
    }

    // ── 麥克風的音量：12 格，由矮到高，亮的格數就是音量（和錄影視窗的 LevelMeter 一樣）
    component LevelMeter: Row {
        id: meter
        property real level: 0
        readonly property int lit: Math.round(Math.max(0, Math.min(1, level)) * 12)

        spacing: 4
        Accessible.role: Accessible.ProgressBar
        Accessible.name: qsTr("Microphone level")
        Accessible.description: qsTr("%1 percent").arg(Math.round(level * 100))

        Repeater {
            model: 12

            Rectangle {
                required property int index
                anchors.bottom: parent ? parent.bottom : undefined
                width: 8
                height: 10 + index * 2
                radius: 2
                color: index < meter.lit ? Theme.progressFill : Theme.actionSecondaryBorder
            }
        }
    }

    // ── 平常：存的設定的摘要、Set Up Devices
    SettingsGroup {
        id: summaryGroup
        objectName: "deviceSummary"
        visible: !section.active
        title: qsTr("Device Setup")
        hint: section.hasSaved
              ? qsTr("Saved on this computer for everyone who records here. The Record page starts with these devices, and you can still change them there for one recording.")
              : qsTr("Choose which camera points at the customer and which points at you, then check both previews. The Record page starts with the devices you save.")

        Column {
            width: summaryGroup.width
            spacing: Theme.spaceM

            Column {
                objectName: "savedDevices"
                width: parent.width
                spacing: 6
                visible: section.hasSaved

                Repeater {
                    model: ["customer", "staff", "microphone"]

                    RowLayout {
                        id: savedRow
                        required property string modelData
                        width: parent ? parent.width : 0
                        spacing: Theme.spaceM
                        Accessible.role: Accessible.StaticText
                        Accessible.name: savedLabel.text + ": " + savedValue.text

                        Text {
                            id: savedLabel
                            Layout.preferredWidth: 150
                            text: savedRow.modelData === "customer" ? qsTr("Customer camera")
                                : savedRow.modelData === "staff" ? qsTr("Staff camera") : qsTr("Microphone")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontCaption
                            Accessible.ignored: true
                        }

                        Text {
                            id: savedValue
                            Layout.fillWidth: true
                            text: section.savedName(savedRow.modelData)
                            color: Theme.textPrimary
                            font.pixelSize: Theme.fontCaption
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                            Accessible.ignored: true
                        }
                    }
                }
            }

            Note {
                objectName: "savedNotice"
                width: parent.width
                iconSource: "icons/circle-check_successOnDark.svg"
                text: section.devices && section.devices.justSaved
                      ? qsTr("Saved. The Record page starts with these devices.") : ""
            }

            Note {
                objectName: "summaryMismatch"
                width: parent.width
                iconSource: "icons/triangle-alert_warningOnDark.svg"
                text: section.setupState === "changed" || section.setupState === "missing"
                      ? section.mismatchText() + " " + qsTr("Set up the devices again.") : ""
            }

            BusyButton {
                id: setUpButton
                objectName: "setUpDevicesButton"
                variant: section.hasSaved ? "secondary" : "primary"
                text: qsTr("Set Up Devices")
                enabled: section.blockedReason === ""
                Accessible.description: section.blockedReason !== "" ? section.blockedText() : ""
                onClicked: section.openSetup()
            }

            Note {
                objectName: "blockedReason"
                width: parent.width
                iconSource: "icons/video-off_iconNeutral.svg"
                text: section.blockedText()
            }
        }
    }

    // ── Set Up Devices 打開時：探測、選擇、預覽、音量、Save／Cancel
    Column {
        id: setupPanel
        objectName: "deviceSetupPanel"
        width: parent ? parent.width : 0
        visible: section.active
        topPadding: Theme.spaceL
        bottomPadding: Theme.spaceL
        spacing: Theme.spaceL
        Accessible.role: Accessible.Grouping
        Accessible.name: panelTitle.text

        RowLayout {
            width: parent.width
            spacing: Theme.spaceM

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4

                Text {
                    id: panelTitle
                    text: qsTr("Device Setup")
                    color: Theme.textTitle
                    font.pixelSize: Theme.fontBodyLarge
                    font.weight: Font.Bold
                    Accessible.role: Accessible.Heading
                    Accessible.name: text
                }

                Text {
                    Layout.fillWidth: true
                    text: qsTr("Check that each preview shows the right person and speak to test the microphone. Nothing is recorded or uploaded.")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                    wrapMode: Text.Wrap
                }
            }

            BusyButton {
                objectName: "setupRescanButton"
                Layout.alignment: Qt.AlignTop
                visible: section.formShown     // 其他狀態（失敗、沒有權限、裝置不夠）的 Scan Again 在說明裡
                text: qsTr("Scan Again")
                iconSource: "icons/rotate-cw_actionSecondaryText.svg"
                onClicked: section.devices.rescan()
            }
        }

        // 探測中
        Column {
            objectName: "setupScanning"
            width: parent.width
            topPadding: Theme.spaceL
            bottomPadding: Theme.spaceL
            spacing: Theme.spaceS
            visible: section.status === "idle" || section.status === "scanning"
            Accessible.role: Accessible.StaticText
            Accessible.name: scanningText.text

            Image {
                id: spinner
                anchors.horizontalCenter: parent.horizontalCenter
                width: Theme.iconM
                height: Theme.iconM
                sourceSize: Qt.size(Theme.iconM, Theme.iconM)
                source: "icons/loader-circle_textSecondary.svg"
                Accessible.ignored: true

                RotationAnimator on rotation {
                    from: 0
                    to: 360
                    duration: Theme.durSpin
                    loops: Animation.Infinite
                    running: spinner.visible && section.active && Theme.durSpin > 0
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

        // 探測失敗
        InlineError {
            objectName: "setupScanError"
            width: parent.width
            visible: section.status === "error"
            title: qsTr("Couldn't check cameras and microphones")
            detail: qsTr("Check that they're connected, then try again.")
            technicalDetail: section.devices ? section.devices.errorTechnical : ""
            retryText: qsTr("Scan Again")
            onRetry: section.devices.rescan()
        }

        // 沒有相機權限
        EmptyState {
            objectName: "setupAccessOff"
            width: parent.width
            visible: section.status === "ready" && section.cameraAccessOff
            iconSource: Qt.resolvedUrl("icons/video-off_iconNeutral.svg")
            title: qsTr("Camera access is off")
            description: qsTr("Turn on Camera for this app in System Settings > Privacy & Security > Camera, then scan again.")
            actionText: qsTr("Scan Again")
            onActionTriggered: section.devices.rescan()
        }

        // 裝置不夠
        EmptyState {
            objectName: "setupNotEnough"
            width: parent.width
            visible: section.status === "ready" && !section.cameraAccessOff && !section.enoughDevices
            iconSource: Qt.resolvedUrl(section.cameraCount < 2 ? "icons/video-off_iconNeutral.svg" : "icons/mic-off_iconNeutral.svg")
            title: section.cameraCount === 0 ? qsTr("No cameras found")
                 : section.cameraCount === 1 ? qsTr("Only one camera found")
                 : qsTr("No microphone found")
            description: section.cameraCount === 0 && section.devices && section.devices.cameraAccess === "undetermined"
                         ? qsTr("Allow camera access when macOS asks, then scan again.")
                       : section.cameraCount < 2
                         ? qsTr("Recording needs a customer camera and a staff camera. Connect them, then scan again.")
                       : qsTr("Connect a microphone, then scan again.")
            actionText: qsTr("Scan Again")
            onActionTriggered: section.devices.rescan()
        }

        // 打不開的鏡頭（列得出來，但別的 app 正在用，或沒有回應）
        Note {
            objectName: "unavailableCameras"
            width: parent.width
            visible: section.active && section.status === "ready" && !section.cameraAccessOff && text !== ""
            iconSource: "icons/triangle-alert_warningOnDark.svg"
            text: section.devices && section.devices.unavailableCameras.length > 0
                  ? qsTr("Not available: %1. Another app may be using it, or it isn't responding.")
                    .arg(section.devices.unavailableCameras.join(", "))
                  : ""
        }

        // 存的設定對不上現在的裝置：要重新確認
        Note {
            objectName: "setupMismatch"
            width: parent.width
            visible: section.formShown && text !== ""
            iconSource: "icons/triangle-alert_warningOnDark.svg"
            text: section.setupState === "changed" || section.setupState === "missing"
                  ? section.mismatchText() + " " + qsTr("Check each preview, then save again.") : ""
        }

        GridLayout {
            id: cameraGrid
            objectName: "cameraTiles"
            width: parent.width
            visible: section.formShown
            columns: width >= 2 * 260 + Theme.spaceM ? 2 : 1
            columnSpacing: Theme.spaceM
            rowSpacing: Theme.spaceM

            CameraTile {
                objectName: "customerTile"
                label: qsTr("Customer camera")
                hint: qsTr("Points at the customer.")
                previewState: section.devices ? section.devices.customerPreview : "off"
                frame: section.devices ? section.devices.customerFrame : ""
                previewName: qsTr("Live preview from the customer camera")

                DeviceSelect {
                    id: customerSelect
                    objectName: "setupCustomerCamera"
                    width: parent.width
                    model: section.devices ? section.devices.cameras : []
                    currentIndex: section.devices ? section.devices.customerCamera : -1
                    onActivated: (index) => section.devices.selectCustomerCamera(index)
                    Accessible.name: qsTr("Customer camera")
                    Accessible.description: qsTr("Points at the customer.")
                }
            }

            CameraTile {
                id: staffTile
                objectName: "staffTile"
                label: qsTr("Staff camera")
                hint: qsTr("Points at you.")
                errorName: "setupSameCameraError"
                error: section.devices && section.devices.sameCamera
                       ? qsTr("This is the customer camera too. Choose a different camera.") : ""
                previewState: section.devices ? section.devices.staffPreview : "off"
                frame: section.devices ? section.devices.staffFrame : ""
                previewName: qsTr("Live preview from the staff camera")

                DeviceSelect {
                    id: staffSelect
                    objectName: "setupStaffCamera"
                    width: parent.width
                    invalid: section.devices ? section.devices.sameCamera : false
                    model: section.devices ? section.devices.cameras : []
                    currentIndex: section.devices ? section.devices.staffCamera : -1
                    onActivated: (index) => section.devices.selectStaffCamera(index)
                    Accessible.name: qsTr("Staff camera")
                    Accessible.description: staffTile.error !== "" ? staffTile.error : qsTr("Points at you.")
                }
            }
        }

        Rectangle {     // 麥克風：下拉選單、音量、一句說明
            objectName: "microphoneTile"
            width: parent.width
            visible: section.formShown
            implicitHeight: micColumn.implicitHeight + 2 * Theme.spaceM
            color: Theme.tile
            border.color: Theme.tileBorder
            border.width: 1
            radius: Theme.radiusTile

            ColumnLayout {
                id: micColumn
                x: Theme.spaceM
                y: Theme.spaceM
                width: parent.width - 2 * Theme.spaceM
                spacing: 10

                RowLayout {
                    spacing: 10

                    Image {
                        Layout.preferredWidth: Theme.iconM
                        Layout.preferredHeight: Theme.iconM
                        sourceSize: Qt.size(Theme.iconM, Theme.iconM)
                        source: "icons/mic_iconNeutral.svg"
                        Accessible.ignored: true
                    }

                    Text {
                        text: qsTr("Microphone")
                        color: Theme.textTitle
                        font.pixelSize: Theme.fontInput
                        font.weight: Font.DemiBold
                        Accessible.ignored: true
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spaceL

                    DeviceSelect {
                        id: microphoneSelect
                        objectName: "setupMicrophone"
                        Layout.fillWidth: true
                        Layout.maximumWidth: 360
                        model: section.devices ? section.devices.microphones : []
                        currentIndex: section.devices ? section.devices.microphone : -1
                        onActivated: (index) => section.devices.selectMicrophone(index)
                        Accessible.name: qsTr("Microphone")
                        Accessible.description: qsTr("Records both voices.")
                    }

                    LevelMeter {
                        objectName: "microphoneLevel"
                        Layout.alignment: Qt.AlignVCenter
                        level: section.devices ? section.devices.microphoneLevel : 0
                    }

                    Item { Layout.fillWidth: true }
                }

                Text {
                    objectName: "microphoneMessage"
                    Layout.fillWidth: true
                    text: section.microphoneText(section.devices ? section.devices.microphoneState : "off")
                    color: section.devices && (section.devices.microphoneState === "unavailable"
                                               || section.devices.microphoneState === "noSignal"
                                               || section.devices.microphoneState === "denied")
                           ? Theme.warningOnDark : Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                    wrapMode: Text.Wrap
                }
            }
        }

        Note {
            objectName: "saveFailed"
            width: parent.width
            iconSource: "icons/circle-alert_errorOnDark.svg"
            textColor: Theme.errorOnDark
            text: section.devices && section.devices.saveFailed
                  ? qsTr("Couldn't save the device setup on this computer. Try again.") : ""
        }

        RowLayout {
            width: parent.width
            spacing: Theme.spaceS

            BusyButton {
                objectName: "saveDevicesButton"
                variant: "primary"
                visible: section.formShown
                text: qsTr("Save")
                enabled: section.devices ? section.devices.canSave : false
                Accessible.description: qsTr("Save these devices for recording on this computer.")
                onClicked: section.save()
            }

            BusyButton {
                id: cancelButton
                objectName: "cancelDevicesButton"
                text: qsTr("Cancel")
                Accessible.description: qsTr("Close Device Setup without saving. The cameras and microphone turn off.")
                onClicked: section.closeSetup()
            }

            Item { Layout.fillWidth: true }
        }
    }
}
