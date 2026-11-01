pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 background、contentItem、indicator、popup，要用 Basic 樣式（T13）。在這個檔案裡 import，用它的頁面不受影響
import QtQuick.Controls.Basic
import "../components"

// 首頁卡片右上角的下拉選單（設計稿 Main、AdminHome）：Overview 的期間（Today、Last 7 days、Last 30 days）、
// 趨勢卡的分數（Overall、Text、Audio、Facial）。兩端的首頁共用。
// - options：[{value, text}]；value 是現在選的那一個的 value。選了別的發 picked(value)，由頁面改自己的屬性。
// - 高 44 以上；框線預設是 fieldBorder（和卡片 3.1:1，WCAG 1.4.11）；鍵盤焦點時換成 2 px 的焦點框（DS-30）。
// - 趨勢卡在藍底上，顏色改用 trendControl、trendControlBorder（fill、borderColor、textColor、chevron 都可以換）。
// - 日期欄的月曆（DateField.qml，N2）也用它選月、年：清單最高 320、放不下時推回視窗裡，打開時捲到選到的那一個。
ComboBox {
    id: select

    property var options: []
    property string value: ""
    property color fill: Theme.actionSecondaryBg
    property color borderColor: Theme.fieldBorder
    property color textColor: Theme.textPrimary
    property url leadingIcon: ""                         // 文字前面的圖示（期間的日曆）
    property url chevron: "../components/icons/chevron-down_iconNeutral.svg"
    property int popupMaxHeight: 320                     // 選單最高多少：選項很多時（月曆的月、年，報表表單的員工、服務）在選單裡捲動

    signal picked(string value)

    function indexOfValue_(wanted) {
        return Math.max(0, options.findIndex(option => option.value === wanted))
    }

    model: options
    textRole: "text"
    valueRole: "value"
    // 使用者選的時候 ComboBox 自己改 currentIndex（綁定會斷）：value 從外面改時再對一次
    currentIndex: indexOfValue_(value)
    onValueChanged: currentIndex = indexOfValue_(value)
    onActivated: (index) => select.picked(options[index].value)

    implicitHeight: Theme.controlHeight
    leftPadding: leadingIcon.toString() !== "" ? 42 : 16
    rightPadding: 38
    font.pixelSize: Theme.fontCaption
    font.weight: Font.DemiBold
    focusPolicy: Qt.StrongFocus

    contentItem: Text {
        text: select.displayText
        color: select.textColor
        font: select.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    indicator: Image {
        x: select.width - width - 13
        anchors.verticalCenter: parent.verticalCenter
        width: 16
        height: 16
        sourceSize: Qt.size(16, 16)
        source: select.chevron
    }

    background: Rectangle {
        implicitWidth: 140
        radius: 12
        color: select.fill
        border.color: select.visualFocus ? Theme.focusOnDark : select.borderColor
        border.width: select.visualFocus ? 2 : 1

        Image {
            visible: select.leadingIcon.toString() !== ""
            x: 14
            anchors.verticalCenter: parent.verticalCenter
            width: 18
            height: 18
            sourceSize: Qt.size(18, 18)
            source: select.leadingIcon
        }
    }

    delegate: ItemDelegate {
        id: option
        required property var modelData
        required property int index
        width: ListView.view ? ListView.view.width : 0
        height: Theme.controlHeight
        highlighted: select.highlightedIndex === index
        text: modelData.text

        contentItem: Text {
            text: option.text
            color: Theme.textPrimary
            font.pixelSize: Theme.fontCaption
            font.weight: option.index === select.currentIndex ? Font.DemiBold : Font.Normal
            verticalAlignment: Text.AlignVCenter
        }

        background: Rectangle {
            color: option.highlighted ? Theme.navItemHover : "transparent"
            radius: 8
        }
    }

    popup: Popup {
        y: select.height + 4
        width: Math.max(select.width, 180)
        padding: 4
        margins: Theme.spaceS
        implicitHeight: Math.min(contentItem.implicitHeight + topPadding + bottomPadding, select.popupMaxHeight)
        onOpened: optionList.positionViewAtIndex(select.currentIndex, ListView.Contain)

        contentItem: ListView {
            id: optionList
            implicitHeight: contentHeight
            clip: true
            model: select.popup.visible ? select.delegateModel : null
            currentIndex: select.highlightedIndex
            boundsBehavior: Flickable.StopAtBounds
            ScrollIndicator.vertical: ScrollIndicator {}
        }

        background: Rectangle {
            color: Theme.card
            border.color: Theme.tileBorder
            radius: 12
        }
    }
}
