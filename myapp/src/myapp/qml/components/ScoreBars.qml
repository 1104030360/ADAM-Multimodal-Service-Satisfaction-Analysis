pragma ComponentBehavior: Bound
import QtQuick
// 自訂了長條按鈕、提示、捲軸的外觀，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic

// 分數長條圖（new-UIUX PLAN 第 4 節 ScoreBars；設計稿 Main 的 Recent Performance、AdminHome 的 Staff Ranking）。
// - 每一組兩條：選的那一方（value）用分數帶的填色（Theme.bandFill），數字寫在長條頂端；另一方（compare）是灰色
//   （Theme.compareBar），在後面。長條從 0 開始，85、70 兩條虛線，左邊寫 85、70（PLAN 第 2 節「數字照實畫」）。
// - bars：[{value, compare, label, sublabel, tooltip, name}]。value、compare 是 0～100 的分數，null 是沒有資料：
//   沒有長條，頂端寫「—」。label、sublabel 是長條下面的兩行（時間和日期，或名字和筆數）；tooltip 是滑鼠移上去
//   或鍵盤停在那一組時的說明；name 給輔助技術。
// - interactive：每一組是真的按鈕（Tab、Space／Enter、焦點框），按了發 activated(index)。
// - minGroupWidth：每一組最窄多寬。放不下時橫向捲動，所以每一筆都看得到（管理員的排名列出每一位員工）。
// - 沒有長出來的動畫（PLAN 第 2 節：動畫只表示狀態改變）。
Item {
    id: root

    property var bars: []
    property bool interactive: false
    property string frontLabel: ""          // 圖例開頭，例如「Customer score:」
    property string backLabel: ""           // 灰色長條是哪一方，例如「Staff score」
    property string summary: ""             // 整張圖的一句話（輔助技術）
    property real minGroupWidth: 0
    property int plotHeight: 260
    property int groupSpacing: 14
    property color separatorColor: Theme.card   // 前面的長條右邊 2 px 的縫：和卡片同色，兩條才分得開

    readonly property int axisWidth: 32
    readonly property int labelsHeight: 40   // 長條下面兩行字和上面的間距
    readonly property bool scrolls: list.contentWidth > list.width + 1

    signal activated(int index)

    implicitWidth: 480
    implicitHeight: plot.height + (scrolls ? 14 : 0) + 14 + legend.implicitHeight

    function yOf(score) {
        return root.plotHeight * (1 - score / 100)
    }

    // 圖例的一格：色塊＋字
    component Swatch: Row {
        id: swatch
        property color fill
        property string text
        spacing: 6

        Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            width: 10
            height: 10
            radius: 3
            color: swatch.fill
        }

        Text {
            text: swatch.text
            color: Theme.textSecondary
            font.pixelSize: Theme.fontLabel
        }
    }

    Item {
        id: plot
        width: parent.width
        height: root.plotHeight + root.labelsHeight

        // 85、70 的刻度和虛線（裝飾；數字在每一組的長條上和輔助技術的名稱裡）
        Repeater {
            model: [85, 70]

            Item {
                id: guide
                required property int modelData
                width: plot.width
                height: 1
                y: root.yOf(modelData)
                Accessible.ignored: true

                Text {
                    objectName: "guideLabel"
                    anchors.verticalCenter: parent.verticalCenter
                    text: guide.modelData
                    color: Theme.textFaint
                    font.pixelSize: Theme.fontLabel
                    font.features: { "tnum": 1 }
                }

                Row {       // 虛線：4 px 線、4 px 空。用 Rectangle 排：被 Flickable 裁切時和每一種繪圖後端都一樣
                    x: root.axisWidth
                    width: parent.width - root.axisWidth
                    height: 1
                    spacing: 4
                    clip: true

                    Repeater {
                        model: Math.max(0, Math.ceil((guide.width - root.axisWidth) / 8))
                        Rectangle { width: 4; height: 1; color: Theme.guide }
                    }
                }
            }
        }

        Rectangle {     // 0 的基線
            x: root.axisWidth
            y: root.plotHeight
            width: parent.width - root.axisWidth
            height: 1
            color: Theme.tileBorder
        }

        ListView {
            id: list
            objectName: "barList"
            x: root.axisWidth
            width: parent.width - root.axisWidth
            height: parent.height
            orientation: ListView.Horizontal
            spacing: root.groupSpacing
            clip: root.scrolls
            interactive: root.scrolls
            boundsBehavior: Flickable.StopAtBounds
            model: root.bars
            Accessible.role: Accessible.Chart
            Accessible.name: root.summary

            readonly property real groupWidth: count > 0
                ? Math.max(root.minGroupWidth, (width - spacing * (count - 1)) / count) : 0

            ScrollBar.horizontal: ScrollBar {
                parent: list.parent
                anchors.left: list.left
                anchors.right: list.right
                anchors.top: list.bottom
                anchors.topMargin: 4
                policy: root.scrolls ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
                visible: root.scrolls
            }

            delegate: AbstractButton {
                id: group
                objectName: "barGroup"
                required property var modelData
                required property int index

                readonly property bool hasValue: Theme.hasScore(modelData.value)
                readonly property bool hasCompare: Theme.hasScore(modelData.compare)
                readonly property real frontHeight: hasValue ? root.plotHeight * modelData.value / 100 : 0
                readonly property real frontWidth: Math.round(width * 0.6)
                readonly property bool showsTip: hover.hovered || visualFocus

                width: list.groupWidth
                height: list.height
                padding: 0
                hoverEnabled: true
                focusPolicy: root.interactive ? Qt.StrongFocus : Qt.NoFocus
                Accessible.role: root.interactive ? Accessible.Button : Accessible.StaticText
                Accessible.focusable: root.interactive
                Accessible.name: modelData.name !== undefined ? modelData.name : ""
                onClicked: {
                    if (root.interactive)
                        root.activated(index)
                }
                onShowsTipChanged: {
                    if (showsTip)
                        tip.target = group
                    else if (tip.target === group)
                        tip.target = null
                }
                Component.onDestruction: {
                    if (tip.target === group)
                        tip.target = null
                }

                HoverHandler {      // 滑鼠移上去：說明（tooltip）；能點的才換成手指游標
                    id: hover
                    cursorShape: root.interactive ? Qt.PointingHandCursor : Qt.ArrowCursor
                }

                contentItem: Item {
                    Rectangle {     // 另一方：灰色，在後面
                        objectName: "compareBar"
                        visible: group.hasCompare
                        x: Math.round(group.width * 0.36)
                        width: Math.round(group.width * 0.54)
                        height: group.hasCompare ? root.plotHeight * group.modelData.compare / 100 : 0
                        y: root.plotHeight - height
                        color: Theme.compareBar
                        topLeftRadius: 8
                        topRightRadius: 8
                    }

                    Rectangle {     // 兩條之間的縫（設計稿的 box-shadow: 2px 0 0）
                        visible: group.hasValue
                        width: group.frontWidth + 2
                        height: group.frontHeight
                        y: root.plotHeight - height
                        color: root.separatorColor
                        topLeftRadius: 8
                        topRightRadius: 8
                    }

                    Rectangle {     // 選的那一方：分數帶的填色
                        objectName: "valueBar"
                        visible: group.hasValue
                        width: group.frontWidth
                        height: group.frontHeight
                        y: root.plotHeight - height
                        color: Theme.bandFill(group.modelData.value)
                        topLeftRadius: 8
                        topRightRadius: 8
                    }

                    Text {          // 數字：長條夠高就寫在裡面的頂端（白字），太矮或沒有分數就寫在上面
                        objectName: "valueLabel"
                        readonly property bool inside: group.frontHeight >= height + 16
                        width: group.frontWidth
                        y: inside ? root.plotHeight - group.frontHeight + 8
                                  : root.plotHeight - group.frontHeight - height - 4
                        horizontalAlignment: Text.AlignHCenter
                        text: group.hasValue ? group.modelData.value.toFixed(1) : "—"
                        color: inside ? Theme.textOnDark : group.hasValue ? Theme.textPrimary : Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                        font.weight: Font.Bold
                        font.features: { "tnum": 1 }
                    }

                    Column {        // 長條下面：時間和日期，或名字和筆數
                        y: root.plotHeight + 8
                        width: parent.width

                        Text {
                            objectName: "barLabel"
                            width: parent.width
                            text: group.modelData.label !== undefined ? group.modelData.label : ""
                            color: Theme.iconNeutral
                            font.pixelSize: Theme.fontLabel
                            font.features: { "tnum": 1 }
                            elide: Text.ElideRight
                        }

                        Text {
                            objectName: "barSublabel"
                            width: parent.width
                            text: group.modelData.sublabel !== undefined ? group.modelData.sublabel : ""
                            color: Theme.textFaint
                            font.pixelSize: Theme.fontLabel
                            font.features: { "tnum": 1 }
                            elide: Text.ElideRight
                        }
                    }
                }

                background: Rectangle {     // 鍵盤焦點框（DS-30）
                    objectName: "barFocusRing"
                    visible: group.visualFocus
                    color: "transparent"
                    radius: 8
                    border.color: Theme.focusOnDark
                    border.width: 2
                }
            }
        }
    }

    // 圖例：選的那一方用哪三種顏色、灰色是哪一方（PLAN 第 2 節：顏色旁邊一定有字）
    Flow {
        id: legend
        objectName: "barLegend"
        y: plot.height + (root.scrolls ? 14 : 0) + 14
        width: parent.width
        spacing: 18

        Text {
            visible: text !== ""
            text: root.frontLabel
            color: Theme.textPrimary
            font.pixelSize: Theme.fontLabel
            font.weight: Font.DemiBold
        }
        Swatch { fill: Theme.successOnLight; text: qsTr("Good 85+") }
        Swatch { fill: Theme.warningOnLight; text: qsTr("Fair 70–84") }
        Swatch { fill: Theme.errorOnLight; text: qsTr("Needs improvement") }
        Swatch { visible: root.backLabel !== ""; fill: Theme.compareBar; text: root.backLabel }
    }

    // 一組的說明（服務 ID、時間、兩個分數）：滑鼠移上去或鍵盤停在那一組時出現在它上面
    ToolTip {
        id: tip
        objectName: "barTip"
        property Item target: null
        parent: target
        visible: target !== null && text !== ""
        text: target ? target.modelData.tooltip : ""
        x: target ? (target.width - width) / 2 : 0
        y: target ? root.plotHeight - target.frontHeight - height - 28 : 0
        delay: 0
        padding: 10

        contentItem: Text {
            text: tip.text
            color: Theme.toastText
            font.pixelSize: Theme.fontSmall
            lineHeight: 1.2
        }

        background: Rectangle {
            color: Theme.toastBg
            border.color: Theme.toastBorder
            radius: 8
        }
    }
}
