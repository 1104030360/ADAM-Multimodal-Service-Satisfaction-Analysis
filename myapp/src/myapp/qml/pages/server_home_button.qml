pragma ComponentBehavior: Bound
import QtQuick
// 自訂了捲軸、↗ 按鈕的外觀，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 服務人員首頁（new-UIUX PLAN 第 5 節；設計稿 Main、States）。資料是 homeDashboard（myapp/home_dashboard.py，T28）：
// Overview、Recent Performance、Score Trend 三張卡片，各自 1 個查詢，在 launch() 和側邊欄的 Home 時於背景重新查詢；
// 頁面建立時不查（換頁回來時舊的數字先顯示，新的到了再換）。
// - 頁首的 ADAM 字標、New Recording、名字由外殼畫（U0 的 PageHeader），這裡不畫。
// - 服務方塊打開分析頁的那個分頁；長條打開那一筆服務的明細；↗ 打開分析頁的 Completed。
// - 一張卡片讀不到只有那張顯示錯誤（PLAN 5-2）；Retry 只重查那一張，名字沒載入的話一起再載一次。
// - 比視窗高時整頁捲動（Flickable）。
Flickable {
    id: page
    objectName: "homePage"

    readonly property var dashboard: typeof homeDashboard !== "undefined" ? homeDashboard : null
    property var shell: null                // 外殼（setWindow）：換頁、打開明細

    contentWidth: width
    contentHeight: column.implicitHeight
    boundsBehavior: Flickable.StopAtBounds
    clip: true
    ScrollBar.vertical: ScrollBar {}

    function setWindow(window) {
        shell = window
    }

    // 服務方塊、↗：打開分析頁的那個分頁。清單在 launch 時不查（T28），先送出重新整理再換頁
    function openAnalysisTab(tab) {
        serverHome.onLoadAnalysis()
        const analysis = shell ? shell.showPageNow("analysis.qml") : null
        if (analysis && typeof analysis.showTab === "function")
            analysis.showTab(tab)
    }

    function openService(index) {
        const row = page.dashboard.recent.rows[index]
        if (shell && row)
            shell.openServiceDetail(row.serviceId, row.start)
    }

    function retry(section) {
        section.refresh()
        serverHome.reloadAccountName()
    }

    function scoreText(value) {
        return Theme.hasScore(value) ? value.toFixed(1) : qsTr("no data")
    }

    // 長條圖的每一組：選的那一方是彩色，另一方是灰色（PLAN 5-1）
    function recentBars(rows, side) {
        const other = side === "customer" ? "staff" : "customer"
        return rows.map(row => ({
            value: row[side],
            compare: row[other],
            label: row.timeLabel,
            sublabel: row.dayLabel,
            tooltip: qsTr("%1\n%2 · %3\nCustomer %4 · Staff %5").arg(row.serviceId).arg(row.dayLabel)
                .arg(row.timeLabel).arg(scoreText(row.customer)).arg(scoreText(row.staff)),
            name: qsTr("%1, %2 %3, customer %4, staff %5").arg(row.serviceId).arg(row.dayLabel)
                .arg(row.timeLabel).arg(scoreText(row.customer)).arg(scoreText(row.staff))
        }))
    }

    ColumnLayout {
        id: column
        width: page.width
        spacing: Theme.sectionGap

        OverviewCard {
            id: overview
            Layout.fillWidth: true
            section: page.dashboard ? page.dashboard.overview : null
            canRecord: true
            onOpenTab: (tab) => page.openAnalysisTab(tab)
            onNewRecording: page.shell.showPage("record_widget.qml")
            onRetry: page.retry(overview.section)
        }

        GridLayout {
            id: lower
            Layout.fillWidth: true
            columns: page.width >= 520 + 400 + Theme.sectionGap ? 2 : 1
            columnSpacing: Theme.sectionGap
            rowSpacing: Theme.sectionGap

            readonly property real leftWidth: columns === 2 ? (page.width - Theme.sectionGap) * 3 / 5 : page.width

            // ── Recent Performance：最近 8 筆分析完成的服務
            Card {
                id: recentCard
                objectName: "recentCard"
                Layout.preferredWidth: lower.leftWidth
                Layout.fillWidth: lower.columns === 1
                Layout.fillHeight: true
                Accessible.role: Accessible.Grouping
                Accessible.name: qsTr("Recent Performance")

                readonly property var section: page.dashboard ? page.dashboard.recent : null
                readonly property string sectionState: section ? section.state : "loading"
                readonly property var rows: section ? section.rows : []

                ColumnLayout {
                    width: recentCard.availableWidth
                    spacing: 18

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spaceM

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4

                            Text {
                                text: qsTr("Recent Performance")
                                color: Theme.textTitle
                                font.pixelSize: Theme.fontTitleSmall
                                font.weight: Font.Bold
                                Accessible.role: Accessible.Heading
                                Accessible.name: text
                            }

                            Text {
                                objectName: "recentSubtitle"
                                Layout.fillWidth: true
                                text: recentCard.rows.length === 1 ? qsTr("Overall score of the last analyzed service")
                                    : qsTr("Overall score of the last %1 analyzed services")
                                          .arg(recentCard.rows.length > 0 ? recentCard.rows.length : 8)
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontCaption
                                elide: Text.ElideRight
                            }
                        }

                        // ↗：完整的清單在分析頁的 Completed
                        Button {
                            id: openCompleted
                            objectName: "openCompletedButton"
                            Layout.alignment: Qt.AlignTop
                            implicitWidth: Theme.controlHeight
                            implicitHeight: Theme.controlHeight
                            padding: 0
                            focusPolicy: Qt.StrongFocus
                            Accessible.name: qsTr("Open in Analysis")
                            ToolTip.visible: hovered
                            ToolTip.text: Accessible.name
                            ToolTip.delay: Theme.busyDelay
                            onClicked: page.openAnalysisTab("completed")

                            contentItem: Image {
                                fillMode: Image.Pad
                                sourceSize: Qt.size(18, 18)
                                source: "../components/icons/arrow-up-right_iconNeutral.svg"
                            }

                            background: Rectangle {
                                radius: width / 2
                                color: openCompleted.down ? Theme.actionSecondaryPressed
                                     : openCompleted.hovered ? Theme.actionSecondaryHover : Theme.actionSecondaryBg
                                border.color: openCompleted.visualFocus ? Theme.focusOnDark : Theme.tileBorder
                                border.width: openCompleted.visualFocus ? 2 : 1
                            }
                        }
                    }

                    Row {       // 載入中：灰色的長條
                        objectName: "recentSkeleton"
                        visible: recentCard.sectionState === "loading"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 300
                        spacing: 14
                        leftPadding: 32
                        Accessible.ignored: true

                        Repeater {
                            model: [62, 78, 54, 70, 84, 58, 66, 74]
                            Item {
                                required property int modelData
                                width: (recentCard.availableWidth - 32 - 7 * 14) / 8
                                height: 260
                                Rectangle {
                                    anchors.bottom: parent.bottom
                                    width: parent.width * 0.6
                                    height: parent.height * parent.modelData / 100
                                    color: Theme.tile
                                    topLeftRadius: 8
                                    topRightRadius: 8
                                }
                            }
                        }
                    }

                    InlineError {
                        objectName: "recentError"
                        visible: recentCard.sectionState === "error"
                        Layout.fillWidth: true
                        Layout.topMargin: Theme.space3XL
                        Layout.bottomMargin: Theme.space3XL
                        title: qsTr("Couldn't load recent services")
                        detail: recentCard.section ? recentCard.section.errorDetail : ""
                        technicalDetail: recentCard.section ? recentCard.section.errorTechnical : ""
                        failureCount: recentCard.section ? recentCard.section.failureCount : 0
                        onRetry: page.retry(recentCard.section)
                    }

                    EmptyState {
                        objectName: "recentEmpty"
                        visible: recentCard.sectionState === "empty"
                        Layout.fillWidth: true
                        Layout.topMargin: Theme.space3XL
                        Layout.bottomMargin: Theme.space3XL
                        title: qsTr("No analyzed services yet")
                        description: qsTr("Send a recording for analysis. Its scores show up here when it's done.")
                    }

                    ScoreBars {
                        objectName: "recentBars"
                        visible: recentCard.sectionState === "ready"
                        Layout.fillWidth: true
                        interactive: true
                        bars: page.recentBars(recentCard.rows, overview.side)
                        frontLabel: overview.side === "customer" ? qsTr("Customer score:") : qsTr("Staff score:")
                        backLabel: overview.side === "customer" ? qsTr("Staff score") : qsTr("Customer score")
                        summary: (overview.side === "customer"
                                  ? qsTr("Customer overall score of the last %1 analyzed services")
                                  : qsTr("Staff overall score of the last %1 analyzed services")).arg(recentCard.rows.length)
                        onActivated: (index) => page.openService(index)
                    }
                }
            }

            ScoreTrendCard {
                id: trend
                Layout.fillWidth: true
                Layout.fillHeight: true
                section: page.dashboard ? page.dashboard.trend : null
                onRetry: page.retry(trend.section)
            }
        }
    }
}
