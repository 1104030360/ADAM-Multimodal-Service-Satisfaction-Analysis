pragma ComponentBehavior: Bound
import QtQuick
// 自訂了按鈕、分頁、滑桿的 background／contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Shapes
import QtMultimedia
import "../components"

// 服務明細（兩端共用，UX-46）：server_service_detail_page.qml、admin_service_detail_page.qml 只決定 Back 回到哪一頁。
// - 按 Details 立刻換到這頁（UX-22、ENG-04）：mediaModel 在背景查檔案清單、一個一個檔案下載，
//   每個檔案在自己的位置顯示進度；一個檔案失敗只影響它自己，原地 Retry（UX-23、DS-27）。
// - 離開這一頁（Back、側邊欄）時取消還沒下載完的檔案，已經下載好的留在快取（T21）。
// - 三個檔案都結束才能播放（要同步播）；時間用等寬數字；強調色是 Theme 的石墨灰，不是 Material 的粉紅（UX-25）。
// - 圖表有幾張沒載入時，上方的警告列說明並可以重試（DS-52）；圖片非同步解碼，照顯示大小解碼（ENG-19）。
// - 兩個分頁用 StackLayout：切到圖表再切回來，影片不會從頭開始（UX-25）。
// - 等待不到 300 ms 不顯示文字，只有靜止的灰色區塊（DS-25）。
Item {
    id: detail
    anchors.fill: parent

    property string backPage: ""
    readonly property var media: typeof mediaModel !== "undefined" ? mediaModel : null
    readonly property var charts: typeof chartsModel !== "undefined" ? chartsModel : null
    readonly property string pageState: media ? media.state : "idle"
    readonly property bool canPlay: media !== null && media.canPlay
    property int chartIndex: 0

    // Lucide 圖示（ISC，見 images/SOURCES.md）的路徑，24×24；顏色從 Theme 來，不另外存成檔案
    readonly property string iconChevronLeft: "M15 18l-6-6 6-6"
    readonly property string iconDownload: "M12 15V3M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5"
    readonly property string iconClock: "M22 12A10 10 0 1 1 2 12A10 10 0 1 1 22 12M12 6v6l4 2"
    readonly property string iconTriangleAlert:
        "M21.73 18l-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3M12 9v4M12 17h.01"

    // Loader 換頁時，這一頁的 context 在換頁的當下就被清掉，onDestruction 也在那時執行（T21）
    Component.onDestruction: {
        if (detail.media)
            detail.media.close()
    }

    function goBack() {
        mainContentLoader.source = detail.backPage
    }

    // 「customer video」這類說法，用在等待、下載中、沒有這個檔案的文字裡
    function noun(kind) {
        switch (kind) {
        case "cam0": return qsTr("customer video")
        case "cam1": return qsTr("staff video")
        case "audio": return qsTr("audio")
        case "chart": return qsTr("charts")
        }
        return ""
    }

    function queuedText(kind) {
        return kind === "" ? "" : qsTr("Queued after the %1").arg(detail.noun(kind))
    }

    function clock(ms) {
        const total = Math.max(0, Math.floor(ms / 1000))
        const hours = Math.floor(total / 3600)
        const minutes = Math.floor(total / 60) % 60
        const seconds = total % 60
        const mmss = String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0")
        return hours > 0 ? hours + ":" + mmss : mmss
    }

    // 有東西沒載入時，說明能播的是哪幾個（設計稿：「Staff video and audio are ready to play.」）
    function playHint() {
        if (!detail.media || detail.media.state !== "ready")
            return ""
        if (detail.media.pending)
            return qsTr("You can play the videos after they download.")
        if (!detail.canPlay)
            return qsTr("There's nothing to play for this service.")
        const names = [[detail.media.cam0, qsTr("Customer video"), qsTr("customer video")],
                       [detail.media.cam1, qsTr("Staff video"), qsTr("staff video")],
                       [detail.media.audio, qsTr("Audio"), qsTr("audio")]]
        const ready = names.filter(entry => entry[0].state === "ready")
        if (ready.length === names.length)
            return ""
        if (ready.length === 1)
            return qsTr("%1 is ready to play.").arg(ready[0][1])
        return qsTr("%1 and %2 are ready to play.").arg(ready[0][1]).arg(ready[1][2])
    }

    // ── 小元件 ──────────────────────────────────────────────────────────────

    // 線條圖示：Lucide 的路徑畫在 24×24 裡，縮放到 size
    component PathIcon: Item {
        id: icon
        property string path: ""
        property color color: Theme.textSecondary
        property real size: Theme.iconM
        implicitWidth: size
        implicitHeight: size
        Accessible.ignored: true

        Shape {
            width: 24
            height: 24
            scale: icon.size / 24
            transformOrigin: Item.TopLeft
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                strokeColor: icon.color
                strokeWidth: 2
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap
                joinStyle: ShapePath.RoundJoin
                PathSvg { path: icon.path }
            }
        }
    }

    // active 持續超過 busyDelay 才成立（DS-25：300 ms 內什麼都不顯示，免得閃一下）
    component Wait: Timer {
        property bool active: false
        property bool elapsed: false
        interval: Theme.busyDelay
        running: active && !elapsed
        onTriggered: elapsed = true
        onActiveChanged: if (!active) elapsed = false
    }

    component BackButton: Button {
        id: back
        focusPolicy: Qt.StrongFocus
        implicitHeight: 36
        leftPadding: Theme.spaceS - 2
        rightPadding: Theme.spaceS + 2
        topPadding: 0
        bottomPadding: 0
        font.pixelSize: Theme.fontBody
        font.weight: Font.DemiBold
        Accessible.name: text

        contentItem: Row {
            spacing: 4
            PathIcon {
                anchors.verticalCenter: parent.verticalCenter
                size: 18
                color: Theme.actionSecondaryText
                path: detail.iconChevronLeft
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: back.text
                font: back.font
                color: Theme.actionSecondaryText
            }
        }

        background: Rectangle {
            radius: Theme.radiusS
            color: back.down ? Theme.surfaceMuted : back.hovered ? Theme.surfaceAlt : Theme.actionSecondaryBg
            border.color: Theme.actionSecondaryBorder
            border.width: 1

            Rectangle {     // 鍵盤焦點框（DS-30）
                visible: back.visualFocus
                anchors.fill: parent
                anchors.margins: -4
                radius: parent.radius + 4
                color: "transparent"
                border.color: Theme.focusOnLight
                border.width: 2
            }
        }
    }

    component PageTab: TabButton {
        id: tab
        focusPolicy: Qt.StrongFocus
        implicitHeight: 48
        font.pixelSize: Theme.fontBody
        font.weight: checked ? Font.DemiBold : Font.Normal

        contentItem: Text {
            text: tab.text
            font: tab.font
            color: tab.checked ? Theme.textPrimary : Theme.textSecondary
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }

        background: Item {
            Rectangle {     // 選取的分頁：2 px 石墨灰底線（設計稿）
                visible: tab.checked
                anchors.bottom: parent.bottom
                width: parent.width
                height: 2
                color: Theme.segmentSelected
            }
            Rectangle {     // 鍵盤焦點框（DS-30）
                visible: tab.visualFocus
                anchors.fill: parent
                anchors.margins: 2
                radius: Theme.radiusS
                color: "transparent"
                border.color: Theme.focusOnLight
                border.width: 2
            }
        }
    }

    // 石墨灰的滑桿（UX-25：以前是 Material 預設的粉紅）
    component GraphiteSlider: Slider {
        id: slider
        focusPolicy: Qt.StrongFocus

        background: Rectangle {
            x: slider.leftPadding
            y: slider.topPadding + slider.availableHeight / 2 - height / 2
            implicitWidth: 200
            implicitHeight: 4
            width: slider.availableWidth
            height: implicitHeight
            radius: 2
            color: Theme.track

            Rectangle {
                width: slider.visualPosition * parent.width
                height: parent.height
                radius: 2
                color: slider.enabled ? Theme.actionPrimary : Theme.textDisabled
            }
        }

        handle: Rectangle {
            x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
            y: slider.topPadding + slider.availableHeight / 2 - height / 2
            implicitWidth: 16
            implicitHeight: 16
            radius: 8
            color: !slider.enabled ? Theme.textDisabled : slider.pressed ? Theme.actionPrimaryPressed : Theme.actionPrimary

            Rectangle {     // 鍵盤焦點框（DS-30）
                visible: slider.visualFocus
                anchors.fill: parent
                anchors.margins: -4
                radius: width / 2
                color: "transparent"
                border.color: Theme.focusOnLight
                border.width: 2
            }
        }
    }

    // 一支影片的位置：500×300。好了顯示影片；其他狀態各有說明（設計稿「檢視服務」的 4 種狀態）
    component VideoBox: Column {
        id: box
        property var slot: null                 // FileSlot
        property string title: ""
        property string accessibleName: title   // 輔助技術唸的名稱，例如「Customer video」（標題只寫 Customer）
        property string failedTitle: qsTr("Video failed to load")
        readonly property string slotState: slot ? slot.state : "idle"
        property alias output: videoOutput

        spacing: Theme.spaceXS
        Accessible.role: Accessible.Grouping
        Accessible.name: accessibleName

        Wait { id: loadingWait; active: box.slotState === "waiting" || box.slotState === "downloading" }

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: box.title
            color: Theme.textPrimary
            font.pixelSize: Theme.fontTitleSmall
        }

        Rectangle {
            objectName: "videoFrame"
            width: 500
            height: Math.max(300, videoError.visible ? videoError.implicitHeight + 2 * Theme.spaceM : 0)
            radius: Theme.radiusS
            color: box.slotState === "ready" ? Theme.chromeField
                 : box.slotState === "error" ? Theme.contentBg : Theme.surfaceMuted
            border.width: box.slotState === "error" ? 1 : 0
            border.color: Theme.divider
            clip: true

            VideoOutput {
                id: videoOutput
                anchors.fill: parent
                visible: box.slotState === "ready"
            }

            Column {
                objectName: "waitingState"
                anchors.centerIn: parent
                width: parent.width - 2 * Theme.spaceL
                spacing: 8
                visible: box.slotState === "waiting" && loadingWait.elapsed

                PathIcon {
                    anchors.horizontalCenter: parent.horizontalCenter
                    size: 24
                    path: detail.iconClock
                }
                Text {
                    width: parent.width
                    text: qsTr("Waiting to download")
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontBody
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                }
                Text {
                    width: parent.width
                    visible: text !== ""
                    text: box.slot ? detail.queuedText(box.slot.waitingFor) : ""
                    color: Theme.textSecondary
                    font.pixelSize: Theme.fontCaption
                    horizontalAlignment: Text.AlignHCenter
                }
            }

            Column {
                objectName: "downloadingState"
                anchors.centerIn: parent
                spacing: 8
                visible: box.slotState === "downloading" && loadingWait.elapsed

                PathIcon {
                    anchors.horizontalCenter: parent.horizontalCenter
                    size: 24
                    path: detail.iconDownload
                }
                TransferProgress {
                    objectName: "progress"
                    width: 360
                    done: box.slot ? box.slot.done : 0
                    total: box.slot ? box.slot.total : 0
                    label: qsTr("Downloading %1").arg(box.slot ? detail.noun(box.slot.kind) : "")
                }
            }

            EmptyState {
                objectName: "missingState"
                anchors.centerIn: parent
                width: parent.width - 2 * Theme.spaceL
                visible: box.slotState === "missing"
                title: qsTr("No %1").arg(box.slot ? detail.noun(box.slot.kind) : "")
                description: qsTr("This service doesn't have one.")
            }

            InlineError {
                id: videoError
                objectName: "errorState"
                anchors.centerIn: parent
                width: parent.width - 2 * Theme.spaceL
                visible: box.slotState === "error"
                title: box.failedTitle
                detail: box.slot ? box.slot.errorDetail : ""
                technicalDetail: box.slot ? box.slot.errorTechnical : ""
                failureCount: box.slot ? box.slot.failureCount : 0
                onRetry: {
                    if (detail.media && box.slot)
                        detail.media.retry(box.slot.kind)
                }
            }
        }
    }

    // ── 版面 ────────────────────────────────────────────────────────────────

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // 頁首：Back、服務 ID、開始時間（換頁的當下就有，不必等下載，UX-22）
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 56
            Layout.leftMargin: Theme.spaceL
            Layout.rightMargin: Theme.spaceL
            spacing: Theme.spaceM

            BackButton {
                objectName: "backButton"
                text: qsTr("Back")
                onClicked: detail.goBack()
            }
            Text {
                objectName: "serviceIdText"
                text: detail.media ? detail.media.serviceId : ""
                color: Theme.textPrimary
                font.pixelSize: Theme.fontTitleSmall
                font.weight: Font.Bold
                font.features: { "tnum": 1 }
            }
            Text {
                objectName: "startTimeText"
                text: detail.media ? detail.media.startTime : ""
                color: Theme.textSecondary
                font.pixelSize: Theme.fontCaption
                font.features: { "tnum": 1 }
            }
            Item { Layout.fillWidth: true }
        }

        TabBar {
            id: tabBar
            objectName: "tabBar"
            Layout.fillWidth: true
            background: Rectangle {
                color: Theme.contentBg
                Rectangle {
                    anchors.bottom: parent.bottom
                    width: parent.width
                    height: 1
                    color: Theme.divider
                }
            }

            PageTab { objectName: "videoTab"; text: qsTr("Video Playback") }
            PageTab { objectName: "chartsTab"; text: qsTr("Charts") }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            StackLayout {
                id: tabs
                anchors.fill: parent
                currentIndex: tabBar.currentIndex
                visible: detail.pageState === "loading" || detail.pageState === "ready"

                // ── 影片和錄音
                Flickable {
                    id: videoPage
                    clip: true
                    contentWidth: width
                    contentHeight: videoColumn.implicitHeight + 2 * Theme.spaceL
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {}

                    Column {
                        id: videoColumn
                        y: Theme.spaceL
                        anchors.horizontalCenter: parent.horizontalCenter
                        spacing: Theme.spaceL

                        Row {
                            anchors.horizontalCenter: parent.horizontalCenter
                            spacing: Theme.spaceL

                            VideoBox {
                                id: cam0Box
                                objectName: "cam0Box"
                                title: qsTr("Customer")
                                accessibleName: qsTr("Customer video")
                                slot: detail.media ? detail.media.cam0 : null
                            }
                            VideoBox {
                                id: cam1Box
                                objectName: "cam1Box"
                                title: qsTr("Staff")
                                accessibleName: qsTr("Staff video")
                                slot: detail.media ? detail.media.cam1 : null
                            }
                        }

                        // 錄音：沒有畫面，一條細的位置；失敗時換成區塊錯誤
                        Rectangle {
                            id: audioBox
                            objectName: "audioBox"
                            readonly property var slot: detail.media ? detail.media.audio : null
                            readonly property string slotState: slot ? slot.state : "idle"
                            anchors.horizontalCenter: parent.horizontalCenter
                            width: 2 * 500 + Theme.spaceL
                            height: audioError.visible ? audioError.implicitHeight + 2 * Theme.spaceM : 56
                            radius: Theme.radiusS
                            color: slotState === "error" ? Theme.contentBg : Theme.surfaceMuted
                            border.width: slotState === "error" ? 1 : 0
                            border.color: Theme.divider
                            Accessible.role: Accessible.Grouping
                            Accessible.name: qsTr("Audio")

                            Wait { id: audioWait; active: audioBox.slotState === "waiting" || audioBox.slotState === "downloading" }

                            Row {
                                anchors.verticalCenter: parent.verticalCenter
                                x: Theme.spaceM
                                spacing: Theme.spaceS
                                visible: audioBox.slotState !== "error"

                                Image {
                                    anchors.verticalCenter: parent.verticalCenter
                                    visible: audioBox.slotState === "ready"
                                    width: Theme.iconM
                                    height: Theme.iconM
                                    sourceSize: Qt.size(Theme.iconM, Theme.iconM)
                                    source: "../components/icons/circle-check_successOnLight.svg"
                                    Accessible.ignored: true
                                }
                                PathIcon {
                                    anchors.verticalCenter: parent.verticalCenter
                                    visible: audioWait.elapsed
                                    path: audioBox.slotState === "downloading" ? detail.iconDownload : detail.iconClock
                                }
                                Text {
                                    objectName: "audioStatus"
                                    anchors.verticalCenter: parent.verticalCenter
                                    visible: text !== ""
                                    color: Theme.textPrimary
                                    font.pixelSize: Theme.fontBody
                                    text: {
                                        switch (audioBox.slotState) {
                                        case "ready": return qsTr("Audio ready")
                                        case "missing": return qsTr("This service doesn't have audio.")
                                        case "waiting": {
                                            if (!audioWait.elapsed)
                                                return ""
                                            const queued = audioBox.slot ? detail.queuedText(audioBox.slot.waitingFor) : ""
                                            return queued === "" ? qsTr("Waiting to download the audio")
                                                                 : qsTr("Waiting to download the audio · %1").arg(queued)
                                        }
                                        }
                                        return ""
                                    }
                                }
                                TransferProgress {
                                    objectName: "audioProgress"
                                    anchors.verticalCenter: parent.verticalCenter
                                    visible: audioBox.slotState === "downloading" && audioWait.elapsed
                                    width: 420
                                    done: audioBox.slot ? audioBox.slot.done : 0
                                    total: audioBox.slot ? audioBox.slot.total : 0
                                    label: qsTr("Downloading audio")
                                }
                            }

                            InlineError {
                                id: audioError
                                objectName: "audioError"
                                anchors.centerIn: parent
                                width: parent.width - 2 * Theme.spaceL
                                visible: audioBox.slotState === "error"
                                title: qsTr("Audio failed to load")
                                detail: audioBox.slot ? audioBox.slot.errorDetail : ""
                                technicalDetail: audioBox.slot ? audioBox.slot.errorTechnical : ""
                                failureCount: audioBox.slot ? audioBox.slot.failureCount : 0
                                onRetry: {
                                    if (detail.media)
                                        detail.media.retry("audio")
                                }
                            }
                        }

                        // 播放、停止：三個檔案都結束才能按（要同步播放）
                        Row {
                            anchors.horizontalCenter: parent.horizontalCenter
                            spacing: Theme.spaceS

                            BusyButton {
                                id: playButton
                                objectName: "playButton"
                                enabled: detail.canPlay
                                text: player.master.playbackState === MediaPlayer.PlayingState ? qsTr("Pause") : qsTr("Play")
                                onClicked: player.toggle()
                            }
                            BusyButton {
                                objectName: "stopButton"
                                enabled: detail.canPlay
                                text: qsTr("Stop")
                                onClicked: player.stopAll()
                            }
                            Text {
                                objectName: "playHint"
                                anchors.verticalCenter: parent.verticalCenter
                                leftPadding: Theme.spaceS
                                visible: text !== ""
                                text: detail.playHint()
                                color: Theme.textSecondary
                                font.pixelSize: Theme.fontCaption
                            }
                        }

                        Row {
                            anchors.horizontalCenter: parent.horizontalCenter
                            spacing: Theme.spaceM

                            GraphiteSlider {
                                id: positionSlider
                                objectName: "positionSlider"
                                width: 600
                                anchors.verticalCenter: parent.verticalCenter
                                enabled: detail.canPlay && player.master.duration > 0
                                from: 0
                                to: Math.max(1, player.master.duration)
                                Accessible.name: qsTr("Playback position")
                                onMoved: player.seekAll(value)
                            }
                            // 跟著播放的位置走；拖曳中不跟（直接綁 value 的話，拖過一次就不再跟著動）
                            Binding {
                                target: positionSlider
                                property: "value"
                                value: player.master.position
                                when: !positionSlider.pressed
                            }
                            Text {
                                objectName: "timeText"
                                anchors.verticalCenter: parent.verticalCenter
                                text: detail.canPlay ? detail.clock(player.master.position) + " / " + detail.clock(player.master.duration)
                                                     : "--:-- / --:--"
                                color: detail.canPlay ? Theme.textPrimary : Theme.textSecondary
                                font.pixelSize: Theme.fontCaption
                                font.features: { "tnum": 1 }    // 等寬數字：時間跳動時寬度不變（DS-14）
                            }
                        }

                        Row {
                            anchors.horizontalCenter: parent.horizontalCenter
                            spacing: Theme.spaceM

                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: qsTr("Volume")
                                color: Theme.textPrimary
                                font.pixelSize: Theme.fontBody
                            }
                            GraphiteSlider {
                                id: volumeSlider
                                objectName: "volumeSlider"
                                anchors.verticalCenter: parent.verticalCenter
                                width: 300
                                from: 0
                                to: 1
                                value: 0.5      // 和 AudioOutput 的音量是同一個值（UX-25：以前顯示 0.5、實際是 1）
                                Accessible.name: qsTr("Volume")
                            }
                        }
                    }

                    // 三個播放器一起播、一起停（顧客、員工、錄音）。沒有下載好的那一個不播
                    QtObject {
                        id: player
                        readonly property var players: [cam0Player, cam1Player, audioPlayer]
                        readonly property var master: cam0Box.slotState === "ready" ? cam0Player
                                                    : cam1Box.slotState === "ready" ? cam1Player : audioPlayer

                        function ready() {
                            return players.filter(p => p.source.toString() !== "")
                        }
                        function toggle() {
                            if (!detail.canPlay)
                                return
                            const playing = master.playbackState === MediaPlayer.PlayingState
                            for (const p of ready())
                                playing ? p.pause() : p.play()
                        }
                        function stopAll() {
                            for (const p of players)
                                p.stop()
                        }
                        function seekAll(position) {
                            for (const p of ready())
                                p.setPosition(position)
                        }
                    }

                    MediaPlayer {
                        id: cam0Player
                        source: detail.media ? detail.media.cam0.source : ""
                        videoOutput: cam0Box.output
                    }
                    MediaPlayer {
                        id: cam1Player
                        source: detail.media ? detail.media.cam1.source : ""
                        videoOutput: cam1Box.output
                    }
                    MediaPlayer {
                        id: audioPlayer
                        source: detail.media ? detail.media.audio.source : ""
                        audioOutput: AudioOutput { volume: volumeSlider.value }
                    }
                }

                // ── 圖表
                Item {
                    id: chartsPage
                    readonly property string chartsState: detail.charts ? detail.charts.state : "idle"
                    readonly property int readyCount: detail.charts ? detail.charts.readyCount : 0
                    readonly property int failedCount: detail.charts ? detail.charts.failedCount : 0
                    readonly property int totalCount: detail.charts ? detail.charts.totalCount : 0
                    readonly property string progressLabel: detail.charts && detail.charts.currentNumber > 0
                        ? qsTr("Downloading chart %1 of %2").arg(detail.charts.currentNumber).arg(totalCount) : ""

                    Wait { id: chartsWait; active: chartsPage.chartsState === "downloading" }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.topMargin: Theme.spaceL
                        anchors.bottomMargin: Theme.spaceL
                        spacing: Theme.spaceM

                        // 有幾張沒載入：說明並可以重試，其他的照常看（DS-52）
                        Rectangle {
                            objectName: "chartsWarning"
                            Layout.alignment: Qt.AlignHCenter
                            Layout.preferredWidth: chartsPage.width * 0.8
                            Layout.preferredHeight: warningRow.implicitHeight + 2 * Theme.spaceS
                            visible: chartsPage.failedCount > 0 && chartsPage.readyCount > 0
                            radius: Theme.radiusS
                            color: Theme.warningTint
                            Accessible.role: Accessible.AlertMessage
                            Accessible.name: warningTitle.text
                            Accessible.description: warningText.text

                            RowLayout {
                                id: warningRow
                                anchors.fill: parent
                                anchors.leftMargin: Theme.spaceM
                                anchors.rightMargin: Theme.spaceS
                                spacing: 12

                                PathIcon {
                                    color: Theme.warningOnLight
                                    path: detail.iconTriangleAlert
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 0
                                    Text {
                                        id: warningTitle
                                        objectName: "chartsWarningTitle"
                                        Layout.fillWidth: true
                                        text: chartsPage.failedCount === 1 ? qsTr("1 chart didn't load")
                                                                          : qsTr("%1 charts didn't load").arg(chartsPage.failedCount)
                                        color: Theme.textPrimary
                                        font.pixelSize: Theme.fontBody
                                        font.weight: Font.DemiBold
                                    }
                                    Text {
                                        id: warningText
                                        objectName: "chartsWarningText"
                                        Layout.fillWidth: true
                                        text: qsTr("You can view the other %1 now.").arg(chartsPage.readyCount)
                                        color: Theme.textPrimary
                                        font.pixelSize: Theme.fontCaption
                                        wrapMode: Text.Wrap
                                    }
                                }
                                BusyButton {
                                    objectName: "chartsRetryButton"
                                    text: qsTr("Retry")
                                    busyText: qsTr("Retrying…")
                                    busy: chartsPage.chartsState === "downloading"
                                    onClicked: {
                                        if (!busy && detail.charts)
                                            detail.charts.retry()
                                    }
                                }
                            }
                        }

                        // 已經有幾張可以看、其他的還在下載
                        TransferProgress {
                            objectName: "chartsProgress"
                            Layout.alignment: Qt.AlignHCenter
                            Layout.preferredWidth: chartsPage.width * 0.8
                            visible: chartsWait.elapsed && chartsPage.readyCount > 0 && chartsPage.progressLabel !== ""
                            done: detail.charts ? detail.charts.done : 0
                            total: detail.charts ? detail.charts.total : 0
                            label: chartsPage.progressLabel
                        }

                        Rectangle {
                            objectName: "chartFrame"
                            Layout.alignment: Qt.AlignHCenter
                            Layout.preferredWidth: chartsPage.width * 0.8
                            Layout.fillHeight: true
                            radius: Theme.radiusS
                            color: chartsPage.readyCount > 0 ? Theme.contentBg
                                 : chartsPage.chartsState === "error" ? Theme.contentBg : Theme.surfaceMuted
                            border.width: chartsPage.chartsState === "error" ? 1 : 0
                            border.color: Theme.divider
                            clip: true

                            Image {
                                id: chartImage
                                objectName: "chartImage"
                                anchors.fill: parent
                                visible: chartsPage.readyCount > 0
                                fillMode: Image.PreserveAspectFit
                                asynchronous: true      // 解碼不在 GUI 執行緒（ENG-19）
                                // 照顯示的大小解碼：大張的圖表不會整張放進記憶體（ENG-19）
                                sourceSize: Qt.size(Math.ceil(width * Screen.devicePixelRatio),
                                                    Math.ceil(height * Screen.devicePixelRatio))
                                source: chartsPage.readyCount > 0
                                        ? detail.charts.chartsSources[Math.min(detail.chartIndex, chartsPage.readyCount - 1)] : ""
                                Accessible.role: Accessible.Graphic
                                Accessible.name: qsTr("Chart %1 of %2").arg(detail.chartIndex + 1).arg(chartsPage.readyCount)
                            }

                            Text {
                                anchors.centerIn: parent
                                visible: chartImage.visible && chartImage.status === Image.Error
                                text: qsTr("This chart can't be shown.")
                                color: Theme.textSecondary
                                font.pixelSize: Theme.fontCaption
                            }

                            // 一張都還沒好：下載中的那一張的進度
                            Column {
                                objectName: "chartsDownloading"
                                anchors.centerIn: parent
                                spacing: 8
                                visible: chartsWait.elapsed && chartsPage.readyCount === 0

                                PathIcon {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    size: 24
                                    path: detail.iconDownload
                                }
                                TransferProgress {
                                    width: 360
                                    done: detail.charts ? detail.charts.done : 0
                                    total: detail.charts ? detail.charts.total : 0
                                    label: chartsPage.progressLabel !== "" ? chartsPage.progressLabel : qsTr("Downloading charts")
                                }
                            }

                            EmptyState {
                                objectName: "chartsEmpty"
                                anchors.centerIn: parent
                                width: Math.min(parent.width - 2 * Theme.spaceL, 360)
                                visible: chartsPage.chartsState === "empty"
                                title: qsTr("No charts yet")
                                description: qsTr("Charts appear here after the analysis is complete.")
                            }

                            InlineError {
                                objectName: "chartsError"
                                anchors.centerIn: parent
                                width: Math.min(parent.width - 2 * Theme.spaceL, 480)
                                visible: chartsPage.chartsState === "error"
                                title: qsTr("Charts didn't load")
                                detail: detail.charts ? detail.charts.errorDetail : ""
                                technicalDetail: detail.charts ? detail.charts.errorTechnical : ""
                                failureCount: detail.charts ? detail.charts.failureCount : 0
                                onRetry: {
                                    if (detail.charts)
                                        detail.charts.retry()
                                }
                            }
                        }

                        Row {
                            Layout.alignment: Qt.AlignHCenter
                            spacing: Theme.spaceM
                            visible: chartsPage.readyCount > 0

                            BusyButton {
                                objectName: "previousChartButton"
                                text: qsTr("Previous")
                                enabled: detail.chartIndex > 0
                                onClicked: detail.chartIndex = Math.max(0, detail.chartIndex - 1)
                            }
                            Text {
                                objectName: "chartCounter"
                                anchors.verticalCenter: parent.verticalCenter
                                text: (Math.min(detail.chartIndex, chartsPage.readyCount - 1) + 1) + " / " + chartsPage.readyCount
                                color: Theme.textPrimary
                                font.pixelSize: Theme.fontCaption
                                font.features: { "tnum": 1 }
                            }
                            BusyButton {
                                objectName: "nextChartButton"
                                text: qsTr("Next")
                                enabled: detail.chartIndex < chartsPage.readyCount - 1
                                onClicked: detail.chartIndex = Math.min(chartsPage.readyCount - 1, detail.chartIndex + 1)
                            }
                        }
                    }
                }
            }

            // 還沒打開任何服務（例如直接切到這一頁）
            EmptyState {
                objectName: "noServiceState"
                anchors.horizontalCenter: parent.horizontalCenter
                y: Theme.space4XL
                width: Math.min(parent.width, 360)
                visible: detail.pageState === "idle"
                title: qsTr("No service selected")
                description: qsTr("Go back and choose a service to view.")
                actionText: qsTr("Back")
                onActionTriggered: detail.goBack()
            }

            // 檔案清單查不到（例如連不上資料庫）：整頁的區塊錯誤
            InlineError {
                objectName: "listError"
                anchors.horizontalCenter: parent.horizontalCenter
                y: Theme.space4XL
                width: Math.min(parent.width, 480)
                visible: detail.pageState === "error"
                title: qsTr("Couldn't load this service")
                detail: detail.media ? detail.media.errorDetail : ""
                technicalDetail: detail.media ? detail.media.errorTechnical : ""
                failureCount: detail.media ? detail.media.failureCount : 0
                busy: detail.media !== null && detail.media.busy
                onRetry: {
                    if (detail.media)
                        detail.media.reload()
                }
            }
        }
    }

    // 換了一筆服務、圖表變少時，目前的圖表編號留在範圍內
    Connections {
        target: detail.media
        function onHeaderChanged() { detail.chartIndex = 0 }
    }
    Connections {
        target: detail.charts
        function onChartsSourceChanged() {
            detail.chartIndex = Math.max(0, Math.min(detail.chartIndex, detail.charts.readyCount - 1))
        }
    }
}
