pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 TextField、Popup、ItemDelegate 的 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts

// 頁首的「搜尋服務 ID」（UD5，new-UIUX PLAN 第 4 節；資料在 myapp/search.py 的 ServiceSearch，T32）。
// 兩個外殼放在 PageHeader 裡（頁首右邊、主要動作的左邊），明細頁沒有：
//     ServiceSearchField { search: serviceSearch; wide: header.wordmark; visible: header.backText === ""
//                          onChosen: (serviceId, startTime) => window.openServiceDetail(serviceId, startTime) }
// - 44 高的膠囊、左邊放大鏡。首頁最寬 460、內頁 360；頁首放不下時最窄 200。
// - 每打一個字交給 search.setQuery()（Python 停 250 ms 才查）。下面的清單（Popup）列出結果：服務 ID、時間、
//   狀態，管理員多一個員工名字；也顯示 No services match、錯誤（Retry）、服務 ID 的格式說明。
//   查得比 busyDelay（300 ms）久才顯示 Searching…，查得快的時候不閃。新的字還在查時，舊的結果留著、變淡（DS-33）。
// - 鍵盤：↓↑ 選結果、Enter 打開（錯誤時是 Retry）、Escape 關掉清單（清單已經關了就清空欄位）。
//   焦點一直留在欄位上（像 combo box）：清單裡的列和按鈕都不拿焦點，選到的那一列用 Accessible.announce 唸出來。
// - 選了一筆：關掉清單、清空欄位，發出 chosen(serviceId, startTime)。
FocusScope {
    id: root

    property var search: null              // ServiceSearch（context property serviceSearch）；測試可以給替身
    property bool wide: false              // 首頁：最寬 460；內頁 360
    property int currentIndex: -1          // 鍵盤、滑鼠選到的那一列
    property var announced: []             // 唸過的內容（Accessible.announce 在測試裡看不到，T17 的做法）

    readonly property alias text: field.text
    readonly property string status: search ? search.state : "idle"
    readonly property bool refreshing: search ? search.refreshing : false
    readonly property var results: search ? search.results : []
    readonly property bool searchesNames: search ? search.searchesNames : false
    readonly property bool dropdownOpen: dropdown.opened
    // 清單裡有東西可以看：結果、0 筆、錯誤、格式說明；查詢中要過了 busyDelay 才算
    readonly property bool hasContent: status === "ready" || status === "empty" || status === "error"
                                       || status === "invalid" || (status === "loading" && internal.slow)
    readonly property bool wanted: field.activeFocus && field.text.trim() !== "" && hasContent && !internal.dismissed
    // 欄位的 Accessible.description、結果到了唸的那一句
    readonly property string summary: {
        switch (status) {
        case "loading": return qsTr("Searching…")
        case "invalid": return root.invalidText
        case "empty": return qsTr("No services match")
        case "error": return qsTr("Couldn't search")
        case "ready": return search && search.hasMore ? qsTr("More than %1 services found").arg(search.limit)
                           : results.length === 1 ? qsTr("1 service found")
                           : qsTr("%1 services found").arg(results.length)
        }
        return ""
    }
    readonly property string invalidText: qsTr("Service IDs start with S and the date, for example S20261031143000.")

    signal chosen(string serviceId, string startTime)

    implicitWidth: wide ? 460 : 360
    implicitHeight: Theme.controlHeight
    Layout.fillWidth: true
    Layout.minimumWidth: 200
    Layout.preferredWidth: implicitWidth
    Layout.maximumWidth: implicitWidth

    onWantedChanged: wanted ? dropdown.open() : dropdown.close()
    onResultsChanged: currentIndex = results.length > 0 ? 0 : -1
    onCurrentIndexChanged: if (currentIndex >= 0) list.positionViewAtIndex(currentIndex, ListView.Contain)
    onStatusChanged: {
        internal.slow = false
        if (status === "loading")
            slowTimer.restart()
        else
            slowTimer.stop()
        Qt.callLater(announceSummary)
    }
    onRefreshingChanged: Qt.callLater(announceSummary)     // 新的結果到了（狀態一直是 ready）

    QtObject {
        id: internal
        property bool slow: false           // 查詢中超過 busyDelay：顯示 Searching…
        property bool dismissed: false      // Escape、點了外面：這段文字不再自動打開清單
    }

    Timer {
        id: slowTimer
        interval: Theme.busyDelay
        onTriggered: internal.slow = true
    }

    // 結果到了、0 筆、錯誤、格式不對：唸一次（同一輪事件裡狀態和 refreshing 一起變時，callLater 只唸一次）
    function announceSummary() {
        if (!field.activeFocus || status === "idle" || status === "loading" || refreshing)
            return
        if (status === "error")
            announce(summary + ". " + search.errorDetail, Accessible.Assertive)
        else
            announce(summary, Accessible.Polite)
    }

    function announce(text, politeness) {
        if (text === "")
            return
        announced = announced.concat([text])
        Accessible.announce(text, politeness)
    }

    // "2026-10-31 09:15:00" → "Oct 31, 2026 · 09:15"（介面是英文，月份用 en_US 的縮寫）
    function timeText(startTime) {
        const value = startTime || ""
        if (value.length < 16)
            return value
        const date = new Date(Number(value.slice(0, 4)), Number(value.slice(5, 7)) - 1, Number(value.slice(8, 10)),
                              Number(value.slice(11, 13)), Number(value.slice(14, 16)))
        return isNaN(date.getTime()) ? value : Qt.locale("en_US").toString(date, qsTr("MMM d, yyyy · HH:mm"))
    }

    function detailText(row) {
        const time = timeText(row.startTime)
        return searchesNames && row.staffName ? qsTr("%1 · %2").arg(row.staffName).arg(time) : time
    }

    // 螢幕閱讀器唸的一列：「S20261031091500, Oct 31, 2026 · 09:15, Completed」，管理員多員工名字。
    // 狀態的字和 StatusChip 一樣（這裡沒有 Failed：搜尋不知道送出分析的歷史）
    function spokenText(row) {
        const label = row.status === 3 ? qsTr("Completed") : row.status === 2 ? qsTr("Analyzing") : qsTr("Not Analyzed")
        return [row.serviceId, detailText(row), label].join(", ")
    }

    function choose(index) {
        const row = results[index]
        if (!row || refreshing)
            return
        const serviceId = row.serviceId
        const startTime = row.startTime
        internal.dismissed = false
        field.clear()
        if (search)
            search.clear()
        dropdown.close()
        chosen(serviceId, startTime)
    }

    function move(step) {
        if (!dropdown.opened) {             // 關掉的清單：↓ 再打開
            internal.dismissed = false
            return
        }
        if (results.length === 0)
            return
        currentIndex = Math.max(0, Math.min(results.length - 1, currentIndex + step))
        announce(spokenText(results[currentIndex]), Accessible.Polite)     // 那一列可能還沒建立：從資料組
    }

    function activate() {
        if (status === "error") {
            search.retry()
        } else if (!dropdown.opened) {
            internal.dismissed = false
        } else if (status === "ready" && !refreshing && currentIndex >= 0) {
            choose(currentIndex)
        } else if (search) {
            search.searchNow()              // 還在等 250 ms：不等了
        }
    }

    TextField {
        id: field
        objectName: "searchInput"
        anchors.fill: parent
        focus: true
        leftPadding: 44
        rightPadding: Theme.spaceM
        font.pixelSize: Theme.fontInput
        color: Theme.textPrimary
        placeholderText: root.searchesNames ? qsTr("Search by service ID or staff name") : qsTr("Search by service ID")
        placeholderTextColor: Theme.textFaint
        selectionColor: Theme.chromeSelection
        selectedTextColor: Theme.textTitle
        selectByMouse: true
        inputMethodHints: Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
        Accessible.name: placeholderText    // placeholder 會消失，名稱不能靠它（DS-31）
        Accessible.description: root.summary

        onActiveFocusChanged: if (activeFocus) internal.dismissed = false    // 回到欄位：結果再出現
        onTextEdited: {
            internal.dismissed = false
            if (root.search)
                root.search.setQuery(text)
        }

        Keys.onDownPressed: root.move(1)
        Keys.onUpPressed: root.move(-1)
        Keys.onReturnPressed: root.activate()
        Keys.onEnterPressed: root.activate()
        Keys.onEscapePressed: (event) => {
            if (dropdown.opened) {
                internal.dismissed = true
            } else if (field.text !== "") {
                field.clear()
                if (root.search)
                    root.search.clear()
            } else {
                event.accepted = false
            }
        }

        background: Rectangle {
            objectName: "searchBackground"
            radius: Theme.radiusPill
            color: Theme.field
            border.width: field.activeFocus ? 2 : 1     // 焦點框（DS-30）：和其他輸入框一樣，框線變亮、變粗
            border.color: field.activeFocus ? Theme.focusOnDark : Theme.fieldBorder

            Image {
                anchors.left: parent.left
                anchors.leftMargin: Theme.spaceM
                anchors.verticalCenter: parent.verticalCenter
                width: 18
                height: 18
                sourceSize: Qt.size(18, 18)
                source: "icons/search_textFaint.svg"
                Accessible.ignored: true
            }
        }
    }

    Popup {
        id: dropdown
        objectName: "searchDropdown"
        y: root.height + 6
        // 管理員的列多一個員工名字：至少 440，時間才不會被截掉。欄位在頁首的右邊，比欄位寬時往左長
        width: Math.max(root.width, root.searchesNames ? 440 : 360)
        x: root.width - width
        z: Theme.zPageOverlay
        margins: Theme.spaceS                // 太高時縮在視窗裡，清單自己捲動
        padding: 6
        modal: false
        focus: false                         // 焦點留在欄位上，才能繼續打字
        closePolicy: Popup.CloseOnPressOutsideParent
        onClosed: if (root.wanted) internal.dismissed = true    // 點了外面

        background: Rectangle {
            color: Theme.card
            radius: Theme.radiusTile
            border.color: Theme.cardBorder
            border.width: 1
        }

        contentItem: ColumnLayout {
            spacing: 0

            Accessible.role: Accessible.List
            Accessible.name: qsTr("Search results")

            // 查詢中、0 筆、格式說明：一行字
            Row {
                objectName: "searchMessage"
                visible: !list.visible && (root.status === "loading" || root.status === "empty"
                                           || root.status === "invalid")
                Layout.fillWidth: true
                Layout.preferredHeight: Math.max(Theme.controlHeight, implicitHeight)
                leftPadding: Theme.spaceS
                topPadding: Theme.spaceS
                bottomPadding: Theme.spaceS
                spacing: Theme.spaceS

                Image {
                    id: spinner
                    anchors.verticalCenter: parent.verticalCenter
                    visible: root.status === "loading"
                    width: Theme.iconS
                    height: Theme.iconS
                    sourceSize: Qt.size(Theme.iconS, Theme.iconS)
                    source: "icons/loader-circle_textSecondary.svg"
                    Accessible.ignored: true

                    // M-7：一圈 1000 ms、等速。減少動態（durSpin 是 0）時是靜止的 loader-circle（DS-43）
                    RotationAnimator on rotation {
                        from: 0
                        to: 360
                        duration: Theme.durSpin
                        loops: Animation.Infinite
                        running: spinner.visible && Theme.durSpin > 0
                    }
                }

                Text {
                    objectName: "searchMessageText"
                    anchors.verticalCenter: parent.verticalCenter
                    width: dropdown.availableWidth - 2 * Theme.spaceS - (spinner.visible ? spinner.width + Theme.spaceS : 0)
                    text: root.status === "empty" ? qsTr("No services match “%1”").arg(field.text.trim())
                                                  : root.summary
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontCaption
                    wrapMode: Text.Wrap
                }
            }

            // 錯誤：原因和 Retry 留在清單裡，不跳對話框（T32）。按鈕不拿焦點，Enter 也是 Retry
            RowLayout {
                objectName: "searchError"
                visible: root.status === "error"
                Layout.fillWidth: true
                Layout.margins: Theme.spaceS
                spacing: Theme.spaceS

                Image {
                    Layout.alignment: Qt.AlignTop
                    Layout.topMargin: 2
                    Layout.preferredWidth: Theme.iconS
                    Layout.preferredHeight: Theme.iconS
                    sourceSize: Qt.size(Theme.iconS, Theme.iconS)
                    source: "icons/circle-alert_errorOnDark.svg"
                    Accessible.ignored: true
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2

                    Text {
                        objectName: "searchErrorTitle"
                        Layout.fillWidth: true
                        text: qsTr("Couldn't search")
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontCaption
                        font.weight: Font.DemiBold
                    }

                    Text {
                        objectName: "searchErrorDetail"
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: root.search ? root.search.errorDetail : ""
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontSmall
                        wrapMode: Text.Wrap
                    }

                    Text {     // 同一次斷線連續失敗 3 次：和 InlineError 一樣多一行建議（DS-52 的原句）
                        Layout.fillWidth: true
                        visible: root.search !== null && root.search.failureCount >= 3
                        text: qsTr("If it still fails, check your network or contact your administrator.")
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontSmall
                        wrapMode: Text.Wrap
                    }
                }

                BusyButton {
                    objectName: "searchRetry"
                    Layout.alignment: Qt.AlignVCenter
                    focusPolicy: Qt.NoFocus
                    text: qsTr("Retry")
                    onClicked: root.search.retry()
                }
            }

            ListView {
                id: list
                objectName: "searchResults"
                visible: root.status === "ready" || (root.refreshing && count > 0)
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredHeight: contentHeight
                implicitHeight: contentHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                model: root.results
                currentIndex: root.currentIndex
                opacity: root.refreshing ? 0.5 : 1      // 新的字還在查：舊的結果留著、變淡（DS-33）

                delegate: ItemDelegate {
                    id: row
                    objectName: "searchResult"

                    required property int index
                    required property var modelData
                    readonly property string spoken: root.spokenText(modelData)

                    width: ListView.view ? ListView.view.width : 0
                    height: 56
                    padding: 0
                    leftPadding: Theme.spaceS
                    rightPadding: Theme.spaceS
                    focusPolicy: Qt.NoFocus         // 焦點留在欄位上
                    hoverEnabled: true
                    highlighted: index === root.currentIndex
                    onHoveredChanged: if (hovered) root.currentIndex = index
                    onClicked: root.choose(index)

                    Accessible.role: Accessible.ListItem
                    Accessible.name: spoken
                    Accessible.selected: highlighted

                    // 選到的那一列：比卡片亮兩階（和次要按鈕按下去同一個灰），鍵盤和滑鼠選到的是同一列
                    background: Rectangle {
                        radius: Theme.radiusM
                        color: row.down ? Theme.actionSecondaryBorder
                             : row.highlighted ? Theme.actionSecondaryPressed : "transparent"
                    }

                    contentItem: RowLayout {
                        spacing: Theme.spaceM

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2

                            Text {
                                objectName: "resultServiceId"
                                Layout.fillWidth: true
                                text: row.modelData.serviceId
                                color: Theme.textPrimary
                                font.pixelSize: Theme.fontInput
                                font.weight: Font.DemiBold
                                font.features: { "tnum": 1 }
                                elide: Text.ElideRight
                                Accessible.ignored: true
                            }

                            Text {
                                objectName: "resultDetail"
                                Layout.fillWidth: true
                                text: root.detailText(row.modelData)
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSmall
                                elide: Text.ElideRight
                                Accessible.ignored: true
                            }
                        }

                        StatusChip {
                            status: row.modelData.status
                        }
                    }
                }
            }

            // 超過 10 筆：只列出前 10 筆，請使用者多打幾個字
            Text {
                objectName: "searchMore"
                visible: list.visible && root.search !== null && root.search.hasMore
                Layout.fillWidth: true
                Layout.margins: Theme.spaceS
                text: root.search ? qsTr("Showing the first %1. Keep typing to narrow it down.").arg(root.search.limit) : ""
                color: Theme.textFaint
                font.pixelSize: Theme.fontLabel
                wrapMode: Text.Wrap
            }
        }
    }
}
