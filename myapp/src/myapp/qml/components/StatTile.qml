pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic

// 數字方塊（new-UIUX PLAN 第 4 節；設計稿 Main、AdminHome 的 Overview）：圖示方塊 52 px、標籤、大數字（36 px，
// 等寬數字），最下面一行是和上一期比的變化（箭頭＋%）和分數帶的字，或一句說明（服務方塊的 Analyzed、Review →）。
// - 分數帶一定寫字，不只靠顏色（PLAN 第 2 節）；變化有箭頭：往上綠、往下紅，沒有變是灰色、沒有箭頭。
// - interactive：整塊是一個真的按鈕（服務人員端的服務方塊，打開分析頁的那個分頁）：Tab 停得到、Space／Enter 按下、
//   有焦點框，按了發 activated()。不能點的方塊不接受焦點，滑鼠移上去也不變，輔助技術唸成一般的文字。
// - loading：還沒有資料，畫灰色的骨架（PLAN 5-2：不轉圈；設計稿 States 的 Loading）。
AbstractButton {
    id: tile

    property url iconSource: ""
    property color iconFill: Theme.tile             // 待辦的方塊換成飽和的填色（Not analyzed：warningOnLight）
    property color iconBorder: Theme.tileBorder
    property string label: ""
    property string valueText: "—"                 // 排好的數字（「82.4」「41」）；沒有資料是「—」，不是 0
    property var delta: null                        // 和上一期比的變化（%）；null／undefined＝不比，不顯示
    property string bandText: ""                    // Theme.bandName(分數)：Good、Fair、Needs improvement
    property color bandColor: Theme.textMuted       // Theme.bandColor(分數)
    property string note: ""                        // 沒有變化、分數帶的方塊寫一句說明
    property color noteColor: Theme.textFaint
    property bool noteStrong: false                 // 說明要粗體（Review →）
    property url noteIcon: ""                       // 說明後面的小圖示
    property bool interactive: false
    property bool loading: false

    readonly property bool hasDelta: typeof delta === "number" && !isNaN(delta)
    readonly property color deltaColor: !hasDelta || delta === 0 ? Theme.textMuted
                                        : delta > 0 ? Theme.successOnDark : Theme.errorOnDark

    signal activated()

    implicitWidth: Math.max(118, implicitContentWidth)
    implicitHeight: implicitContentHeight
    padding: 0
    focusPolicy: interactive ? Qt.StrongFocus : Qt.NoFocus
    hoverEnabled: interactive
    onClicked: {
        if (interactive && !loading)
            activated()
    }

    // 輔助技術唸：「Overall 82.4, up 3.2%, Good」「Completed 41, Analyzed」
    Accessible.role: interactive ? Accessible.Button : Accessible.StaticText
    Accessible.focusable: interactive
    Accessible.name: {
        if (loading)
            return qsTr("%1, loading").arg(label)
        const parts = [qsTr("%1 %2").arg(label).arg(valueText === "—" ? qsTr("no data") : valueText)]
        if (hasDelta)
            parts.push(delta > 0 ? qsTr("up %1%").arg(Math.abs(delta).toFixed(1))
                       : delta < 0 ? qsTr("down %1%").arg(Math.abs(delta).toFixed(1)) : qsTr("no change"))
        if (bandText !== "")
            parts.push(bandText)
        if (note !== "")
            parts.push(note)
        return parts.join(", ")
    }

    HoverHandler {
        enabled: tile.interactive
        cursorShape: Qt.PointingHandCursor
    }

    contentItem: Item {
        implicitWidth: tile.loading ? skeleton.implicitWidth : column.implicitWidth
        implicitHeight: tile.loading ? skeleton.implicitHeight : column.implicitHeight

        Column {
            id: column
            objectName: "tileContent"
            visible: !tile.loading
            width: parent.width
            spacing: 10

            Rectangle {     // 圖示方塊（裝飾，輔助技術略過）
                objectName: "iconBox"
                width: Theme.statTile
                height: Theme.statTile
                radius: Theme.radiusTile
                color: tile.iconFill
                border.color: tile.iconBorder
                border.width: 1
                Accessible.ignored: true

                Image {
                    anchors.centerIn: parent
                    width: 24
                    height: 24
                    sourceSize: Qt.size(24, 24)
                    source: tile.iconSource
                }
            }

            Text {
                objectName: "tileLabel"
                width: parent.width
                text: tile.label
                color: Theme.textSecondary
                font.pixelSize: Theme.fontCaption
                elide: Text.ElideRight
            }

            Text {
                objectName: "tileValue"
                width: parent.width
                text: tile.valueText
                color: Theme.textTitle
                font.pixelSize: Theme.fontStat
                font.weight: Font.Bold
                font.features: { "tnum": 1 }       // 等寬數字：換期間時數字不會左右跳
                lineHeightMode: Text.FixedHeight
                lineHeight: 40
            }

            Row {
                objectName: "tileFooter"
                spacing: 10
                height: 18

                Row {       // 變化：↑ 3.2%
                    objectName: "tileDelta"
                    visible: tile.hasDelta
                    spacing: 3
                    anchors.verticalCenter: parent.verticalCenter

                    Image {
                        visible: tile.hasDelta && tile.delta !== 0
                        anchors.verticalCenter: parent.verticalCenter
                        width: 13
                        height: 13
                        sourceSize: Qt.size(13, 13)
                        source: tile.hasDelta && tile.delta > 0 ? "icons/arrow-up_successOnDark.svg"
                                                                : "icons/arrow-down_errorOnDark.svg"
                    }

                    Text {
                        objectName: "tileDeltaText"
                        text: tile.hasDelta ? qsTr("%1%").arg(Math.abs(tile.delta).toFixed(1)) : ""
                        color: tile.deltaColor
                        font.pixelSize: Theme.fontSmall
                        font.features: { "tnum": 1 }
                    }
                }

                Text {
                    objectName: "tileBand"
                    visible: text !== ""
                    anchors.verticalCenter: parent.verticalCenter
                    text: tile.bandText
                    color: tile.bandColor
                    font.pixelSize: Theme.fontSmall
                    font.weight: Font.DemiBold
                }

                Row {       // 說明：Analyzed、In progress、Review →
                    visible: tile.note !== ""
                    spacing: 4
                    anchors.verticalCenter: parent.verticalCenter

                    Text {
                        objectName: "tileNote"
                        text: tile.note
                        color: tile.noteColor
                        font.pixelSize: Theme.fontSmall
                        font.weight: tile.noteStrong ? Font.DemiBold : Font.Normal
                    }

                    Image {
                        visible: tile.noteIcon.toString() !== ""
                        anchors.verticalCenter: parent.verticalCenter
                        width: 14
                        height: 14
                        sourceSize: Qt.size(14, 14)
                        source: tile.noteIcon
                    }
                }
            }
        }

        // 骨架（設計稿 States）：圖示方塊、標籤、數字、最下面一行，各一塊灰色
        Column {
            id: skeleton
            objectName: "tileSkeleton"
            visible: tile.loading
            spacing: 12
            Accessible.ignored: true

            Rectangle { width: Theme.statTile; height: Theme.statTile; radius: Theme.radiusTile; color: Theme.tile }
            Rectangle { width: 64; height: 12; radius: 6; color: Theme.cardBorder }
            Rectangle { width: 72; height: 32; radius: 8; color: Theme.cardBorder }
            Rectangle { width: 96; height: 10; radius: 5; color: Theme.tile }
        }
    }

    background: Item {
        Rectangle {     // 鍵盤焦點框（DS-30）：2 px，往外 6 px
            objectName: "tileFocusRing"
            visible: tile.visualFocus
            anchors.fill: parent
            anchors.margins: -6
            radius: Theme.radiusTile
            color: "transparent"
            border.color: Theme.focusOnDark
            border.width: 2
        }
    }
}
