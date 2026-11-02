pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtCharts
import "../components"

// 首頁的 Score Trend 卡片（new-UIUX PLAN 5-1；設計稿 Main、AdminHome）。兩端共用，資料是 homeDashboard.trend。
// - 藍底的卡片（UD4）：近 14 天每天的平均，兩條線：顧客是白色實線，員工是淺藍虛線（不只靠顏色分）。
// - 沒有服務（或那天沒有這個分數）的日子不連過去，線在那裡斷開（PLAN 第 2 節「數字照實畫」）：
//   每一段連續的日子是一條 LineSeries，只有一天的畫一個點。Y 軸 50～100（設計稿），有低於 50 的分數才往下延伸。
// - 換分數（Overall、Text、Audio、Facial）不再查詢：資料已經都在（T28）。圖不做長出來的動畫（PLAN 第 2 節）。
// - 狀態：載入中是骨架；14 天都沒有分數是空狀態；讀不到是 InlineError。重新查詢時舊的線留著。
Card {
    id: card

    property var section: null              // homeDashboard.trend（TrendSection）
    property bool allStaff: false
    property string metric: "overall"
    property var lines: []                  // createSeries 建的線段：重畫時先拿掉
    property bool updatingShown: false

    signal retry()

    readonly property string sectionState: section ? section.state : "loading"
    readonly property bool ready: sectionState === "ready"
    readonly property var labels: section ? section.labels : []
    readonly property var customerValues: section ? section.series.customer[metric] : []
    readonly property var staffValues: section ? section.series.staff[metric] : []
    readonly property string metricLabel: metric === "overall" ? qsTr("Overall") : metric === "text" ? qsTr("Text")
                                        : metric === "audio" ? qsTr("Audio") : qsTr("Facial")
    readonly property real axisMin: {
        let low = 50
        for (const value of customerValues.concat(staffValues)) {
            if (Theme.hasScore(value) && value < low)
                low = Math.floor(value / 10) * 10
        }
        return low
    }
    readonly property var gaps: {       // 兩方都沒有分數的日子（第幾天）
        const days = []
        for (let i = 0; i < labels.length; ++i) {
            if (!Theme.hasScore(customerValues[i]) && !Theme.hasScore(staffValues[i]))
                days.push(i)
        }
        return days
    }

    fill: Theme.trendPanel
    borderColor: Theme.trendPanelBorder
    objectName: "trendCard"
    Accessible.role: Accessible.Grouping
    Accessible.name: qsTr("Score Trend")

    function latest(values, who) {
        for (let i = values.length - 1; i >= 0; --i) {
            if (Theme.hasScore(values[i]))
                return qsTr("%1 · %2 on %3").arg(who).arg(values[i].toFixed(1)).arg(labels[i])
        }
        return qsTr("%1 · no data").arg(who)
    }

    function gapText() {
        if (gaps.length === 0 || gaps.length === labels.length)
            return ""
        return gaps.length === 1 ? qsTr("%1 · no data").arg(labels[gaps[0]]) : qsTr("No data on %1 days").arg(gaps.length)
    }

    // 線重畫：拿掉舊的線段，每一段連續的日子建一條（ENG-19：每條線一次給完，不逐點 append）
    function redraw() {
        for (const line of lines)
            chart.removeSeries(line)
        const created = []
        for (const side of ["staff", "customer"]) {      // 顧客的線畫在上面
            const singles = []
            const runs = section ? section.runs(side, metric) : []
            for (const run of runs) {
                if (run.length === 1) {
                    singles.push(run[0])
                    continue
                }
                const line = chart.createSeries(ChartView.SeriesTypeLine, "", axisX, axisY)
                line.color = side === "customer" ? Theme.trendCustomerLine : Theme.trendStaffLine
                line.width = side === "customer" ? 2.5 : 2
                line.style = side === "customer" ? Qt.SolidLine : Qt.DashLine
                line.capStyle = Qt.RoundCap
                section.fillSeries(line, run)
                created.push(line)
            }
            if (section)
                section.fillSeries(side === "customer" ? customerDots : staffDots, singles)
        }
        lines = created
        floorLine.clear()
        floorLine.append(0, axisMin)
        floorLine.append(Math.max(1, labels.length - 1), axisMin)
    }

    onMetricChanged: redraw()
    onAxisMinChanged: redraw()
    Component.onCompleted: redraw()

    Connections {
        target: card.section
        function onTrendChanged() { card.redraw() }
        function onStateChanged() {
            if (!card.section.refreshing)
                card.updatingShown = false
        }
    }
    Timer {
        interval: Theme.busyDelay
        running: card.section !== null && card.section.refreshing
        onTriggered: card.updatingShown = true
    }

    ColumnLayout {
        width: card.availableWidth
        spacing: 18

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceM

            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: 4

                Text {
                    text: qsTr("Score Trend")
                    color: Theme.textTitle
                    font.pixelSize: Theme.fontTitleSmall
                    font.weight: Font.Bold
                    Accessible.role: Accessible.Heading
                    Accessible.name: text
                }

                Text {
                    objectName: "trendSubtitle"
                    Layout.fillWidth: true
                    text: {
                        const base = card.allStaff ? qsTr("%1 · all staff · last 14 days").arg(card.metricLabel)
                                                   : qsTr("%1 · daily average · last 14 days").arg(card.metricLabel)
                        return card.updatingShown ? qsTr("%1 · Updating…").arg(base) : base
                    }
                    color: Theme.trendPanelText
                    font.pixelSize: Theme.fontCaption
                    elide: Text.ElideRight
                }
            }

            HomeSelect {
                objectName: "metricBox"
                visible: card.ready
                Layout.alignment: Qt.AlignTop
                Layout.preferredWidth: 132
                fill: Theme.trendControl
                borderColor: Theme.trendControlBorder
                textColor: Theme.textTitle
                chevron: "../components/icons/chevron-down_textTitle.svg"
                Accessible.name: qsTr("Score")
                value: card.metric
                options: [{ value: "overall", text: qsTr("Overall") }, { value: "text", text: qsTr("Text") },
                          { value: "audio", text: qsTr("Audio") }, { value: "facial", text: qsTr("Facial") }]
                onPicked: (value) => card.metric = value
            }
        }

        Rectangle {     // 載入中：圖的骨架
            objectName: "trendSkeleton"
            visible: card.sectionState === "loading"
            Layout.fillWidth: true
            Layout.preferredHeight: 220
            radius: 8
            color: Theme.trendControl
            Accessible.ignored: true
        }

        InlineError {
            objectName: "trendError"
            visible: card.sectionState === "error"
            Layout.fillWidth: true
            Layout.topMargin: Theme.spaceM
            Layout.bottomMargin: Theme.spaceM
            title: qsTr("Couldn't load the trend")
            detail: card.section ? card.section.errorDetail : ""
            technicalDetail: card.section ? card.section.errorTechnical : ""
            failureCount: card.section ? card.section.failureCount : 0
            onRetry: card.retry()
        }

        EmptyState {
            objectName: "trendEmpty"
            visible: card.sectionState === "empty"
            Layout.fillWidth: true
            Layout.topMargin: Theme.spaceM
            Layout.bottomMargin: Theme.spaceM
            title: qsTr("No scores in the last 14 days")
            description: qsTr("The trend shows up after services are analyzed.")
        }

        Item {
            id: chartArea
            visible: card.ready
            Layout.fillWidth: true
            Layout.preferredHeight: 220

            ChartView {
                id: chart
                objectName: "trendChart"
                x: 32
                width: parent.width - 32
                height: parent.height
                antialiasing: true
                legend.visible: false
                backgroundColor: "transparent"
                plotAreaColor: "transparent"
                backgroundRoundness: 0
                margins.top: 0
                margins.bottom: 0
                margins.left: 0
                margins.right: 0
                // 圖表讀不出來給輔助技術：名稱寫哪一個分數、哪幾天，說明寫兩條線最後的分數和沒有資料的日子
                Accessible.role: Accessible.Chart
                Accessible.name: qsTr("%1 score, daily average from %2 to %3").arg(card.metricLabel)
                    .arg(card.labels.length ? card.labels[0] : "").arg(card.labels.length ? card.labels[card.labels.length - 1] : "")
                Accessible.description: [card.latest(card.customerValues, qsTr("Customer")),
                                         card.latest(card.staffValues, qsTr("Staff")), card.gapText()]
                                        .filter(text => text !== "").join(". ")

                ValueAxis {
                    id: axisX
                    min: 0
                    max: Math.max(1, card.labels.length - 1)
                    visible: false
                    gridVisible: false
                    lineVisible: false
                    labelsVisible: false
                }

                ValueAxis {
                    id: axisY
                    min: card.axisMin
                    max: 100
                    visible: false
                    gridVisible: false
                    lineVisible: false
                    labelsVisible: false
                }

                // 85、70 的虛線和底線（PLAN 第 4 節）
                LineSeries {
                    axisX: axisX
                    axisY: axisY
                    color: Theme.trendGuide
                    width: 1
                    style: Qt.DashLine
                    XYPoint { x: 0; y: 85 }
                    XYPoint { x: 13; y: 85 }
                }

                LineSeries {
                    axisX: axisX
                    axisY: axisY
                    color: Theme.trendGuide
                    width: 1
                    style: Qt.DashLine
                    XYPoint { x: 0; y: 70 }
                    XYPoint { x: 13; y: 70 }
                }

                LineSeries {
                    id: floorLine
                    axisX: axisX
                    axisY: axisY
                    color: Theme.trendGuide
                    width: 1
                }

                // 只有一天、前後都沒有資料的分數：畫一個點（線連不起來）
                ScatterSeries {
                    id: staffDots
                    objectName: "staffDots"
                    axisX: axisX
                    axisY: axisY
                    color: Theme.trendStaffLine
                    borderColor: Theme.trendStaffLine
                    markerSize: 7
                }

                ScatterSeries {
                    id: customerDots
                    objectName: "customerDots"
                    axisX: axisX
                    axisY: axisY
                    color: Theme.trendCustomerLine
                    borderColor: Theme.trendCustomerLine
                    markerSize: 7
                }
            }

            Repeater {      // 左邊的 85、70
                model: [85, 70]

                Text {
                    required property int modelData
                    y: chart.y + chart.plotArea.y + chart.plotArea.height * (100 - modelData) / (100 - card.axisMin)
                       - height / 2
                    text: modelData
                    color: Theme.trendPanelText
                    font.pixelSize: Theme.fontLabel
                    font.features: { "tnum": 1 }
                    Accessible.ignored: true
                }
            }
        }

        // 日期：第一天、沒有資料的日子、今天
        Item {
            visible: card.ready
            Layout.fillWidth: true
            Layout.leftMargin: 32
            implicitHeight: firstDay.implicitHeight
            Accessible.ignored: true

            Text {
                id: firstDay
                objectName: "trendFirstDay"
                text: card.labels.length ? card.labels[0] : ""
                color: Theme.trendPanelText
                font.pixelSize: Theme.fontLabel
            }

            Text {
                objectName: "trendGapNote"
                anchors.horizontalCenter: parent.horizontalCenter
                text: card.gapText()
                color: Theme.trendPanelText
                font.pixelSize: Theme.fontLabel
            }

            Text {
                objectName: "trendLastDay"
                anchors.right: parent.right
                text: card.labels.length ? card.labels[card.labels.length - 1] : ""
                color: Theme.trendPanelText
                font.pixelSize: Theme.fontLabel
            }
        }

        // 圖例：哪一條線是誰、最後一天的分數
        Flow {
            visible: card.ready
            Layout.fillWidth: true
            spacing: Theme.spaceL

            Row {
                spacing: 8

                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 22
                    height: 3
                    radius: 1.5
                    color: Theme.trendCustomerLine
                }

                Text {
                    objectName: "customerLegend"
                    text: card.latest(card.customerValues, qsTr("Customer"))
                    color: Theme.trendPanelText
                    font.pixelSize: Theme.fontLabel
                }
            }

            Row {
                spacing: 8

                Row {       // 虛線
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 3

                    Repeater {
                        model: 3
                        Rectangle { width: 5; height: 2; color: Theme.trendStaffLine }
                    }
                }

                Text {
                    objectName: "staffLegend"
                    text: card.latest(card.staffValues, qsTr("Staff"))
                    color: Theme.trendPanelText
                    font.pixelSize: Theme.fontLabel
                }
            }
        }
    }
}
