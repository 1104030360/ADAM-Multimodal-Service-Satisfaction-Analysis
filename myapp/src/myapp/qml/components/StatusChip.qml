import QtQuick

// 服務狀態標籤（DS-22）：1 Not Analyzed、2 Analyzing、3 Completed、Failed。
// - 「Failed」只會在 status 1 時出現：資料庫沒有失敗狀態，分析服務失敗時把 2 改回 1，
//   桌面端看過 2 又變回 1 才知道（DS-22、D4）。重新送出後 status 變成 2，標籤就跟著資料庫走，
//   不會被還沒清掉的 failed 蓋住。
// - 不轉圈、不閃爍：清單每一列都有一個，狀態改變由 toast 通知。
// - 標籤本身不給螢幕閱讀器（Accessible.ignored）；頁面把 label 寫進那一列的名稱，例如 "S20261031093000, Analyzing"。
// - 報表（new-feature N4，T37）：reportState 不是空字串時改顯示報表的狀態：Generating、Ready（PDF 好了、還沒發佈）、
//   Published、Failed。顏色和服務狀態同一組（產生中是藍、發佈是綠、失敗是紅、還沒發佈是中性），圖示和字一起說明。
Rectangle {
    id: root

    property int status: 1
    property bool failed: false
    property string reportState: ""     // 報表：generating、ready、published、failed；空字串＝服務狀態

    readonly property bool isReport: reportState !== ""
    // 報表的狀態換成服務狀態的同一組外觀：failed → Failed、published → 3、generating → 2、ready → 1
    readonly property int tone: !isReport ? status
                              : reportState === "published" ? 3 : reportState === "generating" ? 2 : 1
    readonly property bool showsFailure: isReport ? reportState === "failed" : failed && status === 1
    readonly property string label: isReport
        ? (showsFailure ? qsTr("Failed") : tone === 3 ? qsTr("Published") : tone === 2 ? qsTr("Generating")
                                                                                       : qsTr("Ready"))
        : showsFailure ? qsTr("Failed")
        : status === 3 ? qsTr("Completed")
        : status === 2 ? qsTr("Analyzing")
        : qsTr("Not Analyzed")
    readonly property color textColor: showsFailure ? Theme.errorOnDark
                                     : tone === 3 ? Theme.successOnDark
                                     : tone === 2 ? Theme.infoOnDark
                                     : Theme.textPrimary

    implicitWidth: content.implicitWidth + 2 * Theme.spaceS
    implicitHeight: Theme.chipHeight
    radius: height / 2
    color: showsFailure ? Theme.errorTint
         : tone === 3 ? Theme.successTint
         : tone === 2 ? Theme.infoTint
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
                  : root.tone === 3 ? "icons/circle-check_successOnDark.svg"
                  : root.tone === 2 ? "icons/clock_infoOnDark.svg"
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
