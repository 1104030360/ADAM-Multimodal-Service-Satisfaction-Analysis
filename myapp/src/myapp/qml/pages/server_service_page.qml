pragma ComponentBehavior: Bound
import QtQuick
// 捲軸、自訂的控制項都用 Basic 樣式（T13）；日期欄（DateField.qml）、按鈕（BusyButton）自己也 import Basic
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// Services（服務人員，新版介面 U2，PLAN §6-4；設計稿 StaffServices）：找過去的服務。頁面標題和說明在外殼的頁首。
// - 條件卡片：From、To（YYYY-MM-DD）、Search；留空就不限，只列出分析完成的服務。
//   日期格式不對、To 早於 From：欄位下方顯示錯誤，不送出，焦點移到第一個有錯的欄位（DS-27）。
// - 結果卡片（ServiceTable.qml，兩端共用）：「18 services · Oct 19 – Nov 1, 2026 · newest first」、
//   Service ID、Date、Length、Customer、Staff、Details；一次 8 筆＋Show More（UD7）。拿掉 Name 欄（UD11）。
// - 第一次打開時用空白的日期查一次：一進來就看得到最近的 8 筆。換頁回來時，欄位是上一次送出的條件，結果留著（UX-24）。
// - 查詢在背景（serviceManager.search，replace=True：連按只留最後一次）；Details 交給外殼的 window.openServiceDetail()，
//   影音和圖表在明細頁的背景下載（UX-22、ENG-04）：這裡不碰網路。
// - 整頁一起捲動；用 Tab 走到下面的按鈕時捲進畫面。
Item {
    id: searchPage

    readonly property var manager: typeof serviceManager !== "undefined" ? serviceManager : null
    readonly property string status: manager ? manager.status : "idle"
    readonly property bool busy: manager !== null && manager.busy

    function submit() {
        if (!manager)
            return
        // 查詢中又送出（例如改了日期按 Enter）：條件一樣就不重送；條件改了就取消前一次、改查新的（replace=True，ENG-14）
        if (manager.busy && startDateField.text.trim() === manager.startDate
                && endDateField.text.trim() === manager.endDate)
            return
        if (!manager.search(startDateField.text, endDateField.text))   // 日期有錯：焦點移到第一個有錯的欄位
            (manager.startDateError !== "" ? startDateField : endDateField).forceActiveFocus()
    }

    // 查到 0 筆時的標題：寫出查了哪一段期間（DS-52）
    function emptyTitle() {
        const start = manager ? manager.startDate : ""
        const end = manager ? manager.endDate : ""
        if (start !== "" && end !== "")
            return qsTr("No services from %1 to %2").arg(start).arg(end)
        if (start !== "")
            return qsTr("No services since %1").arg(start)
        if (end !== "")
            return qsTr("No services up to %1").arg(end)
        return qsTr("No completed services yet")
    }

    // 把 item 捲進畫面（Tab 走到看不到的按鈕時）
    function reveal(item) {
        const top = item.mapToItem(scroller.contentItem, 0, 0).y
        if (top < scroller.contentY)
            scroller.contentY = Math.max(0, top - Theme.spaceM)
        else if (top + item.height > scroller.contentY + scroller.height)
            scroller.contentY = Math.min(scroller.contentHeight - scroller.height,
                                         top + item.height - scroller.height + Theme.spaceM)
    }

    Component.onCompleted: {
        if (!manager)
            return
        startDateField.text = manager.startDate
        endDateField.text = manager.endDate
        if (manager.status === "idle" && !manager.busy)
            manager.search("", "")      // 第一次打開：全部，新的在前
    }

    // 欄位上面的標籤
    component FieldLabel: Text {
        color: Theme.textSecondary
        font.pixelSize: Theme.fontCaption
        font.weight: Font.DemiBold
    }

    Flickable {
        id: scroller
        objectName: "servicesScroller"
        anchors.fill: parent
        contentWidth: width
        contentHeight: content.implicitHeight
        // 最後的內容不會被右下角的通知蓋住（DS-21）
        bottomMargin: typeof toastHost !== "undefined" && toastHost ? toastHost.occupiedHeight : 0
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}

        ColumnLayout {
            id: content
            width: scroller.width
            spacing: Theme.sectionGap

            // ── 條件
            Card {
                objectName: "criteriaCard"
                Layout.fillWidth: true
                topPadding: Theme.spaceL
                bottomPadding: Theme.spaceL
                Accessible.role: Accessible.Grouping
                Accessible.name: qsTr("Find services by date")

                // 一行放不下時往下排（設計稿的 flex-wrap）：Search 和說明跟著輸入框對齊
                Flow {
                    id: criteria
                    width: parent ? parent.width : 0    // 卡片的內容寬度（Pane 不會自己撐開它）
                    spacing: Theme.spaceM

                    readonly property real fieldTop: fromLabel.height + 8   // 輸入框的上緣（標籤下面）
                    readonly property real used: fromColumn.width + toColumn.width + searchColumn.width + 3 * spacing

                    Column {
                        id: fromColumn
                        spacing: 8

                        FieldLabel { id: fromLabel; text: qsTr("From") }
                        // 日期欄和管理員端共用（DateField.qml）。在任一欄按 Enter 都會送出
                        DateField {
                            id: startDateField
                            objectName: "startDateField"
                            accessibleName: qsTr("From date")
                            errorText: searchPage.manager ? searchPage.manager.startDateError : ""
                            invalid: errorText !== ""
                            onActiveFocusChanged: if (activeFocus) searchPage.reveal(this)
                            onTextEdited: {
                                if (searchPage.manager)
                                    searchPage.manager.clearDateError("start")
                            }
                            onAccepted: searchPage.submit()
                        }
                        // 欄位錯誤：出現時才佔位置（只在按了 Search 之後；見 T31 第 8 節）
                        FieldError {
                            objectName: "startDateError"
                            width: startDateField.width
                            visible: hasError
                            text: startDateField.errorText
                        }
                    }

                    Column {
                        id: toColumn
                        spacing: 8

                        FieldLabel { text: qsTr("To") }
                        DateField {
                            id: endDateField
                            objectName: "endDateField"
                            accessibleName: qsTr("To date")
                            errorText: searchPage.manager ? searchPage.manager.endDateError : ""
                            invalid: errorText !== ""
                            onActiveFocusChanged: if (activeFocus) searchPage.reveal(this)
                            onTextEdited: {
                                if (searchPage.manager)
                                    searchPage.manager.clearDateError("end")
                            }
                            onAccepted: searchPage.submit()
                        }
                        FieldError {
                            objectName: "endDateError"
                            width: endDateField.width
                            visible: hasError
                            text: endDateField.errorText
                        }
                    }

                    Item {
                        id: searchColumn
                        width: searchButton.implicitWidth
                        height: criteria.fieldTop + searchButton.height

                        BusyButton {
                            id: searchButton
                            objectName: "searchButton"
                            y: criteria.fieldTop
                            variant: "primary"
                            horizontalPadding: Theme.spaceXL
                            font.pixelSize: Theme.fontInput
                            text: qsTr("Search")
                            busyText: qsTr("Searching…")
                            busy: searchPage.busy
                            onActiveFocusChanged: if (activeFocus) searchPage.reveal(this)
                            onClicked: {
                                if (busy)
                                    return
                                searchPage.submit()
                            }
                        }
                    }

                    // 說明：同一行放得下（至少 240）就填滿剩下的寬度，否則自己一行
                    Text {
                        objectName: "criteriaHint"
                        width: criteria.width - criteria.used >= 240 ? criteria.width - criteria.used : criteria.width
                        topPadding: criteria.width - criteria.used >= 240
                                    ? criteria.fieldTop + Math.max(0, (Theme.controlHeight - 2 * 18) / 2) : 0
                        text: qsTr("Leave a date empty to search without that limit. Only services with a completed analysis are listed.")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                        lineHeight: 18
                        lineHeightMode: Text.FixedHeight
                        wrapMode: Text.Wrap
                    }
                }
            }

            // ── 結果（兩端共用 ServiceTable.qml）
            Card {
                objectName: "resultsCard"
                Layout.fillWidth: true
                bottomPadding: Theme.spaceM

                ServiceTable {
                    id: table
                    width: parent ? parent.width : 0
                    model: searchPage.manager ? searchPage.manager.listModel : null
                    listState: searchPage.status === "error" ? "error"
                             : searchPage.status !== "ready" ? "loading"
                             : searchPage.manager.totalCount > 0 ? "ready" : "empty"
                    busy: searchPage.busy
                    countText: searchPage.manager ? searchPage.manager.countText : ""
                    subtitle: searchPage.manager ? searchPage.manager.subtitle : ""
                    shownText: searchPage.manager ? searchPage.manager.shownText : ""
                    hasMore: searchPage.manager !== null && searchPage.manager.hasMore
                    loadingMore: searchPage.manager !== null && searchPage.manager.loadingMore
                    moreErrorDetail: searchPage.manager ? searchPage.manager.moreErrorDetail : ""
                    moreErrorTechnical: searchPage.manager ? searchPage.manager.moreErrorTechnical : ""
                    moreFailureCount: searchPage.manager ? searchPage.manager.moreFailureCount : 0
                    emptyTitle: searchPage.emptyTitle()
                    emptyDescription: searchPage.manager && (searchPage.manager.startDate !== ""
                                                             || searchPage.manager.endDate !== "")
                                      ? qsTr("Try a different date range.")
                                      : qsTr("Services appear here once their analysis is complete.")
                    errorTitle: qsTr("Couldn't search your services")
                    errorDetail: searchPage.manager ? searchPage.manager.errorDetail : ""
                    errorTechnical: searchPage.manager ? searchPage.manager.errorTechnical : ""
                    failureCount: searchPage.manager ? searchPage.manager.failureCount : 0

                    onOpenDetail: (serviceId, startTime) => window.openServiceDetail(serviceId, startTime)
                    onShowMore: searchPage.manager.showMore()
                    onRetry: searchPage.manager.retry()
                    onItemFocused: (item) => searchPage.reveal(item)
                }
            }
        }
    }
}
