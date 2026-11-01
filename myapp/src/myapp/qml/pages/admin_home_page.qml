import QtQuick 2.15
import QtQuick.Controls 2.15
import QtCharts 2.15
import QtQuick.Layouts 1.15
import "../components"

// 管理員首頁：每日分數、每週分數、排名、趨勢。
// - 資料在 Python 的屬性裡（ENG-08）：頁面建立時每一項送一次載入（ENG-06：不再重複載入），在背景執行，畫面先出來。
// - 每個區塊分成載入中、沒有資料、錯誤（可以重試）、有資料四種畫面（DS-25～27）。
//   重新整理時舊資料留著、變淡；300 ms 內結束就什麼都不變，免得閃一下（DS-25、DS-33）。
// - 分頁是和服務人員首頁、分析頁共用的 SegmentTab：選取的是石墨灰（D2、DS-12），不再是 Material Green。
//   排名的四格是中性的灰底：紅綠只代表狀態（DS-12、DS-13），前 5 名、後 5 名由標題說明。
Page {
    id: root
    width: 1024
    height: 768
    title: qsTr("Home")     // 和側邊欄的名稱一樣（DS-51）；服務人員首頁也是

    background: Rectangle { color: Theme.contentBg }

    property int currentTab: 0

    readonly property color staffColor: Theme.textPrimary       // 趨勢圖的兩條線；勾選框旁邊的色塊就是圖例
    readonly property color customerColor: Theme.infoOnDark
    property var trendLines: []                                  // 目前畫在圖上的線段（ChartView.createSeries）

    // active 持續超過 interval 才成立（DS-25：300 ms 內什麼都不顯示，免得閃一下；超過 2 s 才加上文字）
    component Wait: Timer {
        property bool active: false
        property bool elapsed: false
        interval: Theme.busyDelay
        running: active && !elapsed
        onTriggered: elapsed = true
        onActiveChanged: if (!active) elapsed = false
    }

    // 狀態列：小圖示＋「Updating…」「Loading scores…」這類文字
    component StatusLine: Row {
        id: status
        property string text: ""
        spacing: 6
        visible: text !== ""

        Image {
            id: spinner
            anchors.verticalCenter: parent.verticalCenter
            width: 14
            height: 14
            sourceSize: Qt.size(14, 14)
            source: "../components/icons/loader-circle_textSecondary.svg"
            Accessible.ignored: true

            RotationAnimator on rotation {      // M-7；減少動態時 durSpin 是 0，圖示不轉
                from: 0
                to: 360
                duration: Theme.durSpin
                loops: Animation.Infinite
                running: status.visible && Theme.durSpin > 0
            }
        }

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: status.text
            color: Theme.textSecondary
            font.pixelSize: Theme.fontCaption
            Accessible.role: Accessible.StaticText
            Accessible.name: text
        }
    }

    // 分數環（DS-33）：還沒載入或沒有資料時只畫軌道，中間「—」，不畫 0。分數改變時才重畫 Canvas，不做從 0 畫起的動畫
    component ScoreRing: Item {
        id: ring

        required property string modelData      // total、text、audio、facial
        property string who: ""                  // Customer、Staff（輔助技術唸的名稱）
        property var scores: ({})
        property bool known: false               // false：還沒載入
        property bool updating: false
        property int paints: 0                   // Canvas 畫了幾次（測試用：資料改變時才重畫）

        readonly property real score: known && scores && scores[modelData] !== undefined ? scores[modelData] : -1
        readonly property bool hasScore: score >= 0
        readonly property string label: modelData === "total" ? qsTr("Overall")
                                       : modelData === "text" ? qsTr("Text")
                                       : modelData === "audio" ? qsTr("Audio") : qsTr("Facial")
        readonly property string grade: score >= 85 ? qsTr("good") : score >= 70 ? qsTr("fair") : qsTr("needs improvement")
        readonly property color arcColor: score >= 85 ? Theme.successOnDark
                                        : score >= 70 ? Theme.warningOnDark : Theme.errorOnDark

        implicitWidth: canvas.width
        implicitHeight: canvas.height + 10 + caption.implicitHeight

        Accessible.role: Accessible.Graphic
        Accessible.name: (hasScore ? qsTr("%1 %2 score %3, %4").arg(who).arg(label.toLowerCase())
                                         .arg(Math.round(score)).arg(grade)
                         : known ? qsTr("%1 %2 score, no data").arg(who).arg(label.toLowerCase())
                         : qsTr("%1 %2 score, loading").arg(who).arg(label.toLowerCase()))
                         + (updating ? qsTr(", updating") : "")

        onScoreChanged: canvas.requestPaint()

        Canvas {
            id: canvas
            width: 150
            height: 150

            // 第一次能畫、或從隱藏的分頁切回來時也要畫一次（分數沒變就不會有 scoreChanged）
            onAvailableChanged: if (available) requestPaint()
            onVisibleChanged: if (visible) requestPaint()

            onPaint: {
                ring.paints += 1
                const ctx = getContext("2d")
                const center = width / 2
                const radius = width / 2.5
                ctx.clearRect(0, 0, width, height)
                ctx.lineWidth = 14
                ctx.strokeStyle = String(Theme.track)
                ctx.beginPath()
                ctx.arc(center, center, radius, 0, 2 * Math.PI)
                ctx.stroke()
                if (ring.hasScore) {
                    const start = -Math.PI / 2
                    ctx.strokeStyle = String(ring.arcColor)
                    ctx.beginPath()
                    ctx.arc(center, center, radius, start, start + Math.min(ring.score, 100) / 100 * 2 * Math.PI)
                    ctx.stroke()
                }
            }
        }

        Text {
            objectName: "scoreText"
            anchors.centerIn: canvas
            text: ring.hasScore ? Math.round(ring.score) : "—"
            color: ring.hasScore ? Theme.textPrimary : Theme.textSecondary
            font.pixelSize: Theme.fontHeadline
            font.weight: Font.Bold
            Accessible.ignored: true
        }

        Text {
            id: caption
            y: canvas.height + 10
            anchors.horizontalCenter: canvas.horizontalCenter
            text: ring.label
            color: Theme.textPrimary
            font.pixelSize: Theme.fontBody
            Accessible.ignored: true
        }
    }

    // 一個期間的分數：「Services analyzed today: 6」＋顧客、服務人員各 4 個分數環（DS-33）
    component ScorePanel: ColumnLayout {
        id: panel

        property string countText: ""            // 「Services analyzed today: 6」
        property string emptyText: ""            // 「No completed analyses today」
        property string panelState: "loading"     // loading、empty、error、ready
        property bool dimmed: false              // 重新整理中，而且已經過了 busyDelay
        property bool slow: false                // 第一次載入超過 slowAfter
        property var customerScores: ({})
        property var staffScores: ({})
        property string errorDetail: ""
        property string errorTechnical: ""
        property int failureCount: 0
        readonly property bool known: panelState === "ready" || panelState === "empty"

        signal retry()

        spacing: Theme.spaceL

        // 數量或狀態：一直留著這一行的高度，狀態改變時版面不跳
        RowLayout {
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredHeight: 26
            spacing: Theme.spaceM
            visible: panel.panelState !== "error"

            Text {
                objectName: "panelSummary"
                visible: text !== ""
                text: panel.panelState === "ready" ? panel.countText
                    : panel.panelState === "empty" ? panel.emptyText : ""
                color: panel.panelState === "ready" ? Theme.textPrimary : Theme.textSecondary
                font.pixelSize: panel.panelState === "ready" ? Theme.fontBodyLarge : Theme.fontCaption
            }

            StatusLine {
                objectName: "panelStatus"
                text: panel.dimmed ? qsTr("Updating…")
                    : panel.panelState === "loading" && panel.slow ? qsTr("Loading scores…") : ""
            }
        }

        ColumnLayout {
            objectName: "rings"
            Layout.alignment: Qt.AlignHCenter
            spacing: Theme.spaceL
            visible: panel.panelState !== "error"
            opacity: panel.dimmed ? 0.5 : 1

            Behavior on opacity {       // M-9：重新整理時變淡 150 ms
                enabled: Theme.durBase > 0
                OpacityAnimator { duration: Theme.durBase }
            }

            Text {
                Layout.alignment: Qt.AlignHCenter
                text: qsTr("Customer Scores")
                color: Theme.textPrimary
                font.pixelSize: Theme.fontTitle
            }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                spacing: Theme.space2XL

                Repeater {
                    objectName: "customerRings"
                    model: ["total", "text", "audio", "facial"]
                    ScoreRing {
                        who: qsTr("Customer")
                        scores: panel.customerScores
                        known: panel.known
                        updating: panel.dimmed
                    }
                }
            }

            Text {
                Layout.alignment: Qt.AlignHCenter
                Layout.topMargin: Theme.spaceS
                text: qsTr("Staff Scores")
                color: Theme.textPrimary
                font.pixelSize: Theme.fontTitle
            }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                spacing: Theme.space2XL

                Repeater {
                    objectName: "staffRings"
                    model: ["total", "text", "audio", "facial"]
                    ScoreRing {
                        who: qsTr("Staff")
                        scores: panel.staffScores
                        known: panel.known
                        updating: panel.dimmed
                    }
                }
            }
        }

        // 弧線顏色的說明（UX-07），和服務人員首頁同一個元件
        ScoreLegend {
            objectName: "scoreLegend"
            Layout.alignment: Qt.AlignHCenter
            visible: panel.panelState !== "error"
        }

        // 失敗：整排換成區塊錯誤（DS-33、DS-27）
        InlineError {
            objectName: "scoresError"
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 480
            visible: panel.panelState === "error"
            title: qsTr("Scores failed to load")
            detail: panel.errorDetail
            technicalDetail: panel.errorTechnical
            failureCount: panel.failureCount
            onRetry: panel.retry()
        }
    }

    // 排名的一格：標題＋最多 5 列「1. 姓名 (分數)」。中性的灰底、#333333 的字（11.63:1）：
    // 以前四格各用綠、橘、藍、紅（標題只有 1.87～3.66:1，UX-45），後來的語意色也會讓「後 5 名」看起來像錯誤
    component RankingBox: Rectangle {
        id: box

        property string title: ""
        property var entries: []

        Layout.fillWidth: true
        Layout.fillHeight: true
        radius: Theme.radiusM
        color: Theme.card
        border.color: Theme.cardBorder
        border.width: 1
        Accessible.role: Accessible.List
        Accessible.name: title

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: Theme.spaceL
            spacing: Theme.spaceS

            Text {
                text: box.title
                color: Theme.textPrimary
                font.pixelSize: Theme.fontTitle
                font.weight: Font.Bold
                Accessible.role: Accessible.Heading
                Accessible.name: text
            }

            Repeater {
                model: box.entries
                delegate: Text {
                    required property int index
                    required property var modelData
                    text: qsTr("%1. %2 (%3)").arg(index + 1).arg(modelData.name).arg(modelData.score)
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontBody
                    font.features: { "tnum": 1 }
                    Accessible.role: Accessible.ListItem
                    Accessible.name: text
                }
            }

            Item { Layout.fillHeight: true }    // 清單靠上
        }
    }

    Wait { id: scoresDim; active: serviceScoreManager.refreshing }
    Wait { id: scoresSlow; active: serviceScoreManager.state === "loading"; interval: Theme.slowAfter }
    Wait { id: rankingDim; active: rankingModel.refreshing }
    Wait { id: rankingSlow; active: rankingModel.state === "loading"; interval: Theme.slowAfter }
    Wait { id: trendsDim; active: trendDataManager.refreshing }
    Wait { id: trendsSlow; active: trendDataManager.state === "loading"; interval: Theme.slowAfter }

    // 分頁：和服務人員首頁、分析頁同一個 SegmentTab。以前是 Material 的 TabButton，選取的是 Material Green
    //（白字 2.78:1），而且在 macOS 樣式下 Material.background 根本畫不出來
    RowLayout {
        id: tabBar
        anchors.top: parent.top
        anchors.topMargin: Theme.spaceL
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: Theme.spaceS
        Accessible.role: Accessible.PageTabList
        Accessible.name: qsTr("Home sections")

        SegmentTab {
            objectName: "dailyTab"
            text: qsTr("Daily Scores")
            selected: root.currentTab === 0
            onClicked: root.currentTab = 0
        }
        SegmentTab {
            objectName: "weeklyTab"
            text: qsTr("Weekly Scores")
            selected: root.currentTab === 1
            onClicked: root.currentTab = 1
        }
        SegmentTab {
            objectName: "rankingTab"
            text: qsTr("Ranking")
            selected: root.currentTab === 2
            onClicked: root.currentTab = 2
        }
        SegmentTab {
            objectName: "trendsTab"
            text: qsTr("Trends")
            selected: root.currentTab === 3
            onClicked: root.currentTab = 3
        }
    }

    // Daily Scores Page
    ScorePanel {
        id: dailyPanel
        objectName: "dailyPanel"
        anchors.top: tabBar.bottom
        anchors.topMargin: Theme.space3XL
        anchors.horizontalCenter: parent.horizontalCenter
        visible: root.currentTab == 0
        countText: qsTr("Services analyzed today: %1").arg(serviceScoreManager.dailyCount)
        emptyText: qsTr("No completed analyses today")
        panelState: serviceScoreManager.dailyState
        dimmed: scoresDim.elapsed
        slow: scoresSlow.elapsed
        customerScores: serviceScoreManager.dailyCustomerScores
        staffScores: serviceScoreManager.dailyStaffScores
        errorDetail: serviceScoreManager.errorDetail
        errorTechnical: serviceScoreManager.errorTechnical
        failureCount: serviceScoreManager.failureCount
        onRetry: serviceScoreManager.refresh()
    }

    // Weekly Scores Page
    ScorePanel {
        id: weeklyPanel
        objectName: "weeklyPanel"
        anchors.top: tabBar.bottom
        anchors.topMargin: Theme.space3XL
        anchors.horizontalCenter: parent.horizontalCenter
        visible: root.currentTab == 1
        countText: qsTr("Services analyzed in the last 7 days: %1").arg(serviceScoreManager.weeklyCount)
        emptyText: qsTr("No completed analyses in the last 7 days")
        panelState: serviceScoreManager.weeklyState
        dimmed: scoresDim.elapsed
        slow: scoresSlow.elapsed
        customerScores: serviceScoreManager.weeklyCustomerScores
        staffScores: serviceScoreManager.weeklyStaffScores
        errorDetail: serviceScoreManager.errorDetail
        errorTechnical: serviceScoreManager.errorTechnical
        failureCount: serviceScoreManager.failureCount
        onRetry: serviceScoreManager.refresh()
    }

    // Ranking Page：全部期間、已完成的服務，依平均分數取前 5 名和後 5 名
    Item {
        id: rankingPanel
        objectName: "rankingPanel"
        visible: root.currentTab == 2
        anchors.top: tabBar.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: Theme.space3XL

        StatusLine {
            id: rankingStatus
            objectName: "rankingStatus"
            anchors.horizontalCenter: parent.horizontalCenter
            text: rankingDim.elapsed ? qsTr("Updating…")
                : rankingModel.state === "loading" && rankingSlow.elapsed ? qsTr("Loading ranking…") : ""
        }

        GridLayout {
            id: rankingGrid
            objectName: "rankingGrid"
            anchors.top: parent.top
            anchors.topMargin: Theme.space2XL
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: rankingCaption.top
            anchors.bottomMargin: Theme.spaceM
            visible: rankingModel.state === "ready" || rankingModel.state === "loading"
            opacity: rankingDim.elapsed ? 0.5 : 1
            rows: 2
            columns: 2
            rowSpacing: Theme.space2XL
            columnSpacing: Theme.space2XL

            Behavior on opacity {
                enabled: Theme.durBase > 0
                OpacityAnimator { duration: Theme.durBase }
            }

            RankingBox {
                objectName: "topCustomers"
                title: qsTr("Top 5 Customers")
                entries: rankingModel.topCustomer
            }

            RankingBox {
                objectName: "bottomCustomers"
                title: qsTr("Bottom 5 Customers")
                entries: rankingModel.bottomCustomer
            }

            RankingBox {
                objectName: "topStaff"
                title: qsTr("Top 5 Staff")
                entries: rankingModel.topStaff
            }

            RankingBox {
                objectName: "bottomStaff"
                title: qsTr("Bottom 5 Staff")
                entries: rankingModel.bottomStaff
            }
        }

        Text {
            id: rankingCaption
            anchors.bottom: parent.bottom
            anchors.horizontalCenter: parent.horizontalCenter
            visible: rankingGrid.visible
            text: qsTr("Average scores of completed analyses, all time.")
            color: Theme.textSecondary
            font.pixelSize: Theme.fontCaption
        }

        EmptyState {
            objectName: "rankingEmpty"
            anchors.centerIn: parent
            width: 360
            visible: rankingModel.state === "empty"
            title: qsTr("No rankings yet")
            description: qsTr("Staff appear here after their services are analyzed.")
        }

        InlineError {
            objectName: "rankingError"
            anchors.centerIn: parent
            width: 480
            visible: rankingModel.state === "error"
            title: qsTr("Ranking failed to load")
            detail: rankingModel.errorDetail
            technicalDetail: rankingModel.errorTechnical
            failureCount: rankingModel.failureCount
            onRetry: rankingModel.refresh()
        }
    }

    // Trends Page：沒有資料的日子是空白（UX-30），只畫有資料的點，連續的日子才連線；x 軸是那個月實際的天數
    ColumnLayout {
        objectName: "trendsPanel"
        anchors.left: parent.left
        anchors.leftMargin: Theme.space4XL
        anchors.right: parent.right
        anchors.rightMargin: Theme.space4XL
        anchors.top: tabBar.bottom
        anchors.topMargin: Theme.spaceL
        anchors.bottom: parent.bottom
        spacing: Theme.spaceL
        visible: root.currentTab == 3

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.spaceS

            // 月份和年份
            RowLayout {
                spacing: Theme.spaceL
                Layout.alignment: Qt.AlignCenter

                ComboBox {
                    id: monthSelector
                    objectName: "monthSelector"
                    model: [qsTr("January"), qsTr("February"), qsTr("March"), qsTr("April"), qsTr("May"),
                            qsTr("June"), qsTr("July"), qsTr("August"), qsTr("September"), qsTr("October"),
                            qsTr("November"), qsTr("December")]
                    currentIndex: new Date().getMonth()  // 預設本月；getMonth() 從 0 開始
                    Layout.alignment: Qt.AlignCenter
                    Accessible.name: qsTr("Month")
                    onActivated: root.reloadTrends()  // 只在使用者改選時觸發
                }

                ComboBox {
                    id: yearSelector
                    objectName: "yearSelector"
                    // 今年和前 4 年；跨年之後新的年份會自動出現
                    model: {
                        var thisYear = new Date().getFullYear();
                        var years = [];
                        for (var y = thisYear - 4; y <= thisYear; y++)
                            years.push(y);
                        return years;
                    }
                    currentIndex: 4  // 今年
                    Layout.alignment: Qt.AlignCenter
                    Accessible.name: qsTr("Year")
                    onActivated: root.reloadTrends()
                }
            }

            // 要顯示的線；勾選框左邊的色塊就是圖例（線分成好幾段，ChartView 內建的圖例會重複）
            RowLayout {
                spacing: Theme.spaceL
                Layout.alignment: Qt.AlignCenter

                Rectangle {
                    width: 12
                    height: 12
                    radius: 6
                    color: root.staffColor
                    Accessible.ignored: true
                }

                CheckBox {
                    id: showStaffTrend
                    text: qsTr("Show Staff Trend")
                    checked: true
                    Layout.alignment: Qt.AlignCenter
                    font.pixelSize: Theme.fontCaption
                    onToggled: root.redrawTrends()
                }

                Rectangle {
                    width: 12
                    height: 12
                    radius: 6
                    color: root.customerColor
                    Accessible.ignored: true
                }

                CheckBox {
                    id: showCustomerTrend
                    text: qsTr("Show Customer Trend")
                    checked: true
                    Layout.alignment: Qt.AlignCenter
                    font.pixelSize: Theme.fontCaption
                    onToggled: root.redrawTrends()
                }

                StatusLine {
                    objectName: "trendsStatus"
                    Layout.preferredWidth: implicitWidth
                    text: trendsDim.elapsed ? qsTr("Updating…")
                        : trendDataManager.state === "loading" && trendsSlow.elapsed ? qsTr("Loading trends…") : ""
                }
            }

            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true

                ChartView {
                    id: chartView
                    objectName: "trendChart"
                    anchors.fill: parent
                    antialiasing: true
                    legend.visible: false
                    // 深色介面（U0）：圖表透明，刻度、格線改成淺色；不換 ChartView 的 theme（會蓋掉線的顏色）
                    backgroundColor: "transparent"
                    plotAreaColor: "transparent"
                    // 圖表讀不出來給輔助技術：名稱寫哪一個月，說明寫每條線有幾天的資料
                    Accessible.role: Accessible.Chart
                    Accessible.name: qsTr("Daily average scores, %1 %2")
                        .arg(monthSelector.model[trendDataManager.month - 1]).arg(trendDataManager.year)
                    Accessible.description: qsTr("Days with data: staff %1, customers %2.")
                        .arg(trendDataManager.staffPoints.length).arg(trendDataManager.customerPoints.length)
                    visible: trendDataManager.state === "ready" || trendDataManager.state === "loading"
                    opacity: trendsDim.elapsed ? 0.5 : 1

                    Behavior on opacity {
                        enabled: Theme.durBase > 0
                        OpacityAnimator { duration: Theme.durBase }
                    }

                    ValueAxis {
                        id: valueAxisX
                        min: 1
                        max: trendDataManager.daysInMonth      // 那個月實際的天數
                        tickCount: trendDataManager.daysInMonth
                        labelFormat: "%.0f"
                        titleText: qsTr("Day")
                        titleBrush: Theme.textSecondary
                        labelsColor: Theme.textSecondary
                        gridLineColor: Theme.cardBorder
                        color: Theme.tileBorder
                    }

                    ValueAxis {
                        id: valueAxisY
                        min: 0
                        max: 100
                        tickCount: 11
                        labelFormat: "%.0f"
                        titleText: qsTr("Score")
                        titleBrush: Theme.textSecondary
                        labelsColor: Theme.textSecondary
                        gridLineColor: Theme.cardBorder
                        color: Theme.tileBorder
                    }

                    // 每一天的點；連續的日子之間的線由 redrawTrends() 另外建立
                    ScatterSeries {
                        id: staffDots
                        name: qsTr("Staff Score Trends")
                        axisX: valueAxisX
                        axisY: valueAxisY
                        color: root.staffColor
                        borderColor: root.staffColor
                        markerSize: 8
                        visible: showStaffTrend.checked
                    }

                    ScatterSeries {
                        id: customerDots
                        name: qsTr("Customer Score Trends")
                        axisX: valueAxisX
                        axisY: valueAxisY
                        color: root.customerColor
                        borderColor: root.customerColor
                        markerSize: 8
                        visible: showCustomerTrend.checked
                    }
                }

                EmptyState {
                    objectName: "trendsEmpty"
                    anchors.centerIn: parent
                    width: 360
                    visible: trendDataManager.state === "empty"
                    title: qsTr("No completed analyses in %1 %2")
                           .arg(monthSelector.model[trendDataManager.month - 1]).arg(trendDataManager.year)
                    description: qsTr("Choose another month to see its trend.")
                }

                InlineError {
                    objectName: "trendsError"
                    anchors.centerIn: parent
                    width: 480
                    visible: trendDataManager.state === "error"
                    title: qsTr("Trends failed to load")
                    detail: trendDataManager.errorDetail
                    technicalDetail: trendDataManager.errorTechnical
                    failureCount: trendDataManager.failureCount
                    onRetry: trendDataManager.retry()
                }
            }
        }
    }

    // 新的趨勢資料回來（或換了要顯示的線）：整張圖重畫
    Connections {
        target: trendDataManager
        function onTrendChanged() {
            root.redrawTrends()
        }
    }

    // 依照目前選的月份、年份載入趨勢；連續換月份時只留最後一次（replace）
    function reloadTrends() {
        trendDataManager.load(monthSelector.currentIndex + 1, Number(yearSelector.currentText));
    }

    // 點用兩個 ScatterSeries；每一段連續有資料的日子建一條線。每條線一次給完（ENG-19）
    function redrawTrends() {
        for (const line of trendLines)
            chartView.removeSeries(line)
        const lines = []
        if (showStaffTrend.checked)
            addTrendLines(lines, trendDataManager.staffRuns, root.staffColor)
        if (showCustomerTrend.checked)
            addTrendLines(lines, trendDataManager.customerRuns, root.customerColor)
        trendLines = lines
        trendDataManager.fillSeries(staffDots, trendDataManager.staffPoints)
        trendDataManager.fillSeries(customerDots, trendDataManager.customerPoints)
    }

    function addTrendLines(lines, runs, color) {
        for (const run of runs) {
            if (run.length < 2)
                continue        // 只有一天：只有點，沒有線
            const line = chartView.createSeries(ChartView.SeriesTypeLine, "", valueAxisX, valueAxisY)
            line.color = color
            line.width = 2
            trendDataManager.fillSeries(line, run)
            lines.push(line)
        }
    }

    Component.onCompleted: {
        // 每一項只載入一次（ENG-06）。已經有的資料先照舊顯示，新的回來再換上去
        serviceScoreManager.refresh();
        rankingModel.refresh();
        redrawTrends();
        reloadTrends();
    }
}
