pragma ComponentBehavior: Bound
import QtQuick
// 自訂了清單項目、翻頁按鈕的 background／contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 報表（兩端共用，UX-46）：server_reports_page.qml、admin_reports_page.qml 都是這一頁（新版介面 PLAN 6-6，
// 設計稿 StaffReports、AdminReports）。頁面標題和說明在外殼的頁首，這裡不畫。
// - 左邊一張卡片：「3 reports」，每一份報表一個按鈕（名稱、頁數、日期），選到的有底色。新的在前，一進來就打開第一份。
// - 右邊一張卡片：報表名稱、上一頁、「Page 1 of 4」、下一頁、Open in Default App；頁面放在淺灰的紙上（UD8）。
//   頁面是 A4 直式，最寬 520（設計稿），也不超過看得到的高度：1440×900 的視窗不用捲動就看得到整頁。
// - 管理員端沒有 Staff 選單：報表還沒有來源（frontend-rebuild PLAN D7），PDF 也沒有記是哪一位員工的（T33）。
// - 沒有報表：空狀態「No reports yet」（D7）。開檔、畫頁面、數頁數都在 pdf lane（ENG-07）；
//   換頁時舊的頁面留著、變淡（DS-25）；打不開時原地顯示錯誤和 Retry（DS-27）。
Item {
    id: reports

    readonly property var handler: typeof pdfHandler !== "undefined" ? pdfHandler : null
    readonly property string listState: handler ? handler.state : "idle"
    readonly property string viewState: handler ? handler.viewState : "idle"
    readonly property var items: handler ? handler.reports : []     // [{name, title, pages, modified}]，新的在前
    readonly property bool hasPage: handler !== null && handler.pageSource !== ""

    // 版面（設計稿）：清單卡最寬 380，和檢視器之間 24；紙的內距 24、頁面最寬 520
    readonly property int gap: Theme.sectionGap
    readonly property int listWidth: Math.round(Math.min(380, Math.max(260, (width - gap) * 0.3)))
    readonly property int paperPadding: Theme.spaceXL
    readonly property real pageAspect: 210 / 297
    // 頁面以外的高度：卡片的上下內距、工具列、間距、紙的上下內距
    readonly property real viewerChrome: viewerCard.topPadding + viewerCard.bottomPadding + Theme.controlHeight
                                         + viewerColumn.spacing + 2 * paperPadding
    readonly property real pageWidth: Math.floor(Math.max(200, Math.min(520, viewerCard.availableWidth - 2 * paperPadding,
                                                                        Math.max(360, height - viewerChrome) * pageAspect)))
    readonly property real pageHeight: Math.floor(pageWidth / pageAspect)

    function openReport(name) {
        if (handler)
            handler.open(name, Math.round(pageHeight * Screen.devicePixelRatio))
    }

    function pagesText(pages) {
        return pages === 1 ? qsTr("1 page") : qsTr("%1 pages").arg(pages)
    }

    Component.onCompleted: {
        if (!handler)
            return
        handler.refresh()
        if (handler.current === "")
            openFirst.start()
    }

    // 一進來就打開最新的一份（設計稿：第一份是選好的）。等這一輪的版面算完，頁面的大小才準（決定解析度）
    Timer {
        id: openFirst
        interval: 0
        onTriggered: {
            if (reports.handler && reports.handler.current === "" && reports.items.length > 0)
                reports.openReport(reports.items[0].name)
        }
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

    // 清單裡的一份報表：整塊可以點（64 高），Tab、Space、Return 都能按
    component ReportItem: ItemDelegate {
        id: item

        required property int index
        readonly property var entry: reports.items[index] || ({ name: "", title: "", pages: 0, modified: "" })
        readonly property bool selected: reports.handler !== null && reports.handler.current === entry.name
        readonly property string meta: entry.pages > 0 ? qsTr("%1 · %2").arg(reports.pagesText(entry.pages)).arg(entry.modified)
                                                        : entry.modified

        objectName: "reportItem"
        width: ListView.view ? ListView.view.width : 0
        height: 64
        padding: 12
        focusPolicy: Qt.StrongFocus
        text: entry.title
        highlighted: selected
        Accessible.name: entry.title
        Accessible.description: meta
        Accessible.selectable: true
        Accessible.selected: selected
        Keys.onReturnPressed: click()
        Keys.onEnterPressed: click()
        onClicked: reports.openReport(entry.name)

        contentItem: Row {
            spacing: 12

            Image {
                anchors.verticalCenter: parent.verticalCenter
                width: Theme.iconNav
                height: Theme.iconNav
                sourceSize: Qt.size(Theme.iconNav, Theme.iconNav)
                source: item.selected ? "../components/icons/file-chart-column_chromeIcon.svg"
                                      : "../components/icons/file-chart-column_textFaint.svg"
                Accessible.ignored: true
            }

            Column {
                anchors.verticalCenter: parent.verticalCenter
                width: item.availableWidth - Theme.iconNav - 12
                spacing: 2

                Text {
                    objectName: "reportTitle"
                    width: parent.width
                    text: item.entry.title
                    color: Theme.textTitle
                    font.pixelSize: Theme.fontInput
                    font.weight: Font.DemiBold
                    elide: Text.ElideMiddle
                    Accessible.ignored: true
                }

                Text {
                    objectName: "reportMeta"
                    width: parent.width
                    text: item.meta
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                    font.features: { "tnum": 1 }
                    elide: Text.ElideRight
                    Accessible.ignored: true
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

    // 上一頁、下一頁：44 的圓形按鈕，只有圖示（名稱給輔助技術）
    component PageButton: Button {
        id: button

        property url iconSource: ""             // 可以按的時候
        property url disabledIconSource: ""     // 停用（第一頁、最後一頁）

        implicitWidth: Theme.controlHeight
        implicitHeight: Theme.controlHeight
        padding: 0
        focusPolicy: Qt.StrongFocus
        Accessible.name: text
        Keys.onReturnPressed: click()
        Keys.onEnterPressed: click()

        contentItem: Item {
            Image {
                anchors.centerIn: parent
                width: 18
                height: 18
                sourceSize: Qt.size(18, 18)
                source: button.enabled ? button.iconSource : button.disabledIconSource
                Accessible.ignored: true
            }
        }

        background: Rectangle {
            radius: height / 2
            color: !button.enabled ? Theme.actionDisabledBg : button.down ? Theme.actionSecondaryPressed
                 : button.hovered ? Theme.actionSecondaryHover : Theme.actionSecondaryBg
            border.width: 1
            border.color: button.enabled ? Theme.actionSecondaryBorder : Theme.cardBorder

            Rectangle {     // 鍵盤焦點框（DS-30）
                visible: button.visualFocus
                anchors.fill: parent
                anchors.margins: -4
                radius: parent.radius + 4
                color: "transparent"
                border.color: Theme.focusOnDark
                border.width: 2
            }
        }
    }

    Wait { id: openingWait; active: reports.viewState === "loading" }

    // ── 沒有報表（D7）
    Card {
        objectName: "reportsEmptyCard"
        width: parent.width
        visible: reports.listState === "empty"
        topPadding: Theme.space4XL
        bottomPadding: Theme.space4XL

        EmptyState {
            objectName: "reportsEmpty"
            width: parent.width
            visible: reports.listState === "empty"
            title: qsTr("No reports yet")
            description: qsTr("Reports will appear here once they're available.")
        }
    }

    // ── 清單和檢視器。內容比視窗高時整頁捲動（新版介面：頁面自己處理捲動）
    Flickable {
        id: scroller
        anchors.fill: parent
        visible: reports.listState === "ready"
        contentWidth: width
        contentHeight: Math.max(listCard.height, viewerCard.height)
        boundsBehavior: Flickable.StopAtBounds
        clip: true
        ScrollBar.vertical: ScrollBar {}

        Card {
            id: listCard
            objectName: "reportListCard"
            width: reports.listWidth
            topPadding: Theme.spaceL
            bottomPadding: Theme.spaceL
            leftPadding: Theme.spaceM
            rightPadding: Theme.spaceM

            ColumnLayout {
                width: parent.width
                spacing: 8

                Text {
                    objectName: "reportCount"
                    Layout.leftMargin: 12
                    Layout.rightMargin: 12
                    Layout.bottomMargin: 4
                    Layout.fillWidth: true
                    text: reports.items.length === 1 ? qsTr("1 report") : qsTr("%1 reports").arg(reports.items.length)
                    color: Theme.textTitle
                    font.pixelSize: Theme.fontBody
                    font.weight: Font.Bold
                    Accessible.role: Accessible.Heading
                    Accessible.name: text
                }

                // 報表很多時，清單在卡片裡捲動，卡片不比檢視器高
                ListView {
                    id: reportList
                    objectName: "reportList"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(contentHeight, Math.max(64 * 4 + 8 * 3,
                        viewerCard.height - listCard.topPadding - listCard.bottomPadding - 34))
                    clip: true
                    spacing: 8
                    interactive: contentHeight > height
                    model: reports.items.length     // 只看份數：頁數數好時同一個項目原地更新，焦點不會不見
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { policy: reportList.interactive ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff }
                    Accessible.role: Accessible.List
                    Accessible.name: qsTr("Reports")
                    delegate: ReportItem {}
                }
            }
        }

        Card {
            id: viewerCard
            objectName: "reportViewer"
            x: listCard.width + reports.gap
            width: scroller.width - x
            topPadding: Theme.spaceL
            bottomPadding: Theme.spaceXL
            leftPadding: Theme.spaceXL
            rightPadding: Theme.spaceXL

            ColumnLayout {
                id: viewerColumn
                width: parent.width
                spacing: Theme.spaceM

                RowLayout {
                    id: toolbar
                    Layout.fillWidth: true
                    Layout.preferredHeight: Theme.controlHeight
                    spacing: 14

                    Text {
                        objectName: "reportName"
                        Layout.fillWidth: true
                        text: reports.handler ? reports.handler.currentTitle : ""
                        color: Theme.textTitle
                        font.pixelSize: Theme.fontBodyLarge
                        font.weight: Font.Bold
                        elide: Text.ElideMiddle
                        Accessible.role: Accessible.Heading
                        Accessible.name: text
                    }

                    PageButton {
                        objectName: "previousPageButton"
                        visible: reports.hasPage && reports.viewState !== "error"
                        text: qsTr("Previous page")
                        iconSource: "../components/icons/chevron-left_actionSecondaryText.svg"
                        disabledIconSource: "../components/icons/chevron-left_textDisabled.svg"
                        enabled: reports.viewState === "ready" && reports.handler.page > 1
                        onClicked: reports.handler.previousPage()
                    }
                    Text {
                        objectName: "pageCounter"
                        visible: reports.hasPage && reports.viewState !== "error"
                        text: reports.handler ? qsTr("Page %1 of %2").arg(reports.handler.page).arg(reports.handler.pageCount) : ""
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontCaption
                        font.features: { "tnum": 1 }
                    }
                    PageButton {
                        objectName: "nextPageButton"
                        visible: reports.hasPage && reports.viewState !== "error"
                        text: qsTr("Next page")
                        iconSource: "../components/icons/chevron-right_actionSecondaryText.svg"
                        disabledIconSource: "../components/icons/chevron-right_textDisabled.svg"
                        enabled: reports.viewState === "ready" && reports.handler.page < reports.handler.pageCount
                        onClicked: reports.handler.nextPage()
                    }
                    // 放大、列印、搜尋交給系統的 PDF 程式（UX-27）
                    BusyButton {
                        objectName: "openExternallyButton"
                        visible: reports.hasPage && reports.viewState !== "error"
                        text: qsTr("Open in Default App")
                        font.pixelSize: Theme.fontCaption
                        onClicked: Qt.openUrlExternally(reports.handler.currentFileUrl)
                    }
                }

                // 淺灰的紙（UD8）：白色的 PDF 頁面直接放在深色卡片上太刺眼
                Rectangle {
                    id: paper
                    objectName: "paper"
                    Layout.fillWidth: true
                    Layout.preferredHeight: reports.pageHeight + 2 * reports.paperPadding
                    visible: reports.viewState !== "error"
                    radius: 12
                    color: Theme.paper

                    Image {
                        id: pageImage
                        objectName: "pageImage"
                        anchors.centerIn: parent
                        width: reports.pageWidth
                        height: reports.pageHeight
                        visible: reports.hasPage
                        opacity: reports.viewState === "loading" && openingWait.elapsed ? 0.5 : 1    // 換頁中：舊的頁面變淡
                        fillMode: Image.PreserveAspectFit
                        asynchronous: true      // 解碼不在 GUI 執行緒（ENG-19）
                        sourceSize.height: Math.ceil(height * Screen.devicePixelRatio)
                        source: reports.handler ? reports.handler.pageSource : ""
                        Accessible.role: Accessible.Graphic
                        Accessible.name: reports.handler ? qsTr("%1, page %2 of %3").arg(reports.handler.currentTitle)
                                                            .arg(reports.handler.page).arg(reports.handler.pageCount) : ""
                    }

                    Text {
                        objectName: "openingText"
                        anchors.centerIn: parent
                        visible: reports.viewState === "loading" && !reports.hasPage && openingWait.elapsed
                        text: qsTr("Opening %1…").arg(reports.handler ? reports.handler.currentTitle : "")
                        color: Theme.chromeOnLight
                        font.pixelSize: Theme.fontCaption
                    }
                }

                InlineError {
                    objectName: "reportError"
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.space3XL
                    Layout.bottomMargin: Theme.space3XL
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
        }
    }
}
