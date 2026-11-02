pragma ComponentBehavior: Bound
import QtQuick
// 分頁、表格的按鈕自訂了 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 分析頁（new-UIUX PLAN 6-3、UD6、UD7；設計稿 StaffAnalysis）：送出分析、看結果。
// 頁面標題和說明在外殼的頁首，這一頁不畫標題。一張卡片裡：
// - 三個分頁照流程排：Not analyzed → Analyzing → Completed，各有數量。沒有 Status 欄（分頁已經說了狀態）。
//   打開時停在 serviceModel.defaultTab（有沒送出的就是 Not analyzed，沒有就是 Completed，T30）；
//   之後清單怎麼變都不自己換分頁。首頁的服務方塊呼叫 showTab()，通知的 View Results 呼叫 showService()。
// - 每個分頁一次 8 筆，「Showing 8 of 41」和 Show More（PagedRowModel：下一頁已經載入，按下去馬上出現）。
// - 第一次載入：卡片和分頁先出現，過了 busyDelay 才出現灰色的骨架（不轉圈）；不顯示空狀態，也不顯示假的 0。
//   第一次就失敗：卡片裡是區塊錯誤＋Retry（DS-27）。已經有資料時更新不到：清單留著，由 serviceModel 跳一則警告。
// - Analyze 在背景送出：那一列的按鈕忙碌（Sending…），結果用 toast 說（DS-24、DS-52）。
// - View 經過外殼的 openServiceDetail() 打開服務明細。
// - Completed 的 Report 欄（UX-20 的 PDF 按鈕，new-feature N4，T37）：管理員發佈了才有 Report（經過外殼的 openReport()
//   打開）；還沒有 PDF 寫「Not generated yet」，有 PDF 還沒發佈寫「Not published yet」（R6：只給狀態，不給報表）。
//   狀態在 reportsModel.serviceStates（頁面開著時每 15 秒更新）。
// - 這一頁的資料都在 serviceModel（Python）：日期、長度、「Sent 21 min ago」也是，頁面不自己算時間。
Item {
    id: analysisPage

    readonly property var vm: typeof serviceModel !== "undefined" ? serviceModel : null
    readonly property var reportsVm: typeof reportsModel !== "undefined" ? reportsModel : null
    property var shell: null                        // 外殼（setWindow）：View 用它打開明細
    property string selectedTab: ""                 // 還不知道預設的分頁時是空字串：一個都不選，不先跳到別的分頁
    property string highlightedServiceId: ""        // View Results 標出的那一列（靜止的底色，不閃，DS-42）
    property bool revealPending: false              // 那一列還沒捲進畫面（清單可能還在重新整理）
    property bool showSkeleton: false               // 第一次載入超過 busyDelay（300 ms）才顯示骨架（DS-25）
    readonly property bool loading: !vm || vm.status === "loading"
    readonly property bool failed: vm !== null && vm.status === "error"
    readonly property bool ready: vm !== null && vm.status === "ready"
    readonly property var tabKeys: ["notAnalyzed", "analyzing", "completed"]
    // 通知堆疊蓋住內容區的右下角：捲到最底時留出它的高度，最後一列一定看得到（DS-21）
    readonly property int listBottomMargin: typeof toastHost !== "undefined" && toastHost ? toastHost.occupiedHeight : 0

    function setWindow(window) {
        shell = window
    }

    function modelFor(key) {
        if (!vm)
            return null
        return key === "completed" ? vm.completedModel : key === "analyzing" ? vm.analyzingModel : vm.notAnalyzedModel
    }

    // 首頁的服務方塊：換到那個分頁（"notAnalyzed"、"analyzing"、"completed"）
    function showTab(tab) {
        if (tabKeys.indexOf(tab) < 0)
            return
        highlightedServiceId = ""
        revealPending = false
        selectedTab = tab
        scroller.contentY = 0
    }

    // 通知上的「View Results」：選 Completed、標出那一列，捲到看得到的地方。那一列不在前 8 筆時，
    // serviceModel.pageOpened(sid) 已經讓 Completed 顯示到它那一頁（T30）
    function showService(serviceId) {
        showTab("completed")
        highlightedServiceId = serviceId
        revealPending = true
        Qt.callLater(revealHighlighted)
    }

    function revealHighlighted() {
        if (!revealPending || !vm)
            return
        if (vm.indexOfService("completed", highlightedServiceId) < 0)
            return      // 清單還在重新整理：那一列到了再捲（下面的 Connections）
        if (!tryReveal())
            revealTimer.restart()   // 那一列的 delegate、卡片的版面還沒排好：下一個畫格再試
    }

    function tryReveal() {
        const index = vm ? vm.indexOfService("completed", highlightedServiceId) : -1
        const list = completedPanel.list
        if (index < 0 || completedPanel.y <= 0 || list.height < (index + 1) * Theme.tableRow)
            return false
        list.forceLayout()
        const item = list.itemAtIndex(index)
        if (!item)
            return false
        revealPending = false
        ensureVisible(item)
        return true
    }

    // 列多了之後，ListView 的高度、delegate 和外面 ColumnLayout 的位置要等下一次排版才對（View Results 的那一列
    // 可能剛載入）：每個畫格試一次，最多一秒
    Timer {
        id: revealTimer
        interval: 16
        repeat: true
        property int attempts: 0
        onRunningChanged: {
            if (running)
                attempts = 0
        }
        onTriggered: {
            attempts += 1
            if (!analysisPage.revealPending || analysisPage.tryReveal() || attempts >= 60)
                stop()
        }
    }

    // 讓 item 整個出現在捲動區裡，而且不在通知堆疊下面（View Results 的那一列、用鍵盤移到畫面外的按鈕）
    function ensureVisible(item) {
        const visibleHeight = Math.max(item.height, scroller.height - listBottomMargin)
        const top = item.mapToItem(scroller.contentItem, 0, 0).y - Theme.spaceM
        const bottom = top + item.height + 2 * Theme.spaceM
        if (top < scroller.contentY)
            scroller.contentY = Math.max(0, top)
        else if (bottom > scroller.contentY + visibleHeight)
            scroller.contentY = Math.min(bottom - visibleHeight,
                                         Math.max(0, scroller.contentHeight + scroller.bottomMargin - scroller.height))
    }

    function chooseDefaultTab() {
        if (selectedTab === "" && vm && vm.defaultTab !== "")
            selectedTab = vm.defaultTab
    }

    function analyze(serviceId) {
        if (vm)
            vm.analyzeService(serviceId)    // 結果用 toast 說；送出期間那一列的按鈕忙碌
    }

    function openDetail(serviceId, startTime) {
        if (shell && typeof shell.openServiceDetail === "function")
            shell.openServiceDetail(serviceId, startTime)
    }

    function openReport(reportId) {
        if (shell && typeof shell.openReport === "function")
            shell.openReport(reportId)
    }

    // 分數：小數一位（serviceModel 已經四捨五入），旁邊一定寫分數帶（PLAN 第 2 節）；沒有分數是「—」
    function scoreText(score) {
        return Theme.hasScore(score) ? Number(score).toFixed(1) : "—"
    }

    // 欄寬：Service ID、Recorded 在三個分頁的位置一樣（換分頁時不跳）；按鈕的欄固定寬，其餘平分
    readonly property real idWidthRatio: 0.24
    readonly property real recordedWidthRatio: 0.2

    function columnWidths(kind, total) {
        const id = Math.round(total * idWidthRatio)
        const recorded = Math.round(total * recordedWidthRatio)
        if (kind === "analyzing")
            return [id, recorded, total - id - recorded]
        const action = kind === "completed" ? 120 : 160
        const report = kind === "completed" ? 150 : 0      // Report 欄（new-feature N4）
        const rest = total - id - recorded - action - report
        if (kind === "completed")
            return [id, recorded, Math.round(rest / 2), rest - Math.round(rest / 2), report, action]
        return [id, recorded, rest, action]
    }

    onLoadingChanged: {
        if (!loading)
            showSkeleton = false
    }

    Timer {
        interval: Theme.busyDelay
        running: analysisPage.loading
        onTriggered: analysisPage.showSkeleton = true
    }

    Connections {
        target: analysisPage.vm
        function onDefaultTabChanged() {
            analysisPage.chooseDefaultTab()
        }
    }

    Connections {
        target: analysisPage.vm ? analysisPage.vm.completedModel : null
        function onCountChanged() {
            if (analysisPage.revealPending)
                Qt.callLater(analysisPage.revealHighlighted)
        }
    }

    Component.onCompleted: {
        chooseDefaultTab()
        if (reportsVm)
            reportsVm.pageOpened("analysis")    // Report 欄的狀態：現在讀一次，頁面開著時每 15 秒
    }
    Component.onDestruction: {
        if (reportsVm)
            reportsVm.pageClosed("analysis")
    }

    // ── 分頁按鈕：外框裡三個膠囊，選到的是亮底深字（設計稿 StaffAnalysis）
    component StatusTab: Button {
        id: tab
        required property string key
        required property string label
        readonly property var model: analysisPage.modelFor(key)
        readonly property int count: model ? model.total : 0
        readonly property bool selected: analysisPage.selectedTab === key
        readonly property bool showsCount: analysisPage.ready      // 載入中、載入失敗時的 0 是假的

        objectName: key + "Tab"
        text: label
        implicitHeight: Theme.controlHeight
        leftPadding: 18
        rightPadding: 18
        verticalPadding: 0
        focusPolicy: Qt.StrongFocus
        font.pixelSize: Theme.fontCaption
        font.weight: Font.DemiBold
        Accessible.role: Accessible.PageTab
        Accessible.name: showsCount ? qsTr("%1, %2 services").arg(label).arg(count) : label
        Accessible.selected: selected
        onClicked: analysisPage.showTab(key)

        // 左右鍵在分頁之間移動並選取（tablist 的慣例）
        function move(step) {
            const keys = analysisPage.tabKeys
            const next = keys[(keys.indexOf(key) + step + keys.length) % keys.length]
            analysisPage.showTab(next)
            tabRow.tabFor(next).forceActiveFocus(Qt.TabFocusReason)
        }
        Keys.onLeftPressed: move(-1)
        Keys.onRightPressed: move(1)

        contentItem: Row {
            spacing: 8

            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: tab.label
                font: tab.font
                color: tab.selected ? Theme.segmentSelectedText : Theme.segmentIdleText
            }

            Rectangle {     // 數量
                objectName: "countBadge"
                anchors.verticalCenter: parent.verticalCenter
                visible: tab.showsCount
                width: Math.max(22, countText.implicitWidth + 14)
                height: 22
                radius: 11
                color: tab.selected ? Theme.segmentSelectedText : Theme.cardBorder

                Text {
                    id: countText
                    anchors.centerIn: parent
                    text: tab.count
                    color: tab.selected ? Theme.textTitle : Theme.segmentIdleText
                    font.pixelSize: Theme.fontLabel
                    font.weight: Font.DemiBold
                    font.features: { "tnum": 1 }
                }
            }
        }

        background: Rectangle {
            radius: 12
            color: tab.selected ? Theme.segmentSelected : tab.hovered ? Theme.actionSecondaryHover : "transparent"

            Rectangle {     // 鍵盤焦點框（DS-30）：2 px、往外 2 px；只在鍵盤造成焦點時出現
                visible: tab.visualFocus
                anchors.fill: parent
                anchors.margins: -2
                radius: parent.radius + 2
                color: "transparent"
                border.color: Theme.focusOnDark
                border.width: 2
            }
        }
    }

    // ── 表格的一格文字（14 px、等寬數字）
    component Cell: Text {
        height: Theme.tableRow
        color: Theme.textSecondary
        font.pixelSize: Theme.fontCaption
        font.features: { "tnum": 1 }
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        rightPadding: Theme.spaceM
    }

    // 分數和分數帶：「81.4 Fair」；沒有分數是「—」
    component ScoreCell: Item {
        id: scoreCell
        required property var score
        readonly property bool hasScore: Theme.hasScore(score)
        readonly property string text: hasScore ? analysisPage.scoreText(score) + " " + Theme.bandName(score)
                                                : analysisPage.scoreText(score)
        height: Theme.tableRow

        Row {
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - Theme.spaceM
            spacing: 6
            clip: true

            Text {
                text: analysisPage.scoreText(scoreCell.score)
                color: scoreCell.hasScore ? Theme.textTitle : Theme.textMuted
                font.pixelSize: Theme.fontCaption
                font.weight: Font.Bold
                font.features: { "tnum": 1 }
            }
            Text {
                visible: scoreCell.hasScore
                text: Theme.bandName(scoreCell.score)
                color: Theme.bandColor(scoreCell.score)
                font.pixelSize: Theme.fontCaption
                font.weight: Font.DemiBold
            }
        }
    }

    // ── 一列服務。kind：notAnalyzed、analyzing、completed（各自的欄位和動作）
    component ServiceRow: Item {
        id: row
        objectName: "serviceRow"
        required property int index
        required property string serviceId
        required property string startTime
        required property string recorded
        required property string length
        required property string sentText
        required property var staffScore
        required property var customerScore
        required property bool failed
        required property bool sending          // 送出分析中（POST 加上之後的重新整理）
        required property string kind

        readonly property var widths: analysisPage.columnWidths(kind, width)
        readonly property bool highlighted: kind === "completed" && serviceId === analysisPage.highlightedServiceId
        readonly property string lengthText: length !== "" ? length : "—"
        // 報表的狀態（Completed）：published（附 reportId）、unpublished、none；還沒查到是空字串（顯示「—」）
        readonly property var reportInfo: analysisPage.reportsVm && analysisPage.reportsVm.serviceStates[serviceId] !== undefined
                                          ? analysisPage.reportsVm.serviceStates[serviceId] : null
        readonly property string reportState: !analysisPage.reportsVm || !analysisPage.reportsVm.statesLoaded ? ""
                                              : reportInfo ? reportInfo.state : "none"
        readonly property string reportText: reportState === "published" ? qsTr("report published")
                                           : reportState === "unpublished" ? qsTr("Not published yet")
                                           : reportState === "none" ? qsTr("Not generated yet") : "—"

        width: ListView.view ? ListView.view.width : 0
        height: Theme.tableRow
        Accessible.role: Accessible.Row
        Accessible.selected: highlighted
        Accessible.name: {
            const recordedText = qsTr("recorded %1").arg(recorded)
            if (kind === "completed")
                return [serviceId, recordedText, qsTr("customer %1").arg(customerCell.text),
                        qsTr("staff %1").arg(staffCell.text)].concat(row.reportState !== "" ? [row.reportText] : [])
                       .join(", ")
            if (kind === "analyzing")
                return [serviceId, recordedText, sentText].join(", ")
            const parts = [serviceId, recordedText, qsTr("length %1").arg(lengthText)]
            if (failed)
                parts.push(qsTr("analysis failed"))
            return parts.join(", ")
        }

        Rectangle {     // View Results 標出的那一列：往左右多出一點，欄位的字不移動
            objectName: "highlight"
            anchors.fill: parent
            anchors.leftMargin: -Theme.spaceS
            anchors.rightMargin: -Theme.spaceS
            visible: row.highlighted
            color: Theme.infoTint
            border.color: Theme.infoOnDark
            border.width: 1
            radius: Theme.radiusS
        }

        Rectangle {     // 每一列上面的線（沒有斑馬紋，PLAN 6-1）
            width: parent.width
            height: 1
            color: Theme.cardBorder
        }

        Row {
            width: parent.width

            Item {      // Service ID；分析失敗過的，下面多一行
                width: row.widths[0]
                height: Theme.tableRow

                Column {
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - Theme.spaceM

                    Text {
                        objectName: "serviceIdCell"
                        width: parent.width
                        text: row.serviceId
                        color: Theme.textTitle
                        font.pixelSize: Theme.fontCaption
                        font.weight: Font.DemiBold
                        font.features: { "tnum": 1 }
                        elide: Text.ElideRight
                    }
                    Text {
                        objectName: "failedNote"
                        visible: row.kind === "notAnalyzed" && row.failed
                        text: qsTr("Analysis failed")
                        color: Theme.errorOnDark
                        font.pixelSize: Theme.fontSmall
                    }
                }
            }

            Cell {
                objectName: "recordedCell"
                width: row.widths[1]
                text: row.recorded
            }

            Cell {
                objectName: "lengthCell"
                visible: row.kind === "notAnalyzed"
                width: visible ? row.widths[2] : 0
                text: row.lengthText
            }

            Item {
                objectName: "sentCell"
                visible: row.kind === "analyzing"
                width: visible ? row.widths[2] : 0
                height: Theme.tableRow
                property string text: row.sentText

                Row {
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 8

                    Image {
                        anchors.verticalCenter: parent.verticalCenter
                        width: Theme.iconS
                        height: Theme.iconS
                        sourceSize: Qt.size(Theme.iconS, Theme.iconS)
                        source: "../components/icons/clock_infoOnDark.svg"
                        Accessible.ignored: true
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: row.sentText
                        color: Theme.infoOnDark
                        font.pixelSize: Theme.fontCaption
                    }
                }
            }

            ScoreCell {
                id: customerCell
                objectName: "customerCell"
                visible: row.kind === "completed"
                width: visible ? row.widths[2] : 0
                score: row.customerScore
            }

            ScoreCell {
                id: staffCell
                objectName: "staffCell"
                visible: row.kind === "completed"
                width: visible ? row.widths[3] : 0
                score: row.staffScore
            }

            Item {      // Report（Completed）：發佈了是 Report 按鈕，其他寫狀態
                objectName: "reportCell"
                visible: row.kind === "completed"
                width: visible ? row.widths[4] : 0
                height: Theme.tableRow

                Text {
                    objectName: "reportStateText"
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - Theme.spaceM
                    visible: row.reportState !== "published"
                    text: row.reportText
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontCaption
                    elide: Text.ElideRight
                }

                Loader {
                    anchors.verticalCenter: parent.verticalCenter
                    active: row.kind === "completed" && row.reportState === "published"
                    sourceComponent: BusyButton {
                        objectName: "reportButton"
                        text: qsTr("Report")
                        Accessible.name: qsTr("Open the report for %1").arg(row.serviceId)
                        onActiveFocusChanged: {
                            if (activeFocus)
                                analysisPage.ensureVisible(row)
                        }
                        onClicked: analysisPage.openReport(row.reportInfo.reportId)
                    }
                }
            }

            Item {      // 動作：Not analyzed 的 Analyze、Completed 的 View；靠右
                visible: row.kind !== "analyzing"
                width: visible ? row.widths[row.widths.length - 1] : 0
                height: Theme.tableRow

                Loader {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    active: row.kind === "notAnalyzed"
                    sourceComponent: BusyButton {
                        objectName: "analyzeButton"
                        variant: "primary"
                        busy: row.sending       // 一按就擋住第二次點擊；300 ms 後才換字（DS-24）
                        text: row.failed ? qsTr("Retry") : qsTr("Analyze")
                        busyText: qsTr("Sending…")
                        Accessible.name: (busyShown ? busyText : text) + ", " + row.serviceId
                        onActiveFocusChanged: {
                            if (activeFocus)
                                analysisPage.ensureVisible(row)
                        }
                        onClicked: {
                            if (busy)
                                return
                            analysisPage.analyze(row.serviceId)
                        }
                    }
                }

                Loader {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    active: row.kind === "completed"
                    sourceComponent: BusyButton {
                        objectName: "viewButton"
                        text: qsTr("View")
                        Accessible.name: qsTr("View %1").arg(row.serviceId)
                        onActiveFocusChanged: {
                            if (activeFocus)
                                analysisPage.ensureVisible(row)
                        }
                        onClicked: analysisPage.openDetail(row.serviceId, row.startTime)
                    }
                }
            }
        }
    }

    // ── 一個分頁的內容：說明、表格、空狀態、「Showing 8 of 41」＋Show More
    component TabPanel: ColumnLayout {
        id: panel
        required property string kind
        required property string description
        required property var titles            // 表頭（最後一欄的按鈕沒有表頭）
        property string emptyTitle: ""
        property string emptyDescription: ""
        property string emptyActionText: ""
        signal emptyAction()

        readonly property var rows: analysisPage.modelFor(kind)
        readonly property alias list: listView
        readonly property bool empty: rows !== null && rows.total === 0

        objectName: kind + "Table"
        visible: analysisPage.ready && analysisPage.selectedTab === kind
        spacing: 18

        Text {
            objectName: kind + "Description"
            Layout.fillWidth: true
            visible: !panel.empty
            text: panel.description
            color: Theme.textMuted
            font.pixelSize: Theme.fontCaption
            wrapMode: Text.Wrap
        }

        Column {
            Layout.fillWidth: true
            visible: !panel.empty

            Row {       // 表頭：12 px、大寫、字距
                readonly property var widths: analysisPage.columnWidths(panel.kind, width)
                id: headerRow
                width: parent.width
                height: 26
                Accessible.ignored: true

                Repeater {
                    model: panel.titles

                    Text {
                        required property int index
                        required property string modelData
                        width: headerRow.widths[index]
                        height: headerRow.height
                        text: modelData
                        color: Theme.textFaint
                        font.pixelSize: Theme.fontLabel
                        font.weight: Font.DemiBold
                        font.letterSpacing: 0.72
                        font.capitalization: Font.AllUppercase
                        verticalAlignment: Text.AlignTop
                        elide: Text.ElideRight
                    }
                }
            }

            ListView {
                id: listView
                objectName: panel.kind + "List"
                width: parent.width
                height: contentHeight
                interactive: false      // 整頁一起捲（外面的 Flickable）
                model: panel.rows
                Accessible.role: Accessible.Table
                Accessible.name: panel.kind === "completed" ? qsTr("Completed services")
                               : panel.kind === "analyzing" ? qsTr("Services being analyzed")
                               : qsTr("Services not analyzed")

                delegate: ServiceRow {
                    kind: panel.kind
                }
            }
        }

        EmptyState {     // 自己把內容置中（最寬 360）；佔滿整列，這個 ColumnLayout 才會跟著卡片一樣寬
            objectName: panel.kind + "Empty"
            Layout.fillWidth: true
            Layout.topMargin: Theme.spaceXL
            Layout.bottomMargin: Theme.spaceXL
            visible: panel.visible && panel.empty
            title: panel.emptyTitle
            description: panel.emptyDescription
            actionText: panel.emptyActionText
            onActionTriggered: panel.emptyAction()
        }

        Item {      // 「Showing 8 of 41」＋Show More：比一頁多的分頁才有
            objectName: panel.kind + "Footer"
            Layout.fillWidth: true
            implicitHeight: footerRow.implicitHeight + 12
            visible: panel.rows !== null && panel.rows.total > panel.rows.pageSize

            Rectangle {
                width: parent.width
                height: 1
                color: Theme.cardBorder
            }

            Row {
                id: footerRow
                y: 8
                spacing: Theme.spaceM

                Text {
                    objectName: "showingText"
                    anchors.verticalCenter: parent.verticalCenter
                    text: panel.rows ? qsTr("Showing %1 of %2").arg(panel.rows.count).arg(panel.rows.total) : ""
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                    font.features: { "tnum": 1 }
                }

                BusyButton {
                    id: showMore
                    objectName: "showMoreButton"
                    visible: panel.rows !== null && panel.rows.hasMore
                    text: qsTr("Show More")
                    busyText: qsTr("Loading…")
                    busy: panel.rows !== null && panel.rows.loadingMore
                    onActiveFocusChanged: {
                        if (activeFocus)
                            analysisPage.ensureVisible(showMore)
                    }
                    onClicked: {
                        if (!busy && panel.rows)
                            panel.rows.showMore()
                    }
                }
            }
        }
    }

    Flickable {
        id: scroller
        objectName: "analysisScroller"
        anchors.fill: parent
        contentWidth: width
        contentHeight: card.height
        bottomMargin: analysisPage.listBottomMargin
        boundsBehavior: Flickable.StopAtBounds
        clip: true
        ScrollBar.vertical: ScrollBar {}

        Card {
            id: card
            objectName: "analysisCard"
            width: scroller.width
            bottomPadding: Theme.spaceM
            Accessible.role: Accessible.Grouping
            Accessible.name: qsTr("Services by analysis status")

            ColumnLayout {
                width: parent.width
                spacing: 18

                Rectangle {     // 分頁的外框
                    Layout.alignment: Qt.AlignLeft
                    implicitWidth: tabRow.implicitWidth + 2
                    implicitHeight: tabRow.implicitHeight + 2
                    radius: 13
                    color: Theme.segmentBg
                    border.color: Theme.cardBorder
                    border.width: 1

                    Row {
                        id: tabRow
                        x: 1
                        y: 1
                        Accessible.role: Accessible.PageTabList
                        Accessible.name: qsTr("Analysis status")

                        function tabFor(key) {
                            return key === "completed" ? completedTab : key === "analyzing" ? analyzingTab : notAnalyzedTab
                        }

                        StatusTab {
                            id: notAnalyzedTab
                            key: "notAnalyzed"
                            label: qsTr("Not analyzed")
                        }
                        StatusTab {
                            id: analyzingTab
                            key: "analyzing"
                            label: qsTr("Analyzing")
                        }
                        StatusTab {
                            id: completedTab
                            key: "completed"
                            label: qsTr("Completed")
                        }
                    }
                }

                // 第一次載入：和表格同樣高的靜止骨架，不轉圈（PLAN 5-2、DS-25）
                Column {
                    objectName: "loadingPlaceholder"
                    Layout.fillWidth: true
                    visible: analysisPage.loading && analysisPage.showSkeleton
                    spacing: Theme.spaceS
                    Accessible.ignored: true

                    Repeater {
                        model: 4

                        Rectangle {
                            width: parent.width
                            height: Theme.controlHeight
                            radius: Theme.radiusM
                            color: Theme.tile
                        }
                    }
                }

                InlineError {
                    objectName: "listError"
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: Theme.spaceXL
                    Layout.bottomMargin: Theme.spaceXL
                    Layout.preferredWidth: 480
                    visible: analysisPage.failed
                    title: qsTr("Couldn't load your services")
                    detail: analysisPage.vm ? analysisPage.vm.errorDetail : ""
                    technicalDetail: analysisPage.vm ? analysisPage.vm.errorTechnical : ""
                    failureCount: analysisPage.vm ? analysisPage.vm.failureCount : 0
                    busy: analysisPage.vm !== null && analysisPage.vm.busy
                    onRetry: {
                        if (analysisPage.vm)
                            analysisPage.vm.refresh()
                    }
                }

                TabPanel {
                    Layout.fillWidth: true
                    kind: "notAnalyzed"
                    description: qsTr("Saved recordings that haven't been sent for analysis yet.")
                    titles: [qsTr("Service ID"), qsTr("Recorded"), qsTr("Length")]
                    emptyTitle: qsTr("No services to analyze")
                    emptyDescription: qsTr("New recordings appear here after they're saved.")
                }

                TabPanel {
                    Layout.fillWidth: true
                    kind: "analyzing"
                    description: qsTr("The analysis service is working on these. You'll get a notification when each one is done.")
                    titles: [qsTr("Service ID"), qsTr("Recorded"), qsTr("Sent")]
                    emptyTitle: qsTr("Nothing is being analyzed")
                    emptyDescription: qsTr("Services you send for analysis appear here until they're done.")
                }

                TabPanel {
                    id: completedPanel
                    Layout.fillWidth: true
                    kind: "completed"
                    description: qsTr("Overall scores. Good 85 and up, Fair 70–84, Needs improvement below 70.")
                    titles: [qsTr("Service ID"), qsTr("Recorded"), qsTr("Customer"), qsTr("Staff"), qsTr("Report")]
                    emptyTitle: qsTr("No completed analyses yet")
                    emptyDescription: qsTr("Services you analyze appear here when they're done.")
                    // 有沒送出的才給捷徑：Not analyzed 也是空的話，按了只會到另一個空的分頁
                    emptyActionText: analysisPage.vm && analysisPage.vm.notAnalyzedModel.total > 0
                                     ? qsTr("View Not analyzed") : ""
                    onEmptyAction: analysisPage.showTab("notAnalyzed")
                }
            }
        }
    }
}
