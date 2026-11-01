import QtQuick

// 欄位錯誤（DS-27）：放在欄位正下方。沒有錯誤時也先保留一行高度，錯誤出現時版面不跳。
// 錯誤出現時用 Polite 唸出來；頁面另外把 text 寫進欄位的 Accessible.description，欄位框線改成 errorOnLight。
Item {
    id: root

    property string text: ""
    property var announced: []      // 唸過的內容，測試和除錯用

    readonly property bool hasError: text !== ""
    readonly property int lineHeight: 20    // 14 px 字的一行（DS-27 的設計稿）

    implicitWidth: message.x + message.implicitWidth
    implicitHeight: Math.max(lineHeight, message.implicitHeight)

    Accessible.role: Accessible.StaticText
    Accessible.name: text
    Accessible.ignored: !hasError

    Image {
        objectName: "fieldErrorIcon"
        visible: root.hasError
        y: (root.lineHeight - height) / 2
        width: 14
        height: 14
        sourceSize: Qt.size(14, 14)
        source: "icons/circle-alert_errorOnDark.svg"
        Accessible.ignored: true
    }

    Text {
        id: message
        objectName: "fieldErrorText"
        x: 14 + 6
        width: Math.max(0, root.width - x)
        text: root.text
        color: Theme.errorOnDark
        font.pixelSize: Theme.fontCaption
        lineHeight: root.lineHeight
        lineHeightMode: Text.FixedHeight
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.Wrap
    }

    QtObject {
        id: internal
        property bool ready: false     // 建立時設定 text 也會觸發 changed，那時只在 onCompleted 唸一次
    }

    function announce() {
        // 在 onTextChanged 裡 hasError 可能還沒重新計算，直接看 text
        if (!internal.ready || text === "")
            return
        announced = announced.concat([text])
        Accessible.announce(text, Accessible.Polite)
    }

    onTextChanged: announce()
    Component.onCompleted: {
        internal.ready = true
        announce()
    }
}
