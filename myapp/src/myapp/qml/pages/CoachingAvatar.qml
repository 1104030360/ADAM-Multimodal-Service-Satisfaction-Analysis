import QtQuick
import "../components"

// 輔導頁的頭像（設計稿「Coaching, redesigned」，T47）：圓圈裡是名字的縮寫（coaching.initials，「Mia Chen」→「MC」）。
// 只是裝飾：名字就寫在旁邊，所以不給螢幕閱讀器。
Rectangle {
    id: root

    property string initials: ""
    property int size: 36

    implicitWidth: size
    implicitHeight: size
    radius: size / 2
    color: Theme.tileSelected
    border.width: 1
    border.color: Theme.tileBorder
    Accessible.ignored: true

    Text {
        anchors.centerIn: parent
        text: root.initials
        color: Theme.iconNeutral
        font.pixelSize: root.size >= 40 ? Theme.fontCaption : Theme.fontSmall
        font.weight: Font.Bold
    }
}
