pragma ComponentBehavior: Bound
import QtQuick
// 自訂了按鈕（BusyButton 自己 import Basic），這裡沒有自訂控制項；和其他頁面一樣用 Basic（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// Services 頁的結果卡片（新版介面 U2，PLAN §6-1、§6-4、§6-9；設計稿 StaffServices、AdminServices）。
// 兩端共用：server_service_page.qml、admin_service_page.qml 只放條件卡片，結果都在這裡（UX-46）。
// - 標題：「18 services」＋「Oct 19 – Nov 1, 2026 · newest first」（管理員端前面多「All staff · 」）。
// - 表格：一列 60（Theme.tableRow），只有底線、沒有斑馬紋；分數一定配分數帶的字（Theme.bandName，PLAN §2）。
//   欄位：Service ID、（管理員端的 Staff）、Date、Length、Customer、Staff（管理員端叫 Staff score）、Details。
// - 一次 8 筆：「Showing 8 of 18」＋Show More（UD7）；Show More 失敗時清單留著，錯誤和 Retry 在按鈕的位置。
// - 狀態：第一次查詢中是灰色骨架（不轉圈，過了 busyDelay 才出現）；重新搜尋時舊的列留著、變淡（DS-25、DS-33）；
//   查到 0 筆是空狀態，查詢失敗是區塊錯誤＋Retry（DS-26、DS-27）。
// - Details 發出 openDetail(serviceId, startTime)，頁面交給外殼的 window.openServiceDetail()。
// - Tab 走到下面的按鈕時發出 itemFocused(item)，頁面把它捲進畫面（Flickable 不會自己捲）。
ColumnLayout {
    id: table

    property var model: null                // RowListModel（service_list.LIST_ROLES）
    property bool showStaffColumn: false    // 管理員端多一欄 Staff
    property string listState: "loading"    // loading（還沒有結果）、ready、empty、error
    property bool busy: false               // 有結果時又在搜尋：舊的列留著、變淡
    property string countText: ""
    property string subtitle: ""
    property string shownText: ""
    property bool hasMore: false
    property bool loadingMore: false
    property string moreErrorDetail: ""
    property string moreErrorTechnical: ""
    property int moreFailureCount: 0
    property string emptyTitle: ""
    property string emptyDescription: ""
    property string errorTitle: ""
    property string errorDetail: ""
    property string errorTechnical: ""
    property int failureCount: 0

    signal openDetail(string serviceId, string startTime)
    signal showMore()
    signal retry()
    signal itemFocused(Item item)

    readonly property int rowCount: model ? model.count : 0
    readonly property bool hasRows: listState === "ready" && rowCount > 0
    readonly property int detailsWidth: 112
    // 欄寬：每一欄先給放得下內容的寬度（服務 ID 不能被截斷），多的照比例分給各欄；
    // 視窗太窄、連最小寬度都放不下時一起縮小（分數帶的字會換到數字下面一行，列高不變）
    readonly property var minimums: showStaffColumn ? [150, 120, 120, 72, 150, 150] : [150, 120, 72, 150, 150]
    readonly property var weights: showStaffColumn ? [0.5, 0.6, 0.6, 0.4, 1, 1] : [0.5, 0.6, 0.4, 1, 1]
    readonly property real minimumSum: minimums.reduce((sum, value) => sum + value, 0)
    readonly property real weightSum: weights.reduce((sum, value) => sum + value, 0)

    function columnWidth(column) {
        const available = Math.max(0, width - detailsWidth)
        if (available <= minimumSum)
            return Math.floor(minimums[column] * available / minimumSum)
        return Math.floor(minimums[column] + (available - minimumSum) * weights[column] / weightSum)
    }

    // 分數：「81.4 Good」；沒有分數是「—」
    function scoreText(score) {
        return Theme.hasScore(score) ? Number(score).toFixed(1) : "—"
    }

    spacing: 14

    // active 持續超過 busyDelay 才成立（DS-25：300 ms 內什麼都不顯示，免得閃一下）
    component Wait: Timer {
        property bool active: false
        property bool elapsed: false
        interval: Theme.busyDelay
        running: active && !elapsed
        onTriggered: elapsed = true
        onActiveChanged: if (!active) elapsed = false
    }

    component HeaderCell: Text {
        color: Theme.textFaint
        font.pixelSize: Theme.fontLabel
        font.weight: Font.DemiBold
        font.capitalization: Font.AllUppercase
        font.letterSpacing: 0.7
        rightPadding: Theme.spaceM
        elide: Text.ElideRight
        verticalAlignment: Text.AlignVCenter
    }

    component Cell: Text {
        height: Theme.tableRow
        color: Theme.textSecondary
        font.pixelSize: Theme.fontCaption
        font.features: { "tnum": 1 }
        rightPadding: Theme.spaceM
        elide: Text.ElideRight
        verticalAlignment: Text.AlignVCenter
    }

    // 分數欄：粗體的數字＋分數帶的字（顏色不是唯一的線索，PLAN §2）。欄太窄時分數帶換到下一行
    component ScoreCell: Item {
        id: scoreCell
        property var score: null
        height: Theme.tableRow
        clip: true

        Flow {
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - Theme.spaceM
            spacing: 6

            Text {
                objectName: "scoreValue"
                text: table.scoreText(scoreCell.score)
                color: Theme.hasScore(scoreCell.score) ? Theme.textTitle : Theme.textMuted
                font.pixelSize: Theme.fontCaption
                font.weight: Font.Bold
                font.features: { "tnum": 1 }
            }
            Text {
                objectName: "scoreBand"
                visible: text !== ""
                text: Theme.bandName(scoreCell.score)
                color: Theme.bandColor(scoreCell.score)
                font.pixelSize: Theme.fontCaption
                font.weight: Font.DemiBold
            }
        }
    }

    Wait { id: firstWait; active: table.listState === "loading" }
    Wait { id: dimWait; active: table.busy && table.hasRows }

    // ── 標題：「18 services」＋範圍（基線對齊）
    RowLayout {
        objectName: "resultsTitle"
        Layout.fillWidth: true
        visible: table.hasRows
        spacing: 12

        Text {
            objectName: "countText"
            Layout.alignment: Qt.AlignBaseline
            text: table.countText
            color: Theme.textTitle
            font.pixelSize: Theme.fontTitleSmall
            font.bold: true
            Accessible.role: Accessible.Heading
            Accessible.name: text
        }
        Text {
            objectName: "subtitleText"
            Layout.alignment: Qt.AlignBaseline
            Layout.fillWidth: true
            text: table.subtitle
            color: Theme.textMuted
            font.pixelSize: Theme.fontCaption
            elide: Text.ElideRight
        }
    }

    // 表頭、列、Show More 之間沒有間距（每一列自己有上面的線）
    ColumnLayout {
        Layout.fillWidth: true
        visible: table.hasRows
        spacing: 0

        // ── 表頭
        Row {
            objectName: "tableHeader"
            Layout.fillWidth: true
            bottomPadding: 10

            HeaderCell { width: table.columnWidth(0); text: qsTr("Service ID") }
            HeaderCell { visible: table.showStaffColumn; width: visible ? table.columnWidth(1) : 0; text: qsTr("Staff") }
            HeaderCell { width: table.columnWidth(table.showStaffColumn ? 2 : 1); text: qsTr("Date") }
            HeaderCell { width: table.columnWidth(table.showStaffColumn ? 3 : 2); text: qsTr("Length") }
            HeaderCell { width: table.columnWidth(table.showStaffColumn ? 4 : 3); text: qsTr("Customer") }
            HeaderCell {
                width: table.columnWidth(table.showStaffColumn ? 5 : 4)
                text: table.showStaffColumn ? qsTr("Staff score") : qsTr("Staff")
            }
        }

        // ── 列：高度跟著內容（外面的頁面負責捲動）
        ListView {
            id: list
            objectName: "resultList"
            Layout.fillWidth: true
            Layout.preferredHeight: contentHeight
            interactive: false
            model: table.model
            opacity: dimWait.elapsed ? 0.5 : 1      // 重新搜尋：舊的列留著、變淡（DS-25）
            Accessible.role: Accessible.Table
            Accessible.name: qsTr("Services")

            Behavior on opacity {       // M-9：重新搜尋時變淡
                enabled: Theme.durBase > 0
                OpacityAnimator { duration: Theme.durBase }
            }

            delegate: Item {
                id: row
                objectName: "resultRow"
                required property int index
                required property string serviceId
                required property string staffName
                required property string startTime
                required property string dateText
                required property string lengthText
                required property var customerScore
                required property var staffScore

                width: ListView.view ? ListView.view.width : 0  // 收尾時 delegate 先離開 ListView，view 會是 null
                height: Theme.tableRow
                Accessible.role: Accessible.Row
                Accessible.name: [serviceId].concat(table.showStaffColumn ? [staffName] : [])
                    .concat([dateText, lengthText,
                             qsTr("Customer %1 %2").arg(table.scoreText(customerScore)).arg(Theme.bandName(customerScore)),
                             qsTr("Staff %1 %2").arg(table.scoreText(staffScore)).arg(Theme.bandName(staffScore))])
                    .filter(part => part.trim() !== "").join(", ")

                Rectangle {     // 每一列上面一條線（沒有斑馬紋，PLAN 6-1）
                    width: parent.width
                    height: 1
                    color: Theme.cardBorder
                }

                Row {
                    Cell {
                        objectName: "serviceIdCell"
                        width: table.columnWidth(0)
                        text: row.serviceId
                        color: Theme.textTitle
                        font.weight: Font.DemiBold
                    }
                    Cell {
                        objectName: "staffCell"
                        visible: table.showStaffColumn
                        width: visible ? table.columnWidth(1) : 0
                        text: row.staffName
                        color: Theme.textPrimary
                    }
                    Cell {
                        objectName: "dateCell"
                        width: table.columnWidth(table.showStaffColumn ? 2 : 1)
                        text: row.dateText
                    }
                    Cell {
                        objectName: "lengthCell"
                        width: table.columnWidth(table.showStaffColumn ? 3 : 2)
                        text: row.lengthText !== "" ? row.lengthText : "—"
                    }
                    ScoreCell {
                        objectName: "customerCell"
                        width: table.columnWidth(table.showStaffColumn ? 4 : 3)
                        score: row.customerScore
                    }
                    ScoreCell {
                        objectName: "staffScoreCell"
                        width: table.columnWidth(table.showStaffColumn ? 5 : 4)
                        score: row.staffScore
                    }
                    Item {
                        width: table.detailsWidth
                        height: Theme.tableRow

                        BusyButton {
                            objectName: "detailsButton"
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            horizontalPadding: 18
                            font.pixelSize: Theme.fontCaption
                            text: qsTr("Details")
                            Accessible.name: qsTr("Details, %1").arg(row.serviceId)
                            onActiveFocusChanged: if (activeFocus) table.itemFocused(this)
                            onClicked: table.openDetail(row.serviceId, row.startTime)
                        }
                    }
                }
            }
        }

        // ── 下面：「Showing 8 of 18」＋Show More；失敗時換成區塊錯誤（Retry 就是再按一次 Show More）
        Item {
            objectName: "listFooter"
            Layout.fillWidth: true
            implicitHeight: footerRow.implicitHeight + 12
            visible: table.hasMore || table.loadingMore || table.moreErrorDetail !== ""

            Rectangle {
                width: parent.width
                height: 1
                color: Theme.cardBorder
            }

            RowLayout {
                id: footerRow
                y: 8
                width: parent.width
                spacing: Theme.spaceM

                Text {
                    objectName: "shownText"
                    Layout.alignment: Qt.AlignVCenter
                    text: table.shownText
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }

                BusyButton {
                    id: showMoreButton
                    objectName: "showMoreButton"
                    visible: table.moreErrorDetail === ""
                    horizontalPadding: 18
                    font.pixelSize: Theme.fontCaption
                    text: qsTr("Show More")
                    busyText: qsTr("Loading…")
                    busy: table.loadingMore
                    onActiveFocusChanged: if (activeFocus) table.itemFocused(this)
                    onClicked: {
                        if (!busy)      // 輔助技術的「按下」不經過 BusyButton 的擋板
                            table.showMore()
                    }
                }

                InlineError {
                    objectName: "moreError"
                    Layout.fillWidth: true
                    visible: table.moreErrorDetail !== ""
                    title: qsTr("Couldn't load more services")
                    detail: table.moreErrorDetail
                    technicalDetail: table.moreErrorTechnical
                    failureCount: table.moreFailureCount
                    busy: table.loadingMore
                    onRetry: table.showMore()
                }

                Item { Layout.fillWidth: true; visible: table.moreErrorDetail === "" }
            }
        }
    }

    // ── 第一次查詢中：灰色骨架（PLAN 5-2：不轉圈）
    Column {
        objectName: "listSkeleton"
        Layout.fillWidth: true
        visible: table.listState === "loading" && firstWait.elapsed
        spacing: 0
        Accessible.role: Accessible.StaticText
        Accessible.name: qsTr("Loading services")

        Rectangle {
            width: 160
            height: 20
            radius: 6
            color: Theme.tile
        }
        Repeater {
            model: 4
            Item {
                required property int index
                width: parent.width
                height: Theme.tableRow

                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width * (0.9 - 0.08 * parent.index)
                    height: 16
                    radius: 6
                    color: Theme.tile
                }
            }
        }
    }

    EmptyState {
        objectName: "searchEmpty"
        Layout.alignment: Qt.AlignHCenter
        Layout.preferredWidth: Math.min(table.width, 480)
        Layout.topMargin: Theme.spaceM
        Layout.bottomMargin: Theme.spaceM
        visible: table.listState === "empty"
        title: table.emptyTitle
        description: table.emptyDescription
    }

    InlineError {
        objectName: "searchError"
        Layout.alignment: Qt.AlignHCenter
        Layout.preferredWidth: Math.min(table.width, 520)
        Layout.topMargin: Theme.spaceS
        Layout.bottomMargin: Theme.spaceS
        visible: table.listState === "error"
        title: table.errorTitle
        detail: table.errorDetail
        technicalDetail: table.errorTechnical
        failureCount: table.failureCount
        busy: table.busy
        onRetry: table.retry()
    }
}
