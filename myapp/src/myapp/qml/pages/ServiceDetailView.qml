pragma ComponentBehavior: Bound
import QtQuick
// 自訂了按鈕、分頁、滑桿的 background／contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtMultimedia
import "../components"

// 服務明細（兩端共用，UX-46；新版介面 U2，PLAN §6-5，設計稿 StaffServiceDetail）：看影片和圖表。
// server_service_detail_page.qml、admin_service_detail_page.qml 只決定「‹ Services」回到哪一頁。
// - 頁首在外殼（PageHeader）：這一頁給 headerTitle（服務 ID）、headerSubtitle（「Oct 31, 2026 · 09:15–09:21 ·
//   Analysis completed」，管理員端前面多服務人員的名字）、headerBackText（Services）和 goBack()。
//   頁首、分數和檔案清單是同一個查詢（T31 第 3 節）。
// - 分數列：Customer、Staff 各一列，Overall（大字＋分數帶，Theme.bandName）、Text、Audio、Facial；沒有分數時說明原因。
// - Videos：兩支影片並排，下面一組共用的控制（播放、時間、進度、音量），三個檔案一起播。
//   每個檔案在自己的位置顯示下載狀態（排隊、進度、沒有這個檔案、失敗＋Retry，T21）；錄音只有沒好的時候才出現一列。
// - Charts：一次一張，放在淺灰的紙上（Theme.paper，UD8），「Chart 1 of 4」、Previous、Next；
//   有幾張沒載入時，上方的警告列說明並可以重試（DS-52）。圖片非同步解碼，照顯示大小解碼（ENG-19）。
// - 按 Details 立刻換到這頁（UX-22、ENG-04）；離開這一頁時取消還沒下載完的檔案，已經下載好的留在快取（T21）。
// - 兩個分頁用 StackLayout 都留著：切到圖表再切回來，影片不會從頭開始（UX-25）。
// - 等待不到 300 ms 不顯示文字（DS-25）。整頁一起捲動；在 1440×900 時影片的高度會縮到控制列也看得到。
Item {
    id: detail

    property string backPage: ""
    property bool showStaffName: false      // 管理員端：頁首的說明前面加上這筆服務的服務人員
    readonly property var media: typeof mediaModel !== "undefined" ? mediaModel : null
    readonly property var charts: typeof chartsModel !== "undefined" ? chartsModel : null
    readonly property string pageState: media ? media.state : "idle"
    readonly property bool canPlay: media !== null && media.canPlay
    property int chartIndex: 0

    // 外殼的頁首（PageHeader）讀這三個；按「‹ Services」呼叫 goBack()
    readonly property string headerTitle: media && media.serviceId !== "" ? media.serviceId : qsTr("Service Details")
    readonly property string headerSubtitle: !media ? ""
        : showStaffName && media.staffName !== "" && media.headerText !== "" ? media.staffName + " · " + media.headerText
        : media.headerText
    readonly property string headerBackText: qsTr("Services")

    // 影片框的高度：4:3，但不超過「整頁不必捲動就看得到控制列」的高度（設計稿的比例，在 900 高的視窗也能操作）
    readonly property real videoWidth: cam0Box.width
    readonly property real videoHeight: Math.round(Math.max(240, Math.min(videoWidth * 3 / 4, scroller.height - 350)))

    // Loader 換頁時，這一頁的 context 在換頁的當下就被清掉，onDestruction 也在那時執行（T21）
    Component.onDestruction: {
        if (detail.media)
            detail.media.close()
    }

    // 回到 Services（同步換頁，和以前的 Back 一樣：換頁的當下就取消還沒下載完的檔案）
    function goBack() {
        window.showPageNow(detail.backPage)
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

    // 有東西沒載入時，說明能播的是哪幾個（設計稿：「Staff video and audio are ready to play.」）；都好了是空字串
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

    // 分數的文字：Overall 一位小數（和清單一樣），Text、Audio、Facial 取整數（設計稿）；沒有是「—」
    function scoreText(score, decimals) {
        return Theme.hasScore(score) ? Number(score).toFixed(decimals) : "—"
    }

    // 把 item 捲進畫面（Tab 走到看不到的按鈕時）
    function reveal(item) {
        const top = item.mapToItem(scroller.contentItem, 0, 0).y
        if (top < scroller.contentY)
            scroller.contentY = Math.max(0, top - Theme.spaceM)
        else if (top + item.height > scroller.contentY + scroller.height)
            scroller.contentY = Math.min(scroller.contentHeight - scroller.height,
                                         top + item.height - scroller.height + Theme.spaceM)
    }

    // ── 小元件 ──────────────────────────────────────────────────────────────

    // active 持續超過 busyDelay 才成立（DS-25：300 ms 內什麼都不顯示，免得閃一下）
    component Wait: Timer {
        property bool active: false
        property bool elapsed: false
        interval: Theme.busyDelay
        running: active && !elapsed
        onTriggered: elapsed = true
        onActiveChanged: if (!active) elapsed = false
    }

    // 狀態圖示（預先上色的 Lucide SVG）
    component StateIcon: Image {
        width: 24
        height: 24
        sourceSize: Qt.size(24, 24)
        Accessible.ignored: true
    }

    // Videos／Charts：分段切換（設計稿：深色的外框，選到的是亮底深字）
    component ContentTab: TabButton {
        id: tab
        focusPolicy: Qt.StrongFocus
        implicitHeight: Theme.controlHeight
        implicitWidth: label.implicitWidth + 2 * Theme.spaceL
        font.pixelSize: Theme.fontCaption
        font.weight: Font.DemiBold
        Accessible.name: text

        contentItem: Text {
            id: label
            text: tab.text
            font: tab.font
            color: tab.checked ? Theme.segmentSelectedText : Theme.segmentIdleText
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }

        background: Rectangle {
            radius: 12
            color: tab.checked ? Theme.segmentSelected : tab.hovered ? Theme.actionSecondaryHover : "transparent"

            Rectangle {     // 鍵盤焦點框（DS-30）
                visible: tab.visualFocus
                anchors.fill: parent
                anchors.margins: 2
                radius: 10
                color: "transparent"
                border.color: tab.checked ? Theme.segmentSelectedText : Theme.focusOnDark
                border.width: 2
            }
        }
    }

    // 淺色的滑桿：軌道 track、已經走過的 actionPrimary（UX-25：以前是 Material 預設的粉紅）
    component GraphiteSlider: Slider {
        id: slider
        focusPolicy: Qt.StrongFocus
        implicitHeight: Theme.controlHeight

        background: Rectangle {
            x: slider.leftPadding
            y: slider.topPadding + slider.availableHeight / 2 - height / 2
            implicitWidth: 96
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
                border.color: Theme.focusOnDark
                border.width: 2
            }
        }
    }

    // 分數列的一格：「Overall 91.7」「Text 90」（名稱小字、數字大字，基線對齊）
    component ScorePart: Row {
        id: part
        property string name: ""
        property string value: ""
        property bool large: false
        spacing: 6
        height: 28

        Text {
            anchors.baseline: number.baseline
            text: part.name
            color: Theme.textSecondary
            font.pixelSize: Theme.fontCaption
        }
        Text {
            id: number
            objectName: "scoreValue"
            anchors.bottom: parent.bottom
            text: part.value
            color: Theme.textTitle
            font.pixelSize: part.large ? Theme.fontHeadline : Theme.fontBodyLarge
            font.bold: true
            font.features: { "tnum": 1 }
        }
    }

    // 分數列的一列：CUSTOMER  Overall 91.7 Good  Text 90  Audio 88  Facial 95
    component ScoreRow: Flow {
        id: scoreRow
        property string label: ""
        property var scores: ({})
        readonly property bool known: detail.pageState === "ready"

        spacing: Theme.spaceXL
        Accessible.role: Accessible.Grouping
        Accessible.name: known ? qsTr("%1: Overall %2 %3, Text %4, Audio %5, Facial %6")
                                 .arg(label).arg(detail.scoreText(scores.total, 1)).arg(Theme.bandName(scores.total))
                                 .arg(detail.scoreText(scores.text, 0)).arg(detail.scoreText(scores.audio, 0))
                                 .arg(detail.scoreText(scores.facial, 0))
                               : label

        Text {
            width: 72
            height: 28
            verticalAlignment: Text.AlignBottom
            bottomPadding: 4
            text: scoreRow.label
            color: Theme.textFaint
            font.pixelSize: Theme.fontSmall
            font.weight: Font.DemiBold
            font.capitalization: Font.AllUppercase
            font.letterSpacing: 1
            Accessible.ignored: true
        }
        Row {
            spacing: 6
            ScorePart {
                objectName: "overall"
                name: qsTr("Overall")
                value: scoreRow.known ? detail.scoreText(scoreRow.scores.total, 1) : ""
                large: true
            }
            Text {
                objectName: "overallBand"
                height: 28
                verticalAlignment: Text.AlignBottom
                bottomPadding: 4
                visible: text !== ""
                text: scoreRow.known ? Theme.bandName(scoreRow.scores.total) : ""
                color: Theme.bandColor(scoreRow.scores.total)
                font.pixelSize: Theme.fontCaption
                font.weight: Font.DemiBold
            }
        }
        ScorePart { name: qsTr("Text"); value: scoreRow.known ? detail.scoreText(scoreRow.scores.text, 0) : "" }
        ScorePart { name: qsTr("Audio"); value: scoreRow.known ? detail.scoreText(scoreRow.scores.audio, 0) : "" }
        ScorePart { name: qsTr("Facial"); value: scoreRow.known ? detail.scoreText(scoreRow.scores.facial, 0) : "" }
    }

    // 一支影片的位置：標題＋4:3 的框。好了顯示影片；其他狀態各有說明（設計稿「檢視服務」的 4 種狀態，T21）
    component VideoBox: Column {
        id: box
        property var slot: null                 // FileSlot
        property string title: ""
        property string accessibleName: title   // 輔助技術唸的名稱，例如「Customer video」（標題只寫 Customer）
        property string failedTitle: qsTr("Video failed to load")
        readonly property string slotState: slot ? slot.state : "idle"
        property alias output: videoOutput

        spacing: 8
        Accessible.role: Accessible.Grouping
        Accessible.name: accessibleName

        Wait { id: loadingWait; active: box.slotState === "waiting" || box.slotState === "downloading" }

        Text {
            text: box.title
            color: Theme.textSecondary
            font.pixelSize: Theme.fontCaption
            font.weight: Font.DemiBold
        }

        Rectangle {
            objectName: "videoFrame"
            width: box.width
            height: Math.max(detail.videoHeight, videoError.visible ? videoError.implicitHeight + 2 * Theme.spaceM : 0)
            radius: 12
            color: Theme.contentBg
            border.width: 1
            border.color: Theme.cardBorder
            clip: true

            VideoOutput {
                id: videoOutput
                anchors.fill: parent
                anchors.margins: 1
                visible: box.slotState === "ready"
            }

            Column {
                objectName: "waitingState"
                anchors.centerIn: parent
                width: parent.width - 2 * Theme.spaceL
                spacing: 8
                visible: box.slotState === "waiting" && loadingWait.elapsed

                StateIcon {
                    anchors.horizontalCenter: parent.horizontalCenter
                    source: "../components/icons/clock_textSecondary.svg"
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

                StateIcon {
                    anchors.horizontalCenter: parent.horizontalCenter
                    source: "../components/icons/download_textSecondary.svg"
                }
                TransferProgress {
                    objectName: "progress"
                    width: 360      // 固定寬度：跟著版面變的寬度會讓文字的高度在排版時來回算（binding loop）
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

    Flickable {
        id: scroller
        objectName: "detailScroller"
        anchors.fill: parent
        visible: detail.pageState === "loading" || detail.pageState === "ready"
        contentWidth: width
        contentHeight: content.implicitHeight
        // 最後的內容不會被右下角的通知蓋住（DS-21）
        bottomMargin: typeof toastHost !== "undefined" && toastHost ? toastHost.occupiedHeight : 0
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}

        ColumnLayout {
            id: content
            width: scroller.width
            spacing: Theme.spaceL

            // ── 分數列
            Card {
                objectName: "scoreStrip"
                Layout.fillWidth: true
                topPadding: 18
                bottomPadding: 18
                Accessible.role: Accessible.Grouping
                Accessible.name: qsTr("Scores for this service")

                Item {
                    width: parent ? parent.width : 0
                    implicitHeight: noScores.visible ? noScores.implicitHeight : scoreRows.implicitHeight

                    Flow {
                        id: scoreRows
                        width: parent.width
                        visible: !noScores.visible
                        spacing: 48

                        ScoreRow {
                            objectName: "customerScores"
                            label: qsTr("Customer")
                            scores: detail.media ? detail.media.scores.customer : ({})
                        }
                        ScoreRow {
                            objectName: "staffScores"
                            label: qsTr("Staff")
                            scores: detail.media ? detail.media.scores.staff : ({})
                        }
                    }

                    // 還沒分析完（或分析完但沒有分數）：說明，不顯示一排「—」
                    Text {
                        id: noScores
                        objectName: "noScores"
                        width: parent.width
                        visible: detail.pageState === "ready" && detail.media !== null && !detail.media.hasScores
                        text: detail.media && detail.media.analysisStatus === 3
                              ? qsTr("This service has no scores.")
                              : qsTr("Scores appear here once the analysis is complete.")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontCaption
                        wrapMode: Text.Wrap
                    }
                }
            }

            // ── 影片和圖表
            Card {
                objectName: "mediaCard"
                Layout.fillWidth: true
                topPadding: Theme.spaceL
                Accessible.role: Accessible.Grouping
                Accessible.name: qsTr("Videos and charts")

                ColumnLayout {
                    width: parent ? parent.width : 0
                    spacing: 18

                    // Videos／Charts（外框是分段切換的樣子）
                    TabBar {
                        id: tabBar
                        objectName: "tabBar"
                        Layout.alignment: Qt.AlignLeft
                        spacing: 0
                        padding: 1
                        Accessible.name: qsTr("Service content")

                        background: Rectangle {
                            radius: 13
                            color: Theme.segmentBg
                            border.color: Theme.cardBorder
                            border.width: 1
                        }

                        ContentTab { objectName: "videoTab"; text: qsTr("Videos") }
                        ContentTab { objectName: "chartsTab"; text: qsTr("Charts") }
                    }

                    // 兩個分頁都留著，而且都有大小（StackLayout 排好每一個，只顯示目前的）：
                    // 切到圖表再切回來，影片不會從頭開始（UX-25）；圖表在切過去之前就知道顯示的大小，照那個大小解碼（ENG-19）
                    StackLayout {
                        id: tabs
                        Layout.fillWidth: true
                        currentIndex: tabBar.currentIndex

                        // ── Videos
                        ColumnLayout {
                            id: videoPage
                            objectName: "videoPage"
                            Layout.fillWidth: true
                            spacing: 18

                            GridLayout {
                                id: videoGrid
                                Layout.fillWidth: true
                                columns: 2
                                columnSpacing: Theme.spaceM
                                rowSpacing: Theme.spaceM

                                VideoBox {
                                    id: cam0Box
                                    objectName: "cam0Box"
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1     // 兩欄一樣寬
                                    title: qsTr("Customer")
                                    accessibleName: qsTr("Customer video")
                                    slot: detail.media ? detail.media.cam0 : null
                                }
                                VideoBox {
                                    id: cam1Box
                                    objectName: "cam1Box"
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 1     // 兩欄一樣寬
                                    title: qsTr("Staff")
                                    accessibleName: qsTr("Staff video")
                                    slot: detail.media ? detail.media.cam1 : null
                                }
                            }

                            // 錄音沒有畫面：只有還沒好（排隊、下載中、沒有、失敗）的時候才出現一列
                            Rectangle {
                                id: audioBox
                                objectName: "audioBox"
                                readonly property var slot: detail.media ? detail.media.audio : null
                                readonly property string slotState: slot ? slot.state : "idle"
                                Layout.fillWidth: true
                                Layout.preferredHeight: audioError.visible ? audioError.implicitHeight + 2 * Theme.spaceM : 56
                                visible: slotState !== "ready" && slotState !== "idle"
                                         && (slotState !== "waiting" && slotState !== "downloading" || audioWait.elapsed)
                                radius: 12
                                color: Theme.tile
                                border.width: 1
                                border.color: Theme.tileBorder
                                Accessible.role: Accessible.Grouping
                                Accessible.name: qsTr("Audio")

                                Wait { id: audioWait; active: audioBox.slotState === "waiting" || audioBox.slotState === "downloading" }

                                Row {
                                    anchors.verticalCenter: parent.verticalCenter
                                    x: Theme.spaceM
                                    spacing: Theme.spaceS
                                    visible: audioBox.slotState !== "error"

                                    StateIcon {
                                        anchors.verticalCenter: parent.verticalCenter
                                        width: Theme.iconM
                                        height: Theme.iconM
                                        visible: audioWait.elapsed
                                        source: audioBox.slotState === "downloading" ? "../components/icons/download_textSecondary.svg"
                                                                                     : "../components/icons/clock_textSecondary.svg"
                                    }
                                    Text {
                                        objectName: "audioStatus"
                                        anchors.verticalCenter: parent.verticalCenter
                                        visible: text !== ""
                                        color: Theme.textPrimary
                                        font.pixelSize: Theme.fontCaption
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

                            // 共用的控制：播放、時間、進度、音量。三個檔案都結束才能播（要同步播放）
                            Rectangle {
                                objectName: "playerBar"
                                Layout.fillWidth: true
                                Layout.preferredHeight: controls.implicitHeight + 20
                                radius: Theme.radiusTile
                                color: Theme.tile
                                border.width: 1
                                border.color: Theme.tileBorder

                                RowLayout {
                                    id: controls
                                    anchors.fill: parent
                                    anchors.leftMargin: 14
                                    anchors.rightMargin: 14
                                    spacing: 18

                                    Button {
                                        id: playButton
                                        objectName: "playButton"
                                        readonly property bool playing: player.master.playbackState === MediaPlayer.PlayingState
                                        implicitWidth: Theme.controlHeight
                                        implicitHeight: Theme.controlHeight
                                        focusPolicy: Qt.StrongFocus
                                        enabled: detail.canPlay
                                        text: playing ? qsTr("Pause") : qsTr("Play")
                                        Accessible.name: text
                                        onActiveFocusChanged: if (activeFocus) detail.reveal(this)
                                        onClicked: player.toggle()

                                        contentItem: Item {
                                            Image {
                                                anchors.centerIn: parent
                                                width: 18
                                                height: 18
                                                sourceSize: Qt.size(18, 18)
                                                source: playButton.playing ? "../components/icons/pause_actionPrimaryText.svg"
                                                                           : "../components/icons/play_actionPrimaryText.svg"
                                                opacity: playButton.enabled ? 1 : 0.35
                                                Accessible.ignored: true
                                            }
                                        }

                                        background: Rectangle {
                                            radius: width / 2
                                            color: !playButton.enabled ? Theme.actionDisabledBg
                                                 : playButton.down ? Theme.actionPrimaryPressed
                                                 : playButton.hovered ? Theme.actionPrimaryHover : Theme.actionPrimary

                                            Rectangle {     // 鍵盤焦點框（DS-30）
                                                visible: playButton.visualFocus
                                                anchors.fill: parent
                                                anchors.margins: -4
                                                radius: width / 2
                                                color: "transparent"
                                                border.color: Theme.focusOnDark
                                                border.width: 2
                                            }
                                        }
                                    }

                                    Text {
                                        objectName: "timeText"
                                        text: detail.canPlay ? detail.clock(player.master.position) + " / " + detail.clock(player.master.duration)
                                                             : "--:-- / --:--"
                                        color: detail.canPlay ? Theme.textPrimary : Theme.textMuted
                                        font.pixelSize: Theme.fontCaption
                                        font.features: { "tnum": 1 }    // 等寬數字：時間跳動時寬度不變（DS-14）
                                    }

                                    GraphiteSlider {
                                        id: positionSlider
                                        objectName: "positionSlider"
                                        Layout.fillWidth: true
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

                                    Image {
                                        width: 18
                                        height: 18
                                        sourceSize: Qt.size(18, 18)
                                        source: "../components/icons/volume-2_iconNeutral.svg"
                                        Accessible.ignored: true
                                    }
                                    GraphiteSlider {
                                        id: volumeSlider
                                        objectName: "volumeSlider"
                                        Layout.preferredWidth: 96
                                        from: 0
                                        to: 1
                                        value: 0.5      // 和 AudioOutput 的音量是同一個值（UX-25：以前顯示 0.5、實際是 1）
                                        Accessible.name: qsTr("Volume")
                                    }
                                }
                            }

                            // 有東西沒載入：說明能播的是哪幾個；都好了：說明三個檔案一起播
                            Text {
                                objectName: "playHint"
                                Layout.fillWidth: true
                                visible: text !== ""
                                text: detail.playHint()
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSmall
                                wrapMode: Text.Wrap
                            }
                            Text {
                                objectName: "playNote"
                                Layout.fillWidth: true
                                visible: detail.canPlay && detail.playHint() === ""
                                text: qsTr("Both videos and the audio play together.")
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSmall
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

                        // ── Charts
                        ColumnLayout {
                            id: chartsPage
                            objectName: "chartsPage"
                            Layout.fillWidth: true
                            spacing: Theme.spaceM

                            readonly property string chartsState: detail.charts ? detail.charts.state : "idle"
                            readonly property int readyCount: detail.charts ? detail.charts.readyCount : 0
                            readonly property int failedCount: detail.charts ? detail.charts.failedCount : 0
                            readonly property int totalCount: detail.charts ? detail.charts.totalCount : 0
                            readonly property int shownNumber: Math.min(detail.chartIndex, readyCount - 1) + 1
                            readonly property string progressLabel: detail.charts && detail.charts.currentNumber > 0
                                ? qsTr("Downloading chart %1 of %2").arg(detail.charts.currentNumber).arg(totalCount) : ""
                            // 紙上的圖片區：16:9，但和影片一樣不超過一個畫面
                            readonly property real imageHeight: Math.round(Math.max(200, Math.min((width - 40) * 9 / 16,
                                                                                                scroller.height - 330)))

                            Wait { id: chartsWait; active: chartsPage.chartsState === "downloading" }

                            // 有幾張沒載入：說明並可以重試，其他的照常看（DS-52）
                            Rectangle {
                                objectName: "chartsWarning"
                                Layout.fillWidth: true
                                Layout.preferredHeight: warningRow.implicitHeight + 2 * Theme.spaceS
                                visible: chartsPage.failedCount > 0 && chartsPage.readyCount > 0
                                radius: 12
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

                                    StateIcon {
                                        width: Theme.iconM
                                        height: Theme.iconM
                                        source: "../components/icons/triangle-alert_warningOnDark.svg"
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
                                            font.pixelSize: Theme.fontCaption
                                            font.weight: Font.DemiBold
                                        }
                                        Text {
                                            id: warningText
                                            objectName: "chartsWarningText"
                                            Layout.fillWidth: true
                                            text: qsTr("You can view the other %1 now.").arg(chartsPage.readyCount)
                                            color: Theme.textPrimary
                                            font.pixelSize: Theme.fontSmall
                                            wrapMode: Text.Wrap
                                        }
                                    }
                                    BusyButton {
                                        objectName: "chartsRetryButton"
                                        font.pixelSize: Theme.fontCaption
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
                                Layout.fillWidth: true
                                visible: chartsWait.elapsed && chartsPage.readyCount > 0 && chartsPage.progressLabel !== ""
                                done: detail.charts ? detail.charts.done : 0
                                total: detail.charts ? detail.charts.total : 0
                                label: chartsPage.progressLabel
                            }

                            // 「Chart 1 of 4」、Previous、Next
                            RowLayout {
                                Layout.fillWidth: true
                                visible: chartsPage.readyCount > 0
                                spacing: Theme.spaceM

                                Text {
                                    objectName: "chartCounter"
                                    Layout.fillWidth: true
                                    text: qsTr("Chart %1 of %2").arg(chartsPage.shownNumber).arg(chartsPage.readyCount)
                                    color: Theme.textSecondary
                                    font.pixelSize: Theme.fontCaption
                                    font.features: { "tnum": 1 }
                                }
                                BusyButton {
                                    objectName: "previousChartButton"
                                    font.pixelSize: Theme.fontCaption
                                    text: qsTr("Previous")
                                    enabled: detail.chartIndex > 0
                                    onClicked: detail.chartIndex = Math.max(0, detail.chartIndex - 1)
                                }
                                BusyButton {
                                    objectName: "nextChartButton"
                                    font.pixelSize: Theme.fontCaption
                                    text: qsTr("Next")
                                    enabled: detail.chartIndex < chartsPage.readyCount - 1
                                    onClicked: detail.chartIndex = Math.min(chartsPage.readyCount - 1, detail.chartIndex + 1)
                                }
                            }

                            // 圖表：白底的圖片放在淺灰的紙上（UD8）；一張都還沒好時，是一般的深色框加上狀態
                            Rectangle {
                                objectName: "chartFrame"
                                Layout.fillWidth: true
                                Layout.preferredHeight: chartsPage.imageHeight + (chartsPage.readyCount > 0 ? 40 : 0)
                                radius: 12
                                color: chartsPage.readyCount > 0 ? Theme.paper : Theme.contentBg
                                border.width: chartsPage.readyCount > 0 ? 0 : 1
                                border.color: Theme.cardBorder
                                clip: true

                                Image {
                                    id: chartImage
                                    objectName: "chartImage"
                                    anchors.fill: parent
                                    anchors.margins: Theme.spaceL
                                    visible: chartsPage.readyCount > 0
                                    fillMode: Image.PreserveAspectFit
                                    asynchronous: true      // 解碼不在 GUI 執行緒（ENG-19）
                                    // 照顯示的大小解碼：大張的圖表不會整張放進記憶體（ENG-19）
                                    sourceSize: Qt.size(Math.ceil(width * Screen.devicePixelRatio),
                                                        Math.ceil(height * Screen.devicePixelRatio))
                                    source: chartsPage.readyCount > 0
                                            ? detail.charts.chartsSources[chartsPage.shownNumber - 1] : ""
                                    Accessible.role: Accessible.Graphic
                                    Accessible.name: qsTr("Chart %1 of %2").arg(chartsPage.shownNumber).arg(chartsPage.readyCount)
                                }

                                Text {
                                    anchors.centerIn: parent
                                    visible: chartImage.visible && chartImage.status === Image.Error
                                    text: qsTr("This chart can't be shown.")
                                    color: Theme.warningOnLight     // 淺色的紙上的字
                                    font.pixelSize: Theme.fontCaption
                                }

                                // 一張都還沒好：下載中的那一張的進度
                                Column {
                                    objectName: "chartsDownloading"
                                    anchors.centerIn: parent
                                    spacing: 8
                                    visible: chartsWait.elapsed && chartsPage.readyCount === 0

                                    StateIcon {
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        source: "../components/icons/download_textSecondary.svg"
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
                        }
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

    // 頁首和檔案清單查不到（例如連不上資料庫）：整頁的區塊錯誤
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

    // 換了一筆服務、圖表變少時，目前的圖表編號留在範圍內
    Connections {
        target: detail.media
        function onHeaderChanged() {
            if (detail.charts === null || detail.charts.readyCount === 0)
                detail.chartIndex = 0
        }
    }
    Connections {
        target: detail.charts
        function onChartsSourceChanged() {
            detail.chartIndex = Math.max(0, Math.min(detail.chartIndex, detail.charts.readyCount - 1))
        }
    }
}
