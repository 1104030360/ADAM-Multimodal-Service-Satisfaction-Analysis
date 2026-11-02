pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic

// 側邊欄的按鈕（DS-32、DS-30、UX-41、UX-44；新版介面 PLAN 第 4 節），兩個外殼共用。是真的 Button：Tab 停得到，
// Space、Return、Enter 都會按下；VoiceOver 唸「Analysis, button」，目前頁面再加「Current page」。
// - 版面：44 px 的圖示方塊，字（11 px）在方塊下面；整個按鈕（寬 80）都可以點。
// - 方塊的底色：目前頁面 → 按下 → hover → 一般。hover、按下的顏色變化 100 ms（M-1）；
//   目前頁面立刻換，不做動畫（DS-29），所以畫在另一層，不經過 Behavior。
// - 鍵盤焦點框：2 px、往外 2 px，框住圖示方塊；只在鍵盤造成焦點時出現，不做動畫（DS-30）。
// - 圖示：icons/nav/ 裡預先上色的 Lucide SVG（<名稱>_<Theme token>.svg）。一般是 iconNeutral，
//   目前頁面是 navItemCurrentText；不在執行時染色（不開 layer、不用 MultiEffect）。
Button {
    id: control

    property string iconName: ""        // Lucide 圖示的名稱，例如 "house"
    property bool current: false        // 這一頁正在內容區（外殼依 Loader 的頁面決定）

    readonly property color backgroundColor: down ? Theme.navItemPressed
                                           : hovered ? Theme.navItemHover : Theme.navItem
    readonly property color foregroundColor: current ? Theme.textPrimary : Theme.textFaint

    topPadding: 6
    bottomPadding: 6
    leftPadding: 0
    rightPadding: 0
    spacing: 6
    hoverEnabled: true                  // 以前的 MouseArea 沒開，hover 的顏色從來不會出現（UX-44）
    focusPolicy: Qt.StrongFocus         // 全部能用鍵盤操作（DS-05）
    font.pixelSize: Theme.fontNavLabel
    font.weight: current ? Font.DemiBold : Font.Normal

    Accessible.role: Accessible.Button
    Accessible.name: text
    Accessible.description: current ? qsTr("Current page") : ""

    // Space 由 Button 自己處理；Return、Enter 也要能按（UX-41）
    Keys.onReturnPressed: control.click()
    Keys.onEnterPressed: control.click()

    // 和以前的側邊欄一樣，滑鼠移上去變成手指（UX-44）；只換游標，不接點擊
    HoverHandler {
        cursorShape: Qt.PointingHandCursor
    }

    contentItem: Item {
        implicitWidth: 80
        implicitHeight: Theme.navTile + control.spacing + label.implicitHeight

        Rectangle {
            id: tile
            objectName: "navBackground"
            anchors.horizontalCenter: parent.horizontalCenter
            width: Theme.navTile
            height: Theme.navTile
            radius: Theme.radiusNav
            color: control.backgroundColor
            border.color: Theme.navItemBorder
            border.width: 1

            // M-1：hover、按下的顏色 100 ms；減少動態（durFast 是 0）時直接換
            Behavior on color {
                enabled: Theme.durFast > 0
                ColorAnimation { duration: Theme.durFast }
            }

            Rectangle {     // 目前頁面：亮底（DS-32），和主要按鈕同一組顏色，立刻出現
                objectName: "currentBackground"
                anchors.fill: parent
                radius: parent.radius
                visible: control.current
                color: Theme.navItemCurrent
            }

            Image {
                objectName: "navIcon"
                anchors.centerIn: parent
                width: Theme.iconNav
                height: Theme.iconNav
                sourceSize: Qt.size(Theme.iconNav, Theme.iconNav)
                source: control.iconName === "" ? ""
                    : "icons/nav/%1_%2.svg".arg(control.iconName)
                                           .arg(control.current ? "navItemCurrentText" : "iconNeutral")
                Accessible.ignored: true
            }

            Rectangle {     // 鍵盤焦點框（DS-30）
                objectName: "focusRing"
                visible: control.visualFocus
                anchors.fill: parent
                anchors.margins: -4
                radius: parent.radius + 4
                color: "transparent"
                border.color: Theme.focusOnDark
                border.width: 2
            }
        }

        Text {
            id: label
            objectName: "navLabel"
            anchors.top: tile.bottom
            anchors.topMargin: control.spacing
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            text: control.text
            font: control.font
            color: control.foregroundColor
            elide: Text.ElideRight
            Accessible.ignored: true    // 按鈕自己就叫這個名字，不再唸一次
        }
    }

    background: Item {
        implicitWidth: 80
    }
}
