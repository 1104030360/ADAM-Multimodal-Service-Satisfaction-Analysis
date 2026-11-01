pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 background、contentItem，要用 Basic 樣式（T13）。在這個檔案裡 import，用它的頁面不必換掉自己的樣式
import QtQuick.Controls.Basic
import "../components"

// 頁面上方的分頁按鈕（DS-12「按鈕與分頁」）：150×50 的膠囊。三個頁面共用，兩端長得一樣（UX-46）：
// 服務人員首頁的 Daily／Weekly、管理員首頁的四個分頁、分析頁的狀態分頁。
// - 選取的是石墨灰底白字（12.63:1），沒選的是 #E0E0E0 底 #333333 字（9.57:1）。以前選取的是紅色 #E53935
//   （白字只有 4.23:1）或 Material Green（2.78:1）；紅色、綠色只留給狀態（D2、DS-71）。
// - 寬度用粗體量：選取時字變粗，按鈕不變寬，旁邊的分頁不會位移。
// - 鍵盤：Tab 到得了，Space／Enter 選取；焦點框只在鍵盤造成焦點時出現（DS-30）。
//   輔助技術唸「Daily Scores, tab, selected」。
Button {
    id: tab

    property bool selected: false

    implicitWidth: Math.max(150, Math.ceil(boldMetrics.advanceWidth) + leftPadding + rightPadding)
    implicitHeight: Theme.controlHeightL
    horizontalPadding: Theme.spaceL
    verticalPadding: 0
    focusPolicy: Qt.StrongFocus
    font.pixelSize: Theme.fontBody
    font.weight: selected ? Font.DemiBold : Font.Normal
    Accessible.role: Accessible.PageTab
    Accessible.name: text
    Accessible.selected: selected

    TextMetrics {
        id: boldMetrics
        font.pixelSize: tab.font.pixelSize
        font.weight: Font.DemiBold
        text: tab.text
    }

    contentItem: Text {
        text: tab.text
        font: tab.font
        color: tab.selected ? Theme.segmentSelectedText : Theme.segmentIdleText
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        radius: height / 2
        color: tab.selected ? Theme.segmentSelected : Theme.segmentIdle

        Rectangle {     // 鍵盤焦點框（DS-30）：2 px、往外 2 px
            visible: tab.visualFocus
            anchors.fill: parent
            anchors.margins: -4
            radius: parent.radius + 4
            color: "transparent"
            border.color: Theme.focusOnLight
            border.width: 2
        }
    }
}
