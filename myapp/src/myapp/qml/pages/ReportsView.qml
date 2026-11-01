pragma ComponentBehavior: Bound
import QtQuick
// 自訂了清單項目、翻頁按鈕的 background／contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 報表（兩端共用，UX-46）：server_reports_page.qml、admin_reports_page.qml 都是這一頁（新版介面 PLAN 6-6，
// 設計稿 StaffReports、AdminReports；new-feature N4、N5，T37）。頁面標題和說明在外殼的頁首，這裡不畫。
// - 資料在 reportsModel（myapp/reports.py）：打開時讀一次（pageOpened），之後由它自己輪詢，關掉時停（pageClosed）。
//   服務人員只看得到自己已發佈的（sp_list_reports 決定），管理員看得到全部。
// - 左邊一張卡片：「3 reports」，每一份一個按鈕（名稱、第二行的種類和時間）。管理員多一個 New Report（打開
//   ReportGenerator 表單，PLAN 13.1：需要時才展開），每一份右邊有狀態標籤（Generating、Ready、Published、Failed），
//   失敗的第二行是原因。一進來就選第一份（最新的）。
// - 右邊一張卡片：選到的那一份。好了（ready）就是 PDF：名稱、上一頁、「Page 1 of 4」、下一頁、Open in Default App，
//   頁面放在淺灰的紙上（UD8）；管理員多一列狀態和 Publish。產生中是說明；失敗是原因和 Retry。
//   頁面是 A4 直式，最寬 520（設計稿），也不超過看得到的高度。
// - 下載在 io lane、開檔和畫頁面在 pdf lane（pdfHandler）；換頁時舊的頁面留著、變淡（DS-25）；打不開時原地顯示錯誤
//   和 Retry（DS-27）。
Item {
    id: reports

    readonly property var vm: typeof reportsModel !== "undefined" ? reportsModel : null
    readonly property var handler: typeof pdfHandler !== "undefined" ? pdfHandler : null
    readonly property bool admin: vm !== null && vm.isAdmin
    readonly property string listState: vm ? vm.listState : "idle"
    readonly property var current: vm ? vm.selected : ({})          // 選到的那一份（清單的一列）
    readonly property bool hasCurrent: current.reportId !== undefined
    readonly property string currentStatus: hasCurrent ? current.status : ""
    readonly property string viewState: handler ? handler.viewState : "idle"
    readonly property bool hasPage: currentStatus === "ready" && handler !== null && handler.pageSource !== ""
                                    && handler.current === current.reportId
    readonly property bool publishing: hasCurrent && vm !== null && vm.publishing.indexOf(current.reportId) >= 0
    readonly property var generator: generatorLoader.item       // 產生報表的表單（管理員；測試也用它）

    // 版面（設計稿）：清單卡最寬 380，和檢視器之間 24；紙的內距 24、頁面最寬 520。
    // 管理員的清單多一個狀態標籤：寬一點（300～440），名稱才不會被截斷
    readonly property int gap: Theme.sectionGap
    readonly property int listWidth: admin ? Math.round(Math.min(440, Math.max(300, (width - gap) * 0.33)))
                                           : Math.round(Math.min(380, Math.max(260, (width - gap) * 0.3)))
    readonly property int itemHeight: admin ? 72 : 64
    readonly property int paperPadding: Theme.spaceXL
    readonly property real pageAspect: 210 / 297
    // 頁面以外的高度：卡片的上下內距、工具列、管理員的狀態列、間距、紙的上下內距
    readonly property real viewerChrome: viewerCard.topPadding + viewerCard.bottomPadding + Theme.controlHeight
                                         + (admin ? Theme.controlHeight + viewerColumn.spacing : 0)
                                         + viewerColumn.spacing + 2 * paperPadding
    readonly property real pageWidth: Math.floor(Math.max(200, Math.min(520, viewerCard.availableWidth - 2 * paperPadding,
                                                                        Math.max(360, height - viewerChrome) * pageAspect)))
    readonly property real pageHeight: Math.floor(pageWidth / pageAspect)

    function openReport(reportId) {
        if (vm && reportId !== "")
            vm.select(reportId, Math.round(pageHeight * Screen.devicePixelRatio))
    }

    // 沒有選到（或選到的已經不在清單裡）就選第一份；選到的好了、但檢視器還沒打開它（從通知過來的）就打開
    function ensureSelection() {
        if (!vm || listState !== "ready")
            return
        if (!hasCurrent)
            openReport(vm.firstReportId())
        else if (currentStatus === "ready" && handler && handler.current !== current.reportId)
            openReport(current.reportId)
    }

    function openGenerator() {
        if (generatorLoader.item)
            generatorLoader.item.open()
    }

    Component.onCompleted: {
        if (!vm)
            return
        vm.pageOpened("reports")
        settle.start()
    }
    Component.onDestruction: {
        if (vm)
            vm.pageClosed("reports")
    }
    onListStateChanged: settle.restart()

    // 等這一輪的版面算完再選，頁面的大小才準（決定解析度）
    Timer {
        id: settle
        interval: 0
        onTriggered: reports.ensureSelection()
    }

    Connections {
        target: reports.vm
        function onSelectedChanged() {
            if (!reports.hasCurrent)
                settle.restart()    // 選到的那一份不在了：換成第一份
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

    // 清單裡的一份報表：整塊可以點（64 高；管理員 72：第二行前面有狀態標籤），Tab、Space、Return 都能按
    component ReportItem: ItemDelegate {
        id: item

        required property int index
        required property string reportId
        required property string title
        required property string metaText
        required property string chipState
        required property string status
        readonly property bool selected: reports.vm !== null && reports.vm.selectedId === reportId
        readonly property bool failed: status === "failed"

        objectName: "reportItem"
        width: ListView.view ? ListView.view.width : 0
        height: reports.itemHeight
        padding: 12
        focusPolicy: Qt.StrongFocus
        text: title
        highlighted: selected
        Accessible.name: title
        Accessible.description: reports.admin ? chip.label + ", " + metaText : metaText
        Accessible.selectable: true
        Accessible.selected: selected
        Keys.onReturnPressed: click()
        Keys.onEnterPressed: click()
        onClicked: reports.openReport(reportId)

        contentItem: RowLayout {
            spacing: 12

            Image {
                Layout.alignment: Qt.AlignVCenter
                Layout.preferredWidth: Theme.iconNav
                Layout.preferredHeight: Theme.iconNav
                sourceSize: Qt.size(Theme.iconNav, Theme.iconNav)
                source: item.selected ? "../components/icons/file-chart-column_chromeIcon.svg"
                                      : "../components/icons/file-chart-column_textFaint.svg"
                Accessible.ignored: true
            }

            Column {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                spacing: reports.admin ? 4 : 2

                Text {
                    objectName: "reportTitle"
                    width: parent.width
                    text: item.title
                    color: Theme.textTitle
                    font.pixelSize: Theme.fontInput
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                    Accessible.ignored: true
                }

                // 第二行：管理員先是狀態（服務人員的清單都是已發佈的，不必標），再寫種類和時間；失敗的寫原因
                RowLayout {
                    width: parent.width
                    spacing: 8

                    StatusChip {
                        id: chip
                        objectName: "reportChip"
                        visible: reports.admin
                        reportState: item.chipState
                    }

                    Text {
                        objectName: "reportMeta"
                        Layout.fillWidth: true
                        text: item.metaText
                        color: item.failed ? Theme.errorOnDark : Theme.textMuted
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
    Wait { id: listWait; active: reports.listState === "loading" || reports.listState === "idle" }

    // ── 第一次載入：過了 busyDelay 才說在載入（不轉圈，DS-25）
    Text {
        objectName: "reportsLoading"
        anchors.horizontalCenter: parent.horizontalCenter
        y: Theme.space4XL
        visible: listWait.elapsed && (reports.listState === "loading" || reports.listState === "idle")
        text: qsTr("Loading reports…")
        color: Theme.textMuted
        font.pixelSize: Theme.fontCaption
    }

    // ── 清單查不到（第一次就失敗；之後的輪詢失敗時清單留著）
    Card {
        objectName: "reportsErrorCard"
        width: parent.width
        visible: reports.listState === "error"
        topPadding: Theme.space4XL
        bottomPadding: Theme.space4XL

        InlineError {
            objectName: "reportsError"
            width: parent.width
            title: qsTr("Couldn't load reports")
            detail: reports.vm ? reports.vm.errorDetail : ""
            technicalDetail: reports.vm ? reports.vm.errorTechnical : ""
            failureCount: reports.vm ? reports.vm.failureCount : 0
            busy: reports.listState === "loading"
            onRetry: {
                if (reports.vm)
                    reports.vm.refresh()
            }
        }
    }

    // ── 沒有報表
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
            title: reports.admin ? qsTr("No reports yet") : qsTr("No published reports yet")
            description: reports.admin ? qsTr("Generate a report to see it here.")
                                       : qsTr("Reports appear here after an admin publishes them.")
            actionText: reports.admin ? qsTr("New Report") : ""
            onActionTriggered: reports.openGenerator()
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

                RowLayout {
                    Layout.leftMargin: 12
                    Layout.rightMargin: 4
                    Layout.bottomMargin: 4
                    Layout.fillWidth: true
                    spacing: Theme.spaceS

                    Text {
                        objectName: "reportCount"
                        Layout.fillWidth: true
                        readonly property int count: reports.vm ? reports.vm.count : 0
                        text: count === 1 ? qsTr("1 report") : qsTr("%1 reports").arg(count)
                        color: Theme.textTitle
                        font.pixelSize: Theme.fontBody
                        font.weight: Font.Bold
                        elide: Text.ElideRight
                        Accessible.role: Accessible.Heading
                        Accessible.name: text
                    }

                    // 管理員：產生一份新的（表單需要時才打開，PLAN 13.1）
                    BusyButton {
                        objectName: "newReportButton"
                        visible: reports.admin
                        variant: "primary"
                        text: qsTr("New Report")
                        iconSource: "../components/icons/plus_actionPrimaryText.svg"
                        font.pixelSize: Theme.fontCaption
                        onClicked: reports.openGenerator()
                    }
                }

                // 報表很多時，清單在卡片裡捲動，卡片不比檢視器高
                ListView {
                    id: reportList
                    objectName: "reportList"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(contentHeight, Math.max(reports.itemHeight * 4 + 8 * 3,
                        viewerCard.height - listCard.topPadding - listCard.bottomPadding - 58))
                    clip: true
                    spacing: 8
                    interactive: contentHeight > height
                    model: reports.vm ? reports.vm.listModel : null     // 依 report_id 增量更新：狀態變了那一列原地更新
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
                        text: reports.hasCurrent ? reports.current.title : ""
                        color: Theme.textTitle
                        font.pixelSize: Theme.fontBodyLarge
                        font.weight: Font.Bold
                        elide: Text.ElideRight
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

                // 管理員：狀態和發佈（R6：發佈之後服務人員才看得到；沒有取消發佈）
                RowLayout {
                    objectName: "publishRow"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Theme.controlHeight
                    visible: reports.admin
                    spacing: Theme.spaceS

                    StatusChip {
                        objectName: "currentChip"
                        Layout.alignment: Qt.AlignVCenter
                        visible: reports.hasCurrent
                        reportState: reports.hasCurrent ? reports.current.chipState : ""
                    }

                    Text {
                        objectName: "publishNote"
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignVCenter
                        text: {
                            const report = reports.current
                            if (!reports.hasCurrent || report.status !== "ready")
                                return ""       // 產生中、失敗：下面的區塊會說明
                            if (report.kind === "company")
                                return qsTr("Only admins can see company reports.")
                            if (report.published)
                                return qsTr("%1. Staff can see it.").arg(report.publishedText)
                            return qsTr("Staff can't see this report until you publish it.")
                        }
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontCaption
                        elide: Text.ElideRight
                    }

                    BusyButton {
                        objectName: "publishButton"
                        visible: reports.hasCurrent && reports.current.canPublish
                        variant: "primary"
                        text: qsTr("Publish")
                        busyText: qsTr("Publishing…")
                        busy: reports.publishing
                        font.pixelSize: Theme.fontCaption
                        Accessible.name: qsTr("Publish %1").arg(reports.hasCurrent ? reports.current.title : "")
                        onClicked: {
                            if (!busy && reports.vm)
                                reports.vm.publish(reports.current.reportId)
                        }
                    }
                }

                // 淺灰的紙（UD8）：白色的 PDF 頁面直接放在深色卡片上太刺眼
                Rectangle {
                    id: paper
                    objectName: "paper"
                    Layout.fillWidth: true
                    Layout.preferredHeight: reports.pageHeight + 2 * reports.paperPadding
                    visible: reports.currentStatus === "ready" && reports.viewState !== "error"
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
                        source: reports.hasPage ? reports.handler.pageSource : ""
                        Accessible.role: Accessible.Graphic
                        Accessible.name: reports.handler ? qsTr("%1, page %2 of %3").arg(reports.handler.currentTitle)
                                                            .arg(reports.handler.page).arg(reports.handler.pageCount) : ""
                    }

                    Text {
                        objectName: "openingText"
                        anchors.centerIn: parent
                        visible: reports.viewState === "loading" && !reports.hasPage && openingWait.elapsed
                        text: qsTr("Opening %1…").arg(reports.hasCurrent ? reports.current.title : "")
                        color: Theme.chromeOnLight
                        font.pixelSize: Theme.fontCaption
                    }
                }

                InlineError {
                    objectName: "reportError"
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.space3XL
                    Layout.bottomMargin: Theme.space3XL
                    visible: reports.currentStatus === "ready" && reports.viewState === "error"
                    title: qsTr("Couldn't open this report")
                    detail: reports.handler ? reports.handler.errorDetail : ""
                    technicalDetail: reports.handler ? reports.handler.errorTechnical : ""
                    failureCount: reports.handler ? reports.handler.failureCount : 0
                    onRetry: {
                        if (reports.handler)
                            reports.handler.retry()
                    }
                }

                // 產生中、失敗（只有管理員看得到）：和紙一樣大的深色區塊，換一份時版面不跳，清單卡的高度也不變
                Rectangle {
                    objectName: "statusPanel"
                    Layout.fillWidth: true
                    Layout.preferredHeight: reports.pageHeight + 2 * reports.paperPadding
                    visible: reports.currentStatus === "generating" || reports.currentStatus === "failed"
                    radius: 12
                    color: Theme.tile

                    // 產生中：說明，不轉圈（狀態標籤已經說了）
                    Column {
                        objectName: "generatingState"
                        anchors.centerIn: parent
                        width: Math.min(parent.width - 2 * Theme.spaceXL, 420)
                        visible: reports.currentStatus === "generating"
                        spacing: Theme.spaceS

                        Image {
                            anchors.horizontalCenter: parent.horizontalCenter
                            width: Theme.iconM
                            height: Theme.iconM
                            sourceSize: Qt.size(Theme.iconM, Theme.iconM)
                            source: "../components/icons/clock_infoOnDark.svg"
                            Accessible.ignored: true
                        }
                        Text {
                            width: parent.width
                            text: qsTr("Generating this report…")
                            color: Theme.textPrimary
                            font.pixelSize: Theme.fontBody
                            font.weight: Font.DemiBold
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.Wrap
                        }
                        Text {
                            width: parent.width
                            text: qsTr("The analysis service is making the PDF. It opens here when it's ready.")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontCaption
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.Wrap
                        }
                    }

                    // 失敗：原因和 Retry（Retry 是同一個對象的新的一列，舊的留著，R5）
                    InlineError {
                        objectName: "generateError"
                        anchors.centerIn: parent
                        width: Math.min(parent.width - 2 * Theme.spaceXL, 480)
                        visible: reports.currentStatus === "failed"
                        title: qsTr("Couldn't generate this report")
                        detail: reports.hasCurrent ? reports.current.failureReason : ""
                        busy: reports.vm !== null && reports.vm.submitting
                        onRetry: {
                            if (reports.vm && reports.hasCurrent)
                                reports.vm.retry(reports.current.reportId)
                        }
                    }
                }
            }
        }
    }

    // 產生報表的表單（只有管理員）
    Loader {
        id: generatorLoader
        active: reports.admin
        sourceComponent: ReportGenerator {
            vm: reports.vm
        }
    }
}
