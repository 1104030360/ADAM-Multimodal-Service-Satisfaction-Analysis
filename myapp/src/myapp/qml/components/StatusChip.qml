import QtQuick

// 服務狀態標籤（DS-22）：1 Not Analyzed、2 Analyzing、3 Completed、Failed。
// - 「Failed」只會在 status 1 時出現：資料庫沒有失敗狀態，分析服務失敗時把 2 改回 1，
//   桌面端看過 2 又變回 1 才知道（DS-22、D4）。重新送出後 status 變成 2，標籤就跟著資料庫走，
//   不會被還沒清掉的 failed 蓋住。
// - 不轉圈、不閃爍：清單每一列都有一個，狀態改變由 toast 通知。
// - 標籤本身不給螢幕閱讀器（Accessible.ignored）；頁面把 label 寫進那一列的名稱，例如 "S20261031093000, Analyzing"。
Rectangle {
    id: root

    property int status: 1
    property bool failed: false

    readonly property bool showsFailure: failed && status === 1
    readonly property string label: showsFailure ? qsTr("Failed")
                                  : status === 3 ? qsTr("Completed")
                                  : status === 2 ? qsTr("Analyzing")
                                  : qsTr("Not Analyzed")
    readonly property color textColor: showsFailure ? Theme.errorOnDark
                                     : status === 3 ? Theme.successOnDark
                                     : status === 2 ? Theme.infoOnDark
                                     : Theme.textPrimary

    implicitWidth: content.implicitWidth + 2 * Theme.spaceS
    implicitHeight: Theme.chipHeight
    radius: height / 2
    color: showsFailure ? Theme.errorTint
         : status === 3 ? Theme.successTint
         : status === 2 ? Theme.infoTint
         : Theme.tile

    Accessible.ignored: true

    Row {
        id: content
        anchors.centerIn: parent
        spacing: Theme.spaceXS

        Image {
            objectName: "chipIcon"
            anchors.verticalCenter: parent.verticalCenter
            width: 14
            height: 14
            sourceSize: Qt.size(14, 14)
            source: root.showsFailure ? "icons/circle-x_errorOnDark.svg"
                  : root.status === 3 ? "icons/circle-check_successOnDark.svg"
                  : root.status === 2 ? "icons/clock_infoOnDark.svg"
                  : "icons/circle-dashed_textPrimary.svg"
        }

        Text {
            objectName: "chipLabel"
            anchors.verticalCenter: parent.verticalCenter
            text: root.label
            color: root.textColor
            font.pixelSize: Theme.fontCaption
            font.weight: Font.DemiBold
        }
    }
}
