pragma ComponentBehavior: Bound
import QtQuick
// 下拉選單、清單自訂了 background、contentItem、popup，要用 Basic 樣式（T13）：原生樣式不能自訂，深色也做不出來
import QtQuick.Controls.Basic

// 選鏡頭、麥克風的深色下拉選單（錄影頁和設定頁的設備設定共用，T29、T41）。
// 欄位底 field、框線 fieldBorder（invalid 時 errorOnDark、2 px）、鍵盤焦點框 focusOnDark、深色的清單。
// model 是裝置名稱的清單（deviceFinder.cameras 等）；寬度由使用的地方決定，高 44。
ComboBox {
    id: select

    property bool invalid: false

    implicitHeight: Theme.controlHeight
    leftPadding: 14
    rightPadding: 14 + Theme.iconS + 10
    font.pixelSize: Theme.fontCaption
    focusPolicy: Qt.StrongFocus

    contentItem: Text {
        text: select.displayText
        font: select.font
        color: select.enabled ? Theme.textPrimary : Theme.textDisabled
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    indicator: Image {
        x: select.width - width - 14
        y: (select.height - height) / 2
        width: Theme.iconS
        height: Theme.iconS
        sourceSize: Qt.size(Theme.iconS, Theme.iconS)
        source: "icons/chevron-down_iconNeutral.svg"
        Accessible.ignored: true
    }

    background: Rectangle {
        implicitWidth: 200
        implicitHeight: Theme.controlHeight
        radius: 12
        color: Theme.field
        border.width: select.invalid ? 2 : 1
        border.color: select.invalid ? Theme.errorOnDark : Theme.fieldBorder

        Rectangle {     // 鍵盤焦點框（DS-30）：2 px、往外 2 px；滑鼠點的不畫
            visible: select.visualFocus
            anchors.fill: parent
            anchors.margins: -4
            radius: parent.radius + 4
            color: "transparent"
            border.color: Theme.focusOnDark
            border.width: 2
        }
    }

    delegate: ItemDelegate {
        id: option
        required property int index
        required property string modelData

        width: ListView.view ? ListView.view.width : 0
        height: 40
        leftPadding: 10
        highlighted: select.highlightedIndex === index
        Accessible.name: modelData

        contentItem: Text {
            text: option.modelData
            font: select.font
            color: Theme.textPrimary
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }

        background: Rectangle {
            radius: 8
            color: option.highlighted || option.down ? Theme.tile : "transparent"
        }
    }

    popup: Popup {
        y: select.height + 4
        width: select.width
        implicitHeight: Math.min(contentItem.implicitHeight + topPadding + bottomPadding, 280)
        padding: 4

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: select.popup.visible ? select.delegateModel : null
            currentIndex: select.highlightedIndex
            boundsBehavior: Flickable.StopAtBounds
            ScrollIndicator.vertical: ScrollIndicator {}
        }

        background: Rectangle {
            radius: 12
            color: Theme.card
            border.color: Theme.fieldBorder
            border.width: 1
        }
    }
}
