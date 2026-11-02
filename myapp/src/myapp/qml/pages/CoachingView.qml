pragma ComponentBehavior: Bound
import QtQuick
// 自訂了清單項目、分頁的 background／contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 員工輔導（PLAN1 W2；T47 依設計稿「Coaching, redesigned」改版）：管理員的 Coaching（admin_coaching_page.qml）和服務人員的
// My Actions（server_actions_page.qml）都是這一頁。頁面標題和說明在外殼的頁首，這裡不畫。
// - 資料在 coachingModel（myapp/coaching.py）：打開時讀一次（pageOpened），不輪詢；寫入之後由它在同一個工作裡重讀。
//   服務人員只看得到自己的目標（sp_coaching_list 決定），管理員看得到全部。
// - 管理員：最上面一行是兩個指標（有後續證據的完成比例、回顧時間的中位數）和進行中、過期的數量。清單分
//   Open · n、Completed · n（有撤回的才多 Cancelled · n）；舊版的狀態、期限、員工三個篩選拿掉了（T47 第 4 節）。
//   一列：頭像、員工、期限（過期是紅字）、一行目標、下一步的標籤（Pick a Follow-up、Waiting for a Later Service、
//   Ready to Review、Overdue、Completed）。
// - 服務人員：「2 goals」，一列：目標、標籤（New、In Progress、Completed）、期限或完成的日子。
// - 右邊是選到的那一項（CoachingDetail.qml）；一進來選第一項。內容比視窗高時整頁捲動，清單很長時在卡片裡捲。
Item {
    id: page

    readonly property var vm: typeof coachingModel !== "undefined" ? coachingModel : null
    readonly property bool admin: vm !== null && vm.isAdmin
    readonly property string listState: vm ? vm.listState : "idle"
    readonly property int gap: Theme.spaceL
    readonly property int listWidth: Math.round(Math.min(380, Math.max(300, (width - gap) * 0.3)))
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

    // 最上面一行的一格：大字、說明、（淡色的）補充
    component Metric: Row {
        id: metric
        property string value: ""
        property string label: ""
        property string note: ""
        property color noteColor: Theme.textFaint
        spacing: 10
        Accessible.role: Accessible.StaticText
        Accessible.name: [value === "—" ? qsTr("No data") : value, label, note].join(" ")

        Text {
            id: metricValue
            objectName: "metricValue"
            text: metric.value
            color: Theme.textTitle
            font.pixelSize: Theme.fontTitle
            font.weight: Font.Bold
            font.features: { "tnum": 1 }
            Accessible.ignored: true
        }
        Text {
            anchors.baseline: metricValue.baseline
            text: metric.label
            color: Theme.textSecondary
            font.pixelSize: Theme.fontCaption
            Accessible.ignored: true
        }
        Text {
            objectName: "metricNote"
            anchors.baseline: metricValue.baseline
            text: metric.note
            color: metric.noteColor
            font.pixelSize: Theme.fontCaption
            font.features: { "tnum": 1 }
            Accessible.ignored: true
        }
    }

    // 管理員清單的分頁：Open · 3、Completed · 2（設計稿的分段切換）
    component Segment: Button {
        id: segment
        required property string value
        required property int count
        readonly property bool selected: page.vm !== null && page.vm.tab === value
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        implicitHeight: Theme.controlHeight
        focusPolicy: Qt.StrongFocus
        Accessible.role: Accessible.PageTab
        Accessible.name: text
        Accessible.selectable: true
        Accessible.selected: selected
        Keys.onReturnPressed: click()
        Keys.onEnterPressed: click()
        onClicked: {
            if (page.vm)
                page.vm.setTab(value)
        }

        contentItem: Text {
            text: segment.text
            color: segment.selected ? Theme.segmentSelectedText : Theme.segmentIdleText
            font.pixelSize: Theme.fontCaption
            font.weight: Font.DemiBold
            font.features: { "tnum": 1 }
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 12
            color: segment.selected ? Theme.segmentSelected : segment.hovered ? Theme.actionSecondaryHover : "transparent"
            Rectangle {     // 鍵盤焦點框（DS-30）
                visible: segment.visualFocus
                anchors.fill: parent
                anchors.margins: 2
                radius: 10
                color: "transparent"
                border.color: segment.selected ? Theme.segmentSelectedText : Theme.focusOnDark
                border.width: 2
            }
        }
    }

    // 清單裡的一項：整塊可以點，Tab、Space、Return 都能按
    component ActionItem: ItemDelegate {
        id: item

        required property int index
        required property string actionId
        required property string goal
        required property string staffName
        required property string initials
        required property string dueText
        required property bool dueAlert
        required property string chipState
        readonly property bool selected: page.vm !== null && page.vm.selectedId === actionId

        objectName: "actionItem"
        width: ListView.view ? ListView.view.width : 0
        padding: 12
        focusPolicy: Qt.StrongFocus
        text: goal
        highlighted: selected
        Accessible.name: page.admin ? qsTr("%1: %2").arg(staffName).arg(goal) : goal
        Accessible.description: [chip.label, dueText].filter(part => part !== "").join(", ")
        Accessible.selectable: true
        Accessible.selected: selected
        Keys.onReturnPressed: click()
        Keys.onEnterPressed: click()
        onClicked: page.select(actionId)

        contentItem: RowLayout {
            spacing: 12

            CoachingAvatar {
                Layout.alignment: Qt.AlignTop
                visible: page.admin
                initials: item.initials
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: page.admin ? 4 : 6

                // 管理員：員工、期限（過期是紅字）
                RowLayout {
                    Layout.fillWidth: true
                    visible: page.admin
                    spacing: 8

                    Text {
                        objectName: "actionStaff"
                        Layout.fillWidth: true
                        text: item.staffName
                        color: Theme.textTitle
                        font.pixelSize: Theme.fontInput
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                        Accessible.ignored: true
                    }
                    Text {
                        objectName: "actionDue"
                        text: item.dueText
                        color: item.dueAlert ? Theme.errorOnDark : Theme.textFaint
                        font.pixelSize: Theme.fontLabel
                        font.features: { "tnum": 1 }
                        Accessible.ignored: true
                    }
                }

                Text {
                    objectName: "actionGoal"
                    Layout.fillWidth: true
                    text: item.goal
                    color: page.admin ? Theme.textSecondary : Theme.textTitle
                    font.pixelSize: page.admin ? Theme.fontCaption : Theme.fontInput
                    font.weight: page.admin ? Font.Normal : Font.DemiBold
                    wrapMode: page.admin ? Text.NoWrap : Text.Wrap
                    maximumLineCount: page.admin ? 1 : 2
                    elide: Text.ElideRight
                    Accessible.ignored: true
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    CoachingChip {
                        id: chip
                        actionState: item.chipState
                    }
                    Text {
                        objectName: "actionMeta"
                        Layout.fillWidth: true
                        visible: !page.admin
                        text: item.dueText
                        color: item.dueAlert ? Theme.errorOnDark : Theme.textFaint
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
        text: qsTr("Loading goals…")
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
            title: qsTr("Couldn't load goals")
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

    // ── 還沒有任何目標
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
            title: page.admin ? qsTr("No coaching goals yet") : qsTr("No goals yet")
            description: page.admin ? qsTr("Open a service from Services, watch it, then set a goal beside the video.")
                                    : qsTr("When an admin sets a goal for you, it appears here.")
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

            // 管理員：一行兩個指標（PLAN1 W2 的完成條件）和進行中、過期的數量
            Card {
                objectName: "coachingResults"
                width: parent.width
                visible: page.admin
                topPadding: 14
                bottomPadding: 14
                leftPadding: Theme.spaceXL
                rightPadding: Theme.spaceXL
                Accessible.role: Accessible.Grouping
                Accessible.name: qsTr("Coaching results")

                Flow {
                    width: parent ? parent.width : 0
                    spacing: 36

                    Metric {
                        objectName: "completedMetric"
                        value: page.vm ? page.vm.completedShare : ""
                        label: qsTr("completed with a follow-up")
                        note: page.vm && page.vm.completedOf !== "" ? qsTr("· %1").arg(page.vm.completedOf) : ""
                    }
                    Metric {
                        objectName: "reviewTimeMetric"
                        value: page.vm ? page.vm.reviewTimeText : ""
                        label: qsTr("to complete")
                        note: page.vm && page.vm.completedCount > 0 ? qsTr("· median") : qsTr("· none completed yet")
                    }
                    Metric {
                        objectName: "openMetric"
                        value: page.vm ? page.vm.openCountText : ""
                        label: qsTr("open")
                        note: page.vm && page.vm.overdueCount > 0 ? qsTr("· %1 overdue").arg(page.vm.overdueCount)
                                                                  : qsTr("· none overdue")
                        noteColor: page.vm && page.vm.overdueCount > 0 ? Theme.errorOnDark : Theme.textFaint
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
                    topPadding: Theme.spaceM
                    bottomPadding: Theme.spaceM
                    leftPadding: 12
                    rightPadding: 12

                    ColumnLayout {
                        width: parent ? parent.width : 0
                        spacing: 8

                        // 管理員：Open · 3、Completed · 2（有撤回的才多 Cancelled · 1）
                        Rectangle {
                            id: segments
                            objectName: "coachingTabs"
                            Layout.fillWidth: true
                            Layout.leftMargin: 4
                            Layout.rightMargin: 4
                            Layout.bottomMargin: 6
                            visible: page.admin
                            implicitHeight: Theme.controlHeight + 2
                            radius: 13
                            color: Theme.segmentBg
                            border.width: 1
                            border.color: Theme.cardBorder
                            Accessible.role: Accessible.PageTabList
                            Accessible.name: qsTr("Goal status")

                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 1
                                spacing: 0

                                Segment {
                                    objectName: "openTab"
                                    value: "open"
                                    count: page.vm ? page.vm.openCount : 0
                                    text: qsTr("Open · %1").arg(count)
                                }
                                Segment {
                                    objectName: "completedTab"
                                    value: "completed"
                                    count: page.vm ? page.vm.completedCount : 0
                                    text: qsTr("Completed · %1").arg(count)
                                }
                                Segment {
                                    objectName: "cancelledTab"
                                    visible: count > 0
                                    value: "cancelled"
                                    count: page.vm ? page.vm.cancelledCount : 0
                                    text: qsTr("Cancelled · %1").arg(count)
                                }
                            }
                        }

                        // 服務人員：「2 goals」
                        Text {
                            id: countHeading
                            objectName: "actionCount"
                            Layout.leftMargin: 8
                            Layout.bottomMargin: 4
                            Layout.fillWidth: true
                            visible: !page.admin
                            text: page.vm ? page.vm.countText : ""
                            color: Theme.textTitle
                            font.pixelSize: Theme.fontBody
                            font.weight: Font.Bold
                            elide: Text.ElideRight
                            Accessible.role: Accessible.Heading
                            Accessible.name: text
                        }

                        // 這個分頁一項都沒有
                        Text {
                            objectName: "noMatches"
                            Layout.fillWidth: true
                            Layout.margins: 12
                            visible: page.vm !== null && page.vm.shownCount === 0
                            text: page.vm && page.vm.tab === "completed" ? qsTr("No completed goals yet.")
                                : page.vm && page.vm.tab === "cancelled" ? qsTr("No cancelled goals.")
                                : qsTr("No open goals.")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontCaption
                            wrapMode: Text.Wrap
                        }

                        // 很多項時，清單在卡片裡捲動，卡片不比明細高
                        ListView {
                            id: actionList
                            objectName: "actionList"
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.min(contentHeight, Math.max(480,
                                detailCard.height - listCard.topPadding - listCard.bottomPadding
                                - (segments.visible ? segments.height + 14 : countHeading.height + 12)))
                            clip: true
                            spacing: 6
                            interactive: contentHeight > height
                            model: page.vm ? page.vm.listModel : null
                            boundsBehavior: Flickable.StopAtBounds
                            ScrollBar.vertical: ScrollBar { policy: actionList.interactive ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff }
                            Accessible.role: Accessible.List
                            Accessible.name: page.admin ? qsTr("Coaching goals") : qsTr("My goals")
                            delegate: ActionItem {}
                        }
                    }
                }

                Card {
                    id: detailCard
                    objectName: "actionDetailCard"
                    x: listCard.width + page.gap
                    width: parent.width - x
                    topPadding: 22
                    bottomPadding: Theme.spaceXL
                    leftPadding: Theme.cardPaddingH
                    rightPadding: Theme.cardPaddingH

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
                                text: qsTr("Loading this goal…")
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontCaption
                            }
                            EmptyState {
                                objectName: "detailMissing"
                                width: parent.width
                                visible: page.vm !== null && (page.vm.detailState === "missing"
                                                              || page.vm.detailState === "idle")
                                iconSource: Qt.resolvedUrl("../components/icons/target_iconNeutral.svg")
                                title: page.vm && page.vm.detailState === "missing" ? qsTr("This goal isn't available")
                                     : page.vm && page.vm.shownCount === 0 ? qsTr("Nothing in this list")
                                     : qsTr("Choose a goal")
                                description: page.vm && page.vm.detailState === "missing"
                                             ? qsTr("It may have been removed, or it isn't yours.")
                                             : page.vm && page.vm.shownCount === 0 ? qsTr("Goals appear here when they reach this status.")
                                             : qsTr("Its next step, moments, and conversation appear here.")
                            }
                            InlineError {
                                objectName: "detailError"
                                width: parent.width
                                visible: page.vm !== null && page.vm.detailState === "error"
                                title: qsTr("Couldn't load this goal")
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
