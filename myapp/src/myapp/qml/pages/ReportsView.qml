pragma ComponentBehavior: Bound
import QtQuick
// 自訂了清單項目的 background／contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 報表（兩端共用，UX-46）：server_reports_page.qml、admin_reports_page.qml 都是這一頁。
// - 報表還沒有來源（src/myapp/pdf/ 不存在，PLAN D7）：空狀態「No reports yet」，不再放一顆不會動的按鈕（UX-26）。
// - 有 PDF 時：左邊是清單，右邊是檢視器。開檔、畫頁面都在 pdf lane（ENG-07）；解析度依顯示的高度，Retina 上不糊。
//   兩端都能翻頁（以前只有管理員端可以，UX-27）；要放大或列印，用「Open in Default App」交給系統的 PDF 程式。
// - 換頁時舊的頁面留著、變淡（DS-25）；打不開時原地顯示錯誤和 Retry（DS-27）。
Item {
    id: reports
    anchors.fill: parent

    readonly property var handler: typeof pdfHandler !== "undefined" ? pdfHandler : null
    readonly property string listState: handler ? handler.state : "idle"
    readonly property string viewState: handler ? handler.viewState : "idle"
    readonly property bool hasPage: handler !== null && handler.pageSource !== ""

    Component.onCompleted: {
        if (handler)
            handler.refresh()
    }

    function openReport(name) {
        if (handler)
            handler.open(name, Math.round(pageFrame.height * Screen.devicePixelRatio))
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

    component ReportItem: ItemDelegate {
        id: item
        required property string modelData
        objectName: "reportItem"
        width: ListView.view ? ListView.view.width : 0
        height: Theme.controlHeight
        focusPolicy: Qt.StrongFocus
        text: modelData
        highlighted: reports.handler !== null && reports.handler.current === modelData
        Accessible.name: text
        onClicked: reports.openReport(modelData)

        contentItem: Text {
            leftPadding: Theme.spaceS
            text: item.text
            color: Theme.textPrimary
            font.pixelSize: Theme.fontBody
            font.weight: item.highlighted ? Font.DemiBold : Font.Normal
            elide: Text.ElideMiddle
            verticalAlignment: Text.AlignVCenter
        }

        background: Rectangle {
            radius: Theme.radiusS
            color: item.highlighted ? Theme.surfaceMuted : item.hovered ? Theme.surfaceAlt : Theme.contentBg
            border.width: 1
            border.color: item.highlighted ? Theme.textSecondary : Theme.divider

            Rectangle {     // 鍵盤焦點框（DS-30）
                visible: item.visualFocus
                anchors.fill: parent
                anchors.margins: -3
                radius: parent.radius + 3
                color: "transparent"
                border.color: Theme.focusOnLight
                border.width: 2
            }
        }
    }

    Wait { id: openingWait; active: reports.viewState === "loading" }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spaceL
        spacing: Theme.spaceM

        Text {
            text: qsTr("Reports")
            color: Theme.textPrimary
            font.pixelSize: Theme.fontTitle
            Accessible.role: Accessible.Heading
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            EmptyState {
                objectName: "reportsEmpty"
                anchors.horizontalCenter: parent.horizontalCenter
                y: Theme.space4XL
                width: Math.min(parent.width, 360)
                visible: reports.listState === "empty"
                title: qsTr("No reports yet")
                description: qsTr("Reports will appear here once they're available.")
            }

            RowLayout {
                anchors.fill: parent
                visible: reports.listState === "ready"
                spacing: Theme.spaceL

                ListView {
                    id: reportList
                    objectName: "reportList"
                    Layout.preferredWidth: 280
                    Layout.fillHeight: true
                    clip: true
                    spacing: 4
                    model: reports.handler ? reports.handler.reports : []
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {}
                    Accessible.role: Accessible.List
                    Accessible.name: qsTr("Reports")
                    delegate: ReportItem {}
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: Theme.spaceS

                    Rectangle {
                        id: pageFrame
                        objectName: "pageFrame"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        radius: Theme.radiusS
                        color: reports.hasPage || reports.viewState === "error" ? Theme.contentBg : Theme.surfaceMuted
                        border.width: 1
                        border.color: Theme.divider
                        clip: true

                        Image {
                            id: pageImage
                            objectName: "pageImage"
                            anchors.fill: parent
                            anchors.margins: 1
                            visible: reports.hasPage && reports.viewState !== "error"
                            opacity: reports.viewState === "loading" && openingWait.elapsed ? 0.5 : 1    // 換頁中：舊的頁面變淡
                            fillMode: Image.PreserveAspectFit
                            asynchronous: true      // 解碼不在 GUI 執行緒（ENG-19）
                            sourceSize.height: Math.ceil(height * Screen.devicePixelRatio)
                            source: reports.handler ? reports.handler.pageSource : ""
                            Accessible.role: Accessible.Graphic
                            Accessible.name: reports.handler ? qsTr("%1, page %2 of %3").arg(reports.handler.current)
                                                                .arg(reports.handler.page).arg(reports.handler.pageCount) : ""
                        }

                        EmptyState {
                            objectName: "chooseReport"
                            anchors.centerIn: parent
                            width: Math.min(parent.width - 2 * Theme.spaceL, 360)
                            visible: reports.viewState === "idle"
                            title: qsTr("Choose a report")
                            description: qsTr("Select a report on the left to view it here.")
                        }

                        Text {
                            objectName: "openingText"
                            anchors.centerIn: parent
                            visible: reports.viewState === "loading" && !reports.hasPage && openingWait.elapsed
                            text: qsTr("Opening %1…").arg(reports.handler ? reports.handler.current : "")
                            color: Theme.textSecondary
                            font.pixelSize: Theme.fontCaption
                        }

                        InlineError {
                            objectName: "reportError"
                            anchors.centerIn: parent
                            width: Math.min(parent.width - 2 * Theme.spaceL, 480)
                            visible: reports.viewState === "error"
                            title: qsTr("Couldn't open this report")
                            detail: reports.handler ? reports.handler.errorDetail : ""
                            technicalDetail: reports.handler ? reports.handler.errorTechnical : ""
                            failureCount: reports.handler ? reports.handler.failureCount : 0
                            onRetry: {
                                if (reports.handler)
                                    reports.handler.retry()
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        visible: reports.hasPage && reports.viewState !== "error"
                        spacing: Theme.spaceM

                        BusyButton {
                            objectName: "previousPageButton"
                            text: qsTr("Previous")
                            enabled: reports.viewState === "ready" && reports.handler.page > 1
                            onClicked: reports.handler.previousPage()
                        }
                        Text {
                            objectName: "pageCounter"
                            text: reports.handler ? qsTr("Page %1 of %2").arg(reports.handler.page).arg(reports.handler.pageCount) : ""
                            color: Theme.textPrimary
                            font.pixelSize: Theme.fontCaption
                            font.features: { "tnum": 1 }
                        }
                        BusyButton {
                            objectName: "nextPageButton"
                            text: qsTr("Next")
                            enabled: reports.viewState === "ready" && reports.handler.page < reports.handler.pageCount
                            onClicked: reports.handler.nextPage()
                        }
                        Item { Layout.fillWidth: true }
                        BusyButton {
                            objectName: "openExternallyButton"
                            text: qsTr("Open in Default App")
                            onClicked: Qt.openUrlExternally(reports.handler.currentFileUrl)
                        }
                    }
                }
            }
        }
    }
}
