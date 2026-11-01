pragma ComponentBehavior: Bound
import QtQuick
// 自訂了捲軸的外觀，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 管理員首頁（new-UIUX PLAN 第 5 節；設計稿 AdminHome、States）。和服務人員首頁同一個版面，資料是全部員工的
// homeDashboard（myapp/home_dashboard.py，T28）：Overview、Staff Ranking、Score Trend 三張卡片，各自 1 個查詢。
// - 頁面建立時查一次（launch 時、側邊欄按 Home 時；換到別頁再回來才會再查，ENG-06），舊的數字留著直到新的到。
//   名字和等級由 launch() 查：首頁總共 1＋3＝4 個往返。
// - 頁首的 ADAM 字標、Add Account、名字由外殼畫（U0 的 PageHeader），這裡不畫。
// - 管理員端沒有分析頁：服務方塊只是數字，不能點。排名的卡片列出每一位員工（放不下就在卡片裡橫向捲動），
//   所以設計稿的 ↗（完整的排名）沒有東西可以打開，不放（T28）。
// - 舊的 Daily／Weekly／Ranking／Trends 分頁和分數環拿掉了：月份的趨勢不再有（PLAN 5-1：和服務人員端一樣是 14 天）。
Flickable {
    id: page
    objectName: "homePage"

    readonly property var dashboard: typeof homeDashboard !== "undefined" ? homeDashboard : null

    contentWidth: width
    contentHeight: column.implicitHeight
    boundsBehavior: Flickable.StopAtBounds
    clip: true
    ScrollBar.vertical: ScrollBar {}

    function scoreText(value) {
        return Theme.hasScore(value) ? value.toFixed(1) : qsTr("no data")
    }

    function countText(count) {
        return count === 1 ? qsTr("1 service") : qsTr("%1 services").arg(count)
    }

    // 排名的每一組：選的那一方是彩色，另一方是灰色；由高到低（換 Customer／Staff 不再查詢，T28）
    function rankingBars(section, side) {
        if (!section)
            return []
        const rows = side === "customer" ? section.byCustomer : section.byStaff
        const other = side === "customer" ? "staff" : "customer"
        return rows.map(row => ({
            value: row[side],
            compare: row[other],
            label: row.name,
            sublabel: countText(row.count),
            tooltip: qsTr("%1 · %2\nCustomer %3 · Staff %4").arg(row.name).arg(countText(row.count))
                .arg(scoreText(row.customer)).arg(scoreText(row.staff)),
            name: qsTr("%1, %2, customer %3, staff %4").arg(row.name).arg(countText(row.count))
                .arg(scoreText(row.customer)).arg(scoreText(row.staff))
        }))
    }

    Component.onCompleted: {
        if (dashboard)
            dashboard.refresh()     // 三張卡片各 1 個往返；已經有的數字先照舊顯示
    }

    ColumnLayout {
        id: column
        width: page.width
        spacing: Theme.sectionGap

        OverviewCard {
            id: overview
            Layout.fillWidth: true
            section: page.dashboard ? page.dashboard.overview : null
            allStaff: true
            onRetry: overview.section.refresh()
        }

        GridLayout {
            id: lower
            Layout.fillWidth: true
            columns: page.width >= 520 + 400 + Theme.sectionGap ? 2 : 1
            columnSpacing: Theme.sectionGap
            rowSpacing: Theme.sectionGap

            readonly property real leftWidth: columns === 2 ? (page.width - Theme.sectionGap) * 3 / 5 : page.width

            // ── Staff Ranking：30 天內每位員工的平均，由高到低
            Card {
                id: rankingCard
                objectName: "rankingCard"
                Layout.preferredWidth: lower.leftWidth
                Layout.fillWidth: lower.columns === 1
                Layout.fillHeight: true
                Accessible.role: Accessible.Grouping
                Accessible.name: qsTr("Staff Ranking")

                readonly property var section: page.dashboard ? page.dashboard.ranking : null
                readonly property string sectionState: section ? section.state : "loading"
                readonly property var bars: page.rankingBars(section, overview.side)

                ColumnLayout {
                    width: rankingCard.availableWidth
                    spacing: 18

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4

                        Text {
                            text: qsTr("Staff Ranking")
                            color: Theme.textTitle
                            font.pixelSize: Theme.fontTitleSmall
                            font.weight: Font.Bold
                            Accessible.role: Accessible.Heading
                            Accessible.name: text
                        }

                        Text {
                            objectName: "rankingSubtitle"
                            Layout.fillWidth: true
                            text: overview.side === "customer"
                                  ? qsTr("Customer overall score by staff · last 30 days")
                                  : qsTr("Staff overall score by staff · last 30 days")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontCaption
                            elide: Text.ElideRight
                        }
                    }

                    Row {       // 載入中：灰色的長條
                        objectName: "rankingSkeleton"
                        visible: rankingCard.sectionState === "loading"
                        Layout.fillWidth: true
                        Layout.preferredHeight: 300
                        spacing: 12
                        leftPadding: 32
                        Accessible.ignored: true

                        Repeater {
                            model: [88, 82, 78, 74, 70, 64, 58]
                            Item {
                                required property int modelData
                                width: (rankingCard.availableWidth - 32 - 6 * 12) / 7
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
                        objectName: "rankingError"
                        visible: rankingCard.sectionState === "error"
                        Layout.fillWidth: true
                        Layout.topMargin: Theme.space3XL
                        Layout.bottomMargin: Theme.space3XL
                        title: qsTr("Couldn't load the ranking")
                        detail: rankingCard.section ? rankingCard.section.errorDetail : ""
                        technicalDetail: rankingCard.section ? rankingCard.section.errorTechnical : ""
                        failureCount: rankingCard.section ? rankingCard.section.failureCount : 0
                        onRetry: rankingCard.section.refresh()
                    }

                    EmptyState {
                        objectName: "rankingEmpty"
                        visible: rankingCard.sectionState === "empty"
                        Layout.fillWidth: true
                        Layout.topMargin: Theme.space3XL
                        Layout.bottomMargin: Theme.space3XL
                        title: qsTr("No analyzed services in the last 30 days")
                        description: qsTr("Staff appear here after their services are analyzed.")
                    }

                    ScoreBars {
                        objectName: "rankingBars"
                        visible: rankingCard.sectionState === "ready"
                        Layout.fillWidth: true
                        groupSpacing: 12
                        minGroupWidth: 72        // 放不下就在卡片裡橫向捲動：每一位員工都在
                        bars: rankingCard.bars
                        frontLabel: overview.side === "customer" ? qsTr("Customer score:") : qsTr("Staff score:")
                        backLabel: overview.side === "customer" ? qsTr("Staff score") : qsTr("Customer score")
                        summary: (overview.side === "customer"
                                  ? qsTr("Customer overall score by staff, highest first, %1 staff")
                                  : qsTr("Staff overall score by staff, highest first, %1 staff")).arg(rankingCard.bars.length)
                    }
                }
            }

            ScoreTrendCard {
                id: trend
                Layout.fillWidth: true
                Layout.fillHeight: true
                section: page.dashboard ? page.dashboard.trend : null
                allStaff: true
                onRetry: trend.section.refresh()
            }
        }
    }
}
