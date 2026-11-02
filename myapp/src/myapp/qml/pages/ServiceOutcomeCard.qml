pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 Pane 的 background（Card），要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 服務結果與待辦（PLAN1 W1，T44）：分析服務讀這筆服務的逐字稿（已做說話者分離），用 Ollama Cloud 填的。
// 兩端的服務明細都有（ServiceDetailView.qml 放在分數列下面）；資料是 mediaModel 的 outcome、outcomeFilled、todos、
// outcomeModel（myapp/media.py，和頁首、分數同一個查詢）。
// - 結果是一個標籤：圖示＋字，不只靠顏色（Resolved、Documents Missing、Handoff Needed、Unresolved、Unknown；
//   DS-51：狀態標籤用 Title Case）。顏色和服務狀態的標籤同一組（完成綠、要補件或交接橘、沒解決紅、不知道中性）。
// - 待辦是一項一行的清單；標題下面註明「AI-generated from the transcript · 模型」。只能看：沒有負責人、狀態、期限，
//   也不能改（PLAN1 W1 的首版範圍）。AI 寫的字一律當純文字顯示，不解讀成 HTML。
// - 還沒填（NULL：沒有逐字稿、AI 關著、這次的呼叫失敗）：結果是 Unknown、不列待辦，說明分析有逐字稿時會填。
// - 還在查，或沒有這筆服務（serviceFound 是 false，例如服務人員打開別人的服務）：整張卡不顯示。
Card {
    id: card

    property var media: null    // mediaModel（ServiceDetailView 的 detail.media）

    readonly property string outcome: media ? media.outcome : ""
    readonly property bool filled: media !== null && media.outcomeFilled
    readonly property var todos: media ? media.todos : []
    readonly property string modelName: media ? media.outcomeModel : ""
    readonly property string label: {
        switch (outcome) {
        case "resolved": return qsTr("Resolved")
        case "documentsMissing": return qsTr("Documents Missing")
        case "handoffNeeded": return qsTr("Handoff Needed")
        case "unresolved": return qsTr("Unresolved")
        }
        return qsTr("Unknown")
    }
    readonly property string icon: {
        switch (outcome) {
        case "resolved": return "../components/icons/circle-check_successOnDark.svg"
        case "documentsMissing": return "../components/icons/triangle-alert_warningOnDark.svg"
        case "handoffNeeded": return "../components/icons/arrow-right_warningOnDark.svg"
        case "unresolved": return "../components/icons/circle-x_errorOnDark.svg"
        }
        return "../components/icons/circle-dashed_textPrimary.svg"
    }
    readonly property color tint: outcome === "resolved" ? Theme.successTint
                                : outcome === "documentsMissing" || outcome === "handoffNeeded" ? Theme.warningTint
                                : outcome === "unresolved" ? Theme.errorTint : Theme.tile
    readonly property color ink: outcome === "resolved" ? Theme.successOnDark
                               : outcome === "documentsMissing" || outcome === "handoffNeeded" ? Theme.warningOnDark
                               : outcome === "unresolved" ? Theme.errorOnDark : Theme.textPrimary

    visible: media !== null && media.state === "ready" && media.serviceFound && outcome !== ""
    topPadding: 18
    bottomPadding: 18
    Accessible.role: Accessible.Grouping
    Accessible.name: qsTr("Outcome and to-dos for this service")

    RowLayout {
        width: parent ? parent.width : 0
        spacing: Theme.spaceM

        Image {
            Layout.alignment: Qt.AlignTop
            Layout.topMargin: 2
            Layout.preferredWidth: Theme.iconM
            Layout.preferredHeight: Theme.iconM
            sourceSize: Qt.size(Theme.iconM, Theme.iconM)
            source: "../components/icons/clipboard-list_iconNeutral.svg"
            Accessible.ignored: true
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6

            // 標題和結果的標籤
            Row {
                spacing: Theme.spaceS

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("Outcome")
                    color: Theme.textTitle
                    font.pixelSize: Theme.fontBody
                    font.weight: Font.Bold
                    Accessible.role: Accessible.Heading
                    Accessible.name: qsTr("Outcome: %1").arg(card.label)
                }

                Rectangle {
                    objectName: "outcomeChip"
                    anchors.verticalCenter: parent.verticalCenter
                    width: chipContent.implicitWidth + 2 * Theme.spaceS
                    height: Theme.chipHeight
                    radius: height / 2
                    color: card.tint
                    Accessible.ignored: true     // 標題的名稱已經唸出結果

                    Row {
                        id: chipContent
                        anchors.centerIn: parent
                        spacing: Theme.spaceXS

                        Image {
                            objectName: "outcomeIcon"
                            anchors.verticalCenter: parent.verticalCenter
                            width: 14
                            height: 14
                            sourceSize: Qt.size(14, 14)
                            source: card.icon
                        }
                        Text {
                            objectName: "outcomeLabel"
                            anchors.verticalCenter: parent.verticalCenter
                            text: card.label
                            color: card.ink
                            font.pixelSize: Theme.fontCaption
                            font.weight: Font.DemiBold
                        }
                    }
                }
            }

            // AI 產生的標示（和模型名稱），或還沒填的說明
            Text {
                objectName: "outcomeNote"
                Layout.fillWidth: true
                text: !card.filled ? qsTr("Not filled in yet. The analysis fills this in when the service has a transcript.")
                    : card.modelName !== "" ? qsTr("AI-generated from the transcript · %1").arg(card.modelName)
                    : qsTr("AI-generated from the transcript")
                textFormat: Text.PlainText
                color: Theme.textMuted
                font.pixelSize: Theme.fontCaption
                wrapMode: Text.Wrap
            }

            // 待辦：填過了才有這一段
            Text {
                objectName: "todosTitle"
                Layout.topMargin: Theme.spaceS
                visible: card.filled
                text: qsTr("To-Dos")
                color: Theme.textSecondary
                font.pixelSize: Theme.fontCaption
                font.weight: Font.DemiBold
                Accessible.role: Accessible.Heading
                Accessible.name: text
            }

            Text {
                objectName: "noTodos"
                Layout.fillWidth: true
                visible: card.filled && card.todos.length === 0
                text: qsTr("No to-dos were mentioned in this service.")
                color: Theme.textMuted
                font.pixelSize: Theme.fontCaption
                wrapMode: Text.Wrap
            }

            Repeater {
                model: card.filled ? card.todos : []

                delegate: RowLayout {
                    id: todoRow
                    required property string modelData
                    required property int index
                    Layout.fillWidth: true
                    spacing: Theme.spaceS

                    Rectangle {     // 項目符號
                        Layout.alignment: Qt.AlignTop
                        Layout.topMargin: 7
                        Layout.preferredWidth: 6
                        Layout.preferredHeight: 6
                        radius: 3
                        color: Theme.textSecondary
                        Accessible.ignored: true
                    }
                    Text {
                        objectName: "todoItem"
                        Layout.fillWidth: true
                        text: todoRow.modelData
                        textFormat: Text.PlainText
                        color: Theme.textPrimary
                        font.pixelSize: Theme.fontBody
                        wrapMode: Text.Wrap
                        Accessible.role: Accessible.StaticText
                        Accessible.name: qsTr("To-do %1: %2").arg(todoRow.index + 1).arg(todoRow.modelData)
                    }
                }
            }
        }
    }
}
