pragma ComponentBehavior: Bound
import QtQuick
// 自訂了下拉選單的 background、contentItem、popup，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// Services（管理員，新版介面 U2，PLAN §6-9；設計稿 AdminServices）：找任何員工的服務。頁面標題和說明在外殼的頁首。
// - 條件卡片：Staff（All staff＋每位服務人員）、From、To、Search。名單和搜尋都在背景（Python 的 serviceManager）。
//   日期格式不對、To 早於 From：欄位下方顯示錯誤、框線變紅，不送出。名單沒有載入時，搜尋照樣能用（不篩選名字），可以重試。
// - 結果卡片（ServiceTable.qml，和服務人員端共用）：「138 services · All staff · Oct 19 – Nov 1, 2026 · newest first」，
//   多一欄 Staff；一次 8 筆＋Show More（UD7）。只列分析完成的服務，新的在前（T31 第 4 節）。
// - 第一次打開時查一次全部；回到這一頁時，上一次的條件放回欄位，結果留著。
// - Details 交給外殼的 window.openServiceDetail()：先換頁，影音和圖表在明細頁的背景下載（UX-22、ENG-04）。
Item {
    id: searchPage

    readonly property bool searching: serviceManager.state === "loading" || serviceManager.refreshing
    // 使用者在選單選的服務人員（account_id）；空字串＝不篩選（選單第 0 項「All staff」）。名單在背景載入，
    // 換名單時 ComboBox 會回到第 0 項，所以選了誰記在這裡，名單換好再放回去。
    // 用 account_id 不用名字：名字可以改、可以重複（同名的人選單上多寫登入帳號，new-feature T36）
    property string chosenStaff: ""

    function search() {
        if (!serviceManager.search(chosenStaff, startDateField.text, endDateField.text)
                && (serviceManager.startDateError !== "" || serviceManager.endDateError !== ""))
            (serviceManager.startDateError !== "" ? startDateField : endDateField).forceActiveFocus()
    }

    // 空狀態的標題：「No services from 2026-10-01 to 2026-10-31」（DS-52）
    function emptyTitle(query) {
        if (query.start && query.end)
            return qsTr("No services from %1 to %2").arg(query.start).arg(query.end)
        if (query.start)
            return qsTr("No services from %1 on").arg(query.start)
        if (query.end)
            return qsTr("No services up to %1").arg(query.end)
        return qsTr("No completed services yet")
    }

    // 選單顯示 chosenStaff 那個人；沒選是「All staff」。名單裡已經沒有這個人時也回到「All staff」，不再用他篩選
    function showChosenName() {
        const index = chosenStaff !== "" ? serviceManager.staffIds.indexOf(chosenStaff) + 1 : 0
        if (index <= 0)
            chosenStaff = ""
        nameComboBox.currentIndex = Math.max(0, index)
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

    // 回到這一頁時，把上一次搜尋的條件放回欄位（結果還在 Python 裡）；第一次打開時查一次全部
    Component.onCompleted: {
        const query = serviceManager.lastQuery
        startDateField.text = query.start
        endDateField.text = query.end
        chosenStaff = query.staffId
        showChosenName()
        serviceManager.loadNames()
        if (serviceManager.state === "idle")
            serviceManager.search("", "", "")
    }

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
                Accessible.name: qsTr("Find services by staff and date")

                // 一行放不下時往下排（設計稿的 flex-wrap）：Search 和說明跟著輸入框對齊
                Flow {
                    id: criteria
                    width: parent ? parent.width : 0    // 卡片的內容寬度（Pane 不會自己撐開它）
                    spacing: Theme.spaceM

                    readonly property real fieldTop: staffLabel.height + 8  // 輸入框的上緣（標籤下面）
                    readonly property real used: staffColumn.width + fromColumn.width + toColumn.width
                                                 + searchColumn.width + 4 * spacing

                    Column {
                        id: staffColumn
                        spacing: 8

                        FieldLabel { id: staffLabel; text: qsTr("Staff") }
                        // 服務人員：第 0 項是不篩選（以前選了名字就改不回全部，UX-36）
                        ComboBox {
                            id: nameComboBox
                            objectName: "nameComboBox"
                            implicitWidth: 240
                            implicitHeight: Theme.controlHeight
                            model: [qsTr("All staff")].concat(serviceManager.names)
                            currentIndex: 0
                            enabled: serviceManager.names.length > 0
                            font.pixelSize: Theme.fontInput
                            displayText: currentIndex > 0 ? currentText
                                       : serviceManager.names.length === 0 && serviceManager.namesState === "loading"
                                         ? qsTr("Loading staff…") : qsTr("All staff")
                            leftPadding: 14
                            rightPadding: 40
                            Accessible.name: qsTr("Staff")
                            onActivated: (index) => searchPage.chosenStaff = index > 0 ? serviceManager.staffIds[index - 1] : ""
                            // 名單內容變了，ComboBox 會回到第 0 項；等它換好，再放回使用者選的人
                            onModelChanged: Qt.callLater(searchPage.showChosenName)

                            contentItem: Text {
                                leftPadding: 0
                                text: nameComboBox.displayText
                                font: nameComboBox.font
                                color: nameComboBox.enabled ? Theme.textPrimary : Theme.textDisabled
                                verticalAlignment: Text.AlignVCenter
                                elide: Text.ElideRight
                            }

                            indicator: Image {
                                x: nameComboBox.width - width - 14
                                anchors.verticalCenter: parent.verticalCenter
                                width: Theme.iconS
                                height: Theme.iconS
                                sourceSize: Qt.size(Theme.iconS, Theme.iconS)
                                source: "../components/icons/chevron-down_iconNeutral.svg"
                                opacity: nameComboBox.enabled ? 1 : 0.5
                                Accessible.ignored: true
                            }

                            background: Rectangle {
                                radius: 12
                                color: Theme.field
                                border.width: nameComboBox.visualFocus ? 2 : 1
                                border.color: nameComboBox.visualFocus ? Theme.focusOnDark : Theme.fieldBorder
                            }

                            delegate: ItemDelegate {
                                id: option
                                required property int index
                                required property var modelData
                                width: ListView.view ? ListView.view.width : 0
                                height: Theme.controlHeight
                                highlighted: nameComboBox.highlightedIndex === index

                                contentItem: Text {
                                    text: option.modelData
                                    font.pixelSize: Theme.fontInput
                                    font.weight: nameComboBox.currentIndex === option.index ? Font.DemiBold : Font.Normal
                                    color: Theme.textPrimary
                                    verticalAlignment: Text.AlignVCenter
                                    elide: Text.ElideRight
                                }
                                background: Rectangle {
                                    radius: 8
                                    color: option.highlighted || option.hovered ? Theme.tile : "transparent"
                                }
                            }

                            popup: Popup {
                                y: nameComboBox.height + 4
                                width: nameComboBox.width
                                implicitHeight: Math.min(contentItem.implicitHeight + 2 * padding, 360)
                                padding: 4

                                contentItem: ListView {
                                    clip: true
                                    implicitHeight: contentHeight
                                    model: nameComboBox.popup.visible ? nameComboBox.delegateModel : null
                                    currentIndex: nameComboBox.highlightedIndex
                                    boundsBehavior: Flickable.StopAtBounds
                                    ScrollIndicator.vertical: ScrollIndicator {}
                                }

                                background: Rectangle {
                                    radius: 12
                                    color: Theme.card
                                    border.color: Theme.tileBorder
                                    border.width: 1
                                }
                            }
                        }

                        // 名單沒有載入：搜尋照樣可以用（不篩選名字），這裡可以重試
                        RowLayout {
                            objectName: "namesError"
                            width: nameComboBox.width
                            visible: serviceManager.namesState === "error"
                            spacing: Theme.spaceS

                            FieldError {
                                Layout.fillWidth: true
                                text: qsTr("The staff list didn't load.")
                            }
                            BusyButton {
                                font.pixelSize: Theme.fontCaption
                                text: qsTr("Retry")
                                onClicked: serviceManager.loadNames()
                            }
                        }
                    }

                    Column {
                        id: fromColumn
                        spacing: 8

                        FieldLabel { text: qsTr("From") }
                        // 日期欄和服務人員端共用（DateField.qml）。在任一欄按 Enter 都會送出
                        DateField {
                            id: startDateField
                            objectName: "startDateField"
                            implicitWidth: 200
                            accessibleName: qsTr("From date")
                            errorText: serviceManager.startDateError
                            invalid: errorText !== ""
                            onActiveFocusChanged: if (activeFocus) searchPage.reveal(this)
                            onTextEdited: serviceManager.clearDateError("start")
                            onAccepted: searchPage.search()
                        }
                        // 欄位錯誤：出現時才佔位置（只在按了 Search 之後；見 T31 第 8 節）
                        FieldError {
                            objectName: "startDateError"
                            width: startDateField.width
                            visible: hasError
                            text: serviceManager.startDateError
                        }
                    }

                    Column {
                        id: toColumn
                        spacing: 8

                        FieldLabel { text: qsTr("To") }
                        DateField {
                            id: endDateField
                            objectName: "endDateField"
                            implicitWidth: 200
                            accessibleName: qsTr("To date")
                            errorText: serviceManager.endDateError
                            invalid: errorText !== ""
                            onActiveFocusChanged: if (activeFocus) searchPage.reveal(this)
                            onTextEdited: serviceManager.clearDateError("end")
                            onAccepted: searchPage.search()
                        }
                        FieldError {
                            objectName: "endDateError"
                            width: endDateField.width
                            visible: hasError
                            text: serviceManager.endDateError
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
                            busy: searchPage.searching
                            onActiveFocusChanged: if (activeFocus) searchPage.reveal(this)
                            onClicked: {
                                if (busy)       // 輔助技術的「按下」不經過 BusyButton 的擋板
                                    return
                                searchPage.search()
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
                    model: serviceManager.listModel
                    showStaffColumn: true
                    listState: serviceManager.state === "idle" || serviceManager.state === "loading" ? "loading"
                             : serviceManager.state
                    busy: searchPage.searching
                    countText: serviceManager.countText
                    subtitle: serviceManager.subtitle
                    shownText: serviceManager.shownText
                    hasMore: serviceManager.hasMore
                    loadingMore: serviceManager.loadingMore
                    moreErrorDetail: serviceManager.moreErrorDetail
                    moreErrorTechnical: serviceManager.moreErrorTechnical
                    moreFailureCount: serviceManager.moreFailureCount
                    emptyTitle: searchPage.emptyTitle(serviceManager.lastQuery)
                    emptyDescription: serviceManager.lastQuery.staffId ? qsTr("Try a different staff member or date range.")
                                                                    : qsTr("Try a different date range.")
                    errorTitle: qsTr("Search failed")
                    errorDetail: serviceManager.errorDetail
                    errorTechnical: serviceManager.errorTechnical
                    failureCount: serviceManager.failureCount

                    onOpenDetail: (serviceId, startTime) => window.openServiceDetail(serviceId, startTime)
                    onShowMore: serviceManager.showMore()
                    onRetry: serviceManager.retrySearch()
                    onItemFocused: (item) => searchPage.reveal(item)
                }
            }
        }
    }
}
