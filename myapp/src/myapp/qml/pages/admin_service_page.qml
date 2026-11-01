pragma ComponentBehavior: Bound
import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15
import "../components"

// 服務查詢（UX-24、DS-26、DS-27）：服務人員名單和搜尋都在背景（Python 的 serviceManager），畫面不會凍住。
// - 日期格式不對：欄位下方顯示錯誤、框線變紅，不送出。
// - 搜尋中：按鈕忙碌（Searching…）；已經有結果時舊結果留著、變淡。新的搜尋取代還沒回來的舊搜尋。
// - 沒有結果是空狀態，失敗是區塊錯誤＋Retry，兩者分開（UX-38）。
// - Details 先換頁，影音和圖表在明細頁的背景下載（UX-22、ENG-04）：這裡不碰網路。
// - 外觀和服務人員端的查詢頁一樣（UX-46）：同一個日期欄（DateField.qml）、表頭和列的顏色都取自 Theme，
//   Search 是這一頁唯一的主要按鈕（石墨灰，DS-12）。以前是寫死的 #dddddd、#f4f4f4 和 Material 的強調色。
Item {
    id: searchPage
    anchors.fill: parent

    readonly property bool searching: serviceManager.state === "loading" || serviceManager.refreshing
    readonly property int listBottomMargin: typeof toastHost !== "undefined" && toastHost ? toastHost.occupiedHeight : 0
    // 使用者在選單選的名字；空字串＝不篩選（選單第 0 項「All Staff」）。名單在背景載入，
    // 換名單時 ComboBox 會回到第 0 項，所以選了誰記在這裡，名單換好再放回去
    property string chosenName: ""

    // active 持續超過 interval 才成立（DS-25：300 ms 內什麼都不顯示，免得閃一下）
    component Wait: Timer {
        property bool active: false
        property bool elapsed: false
        interval: Theme.busyDelay
        running: active && !elapsed
        onTriggered: elapsed = true
        onActiveChanged: if (!active) elapsed = false
    }

    component HeaderText: Text {
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        color: Theme.textPrimary
        font.pixelSize: Theme.fontBody
        font.weight: Font.DemiBold
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }

    component CellText: Text {
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        color: Theme.textPrimary
        font.pixelSize: Theme.fontBody
        horizontalAlignment: Text.AlignHCenter
        elide: Text.ElideRight
    }

    Wait { id: resultsDim; active: serviceManager.refreshing }

    function search() {
        serviceManager.search(chosenName, startDateField.text, endDateField.text)
    }

    // 空狀態的標題：「No services from 2026-10-01 to 2026-10-31」（DS-52）
    function emptyTitle(query) {
        if (query.start && query.end)
            return qsTr("No services from %1 to %2").arg(query.start).arg(query.end)
        if (query.start)
            return qsTr("No services from %1 on").arg(query.start)
        if (query.end)
            return qsTr("No services up to %1").arg(query.end)
        return qsTr("No services yet")
    }

    // 選單顯示 chosenName；沒選是「All Staff」。名單裡已經沒有這個人時也回到「All Staff」，不再用他篩選
    function showChosenName() {
        const index = chosenName !== "" ? nameComboBox.find(chosenName) : 0
        if (index < 0)
            chosenName = ""
        nameComboBox.currentIndex = Math.max(0, index)
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spaceL
        spacing: Theme.spaceS

        // 服務人員：第 0 項是不篩選（以前選了名字就改不回全部，UX-36）
        ComboBox {
            id: nameComboBox
            objectName: "nameComboBox"
            Layout.preferredWidth: 2 * startDateField.implicitWidth + Theme.spaceM
            model: [qsTr("All Staff")].concat(serviceManager.names)
            currentIndex: 0
            enabled: serviceManager.names.length > 0
            font.pixelSize: Theme.fontBody
            displayText: currentIndex > 0 ? currentText
                       : serviceManager.names.length === 0 && serviceManager.namesState === "loading"
                         ? qsTr("Loading staff…") : qsTr("All Staff")
            Accessible.name: qsTr("Staff")
            onActivated: (index) => searchPage.chosenName = index > 0 ? textAt(index) : ""
            // 名單內容變了，ComboBox 會回到第 0 項；等它換好，再放回使用者選的人
            onModelChanged: Qt.callLater(searchPage.showChosenName)
        }

        // 名單沒有載入：搜尋照樣可以用（不篩選名字），這裡可以重試
        RowLayout {
            objectName: "namesError"
            visible: serviceManager.namesState === "error"
            spacing: Theme.spaceS

            FieldError {
                Layout.preferredWidth: 2 * startDateField.implicitWidth
                text: qsTr("The staff list didn't load.")
            }

            BusyButton {
                text: qsTr("Retry")
                onClicked: serviceManager.loadNames()
            }
        }

        // 查詢條件：日期兩欄＋Search，和服務人員端的查詢頁同一個版面
        RowLayout {
            spacing: Theme.spaceM

            ColumnLayout {
                Layout.alignment: Qt.AlignTop
                spacing: 4

                DateField {
                    id: startDateField
                    objectName: "startDateField"
                    placeholderText: qsTr("Start Date (YYYY-MM-DD)")
                    accessibleName: qsTr("Start date")
                    errorText: serviceManager.startDateError
                    invalid: serviceManager.startDateError !== ""
                    onTextEdited: serviceManager.clearDateError("start")
                    onAccepted: searchPage.search()
                }

                FieldError {
                    objectName: "startDateError"
                    Layout.preferredWidth: startDateField.implicitWidth
                    Layout.preferredHeight: 2 * lineHeight  // 這句在 280 寬會排成兩行：先留好，錯誤出現時版面不跳
                    text: serviceManager.startDateError
                }
            }

            ColumnLayout {
                Layout.alignment: Qt.AlignTop
                spacing: 4

                DateField {
                    id: endDateField
                    objectName: "endDateField"
                    placeholderText: qsTr("End Date (YYYY-MM-DD)")
                    accessibleName: qsTr("End date")
                    errorText: serviceManager.endDateError
                    invalid: serviceManager.endDateError !== ""
                    onTextEdited: serviceManager.clearDateError("end")
                    onAccepted: searchPage.search()
                }

                FieldError {
                    objectName: "endDateError"
                    Layout.preferredWidth: endDateField.implicitWidth
                    Layout.preferredHeight: 2 * lineHeight
                    text: serviceManager.endDateError
                }
            }

            BusyButton {
                id: searchButton
                objectName: "searchButton"
                Layout.alignment: Qt.AlignTop
                variant: "primary"
                text: qsTr("Search")
                busyText: qsTr("Searching…")
                busy: searchPage.searching
                onClicked: {
                    if (busy)       // 輔助技術的「按下」不經過 BusyButton 的擋板
                        return
                    searchPage.search()
                }
            }

            Item { Layout.fillWidth: true }
        }

        // 表頭
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: Theme.controlHeight
            color: Theme.surfaceMuted
            border.color: Theme.divider
            border.width: 1
            radius: Theme.radiusS

            RowLayout {
                anchors.fill: parent
                anchors.margins: Theme.spaceS
                spacing: Theme.spaceL

                HeaderText { text: qsTr("Service ID") }
                HeaderText { text: qsTr("Name") }
                HeaderText { text: qsTr("Date") }
                HeaderText { text: qsTr("Details") }
            }
        }

        // 結果
        Item {
            id: resultArea
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: resultListView
                objectName: "resultList"
                anchors.fill: parent
                // 有結果（包括重新搜尋時留著的舊結果）才顯示清單
                visible: serviceManager.state === "ready"
                opacity: resultsDim.elapsed ? 0.5 : 1
                clip: true
                spacing: 4
                model: resultsModel
                bottomMargin: searchPage.listBottomMargin  // 最後一列不會被通知蓋住（DS-21）
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar {}
                Accessible.role: Accessible.Table
                Accessible.name: qsTr("Search results")

                Behavior on opacity {       // M-9：重新搜尋時變淡
                    enabled: Theme.durBase > 0
                    OpacityAnimator { duration: Theme.durBase }
                }

                delegate: Rectangle {
                    id: resultRow
                    objectName: "resultRow"
                    required property int index
                    required property string service_id
                    required property string name
                    required property string start_datetime

                    width: ListView.view.width
                    height: 60
                    radius: Theme.radiusS
                    color: index % 2 === 0 ? Theme.contentBg : Theme.surfaceAlt
                    border.color: Theme.divider
                    border.width: 1
                    Accessible.role: Accessible.Row
                    Accessible.name: [service_id, name, start_datetime].join(", ")

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: Theme.spaceS
                        spacing: Theme.spaceL

                        CellText {
                            text: resultRow.service_id
                            font.weight: Font.DemiBold
                            font.features: { "tnum": 1 }
                        }
                        CellText {
                            text: resultRow.name
                        }
                        CellText {
                            text: resultRow.start_datetime
                            font.features: { "tnum": 1 }
                        }
                        Item {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            Layout.fillHeight: true

                            BusyButton {
                                objectName: "detailsButton"
                                anchors.centerIn: parent
                                text: qsTr("Details")
                                Accessible.name: text + ", " + resultRow.service_id
                                onClicked: {
                                    if (busy)
                                        return
                                    // 只改屬性、送出背景工作，馬上換頁；頁首的 ID 和時間直接用這一列的
                                    adminHome.load_service_media(resultRow.service_id, resultRow.start_datetime)
                                    mainContentLoader.source = "../../qml/pages/admin_service_detail_page.qml"
                                }
                            }
                        }
                    }
                }
            }

            EmptyState {
                objectName: "searchEmpty"
                anchors.horizontalCenter: parent.horizontalCenter
                y: Theme.space4XL
                width: Math.min(parent.width, 360)
                visible: serviceManager.state === "empty"
                title: searchPage.emptyTitle(serviceManager.lastQuery)
                description: serviceManager.lastQuery.name ? qsTr("Try a different staff member or date range.")
                                                           : qsTr("Try a different date range.")
            }

            InlineError {
                objectName: "searchError"
                anchors.horizontalCenter: parent.horizontalCenter
                y: Theme.space4XL
                width: Math.min(parent.width, 480)
                visible: serviceManager.state === "error"
                title: qsTr("Search failed")
                detail: serviceManager.errorDetail
                technicalDetail: serviceManager.errorTechnical
                failureCount: serviceManager.failureCount
                busy: searchPage.searching
                onRetry: serviceManager.retrySearch()
            }
        }
    }

    // 回到這一頁時，把上一次搜尋的條件放回欄位（結果還在 Python 裡）
    Component.onCompleted: {
        const query = serviceManager.lastQuery
        startDateField.text = query.start
        endDateField.text = query.end
        chosenName = query.name
        showChosenName()
        serviceManager.loadNames()
    }
}
