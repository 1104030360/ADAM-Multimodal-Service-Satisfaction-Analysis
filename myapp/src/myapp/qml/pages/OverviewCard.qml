pragma ComponentBehavior: Bound
import QtQuick
// 自訂了分段切換的外觀，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 首頁的 Overview 卡片（new-UIUX PLAN 5-1、5-2；設計稿 Main、AdminHome、States）。兩端共用，資料是 homeDashboard.overview。
// - 期間（Today、Last 7 days、Last 30 days）、Customer／Staff：三個期間一次查完（T28），換了只是換一份資料，不再查詢。
// - 分數方塊（StatTile）：Overall、Text、Audio、Facial 的平均、分數帶、和上一期比的變化。
// - 服務方塊：Completed、Analyzing、Not analyzed。服務人員端可以點（openTab：打開分析頁的那個分頁）；
//   管理員端沒有分析頁，方塊只是數字，看起來也不像按鈕（不放 Review →）。
// - 狀態（PLAN 5-2）：載入中是灰色骨架（不轉圈，2 秒後多一行 Loading scores…）；30 天內沒有服務是空狀態
//   （服務人員端有 New Recording）；讀不到是 InlineError（Retry、Details）；重新查詢時舊的數字留著，副標寫 Updating…。
Card {
    id: card

    property var section: null              // homeDashboard.overview（OverviewSection）
    property bool allStaff: false           // 管理員端：全部員工
    property bool canRecord: false          // 沒有服務時給 New Recording（服務人員端）
    property string side: "customer"        // 分數方塊看哪一方；左下的長條圖、排名也跟著它
    property string period: "30d"

    signal openTab(string tab)              // 服務方塊：notAnalyzed、analyzing、completed
    signal newRecording()
    signal retry()

    readonly property string sectionState: section ? section.state : "loading"
    readonly property bool loading: sectionState === "loading"
    readonly property bool ready: sectionState === "ready"
    readonly property var current: section ? section.periods[period] : null
    readonly property string periodLabel: period === "today" ? qsTr("Today")
                                        : period === "7d" ? qsTr("Last 7 days") : qsTr("Last 30 days")
    readonly property string compared: period === "today" ? qsTr("compared with yesterday")
                                      : period === "7d" ? qsTr("compared with the 7 days before")
                                      : qsTr("compared with the 30 days before")
    property bool updatingShown: false      // 重新查詢超過 300 ms 才寫 Updating…（DS-25：快的時候不閃）
    property bool slow: false               // 第一次載入超過 2 秒（DS-25）

    objectName: "overviewCard"
    Accessible.role: Accessible.Grouping
    Accessible.name: qsTr("Overview")

    Timer {
        interval: Theme.busyDelay
        running: card.section !== null && card.section.refreshing
        onTriggered: card.updatingShown = true
    }
    Connections {
        target: card.section
        function onStateChanged() {
            if (!card.section.refreshing)
                card.updatingShown = false
            if (!card.loading)
                card.slow = false
        }
    }
    Timer {
        interval: Theme.slowAfter
        running: card.loading
        onTriggered: card.slow = true
    }

    function subtitle() {
        if (sectionState === "empty")
            return allStaff ? qsTr("Last 30 days · 0 services") : qsTr("Last 30 days · 0 services recorded")
        if (!ready || !current)
            return periodLabel
        const count = current.count
        let text
        if (allStaff)
            text = (count === 1 ? qsTr("%1 · 1 service · %2 staff") : qsTr("%1 · %3 services · %2 staff"))
                .arg(periodLabel).arg(current.staffCount).arg(count)
        else
            text = (count === 1 ? qsTr("%1 · 1 service recorded") : qsTr("%1 · %2 services recorded"))
                .arg(periodLabel).arg(count)
        return updatingShown ? qsTr("%1 · Updating…").arg(text) : text
    }

    // Customer／Staff 的分段切換（設計稿：高 44、圓角 12，選到的是亮底深字）
    component SideButton: Button {
        id: sideButton
        required property string key
        readonly property bool selected: card.side === key

        implicitHeight: Theme.controlHeight
        horizontalPadding: Theme.spaceM
        verticalPadding: 0
        font.pixelSize: Theme.fontCaption
        font.weight: Font.DemiBold
        focusPolicy: Qt.StrongFocus
        Accessible.role: Accessible.Button
        Accessible.checkable: true
        Accessible.checked: selected
        onClicked: card.side = key

        contentItem: Text {
            text: sideButton.text
            font: sideButton.font
            color: sideButton.selected ? Theme.segmentSelectedText : Theme.segmentIdleText
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }

        background: Rectangle {
            radius: Theme.radiusNav
            color: sideButton.selected ? Theme.segmentSelected : "transparent"

            Rectangle {     // 鍵盤焦點框（DS-30）
                visible: sideButton.visualFocus
                anchors.fill: parent
                anchors.margins: -3
                radius: parent.radius + 3
                color: "transparent"
                border.color: Theme.focusOnDark
                border.width: 2
            }
        }
    }

    // 方塊上面的小標題（大寫、字距 0.08em）
    component GroupLabel: Text {
        color: Theme.textFaint
        font.pixelSize: Theme.fontLabel
        font.weight: Font.DemiBold
        font.capitalization: Font.AllUppercase
        font.letterSpacing: 1
    }

    ColumnLayout {
        width: card.availableWidth
        spacing: 22

        // ── 標題、副標、Customer／Staff、期間
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spaceM

            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: 4

                Text {
                    objectName: "overviewTitle"
                    text: qsTr("Overview")
                    color: Theme.textTitle
                    font.pixelSize: Theme.fontTitleSmall
                    font.weight: Font.Bold
                    Accessible.role: Accessible.Heading
                    Accessible.name: text
                }

                Text {
                    objectName: "overviewSubtitle"
                    visible: !card.loading
                    Layout.fillWidth: true
                    text: card.subtitle()
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontCaption
                    elide: Text.ElideRight
                }

                Rectangle {     // 載入中：副標的骨架
                    visible: card.loading
                    Layout.topMargin: 4
                    width: 220
                    height: 12
                    radius: 6
                    color: Theme.cardBorder
                }
            }

            Rectangle {
                objectName: "sideToggle"
                visible: card.ready
                Layout.alignment: Qt.AlignTop
                implicitWidth: sides.implicitWidth + 2
                implicitHeight: sides.implicitHeight + 2
                radius: 13
                color: Theme.segmentBg
                border.color: Theme.cardBorder
                Accessible.role: Accessible.Grouping
                Accessible.name: qsTr("Show scores for")

                Row {
                    id: sides
                    x: 1
                    y: 1

                    SideButton { objectName: "sideCustomer"; key: "customer"; text: qsTr("Customer") }
                    SideButton { objectName: "sideStaff"; key: "staff"; text: qsTr("Staff") }
                }
            }

            HomeSelect {
                objectName: "periodBox"
                visible: card.ready
                Layout.alignment: Qt.AlignTop
                implicitHeight: 46
                Layout.preferredWidth: 172
                leadingIcon: "../components/icons/calendar_iconNeutral.svg"
                Accessible.name: qsTr("Period")
                value: card.period
                options: [{ value: "today", text: qsTr("Today") }, { value: "7d", text: qsTr("Last 7 days") },
                          { value: "30d", text: qsTr("Last 30 days") }]
                onPicked: (value) => card.period = value
            }
        }

        // ── 載入中：灰色的骨架（PLAN 5-2：不轉圈）
        ColumnLayout {
            visible: card.loading
            Layout.fillWidth: true
            spacing: 22

            GridLayout {
                objectName: "overviewSkeleton"
                Layout.fillWidth: true
                columns: Math.max(1, Math.min(7, Math.floor((card.availableWidth + 20) / (118 + 20))))
                columnSpacing: 20
                rowSpacing: 24

                Repeater {
                    model: 7
                    StatTile { Layout.fillWidth: true; Layout.preferredWidth: 110; loading: true; label: qsTr("Score") }
                }
            }

            Text {
                objectName: "overviewLoadingText"
                visible: card.slow
                text: qsTr("Loading scores…")
                color: Theme.textFaint
                font.pixelSize: Theme.fontSmall
            }
        }

        // ── 讀不到：InlineError（Retry、Details），其他卡片照常（PLAN 5-2）
        InlineError {
            objectName: "overviewError"
            visible: card.sectionState === "error"
            Layout.fillWidth: true
            Layout.topMargin: Theme.spaceM
            Layout.bottomMargin: Theme.spaceM
            title: qsTr("Couldn't load the overview")
            detail: !card.section ? "" : card.section.updatedAt !== ""
                ? qsTr("%1 Scores from %2 are hidden until they load again.")
                      .arg(card.section.errorDetail).arg(card.section.updatedAt)
                : card.section.errorDetail
            technicalDetail: card.section ? card.section.errorTechnical : ""
            failureCount: card.section ? card.section.failureCount : 0
            onRetry: card.retry()
        }

        // ── 30 天內沒有服務（設計稿 States 的 No services yet）
        ColumnLayout {
            visible: card.sectionState === "empty"
            Layout.fillWidth: true
            Layout.topMargin: Theme.spaceM
            Layout.bottomMargin: Theme.spaceM
            spacing: Theme.spaceM

            EmptyState {
                objectName: "overviewEmpty"
                Layout.fillWidth: true
                title: qsTr("No services in the last 30 days")
                description: card.allStaff ? qsTr("Scores show up here after staff record services and they're analyzed.")
                                           : qsTr("Record a service. Its scores show up here after it's analyzed.")
            }

            BusyButton {
                objectName: "newRecordingButton"
                visible: card.canRecord
                Layout.alignment: Qt.AlignHCenter
                variant: "primary"
                iconSource: "../components/icons/video_actionPrimaryText.svg"
                text: qsTr("New Recording")
                onClicked: card.newRecording()
            }
        }

        // ── 有資料：分數方塊｜服務方塊。寬的時候並排，中間一條線；窄的時候上下
        Item {
            id: groups
            visible: card.ready
            Layout.fillWidth: true
            implicitHeight: sideBySide ? Math.max(scores.implicitHeight, services.implicitHeight)
                                       : scores.implicitHeight + Theme.sectionGap + services.implicitHeight

            readonly property int gap: 36
            readonly property bool sideBySide: width >= 520 + 380 + 2 * gap + 1
            readonly property real scoresWidth: sideBySide ? (width - 2 * gap - 1) * 4 / 7 : width

            ColumnLayout {
                id: scores
                width: groups.scoresWidth
                spacing: 14
                Accessible.role: Accessible.Grouping
                Accessible.name: scoresLabel.text

                GroupLabel {
                    id: scoresLabel
                    objectName: "scoresLabel"
                    text: card.side === "customer"
                        ? (card.allStaff ? qsTr("Customer scores, all staff") : qsTr("Customer scores"))
                        : (card.allStaff ? qsTr("Staff scores, all staff") : qsTr("Staff scores"))
                }

                GridLayout {
                    Layout.fillWidth: true
                    columns: Math.max(1, Math.min(4, Math.floor((scores.width + 16) / (118 + 16))))
                    columnSpacing: 16
                    rowSpacing: 16

                    Repeater {
                        model: [
                            { key: "overall", label: qsTr("Overall"), icon: "../components/icons/gauge_iconNeutral.svg" },
                            { key: "text", label: qsTr("Text"), icon: "../components/icons/file-text_iconNeutral.svg" },
                            { key: "audio", label: qsTr("Audio"), icon: "../components/icons/audio-lines_iconNeutral.svg" },
                            { key: "facial", label: qsTr("Facial"), icon: "../components/icons/scan-face_iconNeutral.svg" }
                        ]

                        StatTile {
                            id: scoreTile
                            required property var modelData
                            readonly property var score: card.current ? card.current[card.side][modelData.key] : null
                            readonly property var number: score ? score.value : null

                            objectName: "scoreTile_" + modelData.key
                            Layout.fillWidth: true
                            Layout.preferredWidth: 110      // 每一格一樣寬：多的空間平均分
                            label: modelData.label
                            iconSource: modelData.icon
                            valueText: Theme.hasScore(number) ? number.toFixed(1) : "—"
                            delta: score ? score.delta : null
                            bandText: Theme.bandName(number)
                            bandColor: Theme.bandColor(number)
                        }
                    }
                }
            }

            Rectangle {
                visible: groups.sideBySide
                x: scores.width + groups.gap
                width: 1
                height: groups.height
                color: Theme.cardBorder
            }

            ColumnLayout {
                id: services
                x: groups.sideBySide ? scores.width + 2 * groups.gap + 1 : 0
                y: groups.sideBySide ? 0 : scores.implicitHeight + Theme.sectionGap
                width: groups.sideBySide ? groups.width - x : groups.width
                spacing: 14
                Accessible.role: Accessible.Grouping
                Accessible.name: servicesLabel.text

                readonly property int waiting: card.current ? card.current.notAnalyzed : 0

                GroupLabel {
                    id: servicesLabel
                    text: card.allStaff ? qsTr("Services, all staff") : qsTr("Services")
                }

                GridLayout {
                    Layout.fillWidth: true
                    columns: Math.max(1, Math.min(3, Math.floor((services.width + 16) / (110 + 16))))
                    columnSpacing: 16
                    rowSpacing: 16

                    StatTile {
                        objectName: "completedTile"
                        Layout.fillWidth: true
                        Layout.preferredWidth: 110      // 每一格一樣寬：多的空間平均分
                        interactive: !card.allStaff
                        iconSource: "../components/icons/circle-check_successOnDark.svg"
                        label: qsTr("Completed")
                        valueText: card.current ? card.current.completed : "—"
                        note: qsTr("Analyzed")
                        onActivated: card.openTab("completed")
                    }

                    StatTile {
                        objectName: "analyzingTile"
                        Layout.fillWidth: true
                        Layout.preferredWidth: 110      // 每一格一樣寬：多的空間平均分
                        interactive: !card.allStaff
                        iconSource: "../components/icons/clock_infoOnDark.svg"
                        label: qsTr("Analyzing")
                        valueText: card.current ? card.current.analyzing : "—"
                        note: qsTr("In progress")
                        onActivated: card.openTab("analyzing")
                    }

                    // 有還沒送出的才用橘色的方塊（待辦，PLAN 第 3 節）；0 筆時是一般的灰色
                    StatTile {
                        objectName: "notAnalyzedTile"
                        Layout.fillWidth: true
                        Layout.preferredWidth: 110      // 每一格一樣寬：多的空間平均分
                        interactive: !card.allStaff
                        iconSource: services.waiting > 0 ? "../components/icons/triangle-alert_textOnDark.svg"
                                                         : "../components/icons/triangle-alert_iconNeutral.svg"
                        iconFill: services.waiting > 0 ? Theme.warningOnLight : Theme.tile
                        iconBorder: services.waiting > 0 ? Theme.warningOnLight : Theme.tileBorder
                        label: qsTr("Not analyzed")
                        valueText: card.current ? card.current.notAnalyzed : "—"
                        note: services.waiting === 0 ? qsTr("Nothing waiting")
                              : card.allStaff ? qsTr("Not sent yet") : qsTr("Review")
                        noteColor: services.waiting > 0 ? Theme.warningOnDark : Theme.textFaint
                        noteStrong: services.waiting > 0
                        noteIcon: services.waiting > 0 && !card.allStaff ? "../components/icons/arrow-right_warningOnDark.svg" : ""
                        onActivated: card.openTab("notAnalyzed")
                    }
                }
            }
        }

        Text {
            objectName: "overviewCaption"
            visible: card.ready
            Layout.fillWidth: true
            text: (card.allStaff
                   ? qsTr("Averages of analyzed services, all staff. Change is %1. Good 85 and up, Fair 70–84, Needs improvement below 70.")
                   : qsTr("Averages of analyzed services. Change is %1. Good 85 and up, Fair 70–84, Needs improvement below 70."))
                  .arg(card.compared)
            color: Theme.textFaint
            font.pixelSize: Theme.fontLabel
            wrapMode: Text.Wrap
        }
    }
}
