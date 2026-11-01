import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import QtQuick.Window 2.15
import QtQuick.Controls.Material 2.15
import "../components"

// 服務查詢（UX-24、DS-26、DS-27）：服務人員名單和搜尋都在背景（Python 的 serviceManager），畫面不會凍住。
// - 日期格式不對：欄位下方顯示錯誤，不送出。
// - 搜尋中：按鈕忙碌（Searching…）；已經有結果時舊結果留著、變淡。新的搜尋取代還沒回來的舊搜尋。
// - 沒有結果是空狀態，失敗是區塊錯誤＋Retry，兩者分開（UX-38）。
// - Details 先換頁，影音和圖表在明細頁的背景下載（UX-22、ENG-04）：這裡不碰網路。
Item {
    id: searchPage
    anchors.fill: parent

    readonly property bool searching: serviceManager.state === "loading" || serviceManager.refreshing
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
        spacing: 10

        // Name Selection：第 0 項是不篩選（以前選了名字就改不回全部，UX-36）
        ComboBox {
            id: nameComboBox
            Layout.fillWidth: true
            model: [qsTr("All Staff")].concat(serviceManager.names)
            currentIndex: 0
            enabled: serviceManager.names.length > 0
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
                Layout.fillWidth: true
                text: qsTr("The staff list didn't load.")
            }

            BusyButton {
                text: qsTr("Retry")
                onClicked: serviceManager.loadNames()
            }
        }

        // Date Selection
        RowLayout {
            spacing: 10

            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 1    // 兩欄平分寬度：錯誤訊息再長，欄位也不會變寬
                Layout.alignment: Qt.AlignTop
                spacing: 4

                TextField {
                    id: startDateField
                    placeholderText: "Start Date (YYYY-MM-DD)"
                    Layout.fillWidth: true
                    Material.accent: serviceManager.startDateError !== "" ? Theme.errorOnLight : Theme.focusOnLight
                    Accessible.name: qsTr("Start date")
                    Accessible.description: serviceManager.startDateError
                    onTextEdited: serviceManager.clearDateError("start")
                    onAccepted: searchPage.search()
                }

                FieldError {
                    objectName: "startDateError"
                    Layout.fillWidth: true
                    text: serviceManager.startDateError
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 1    // 兩欄平分寬度：錯誤訊息再長，欄位也不會變寬
                Layout.alignment: Qt.AlignTop
                spacing: 4

                TextField {
                    id: endDateField
                    placeholderText: "End Date (YYYY-MM-DD)"
                    Layout.fillWidth: true
                    Material.accent: serviceManager.endDateError !== "" ? Theme.errorOnLight : Theme.focusOnLight
                    Accessible.name: qsTr("End date")
                    Accessible.description: serviceManager.endDateError
                    onTextEdited: serviceManager.clearDateError("end")
                    onAccepted: searchPage.search()
                }

                FieldError {
                    objectName: "endDateError"
                    Layout.fillWidth: true
                    text: serviceManager.endDateError
                }
            }
        }

        // Search Button
        BusyButton {
            id: searchButton
            objectName: "searchButton"
            text: qsTr("Search")
            busyText: qsTr("Searching…")
            busy: searchPage.searching
            Layout.alignment: Qt.AlignHCenter
            onClicked: {
                if (busy)       // 輔助技術的「按下」不經過 BusyButton 的擋板
                    return
                searchPage.search()
            }
        }

        // Fixed Headers
        Rectangle {
            Layout.fillWidth: true
            height: 40
            color: "#dddddd"
            border.color: "#cccccc"
            border.width: 1
            radius: 5

            RowLayout {
                anchors.fill: parent
                spacing: 20
                anchors.margins: 10

                Text {
                    text: "Service ID"
                    font.bold: true
                    width: parent.width * 0.25
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                Text {
                    text: "Name"
                    font.bold: true
                    width: parent.width * 0.25
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                Text {
                    text: "Date"
                    font.bold: true
                    width: parent.width * 0.25
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                Text {
                    text: "Details"
                    font.bold: true
                    width: parent.width * 0.25
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }
        }

        // Result List
        Rectangle {
            id: resultArea
            color: "#f4f4f4"
            radius: 10
            border.color: "#cccccc"
            border.width: 1
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.alignment: Qt.AlignTop

            ColumnLayout {
                anchors.fill: parent
                spacing: 10
                anchors.margins: 10
                // 有結果（包括重新搜尋時留著的舊結果）才顯示清單
                visible: serviceManager.state === "ready"
                opacity: resultsDim.elapsed ? 0.5 : 1

                Behavior on opacity {       // M-9：重新搜尋時變淡
                    enabled: Theme.durBase > 0
                    OpacityAnimator { duration: Theme.durBase }
                }

                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    ListView {
                        id: resultListView
                        objectName: "resultList"
                        width: parent.width
                        model: resultsModel  // Bind to serviceManager.resultsModel
                        clip: true
                        bottomMargin: toastHost.occupiedHeight  // 最後一列不會被通知蓋住（DS-21）

                        delegate: Rectangle {
                            width: parent.width
                            height: 60
                            color: index % 2 == 0 ? "#ffffff" : "#f2f2f2"
                            border.color: "#cccccc"
                            border.width: 1
                            radius: 5

                            RowLayout {
                                anchors.fill: parent
                                spacing: 20

                                Text {
                                    text: service_id
                                    font.bold: true
                                    width: parent.width * 0.25
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                Text {
                                    text: name
                                    font.bold: true
                                    width: parent.width * 0.25
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                Text {
                                    text: start_datetime
                                    width: parent.width * 0.25
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                }

                                BusyButton {
                                    objectName: "detailsButton"
                                    text: qsTr("Details")
                                    Accessible.name: text + ", " + service_id
                                    onClicked: {
                                        // 只改屬性、送出背景工作，馬上換頁；頁首的 ID 和時間直接用這一列的
                                        adminHome.load_service_media(service_id, start_datetime)
                                        mainContentLoader.source = "../../qml/pages/admin_service_detail_page.qml"
                                    }
                                }
                            }
                        }
                    }
                }
            }

            EmptyState {
                objectName: "searchEmpty"
                anchors.centerIn: parent
                width: 360
                visible: serviceManager.state === "empty"
                title: searchPage.emptyTitle(serviceManager.lastQuery)
                description: serviceManager.lastQuery.name ? qsTr("Try a different staff member or date range.")
                                                           : qsTr("Try a different date range.")
            }

            InlineError {
                objectName: "searchError"
                anchors.centerIn: parent
                width: 480
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
