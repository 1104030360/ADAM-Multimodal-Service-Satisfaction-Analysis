pragma ComponentBehavior: Bound
import QtQuick
// 自訂了清單項目的 background／contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 員工輔導（PLAN1 W2，T45）：管理員的 Coaching（admin_coaching_page.qml）和服務人員的 My Actions
// （server_actions_page.qml）都是這一頁。頁面標題和說明在外殼的頁首，這裡不畫。
// - 資料在 coachingModel（myapp/coaching.py）：打開時讀一次（pageOpened），不輪詢；寫入之後由它在同一個工作裡重讀。
//   服務人員只看得到自己的行動（sp_coaching_list 決定），管理員看得到全部。
// - 管理員：最上面一張卡片是兩個指標（有後續證據的完成比例、回顧時間）和進行中的數量，跟著員工的篩選。
// - 左邊是清單：目標、狀態標籤、（管理員）員工和期限；管理員的清單上面有篩選（狀態、期限、員工）。
//   員工在主管最後一次動作之後回應過的標「Staff replied」；服務人員還沒確認的標「New」。一進來選第一項。
// - 右邊是選到的那一項（CoachingDetail.qml）。內容比視窗高時整頁捲動。
Item {
    id: page

    readonly property var vm: typeof coachingModel !== "undefined" ? coachingModel : null
    readonly property bool admin: vm !== null && vm.isAdmin
    readonly property string listState: vm ? vm.listState : "idle"
    readonly property bool hasSelection: vm !== null && vm.selectedId !== ""
    readonly property int gap: Theme.sectionGap
    readonly property int listWidth: Math.round(Math.min(420, Math.max(300, (width - gap) * 0.33)))
    readonly property int itemHeight: 76
    readonly property alias detail: detailPanel       // 測試用
    property string openedView: ""                    // pageOpened 時的名字：登出後 vm 是 null，關頁面時照樣登出

    function select(actionId) {
        if (vm && actionId !== "")
            vm.select(actionId)
    }

    // 還沒選（或選到的不在了）就選第一項
    function ensureSelection() {
        if (!vm || listState !== "ready" || vm.selectedId !== "")
            return
        const first = vm.firstActionId()
        if (first !== "")
            select(first)
    }

    Component.onCompleted: {
        if (!vm)
            return
        openedView = admin ? "coaching" : "actions"
        vm.pageOpened(openedView)
        settle.start()
    }
    Component.onDestruction: {
        if (vm && openedView !== "")
            vm.pageClosed(openedView)
    }
    onListStateChanged: settle.restart()

    // 等這一輪的清單更新完再選
    Timer {
        id: settle
        interval: 0
        onTriggered: page.ensureSelection()
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

    // 指標的一格：標籤、大字、說明
    component Metric: ColumnLayout {
        id: metric
        property string label: ""
        property string value: ""
        property string note: ""
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        spacing: 4
        Accessible.role: Accessible.StaticText
        Accessible.name: qsTr("%1: %2, %3").arg(label).arg(value === "—" ? qsTr("no data") : value).arg(note)

        Text {
            Layout.fillWidth: true
            text: metric.label
            color: Theme.textSecondary
            font.pixelSize: Theme.fontCaption
            elide: Text.ElideRight
            Accessible.ignored: true
        }
        Text {
            objectName: "metricValue"
            Layout.fillWidth: true
            text: metric.value
            color: Theme.textTitle
            font.pixelSize: Theme.fontHeadline
            font.weight: Font.Bold
            font.features: { "tnum": 1 }
            Accessible.ignored: true
        }
        Text {
            objectName: "metricNote"
            Layout.fillWidth: true
            text: metric.note
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
            elide: Text.ElideRight
            Accessible.ignored: true
        }
    }

    // 清單裡的一項：整塊可以點（76 高），Tab、Space、Return 都能按
    component ActionItem: ItemDelegate {
        id: item

        required property int index
        required property string actionId
        required property string goal
        required property string metaText
        required property string chipState
        required property bool replied
        required property bool unread
        readonly property bool selected: page.vm !== null && page.vm.selectedId === actionId
        readonly property string flagText: replied ? qsTr("Staff replied") : unread ? qsTr("New") : ""

        objectName: "actionItem"
        width: ListView.view ? ListView.view.width : 0
        height: page.itemHeight
        padding: 12
        focusPolicy: Qt.StrongFocus
        text: goal
        highlighted: selected
        Accessible.name: goal
        Accessible.description: [chip.label, metaText, flagText].filter(part => part !== "").join(", ")
        Accessible.selectable: true
        Accessible.selected: selected
        Keys.onReturnPressed: click()
        Keys.onEnterPressed: click()
        onClicked: page.select(actionId)

        contentItem: RowLayout {
            spacing: 12

            Image {
                Layout.alignment: Qt.AlignVCenter
                Layout.preferredWidth: Theme.iconNav
                Layout.preferredHeight: Theme.iconNav
                sourceSize: Qt.size(Theme.iconNav, Theme.iconNav)
                source: item.selected ? "../components/icons/target_chromeIcon.svg"
                                      : "../components/icons/target_textFaint.svg"
                Accessible.ignored: true
            }

            Column {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                spacing: 6

                // 第一行：目標＋（有的話）Staff replied、New；放在這一行，第二行的期限才不會被擠掉
                RowLayout {
                    width: parent.width
                    spacing: 8

                    Text {
                        objectName: "actionGoal"
                        Layout.fillWidth: true
                        text: item.goal
                        color: Theme.textTitle
                        font.pixelSize: Theme.fontInput
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                        Accessible.ignored: true
                    }
                    Rectangle {     // Staff replied、New：資訊色的小標籤，旁邊一定有字
                        objectName: "actionFlag"
                        visible: item.flagText !== ""
                        implicitWidth: flag.implicitWidth + 12
                        implicitHeight: 20
                        radius: 10
                        color: Theme.infoTint
                        Text {
                            id: flag
                            anchors.centerIn: parent
                            text: item.flagText
                            color: Theme.infoOnDark
                            font.pixelSize: Theme.fontLabel
                            font.weight: Font.DemiBold
                        }
                    }
                }

                RowLayout {
                    width: parent.width
                    spacing: 8

                    CoachingChip {
                        id: chip
                        actionState: item.chipState
                    }
                    Text {
                        objectName: "actionMeta"
                        Layout.fillWidth: true
                        text: item.metaText
                        color: item.chipState === "overdue" ? Theme.warningOnDark : Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                        font.features: { "tnum": 1 }
                        elide: Text.ElideRight
                        Accessible.ignored: true
                    }
                }
            }
        }

        background: Rectangle {
            radius: 12
            color: item.selected ? Theme.tileSelected : item.hovered || item.down ? Theme.tile : "transparent"
            border.width: item.selected ? 1 : 0
            border.color: Theme.actionSecondaryBorder

            Rectangle {     // 鍵盤焦點框（DS-30）：2 px、往外 2 px
                visible: item.visualFocus
                anchors.fill: parent
                anchors.margins: -4
                radius: parent.radius + 4
                color: "transparent"
                border.color: Theme.focusOnDark
                border.width: 2
            }
        }
    }

    Wait { id: listWait; active: page.listState === "loading" || page.listState === "idle" }
    Wait { id: detailWait; active: page.vm !== null && page.vm.detailState === "loading" }

    // ── 第一次載入：過了 busyDelay 才說在載入（不轉圈，DS-25）
    Text {
        objectName: "coachingLoading"
        anchors.horizontalCenter: parent.horizontalCenter
        y: Theme.space4XL
        visible: listWait.elapsed && (page.listState === "loading" || page.listState === "idle")
        text: qsTr("Loading actions…")
        color: Theme.textMuted
        font.pixelSize: Theme.fontCaption
    }

    // ── 清單查不到
    Card {
        objectName: "coachingErrorCard"
        width: parent.width
        visible: page.listState === "error"
        topPadding: Theme.space4XL
        bottomPadding: Theme.space4XL

        InlineError {
            objectName: "coachingError"
            width: parent.width
            title: qsTr("Couldn't load actions")
            detail: page.vm ? page.vm.errorDetail : ""
            technicalDetail: page.vm ? page.vm.errorTechnical : ""
            failureCount: page.vm ? page.vm.failureCount : 0
            busy: page.listState === "loading"
            onRetry: {
                if (page.vm)
                    page.vm.refresh()
            }
        }
    }

    // ── 還沒有任何行動
    Card {
        objectName: "coachingEmptyCard"
        width: parent.width
        visible: page.listState === "empty"
        topPadding: Theme.space4XL
        bottomPadding: Theme.space4XL

        EmptyState {
            objectName: "coachingEmpty"
            width: parent.width
            visible: page.listState === "empty"
            iconSource: Qt.resolvedUrl("../components/icons/target_iconNeutral.svg")
            title: page.admin ? qsTr("No coaching actions yet") : qsTr("No actions yet")
            description: page.admin ? qsTr("Open a service from Services, watch it, then use New Goal on its page.")
                                    : qsTr("When an admin sets an improvement goal for you, it appears here.")
        }
    }

    // ── 指標、清單和明細。內容比視窗高時整頁捲動
    Flickable {
        id: scroller
        objectName: "coachingScroller"
        anchors.fill: parent
        visible: page.listState === "ready"
        contentWidth: width
        contentHeight: column.implicitHeight
        // 最後的內容不會被右下角的通知蓋住（DS-21）
        bottomMargin: typeof toastHost !== "undefined" && toastHost ? toastHost.occupiedHeight : 0
        boundsBehavior: Flickable.StopAtBounds
        clip: true
        ScrollBar.vertical: ScrollBar {}

        Column {
            id: column
            width: scroller.width
            spacing: page.gap

            // 管理員：兩個指標（PLAN1 W2 的完成條件）和進行中的數量，跟著員工的篩選
            Card {
                objectName: "coachingMetrics"
                width: parent.width
                visible: page.admin
                topPadding: 18
                bottomPadding: 18
                Accessible.role: Accessible.Grouping
                Accessible.name: qsTr("Coaching results")

                RowLayout {
                    width: parent ? parent.width : 0
                    spacing: Theme.spaceXL

                    Metric {
                        objectName: "completedMetric"
                        label: qsTr("Completed with follow-up")
                        value: page.vm ? page.vm.completedShare : ""
                        note: page.vm ? page.vm.completedText : ""
                    }
                    Rectangle { Layout.fillHeight: true; implicitWidth: 1; color: Theme.cardBorder }
                    Metric {
                        objectName: "reviewTimeMetric"
                        label: qsTr("Review time")
                        value: page.vm ? page.vm.reviewTimeText : ""
                        note: page.vm ? page.vm.reviewTimeNote : ""
                    }
                    Rectangle { Layout.fillHeight: true; implicitWidth: 1; color: Theme.cardBorder }
                    Metric {
                        objectName: "openMetric"
                        label: qsTr("Open")
                        value: page.vm ? page.vm.openCountText : ""
                        note: page.vm ? page.vm.openNote : ""
                    }
                }
            }

            Item {
                width: parent.width
                height: Math.max(listCard.height, detailCard.height)

                Card {
                    id: listCard
                    objectName: "actionListCard"
                    width: page.listWidth
                    topPadding: Theme.spaceL
                    bottomPadding: Theme.spaceL
                    leftPadding: Theme.spaceM
                    rightPadding: Theme.spaceM

                    ColumnLayout {
                        width: parent ? parent.width : 0
                        spacing: 8

                        Text {
                            id: countHeading
                            objectName: "actionCount"
                            Layout.leftMargin: 12
                            Layout.bottomMargin: 4
                            Layout.fillWidth: true
                            text: page.vm ? page.vm.countText : ""
                            color: Theme.textTitle
                            font.pixelSize: Theme.fontBody
                            font.weight: Font.Bold
                            elide: Text.ElideRight
                            Accessible.role: Accessible.Heading
                            Accessible.name: text
                        }

                        // 管理員的篩選：狀態、期限、員工。清單卡窄（1024 寬的視窗）時一行一個，字才不會被截掉
                        GridLayout {
                            id: filterGrid
                            objectName: "coachingFilters"
                            Layout.fillWidth: true
                            Layout.leftMargin: 4
                            Layout.rightMargin: 4
                            Layout.bottomMargin: 4
                            visible: page.admin
                            columns: listCard.availableWidth >= 340 ? 2 : 1
                            columnSpacing: 8
                            rowSpacing: 8

                            HomeSelect {
                                objectName: "statusFilter"
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                options: page.vm ? page.vm.statusOptions : []
                                value: page.vm ? page.vm.filters.status : "all"
                                Accessible.name: qsTr("Status")
                                onPicked: (value) => page.vm.setFilter("status", value)
                            }
                            HomeSelect {
                                objectName: "dueFilter"
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                options: page.vm ? page.vm.dueOptions : []
                                value: page.vm ? page.vm.filters.due : "any"
                                Accessible.name: qsTr("Due date")
                                onPicked: (value) => page.vm.setFilter("due", value)
                            }
                            HomeSelect {
                                objectName: "staffFilter"
                                Layout.fillWidth: true
                                Layout.columnSpan: filterGrid.columns
                                popupMaxHeight: 320
                                options: page.vm ? page.vm.staffOptions : []
                                value: page.vm ? page.vm.filters.staff : ""
                                Accessible.name: qsTr("Staff")
                                onPicked: (value) => page.vm.setFilter("staff", value)
                            }
                        }

                        // 篩選之後一項都沒有
                        Text {
                            objectName: "noMatches"
                            Layout.fillWidth: true
                            Layout.margins: 12
                            visible: page.vm !== null && page.vm.shownCount === 0
                            text: qsTr("No actions match these filters.")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontCaption
                            wrapMode: Text.Wrap
                        }

                        // 很多項時，清單在卡片裡捲動，卡片不比明細高
                        ListView {
                            id: actionList
                            objectName: "actionList"
                            Layout.fillWidth: true
                            // 卡片裡清單以外的高度：標題（下面 4＋間距 8）、篩選（同上）
                            Layout.preferredHeight: Math.min(contentHeight, Math.max(page.itemHeight * 5 + 8 * 4,
                                detailCard.height - listCard.topPadding - listCard.bottomPadding
                                - countHeading.height - 12 - (filterGrid.visible ? filterGrid.height + 12 : 0)))
                            clip: true
                            spacing: 8
                            interactive: contentHeight > height
                            model: page.vm ? page.vm.listModel : null
                            boundsBehavior: Flickable.StopAtBounds
                            ScrollBar.vertical: ScrollBar { policy: actionList.interactive ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff }
                            Accessible.role: Accessible.List
                            Accessible.name: page.admin ? qsTr("Coaching actions") : qsTr("My actions")
                            delegate: ActionItem {}
                        }
                    }
                }

                Card {
                    id: detailCard
                    objectName: "actionDetailCard"
                    x: listCard.width + page.gap
                    width: parent.width - x
                    topPadding: Theme.spaceL
                    bottomPadding: Theme.spaceXL
                    leftPadding: Theme.spaceXL
                    rightPadding: Theme.spaceXL

                    Item {
                        width: parent ? parent.width : 0
                        implicitHeight: Math.max(detailPanel.visible ? detailPanel.implicitHeight : 0,
                                                 detailStates.implicitHeight)

                        CoachingDetail {
                            id: detailPanel
                            objectName: "coachingDetail"
                            width: parent.width
                            visible: page.vm !== null && page.vm.detailState === "ready"
                            vm: page.vm
                            admin: page.admin
                        }

                        Column {
                            id: detailStates
                            width: parent.width
                            topPadding: Theme.space3XL
                            bottomPadding: Theme.space3XL
                            visible: !detailPanel.visible

                            Text {
                                objectName: "detailLoading"
                                anchors.horizontalCenter: parent.horizontalCenter
                                visible: detailWait.elapsed
                                text: qsTr("Loading this action…")
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontCaption
                            }
                            EmptyState {
                                objectName: "detailMissing"
                                width: parent.width
                                visible: page.vm !== null && (page.vm.detailState === "missing"
                                                              || page.vm.detailState === "idle")
                                iconSource: Qt.resolvedUrl("../components/icons/target_iconNeutral.svg")
                                title: page.vm && page.vm.detailState === "missing" ? qsTr("This action isn't available")
                                                                                   : qsTr("Choose an action")
                                description: page.vm && page.vm.detailState === "missing"
                                             ? qsTr("It may have been removed, or it isn't yours.")
                                             : qsTr("Its goal, comment, services, and history appear here.")
                            }
                            InlineError {
                                objectName: "detailError"
                                width: parent.width
                                visible: page.vm !== null && page.vm.detailState === "error"
                                title: qsTr("Couldn't load this action")
                                detail: page.vm ? page.vm.detailError : ""
                                technicalDetail: page.vm ? page.vm.detailTechnical : ""
                                onRetry: {
                                    if (page.vm)
                                        page.vm.select(page.vm.selectedId)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
