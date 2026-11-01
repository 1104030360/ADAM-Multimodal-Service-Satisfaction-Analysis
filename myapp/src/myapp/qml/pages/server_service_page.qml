pragma ComponentBehavior: Bound
import QtQuick
// 日期欄、表格按鈕自訂了 background，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 服務查詢（UX-24、DS-26、DS-27）。查詢在背景執行（serviceManager.search，replace=True：連按只留最後一次）。
// - 日期格式不對：欄位下方顯示錯誤，不送出，焦點移到那一欄
// - 查不到：空狀態寫出查了哪一段期間；查詢失敗：區塊錯誤＋Retry
// - 換頁回來時，欄位是上一次送出的條件，和下面的結果對得起來
// - Details 先換頁，影音和圖表在明細頁的背景下載（UX-22、ENG-04）：這裡不碰網路
Item {
    id: searchPage
    anchors.fill: parent

    readonly property var manager: typeof serviceManager !== "undefined" ? serviceManager : null
    readonly property var results: typeof resultsModel !== "undefined" ? resultsModel : null
    readonly property string status: manager ? manager.status : "idle"
    readonly property bool busy: manager !== null && manager.busy
    readonly property int resultCount: results ? results.count : 0
    readonly property int listBottomMargin: typeof toastHost !== "undefined" && toastHost ? toastHost.occupiedHeight : 0
    readonly property string dateHint: qsTr("Enter the date as YYYY-MM-DD, for example 2026-10-31.")

    function submit() {
        if (!manager)
            return
        // 查詢中又送出（例如改了日期按 Enter）：條件一樣就不重送；條件改了就取消前一次、改查新的（replace=True，ENG-14）
        if (manager.busy && startDateField.text.trim() === manager.startDate
                && endDateField.text.trim() === manager.endDate)
            return
        if (!manager.search(startDateField.text, endDateField.text))   // 日期格式不對：焦點移到第一個有錯的欄位
            (manager.startDateInvalid ? startDateField : endDateField).forceActiveFocus()
    }

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

    Component.onCompleted: {
        if (manager) {
            startDateField.text = manager.startDate
            endDateField.text = manager.endDate
        }
    }

    component DateField: TextField {
        id: field
        property bool invalid: false
        property string accessibleName: ""

        Layout.preferredWidth: 280
        implicitHeight: Theme.controlHeight
        leftPadding: Theme.spaceM
        rightPadding: Theme.spaceM
        font.pixelSize: Theme.fontBody
        color: Theme.textPrimary
        placeholderTextColor: Theme.textSecondary
        selectByMouse: true
        Accessible.name: accessibleName
        Accessible.description: invalid ? searchPage.dateHint : ""
        onAccepted: searchPage.submit()     // 在任一欄按 Enter 都會送出

        background: Rectangle {
            radius: height / 2
            color: Theme.contentBg
            border.width: field.activeFocus || field.invalid ? 2 : 1
            border.color: field.invalid ? Theme.errorOnLight : field.activeFocus ? Theme.focusOnLight : Theme.textSecondary
        }
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

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spaceL
        spacing: Theme.spaceS

        // 查詢條件
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
                    invalid: searchPage.manager !== null && searchPage.manager.startDateInvalid
                }
                FieldError {
                    objectName: "startDateError"
                    Layout.preferredWidth: 280
                    Layout.preferredHeight: 2 * lineHeight  // 這句在 280 寬會排成兩行：先留好，錯誤出現時版面不跳
                    text: startDateField.invalid ? searchPage.dateHint : ""
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
                    invalid: searchPage.manager !== null && searchPage.manager.endDateInvalid
                }
                FieldError {
                    objectName: "endDateError"
                    Layout.preferredWidth: 280
                    Layout.preferredHeight: 2 * lineHeight
                    text: endDateField.invalid ? searchPage.dateHint : ""
                }
            }

            BusyButton {
                id: searchButton
                objectName: "searchButton"
                Layout.alignment: Qt.AlignTop
                variant: "primary"
                text: qsTr("Search")
                busyText: qsTr("Searching…")
                busy: searchPage.busy
                onClicked: {
                    if (busy)
                        return
                    searchPage.submit()
                }
            }

            Item { Layout.fillWidth: true }
        }

        Text {
            text: qsTr("Only services with completed analyses are listed.")   // 以前畫面上沒說（UX-24）
            color: Theme.textSecondary
            font.pixelSize: Theme.fontCaption
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
                visible: searchPage.status === "ready" && searchPage.resultCount > 0
                opacity: searchPage.busy ? 0.5 : 1      // 查詢中：舊的結果留著、變淡（DS-25）
                clip: true
                spacing: 4
                model: searchPage.results
                bottomMargin: searchPage.listBottomMargin
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar {}
                Accessible.role: Accessible.Table
                Accessible.name: qsTr("Search results")

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

                        Text {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            text: resultRow.service_id
                            color: Theme.textPrimary
                            font.pixelSize: Theme.fontBody
                            font.weight: Font.DemiBold
                            font.features: { "tnum": 1 }
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            text: resultRow.name
                            color: Theme.textPrimary
                            font.pixelSize: Theme.fontBody
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            text: resultRow.start_datetime
                            color: Theme.textPrimary
                            font.pixelSize: Theme.fontBody
                            font.features: { "tnum": 1 }
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight
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
                                    serverHome.load_service_media(resultRow.service_id, resultRow.start_datetime)
                                    mainContentLoader.source = "../../qml/pages/server_service_detail_page.qml"
                                }
                            }
                        }
                    }
                }
            }

            // 還沒查過
            EmptyState {
                objectName: "searchPrompt"
                anchors.horizontalCenter: parent.horizontalCenter
                y: Theme.space4XL
                width: Math.min(parent.width, 360)
                visible: searchPage.status === "idle" && !searchPage.busy
                title: qsTr("Search your services")
                description: qsTr("Enter a date range, or leave the dates empty to see all of them.")
            }

            // 查到 0 筆
            EmptyState {
                objectName: "searchEmpty"
                anchors.horizontalCenter: parent.horizontalCenter
                y: Theme.space4XL
                width: Math.min(parent.width, 360)
                visible: searchPage.status === "ready" && searchPage.resultCount === 0 && !searchPage.busy
                title: searchPage.emptyTitle()
                description: searchPage.manager && (searchPage.manager.startDate !== "" || searchPage.manager.endDate !== "")
                             ? qsTr("Try a different date range.")
                             : qsTr("Services appear here once their analysis is complete.")
            }

            InlineError {
                objectName: "searchError"
                anchors.horizontalCenter: parent.horizontalCenter
                y: Theme.space4XL
                width: Math.min(parent.width, 480)
                visible: searchPage.status === "error"
                title: qsTr("Couldn't search your services")
                detail: searchPage.manager ? searchPage.manager.errorDetail : ""
                technicalDetail: searchPage.manager ? searchPage.manager.errorTechnical : ""
                failureCount: searchPage.manager ? searchPage.manager.failureCount : 0
                busy: searchPage.busy
                onRetry: {
                    if (searchPage.manager)
                        searchPage.manager.retry()
                }
            }
        }
    }
}
