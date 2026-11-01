import QtQuick
import "../components"

// 分數環弧線顏色的說明（UX-07：以前只有顏色，沒有寫門檻）。兩端的首頁共用（UX-46）。
// 顏色和弧線一樣是 DS-13 的成功、警告、錯誤（白底 5.13、5.85、5.62:1）；每一項都有文字，顏色不是唯一的線索。
// 輔助技術把整行當成一句話唸，不逐項唸。
Row {
    id: legend

    spacing: Theme.spaceL
    Accessible.role: Accessible.StaticText
    Accessible.name: qsTr("Arc colors: good is 85 and up, fair is 70 to 84, needs improvement is below 70.")

    Repeater {
        model: [
            { color: Theme.successOnLight, text: qsTr("Good: 85 and up") },
            { color: Theme.warningOnLight, text: qsTr("Fair: 70–84") },
            { color: Theme.errorOnLight, text: qsTr("Needs improvement: below 70") }
        ]

        Row {
            id: item
            required property var modelData
            spacing: Theme.spaceXS
            Accessible.ignored: true

            Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: 10
                height: 10
                radius: 5
                color: item.modelData.color
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: item.modelData.text
                color: Theme.textSecondary
                font.pixelSize: Theme.fontCaption
                Accessible.ignored: true
            }
        }
    }
}
