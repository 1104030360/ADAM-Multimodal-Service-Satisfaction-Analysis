pragma ComponentBehavior: Bound
import QtQuick
// 分頁、表格按鈕自訂了 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 分析頁（UX-17～21、ENG-05、ENG-08、ENG-16）。三個分頁的清單是 serviceModel 的增量模型：
// 重新整理只有 1 個往返，在背景執行；資料沒變的列不會重建，捲動位置也不會跳。
// - 分頁顯示數量；每個分頁沒有資料時有空狀態（DS-26），第一次載入失敗時是區塊錯誤（DS-27）
// - 每一列有狀態標籤（DS-22）；分析中的列顯示已經等了多久；沒有分數顯示「—」，不顯示 -1
// - Analyze 在背景送出：送出期間那一列的按鈕忙碌（Sending…），結果用 toast 說（DS-24、DS-52）；
//   送不出去時按鈕恢復，不跳對話框（DS-20）
// - 這一頁不再自己每 15 秒重新整理：分析完成或失敗由 app 層的監看發現（ENG-16），清單跟著更新
// - 通知上的「View Results」換到這一頁：選 Completed、標出那一列（showService）
Page {
    id: analysisPage
    title: qsTr("Analysis")

    readonly property var vm: typeof serviceModel !== "undefined" ? serviceModel : null
    property string selectedTab: "notAnalyzed"     // 剛錄好的服務在這裡，是要動作的分頁（UX-19）
    property string highlightedServiceId: ""       // View Results 標出的那一列（靜止的底色，不閃，DS-42）
    property bool revealPending: false              // 那一列還沒捲進畫面（清單可能還在重新整理）
    property double now: Date.now()
    readonly property bool loading: !vm || vm.status === "loading"
    readonly property bool failed: vm !== null && vm.status === "error"
    property bool showPlaceholder: false            // 載入超過 busyDelay（300 ms）才顯示（DS-25）
    // 延伸到內容區底部的清單：留出通知堆疊的高度，最後一列一定捲得出來（DS-21）
    readonly property int listBottomMargin: typeof toastHost !== "undefined" && toastHost ? toastHost.occupiedHeight : 0
    readonly property var columnWidths: [220, 230, 230, 200]

    background: Rectangle { color: Theme.contentBg }

    onLoadingChanged: {
        if (!loading)
            showPlaceholder = false
    }

    function scoreText(value) {
        return typeof value === "number" && value >= 0 ? String(Math.round(value)) : "—"
    }

    function waitingText(since) {
        if (!(since > 0))
            return ""
        const minutes = Math.max(0, Math.floor((now - since) / 60000))
        if (minutes < 1)
            return qsTr("Waiting less than 1 min")
        if (minutes < 60)
            return qsTr("Waiting %1 min").arg(minutes)
        return qsTr("Waiting %1 h %2 min").arg(Math.floor(minutes / 60)).arg(minutes % 60)
    }

    function analyze(serviceId) {
        if (vm)
            vm.analyzeService(serviceId)    // 結果用 toast 說；送出期間那一列的按鈕忙碌
    }

    // 外殼收到「View Results」時呼叫：選 Completed、標出那一列，捲到看得到的地方
    function showService(serviceId) {
        selectedTab = "completed"
        highlightedServiceId = serviceId
        revealPending = true
        Qt.callLater(revealHighlighted)
    }

    function revealHighlighted() {
        if (!revealPending || !vm)
            return
        const index = vm.indexOfService("completed", highlightedServiceId)
        if (index < 0)
            return      // 清單還在重新整理：那一列到了再捲（下面的 Connections）
        revealPending = false
        completedTable.view.positionViewAtIndex(index, ListView.Contain)
    }

    Connections {
        target: analysisPage.vm ? analysisPage.vm.completedModel : null
        function onCountChanged() {
            if (analysisPage.revealPending)
                Qt.callLater(analysisPage.revealHighlighted)
        }
    }

    // 「已經等了多久」以分鐘顯示，30 秒更新一次就夠（DS-44）
    Timer {
        objectName: "clockTimer"
        interval: 30000
        running: true
        repeat: true
        onTriggered: analysisPage.now = Date.now()
    }

    Timer {
        interval: Theme.busyDelay
        running: analysisPage.loading
        onTriggered: analysisPage.showPlaceholder = true
    }

    component StatusTab: Button {
        id: tab
        required property string key
        required property string label
        required property int count

        readonly property bool selected: analysisPage.selectedTab === key

        objectName: key + "Tab"
        // 載入中、載入失敗時不顯示數量（0 是假的）
        readonly property bool showsCount: !analysisPage.loading && !analysisPage.failed
        text: showsCount ? qsTr("%1 (%2)").arg(label).arg(count) : label
        implicitWidth: Math.max(150, implicitContentWidth + 2 * Theme.spaceL)
        implicitHeight: Theme.controlHeightL
        focusPolicy: Qt.StrongFocus
        font.pixelSize: Theme.fontBody
        font.weight: selected ? Font.DemiBold : Font.Normal
        Accessible.role: Accessible.PageTab
        Accessible.name: showsCount ? qsTr("%1, %2 services").arg(label).arg(count) : label
        Accessible.selected: selected
        onClicked: analysisPage.selectedTab = key

        contentItem: Text {
            text: tab.text
            font: tab.font
            color: tab.selected ? Theme.segmentSelectedText : Theme.segmentIdleText
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: height / 2
            color: tab.selected ? Theme.segmentSelected : Theme.segmentIdle

            Rectangle {     // 鍵盤焦點框（DS-30）
                visible: tab.visualFocus
                anchors.fill: parent
                anchors.margins: -4
                radius: parent.radius + 4
                color: "transparent"
                border.color: Theme.focusOnLight
                border.width: 2
            }
        }
    }

    component Cell: Text {
        height: 50
        color: Theme.textPrimary
        font.pixelSize: Theme.fontBody
        font.features: { "tnum": 1 }
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    component HeaderRow: Rectangle {
        id: header
        required property var titles

        height: 40
        color: Theme.contentBg
        border.color: Theme.divider
        border.width: 1

        Row {
            anchors.fill: parent

            Repeater {
                model: header.titles

                Text {
                    required property int index
                    required property string modelData
                    width: index < analysisPage.columnWidths.length ? analysisPage.columnWidths[index]
                                                                     : Math.max(0, header.width - 880)
                    height: header.height
                    text: modelData
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontBody
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }
        }
    }

    // 一列服務。kind：completed、analyzing、notAnalyzed（第 3、5 欄依分頁不同）
    component ServiceRow: Rectangle {
        id: row
        objectName: "serviceRow"
        required property int index
        required property string serviceId
        required property string startTime
        required property string endTime
        required property int status
        required property var staffScore
        required property var customerScore
        required property real waitingSince
        required property bool failed
        required property bool sending          // 送出分析中（POST 加上改狀態）
        required property string kind

        readonly property string scores: analysisPage.scoreText(staffScore) + " / " + analysisPage.scoreText(customerScore)
        readonly property string waiting: kind === "analyzing" ? analysisPage.waitingText(waitingSince) : ""
        readonly property bool highlighted: serviceId === analysisPage.highlightedServiceId

        height: 50
        color: highlighted ? Theme.infoTint : index % 2 === 0 ? Theme.surfaceAlt : Theme.contentBg
        border.color: highlighted ? Theme.infoOnLight : Theme.divider
        border.width: 1
        // 狀態寫進那一列的名稱（DS-22：標籤本身不給螢幕閱讀器）
        Accessible.role: Accessible.Row
        Accessible.selected: highlighted
        Accessible.name: [serviceId, chip.label, kind === "completed" ? qsTr("staff / customer %1").arg(scores) : waiting]
                         .filter(part => part !== "").join(", ")

        Row {
            anchors.fill: parent

            Cell {
                objectName: "serviceIdCell"
                width: analysisPage.columnWidths[0]
                text: row.serviceId
            }
            Cell {
                width: analysisPage.columnWidths[1]
                text: row.startTime
            }
            Cell {
                objectName: row.kind === "completed" ? "scoreCell" : "endTimeCell"
                width: analysisPage.columnWidths[2]
                text: row.kind === "completed" ? row.scores : row.endTime
            }
            Item {
                width: analysisPage.columnWidths[3]
                height: row.height

                StatusChip {
                    id: chip
                    objectName: "statusChip"
                    anchors.centerIn: parent
                    status: row.status
                    failed: row.failed
                }
            }
            Item {
                width: Math.max(0, row.width - 880)
                height: row.height

                Cell {
                    objectName: "waitingText"
                    visible: row.kind === "analyzing"
                    anchors.fill: parent
                    text: row.waiting
                    color: Theme.textSecondary
                    font.pixelSize: Theme.fontCaption
                }

                Loader {     // 只有 Not Analyzed 的列有按鈕
                    anchors.centerIn: parent
                    active: row.kind === "notAnalyzed"
                    sourceComponent: BusyButton {
                        objectName: "analyzeButton"
                        width: Math.max(120, implicitWidth)     // 放得下「Sending…」和旋轉圖示，換字時不變寬
                        height: 36
                        busy: row.sending       // 一按就擋住第二次點擊；300 ms 後才換字（DS-24）
                        text: row.failed ? qsTr("Retry") : qsTr("Analyze")
                        busyText: qsTr("Sending…")
                        Accessible.name: (busyShown ? busyText : text) + ", " + row.serviceId
                        onClicked: {
                            if (busy)
                                return
                            analysisPage.analyze(row.serviceId)
                        }
                    }
                }
            }
        }
    }

    // 一個分頁：表頭、清單、空狀態
    component ServiceTable: Item {
        id: table
        required property string kind
        required property var rows          // serviceModel 的 RowListModel
        required property var titles
        property string emptyTitle: ""
        property string emptyDescription: ""
        property string emptyActionText: ""
        signal emptyAction()

        readonly property alias view: listView

        visible: !analysisPage.loading && !analysisPage.failed && analysisPage.selectedTab === kind

        HeaderRow {
            id: header
            width: parent.width
            titles: table.titles
        }

        ListView {
            id: listView
            objectName: table.kind + "List"
            anchors.top: header.bottom
            anchors.bottom: parent.bottom
            width: parent.width
            clip: true
            model: table.rows
            bottomMargin: analysisPage.listBottomMargin
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}
            Accessible.role: Accessible.Table
            Accessible.name: table.kind === "completed" ? qsTr("Completed services")
                           : table.kind === "analyzing" ? qsTr("Services being analyzed")
                           : qsTr("Services not analyzed")

            delegate: ServiceRow {
                width: listView.width
                kind: table.kind
            }
        }

        EmptyState {
            objectName: table.kind + "Empty"
            anchors.horizontalCenter: parent.horizontalCenter
            y: header.height + Theme.space4XL
            width: Math.min(parent.width, 360)
            visible: table.visible && table.rows !== null && table.rows.count === 0
            title: table.emptyTitle
            description: table.emptyDescription
            actionText: table.emptyActionText
            onActionTriggered: table.emptyAction()
        }
    }

    RowLayout {
        id: tabBar
        anchors.top: parent.top
        anchors.topMargin: Theme.spaceS
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: Theme.spaceS
        Accessible.role: Accessible.PageTabList
        Accessible.name: qsTr("Analysis status")

        StatusTab {
            key: "completed"
            label: qsTr("Completed")
            count: analysisPage.vm ? analysisPage.vm.completedModel.count : 0
        }
        StatusTab {
            key: "analyzing"
            label: qsTr("Analyzing")
            count: analysisPage.vm ? analysisPage.vm.analyzingModel.count : 0
        }
        StatusTab {
            key: "notAnalyzed"
            label: qsTr("Not Analyzed")
            count: analysisPage.vm ? analysisPage.vm.notAnalyzedModel.count : 0
        }
    }

    Item {
        id: content
        anchors.top: tabBar.bottom
        anchors.topMargin: Theme.spaceXL
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom

        // 第一次載入：和表格同形狀的靜止 placeholder，不閃（DS-25）
        Column {
            objectName: "loadingPlaceholder"
            width: parent.width
            visible: analysisPage.loading && analysisPage.showPlaceholder
            Accessible.ignored: true

            Repeater {
                model: 4

                Rectangle {
                    required property int index
                    width: content.width
                    height: index === 0 ? 40 : 50
                    color: index === 0 ? Theme.contentBg : Theme.surfaceMuted
                    border.color: Theme.divider
                    border.width: 1
                }
            }
        }

        InlineError {
            objectName: "listError"
            anchors.horizontalCenter: parent.horizontalCenter
            y: Theme.space4XL
            width: Math.min(parent.width, 480)
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

        ServiceTable {
            id: completedTable
            objectName: "completedTable"
            anchors.fill: parent
            kind: "completed"
            rows: analysisPage.vm ? analysisPage.vm.completedModel : null
            titles: [qsTr("Service ID"), qsTr("Start Time"), qsTr("Staff / Customer"), qsTr("Status")]
            emptyTitle: qsTr("No completed analyses yet")
            emptyDescription: qsTr("Services you analyze appear here when they're done.")
            emptyActionText: qsTr("View Not Analyzed")
            onEmptyAction: analysisPage.selectedTab = "notAnalyzed"
        }

        ServiceTable {
            objectName: "analyzingTable"
            anchors.fill: parent
            kind: "analyzing"
            rows: analysisPage.vm ? analysisPage.vm.analyzingModel : null
            titles: [qsTr("Service ID"), qsTr("Start Time"), qsTr("End Time"), qsTr("Status"), qsTr("Waiting")]
            emptyTitle: qsTr("Nothing is being analyzed")
            emptyDescription: qsTr("Services you send for analysis appear here until they're done.")
        }

        ServiceTable {
            objectName: "notAnalyzedTable"
            anchors.fill: parent
            kind: "notAnalyzed"
            rows: analysisPage.vm ? analysisPage.vm.notAnalyzedModel : null
            titles: [qsTr("Service ID"), qsTr("Start Time"), qsTr("End Time"), qsTr("Status"), qsTr("Action")]
            emptyTitle: qsTr("No services to analyze")
            emptyDescription: qsTr("New recordings appear here after they're saved.")
        }
    }
}
