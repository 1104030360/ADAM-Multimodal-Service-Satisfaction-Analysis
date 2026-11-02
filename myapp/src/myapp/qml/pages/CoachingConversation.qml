pragma ComponentBehavior: Bound
import QtQuick
// 自訂了送出按鈕的 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "../components"

// 一項目標的對話（設計稿「Coaching, redesigned」的 Conversation，T47）：取代舊版像留言板的 History。
// - 雙方的訊息是對話框：自己的在右邊（深灰），對方的在左邊（有框線），下面寫誰、什麼時候。
// - 其他的事（訂了目標、讀了、選了後續服務、完成、調整、撤回）是置中的一行小字；原因、結論、新的目標在下面多一行。
//   時間註記不放進對話（在 Moments 裡）。項目都在 Python（coaching.conversation_items）。
// - 進行中的才有輸入框（兩端都可以寫：sp_coaching_respond 讓服務人員回自己的、管理員回全部）。Return 或 Send 送出；
//   空的不送（欄位下面說明），失敗時字留著，成功了清空（訊息出現在上面，不跳通知）。
ColumnLayout {
    id: root

    property var vm: null
    property var items: []
    property string actionId: ""
    property bool canWrite: false
    property string partner: ""         // 對方的名字：「Conversation with Mia Chen」
    property string placeholder: ""     // 「Write to Mia…」「Reply to Alex…」

    readonly property bool saving: vm !== null && actionId !== "" && vm.saving.indexOf(actionId) >= 0
    readonly property var lastError: vm ? vm.actionError : ({})
    readonly property string errorText: lastError.actionId === actionId && actionId !== "" && lastError.op === "respond"
                                        ? lastError.text : ""
    readonly property alias field: composer     // 測試用

    spacing: 12

    function send() {
        if (vm && !saving && actionId !== "")
            vm.respond(actionId, composer.text)
    }

    Connections {
        target: root.vm
        function onSaved(actionId, op) {
            if (actionId === root.actionId && op === "respond")
                composer.text = ""
        }
    }

    Text {
        Layout.fillWidth: true
        text: qsTr("Conversation")
        color: Theme.textFaint
        font.pixelSize: Theme.fontSmall
        font.weight: Font.Bold
        font.capitalization: Font.AllUppercase
        font.letterSpacing: 1
        Accessible.role: Accessible.Heading
        Accessible.name: text
    }

    ColumnLayout {
        objectName: "conversationLog"
        Layout.fillWidth: true
        spacing: 10
        Accessible.role: Accessible.List
        Accessible.name: qsTr("Conversation with %1").arg(root.partner)

        Repeater {
            model: root.items
            delegate: Item {
                id: entry

                required property var modelData
                readonly property bool message: modelData.kind === "message"
                readonly property bool mine: modelData.mine === true

                objectName: message ? "conversationMessage" : "conversationEvent"
                Layout.fillWidth: true
                implicitHeight: message ? bubbleColumn.implicitHeight : eventColumn.implicitHeight
                Accessible.role: Accessible.StaticText
                Accessible.name: message ? qsTr("%1, %2: %3").arg(modelData.author).arg(modelData.whenText)
                                                              .arg(modelData.text)
                                         : [modelData.text].concat(modelData.lines).join(". ") + ", " + modelData.whenText

                // 其他的事：置中的一行小字，前面一個小點；下面是原因、結論
                Column {
                    id: eventColumn
                    visible: !entry.message
                    width: parent.width
                    spacing: 3

                    Row {
                        x: (eventColumn.width - width) / 2
                        spacing: 8

                        Rectangle {
                            y: 5
                            width: 6
                            height: 6
                            radius: 3
                            color: Theme.actionSecondaryBorder
                        }
                        Text {
                            objectName: "eventText"
                            width: Math.min(implicitWidth, eventColumn.width * 0.86)
                            text: qsTr("%1 · %2").arg(entry.modelData.text).arg(entry.modelData.whenText)
                            color: Theme.textFaint
                            font.pixelSize: Theme.fontLabel
                            font.features: { "tnum": 1 }
                            wrapMode: Text.Wrap
                            horizontalAlignment: Text.AlignHCenter
                        }
                    }
                    Repeater {
                        model: entry.modelData.lines
                        // 置中用 x，不用 anchors：Repeater 剛建立 delegate 時 parent 還是 null
                        delegate: Text {
                            required property string modelData
                            objectName: "eventLine"
                            x: (eventColumn.width - width) / 2
                            width: Math.min(implicitWidth, eventColumn.width * 0.76)
                            text: modelData
                            color: Theme.textSecondary
                            font.pixelSize: Theme.fontSmall
                            wrapMode: Text.Wrap
                            horizontalAlignment: Text.AlignHCenter
                        }
                    }
                }

                // 訊息：自己的在右邊，對方的在左邊；最寬是這一欄的 76%
                Column {
                    id: bubbleColumn
                    visible: entry.message
                    x: entry.mine ? parent.width - width : 0
                    width: Math.min(parent.width * 0.76, Math.max(bubbleText.implicitWidth + 28, author.implicitWidth))
                    spacing: 4

                    Rectangle {
                        objectName: "bubble"
                        x: entry.mine ? parent.width - width : 0
                        width: Math.min(parent.width, bubbleText.implicitWidth + 28)
                        height: bubbleText.implicitHeight + 20
                        topLeftRadius: 16
                        topRightRadius: 16
                        bottomLeftRadius: entry.mine ? 16 : 4
                        bottomRightRadius: entry.mine ? 4 : 16
                        color: entry.mine ? Theme.bubbleMine : Theme.tile
                        border.width: entry.mine ? 0 : 1
                        border.color: Theme.tileBorder

                        Text {
                            id: bubbleText
                            objectName: "bubbleText"
                            x: 14
                            y: 10
                            width: parent.width - 28
                            text: entry.modelData.text
                            color: entry.mine ? Theme.textTitle : Theme.textPrimary
                            font.pixelSize: Theme.fontInput
                            lineHeight: 1.2
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                        }
                    }
                    Text {
                        id: author
                        objectName: "bubbleAuthor"
                        x: entry.mine ? parent.width - width : 0
                        text: qsTr("%1 · %2").arg(entry.modelData.author).arg(entry.modelData.whenText)
                        color: Theme.textFaint
                        font.pixelSize: Theme.fontLabel
                        font.features: { "tnum": 1 }
                    }
                }
            }
        }
    }

    // 輸入框（進行中）：膠囊＋圓的 Send
    RowLayout {
        objectName: "composerRow"
        Layout.fillWidth: true
        Layout.topMargin: 4
        visible: root.canWrite
        spacing: 10

        CoachingField {
            id: composer
            objectName: "composerField"
            Layout.fillWidth: true
            cornerRadius: 22
            maximumLength: 2000
            placeholderText: root.placeholder
            invalid: root.errorText !== ""
            errorText: root.errorText
            Accessible.name: qsTr("Message to %1").arg(root.partner)
            onAccepted: root.send()
            onTextChanged: {
                if (root.errorText !== "" && root.vm)
                    root.vm.clearActionError()
            }
        }

        Button {
            id: sendButton
            objectName: "sendMessageButton"
            implicitWidth: Theme.controlHeight
            implicitHeight: Theme.controlHeight
            focusPolicy: Qt.StrongFocus
            text: qsTr("Send")
            Accessible.name: root.saving ? qsTr("Sending…") : text
            Keys.onReturnPressed: click()
            Keys.onEnterPressed: click()
            onClicked: root.send()

            contentItem: Item {
                Image {
                    anchors.centerIn: parent
                    width: 18
                    height: 18
                    sourceSize: Qt.size(18, 18)
                    source: "../components/icons/send_actionPrimaryText.svg"
                    opacity: root.saving ? 0.4 : 1
                    Accessible.ignored: true
                }
            }
            background: Rectangle {
                radius: width / 2
                color: sendButton.down || root.saving ? Theme.actionPrimaryPressed
                     : sendButton.hovered ? Theme.actionPrimaryHover : Theme.actionPrimary

                Rectangle {     // 鍵盤焦點框（DS-30）
                    visible: sendButton.visualFocus
                    anchors.fill: parent
                    anchors.margins: -4
                    radius: width / 2
                    color: "transparent"
                    border.color: Theme.focusOnDark
                    border.width: 2
                }
            }
        }
    }

    FieldError {
        objectName: "conversationError"
        Layout.fillWidth: true
        visible: hasError
        text: root.errorText
    }
}
