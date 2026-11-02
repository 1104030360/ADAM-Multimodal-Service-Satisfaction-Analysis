import QtQuick
import "../components"

// 時間註記的時間（設計稿「Coaching, redesigned」，T47）：暖色的小膠囊，播放的三角形加「02:15」。
// 只是外觀：按的是外面那一列（或按鈕），螢幕閱讀器唸外面的名稱（「Play from 02:15」）。
Rectangle {
    id: root

    property string clock: ""
    property int pillHeight: 24

    implicitWidth: row.implicitWidth + 16
    implicitHeight: pillHeight
    radius: height / 2
    color: Theme.warningTint
    Accessible.ignored: true

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 4

        Image {
            anchors.verticalCenter: parent.verticalCenter
            width: 11
            height: 11
            sourceSize: Qt.size(11, 11)
            source: "../components/icons/play_warningOnDark.svg"
        }
        Text {
            objectName: "momentClock"
            anchors.verticalCenter: parent.verticalCenter
            text: root.clock
            color: Theme.warningOnDark
            font.pixelSize: root.pillHeight >= 28 ? Theme.fontSmall : Theme.fontLabel
            font.weight: Font.Bold
            font.features: { "tnum": 1 }
        }
    }
}
