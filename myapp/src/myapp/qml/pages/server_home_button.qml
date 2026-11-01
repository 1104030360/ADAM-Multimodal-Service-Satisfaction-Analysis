pragma ComponentBehavior: Bound
import QtQuick
// 分頁按鈕自訂了 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 首頁分數（DS-33、UX-05、UX-06）。資料是 serviceScoreManager 的屬性（ENG-08）：
// 頁面不管什麼時候建立都拿得到最新的狀態，背景載入完成時畫面自己更新。
// - 還沒有資料（loading）：只畫軌道，中間「—」；超過 2 s 加「Loading scores…」
// - 沒有資料：環中間「—」，說明「No completed analyses today」；不畫成 0
// - 重新整理（busy）：舊的環留著、變淡，旁邊「Updating…」
// - 失敗（error）：整排換成區塊錯誤，可以重試
Page {
    id: homePage
    title: qsTr("Service Scores")

    readonly property var manager: typeof serviceScoreManager !== "undefined" ? serviceScoreManager : null
    property bool showDaily: true
    readonly property var period: manager ? (showDaily ? manager.daily : manager.weekly) : null
    readonly property bool loading: !manager || manager.status === "loading"
    readonly property bool failed: manager !== null && manager.status === "error"
    readonly property bool refreshing: manager !== null && manager.busy && manager.status === "ready"
    readonly property int serviceCount: period ? period.count : 0
    property bool slow: false   // 第一次載入超過 slowAfter（2 s）

    background: Rectangle { color: Theme.contentBg }

    onLoadingChanged: {
        if (!loading)
            slow = false
    }

    Timer {
        interval: Theme.slowAfter
        running: homePage.loading && homePage.manager !== null && homePage.manager.busy
        onTriggered: homePage.slow = true
    }

    function hasScore(value) {
        return typeof value === "number" && value >= 0
    }

    // 分頁按鈕（Daily／Weekly）：選取的是石墨灰（D2、DS-12）
    component SegmentButton: Button {
        id: segment
        required property bool selected

        implicitWidth: 180
        implicitHeight: 60
        focusPolicy: Qt.StrongFocus
        font.pixelSize: Theme.fontBodyLarge
        Accessible.role: Accessible.PageTab
        Accessible.selected: selected

        contentItem: Text {
            text: segment.text
            font: segment.font
            color: segment.selected ? Theme.segmentSelectedText : Theme.segmentIdleText
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: height / 2
            color: segment.selected ? Theme.segmentSelected : Theme.segmentIdle

            Rectangle {     // 鍵盤焦點框（DS-30）
                visible: segment.visualFocus
                anchors.fill: parent
                anchors.margins: -4
                radius: parent.radius + 4
                color: "transparent"
                border.color: Theme.focusOnLight
                border.width: 2
            }
        }
    }

    // 一個分數環：Canvas 只畫軌道和弧線，數字是另外的 Text，資料改變時才重畫（UX-06）
    component ScoreRing: ColumnLayout {
        id: ring
        required property string kind      // total、text、audio、facial
        required property string side      // customer、staff

        readonly property var score: homePage.period && homePage.period[side] ? homePage.period[side][kind] : undefined
        readonly property bool hasScore: homePage.hasScore(score)
        readonly property string label: kind === "total" ? qsTr("Overall")
                                      : kind === "text" ? qsTr("Text")
                                      : kind === "audio" ? qsTr("Audio")
                                      : qsTr("Facial")
        readonly property string sideLabel: side === "customer" ? qsTr("Customer") : qsTr("Staff")
        readonly property string grade: !hasScore ? "" : score >= 85 ? qsTr("good")
                                      : score >= 70 ? qsTr("fair") : qsTr("needs improvement")
        property int paintCount: 0      // 畫了幾次（測試用：資料改變時要重畫）

        objectName: "scoreRing_" + side + "_" + kind
        spacing: Theme.spaceS
        onScoreChanged: canvas.requestPaint()

        Item {
            Layout.alignment: Qt.AlignHCenter
            implicitWidth: 150
            implicitHeight: 150
            Accessible.role: Accessible.StaticText
            Accessible.name: ring.hasScore
                             ? qsTr("%1 %2 score %3, %4").arg(ring.sideLabel).arg(ring.label.toLowerCase())
                                   .arg(Math.round(ring.score)).arg(ring.grade)
                             : qsTr("%1 %2 score, no data").arg(ring.sideLabel).arg(ring.label.toLowerCase())

            Canvas {
                id: canvas
                anchors.fill: parent
                onPaint: {
                    ring.paintCount++
                    const ctx = getContext("2d")
                    ctx.clearRect(0, 0, width, height)
                    const center = width / 2
                    const radius = width / 2.5
                    ctx.lineWidth = 14
                    ctx.strokeStyle = Theme.track.toString()
                    ctx.beginPath()
                    ctx.arc(center, center, radius, 0, 2 * Math.PI)
                    ctx.stroke()
                    if (!ring.hasScore || ring.score <= 0)
                        return
                    // 弧線用語意色：良好 ≥85、普通 ≥70、待加強（DS-13、DS-33）
                    const color = ring.score >= 85 ? Theme.successOnLight
                                : ring.score >= 70 ? Theme.warningOnLight : Theme.errorOnLight
                    const start = -Math.PI / 2
                    ctx.strokeStyle = color.toString()
                    ctx.beginPath()
                    ctx.arc(center, center, radius, start, start + Math.min(ring.score, 100) / 100 * 2 * Math.PI)
                    ctx.stroke()
                }
            }

            Text {
                objectName: "scoreText"
                anchors.centerIn: parent
                text: ring.hasScore ? Math.round(ring.score) : "—"
                color: Theme.textPrimary
                font.pixelSize: Theme.fontHeadline
                font.weight: Font.Bold
                Accessible.ignored: true
            }
        }

        Text {
            Layout.alignment: Qt.AlignHCenter
            text: ring.label
            color: Theme.textPrimary
            font.pixelSize: Theme.fontBody
            Accessible.ignored: true
        }
    }

    component ScoreRow: ColumnLayout {
        id: scoreRow
        required property string side
        required property string title

        spacing: Theme.spaceL
        Layout.alignment: Qt.AlignHCenter

        Text {
            Layout.alignment: Qt.AlignHCenter
            text: scoreRow.title
            color: Theme.textPrimary
            font.pixelSize: Theme.fontTitle
        }

        RowLayout {
            Layout.alignment: Qt.AlignHCenter
            spacing: Theme.space2XL

            Repeater {
                model: ["total", "text", "audio", "facial"]

                ScoreRing {
                    required property string modelData
                    kind: modelData
                    side: scoreRow.side
                }
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spaceL
        spacing: Theme.spaceL

        RowLayout {
            Layout.alignment: Qt.AlignHCenter
            spacing: Theme.spaceL
            Accessible.role: Accessible.PageTabList
            Accessible.name: qsTr("Period")

            SegmentButton {
                objectName: "dailyTab"
                text: qsTr("Daily Scores")
                selected: homePage.showDaily
                onClicked: homePage.showDaily = true
            }

            SegmentButton {
                objectName: "weeklyTab"
                text: qsTr("Weekly Scores")
                selected: !homePage.showDaily
                onClicked: homePage.showDaily = false
            }
        }

        // 件數、沒有資料的說明、載入中、更新中。「Updating…」接在件數右邊，件數不會被擠開
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 28
            visible: !homePage.failed

            Text {
                id: countText
                objectName: "countText"
                anchors.centerIn: parent
                visible: !homePage.loading
                text: homePage.serviceCount === 0
                      ? (homePage.showDaily ? qsTr("No completed analyses today") : qsTr("No completed analyses this week"))
                      : (homePage.showDaily ? qsTr("Services analyzed today: %1")
                                            : qsTr("Services analyzed this week: %1")).arg(homePage.serviceCount)
                color: Theme.textPrimary
                font.pixelSize: Theme.fontBodyLarge
                font.features: { "tnum": 1 }
            }

            Text {
                objectName: "loadingText"
                anchors.centerIn: parent
                visible: homePage.loading && homePage.slow
                text: qsTr("Loading scores…")
                color: Theme.textSecondary
                font.pixelSize: Theme.fontCaption
            }

            Row {
                objectName: "updatingIndicator"
                anchors.left: countText.right
                anchors.leftMargin: Theme.spaceS
                anchors.verticalCenter: parent.verticalCenter
                visible: homePage.refreshing
                spacing: Theme.spaceXS
                Accessible.role: Accessible.StaticText
                Accessible.name: qsTr("Updating…")

                Image {
                    id: spinner
                    anchors.verticalCenter: parent.verticalCenter
                    width: Theme.iconS
                    height: Theme.iconS
                    sourceSize: Qt.size(Theme.iconS, Theme.iconS)
                    source: "../components/icons/loader-circle_textSecondary.svg"
                    Accessible.ignored: true

                    RotationAnimator on rotation {
                        from: 0
                        to: 360
                        duration: Theme.durSpin
                        loops: Animation.Infinite
                        running: spinner.visible && Theme.durSpin > 0
                    }
                }

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("Updating…")
                    color: Theme.textSecondary
                    font.pixelSize: Theme.fontCaption
                }
            }
        }

        ColumnLayout {
            objectName: "scoreRows"
            visible: !homePage.failed
            opacity: homePage.refreshing ? 0.5 : 1     // 重新整理時舊的分數留著、變淡（DS-25）
            spacing: Theme.space2XL
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignHCenter

            ScoreRow {
                side: "customer"
                title: qsTr("Customer Scores")
            }

            ScoreRow {
                side: "staff"
                title: qsTr("Staff Scores")
            }
        }

        InlineError {
            objectName: "scoresError"
            visible: homePage.failed
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: Theme.space3XL
            Layout.preferredWidth: 480
            title: qsTr("Couldn't load scores")
            detail: homePage.manager ? homePage.manager.errorDetail : ""
            technicalDetail: homePage.manager ? homePage.manager.errorTechnical : ""
            failureCount: homePage.manager ? homePage.manager.failureCount : 0
            busy: homePage.manager !== null && homePage.manager.busy
            onRetry: {
                if (typeof serverHome !== "undefined" && serverHome)
                    serverHome.onLoadHome()     // 分數和還沒載入的帳號名稱一起重試
            }
        }

        Item { Layout.fillHeight: true }
    }
}
