pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic

// 側邊欄的按鈕（DS-32、DS-30、UX-41、UX-44），兩個外殼共用。是真的 Button：Tab 停得到，
// Space、Return、Enter 都會按下；VoiceOver 唸「Analysis, button」，目前頁面再加「Current page」。
// - 底色的優先順序：目前頁面 → 按下 → hover → 一般。hover、按下的顏色變化 100 ms（M-1）；
//   目前頁面立刻換，不做動畫（DS-29），所以畫在另一層，不經過 Behavior。
// - 鍵盤焦點框：2 px、往外 2 px，深色底上用 focusOnDark；只在鍵盤造成焦點時出現，不做動畫（DS-30）。
// - 圖示：icons/nav/ 裡預先上色的 Lucide SVG（<名稱>_<Theme token>.svg）。一般是 chromeIcon，
//   目前頁面是 navItemCurrentText；不在執行時染色（不開 layer、不用 MultiEffect）。
Button {
    id: control

    property string iconName: ""        // Lucide 圖示的名稱，例如 "house"
    property bool current: false        // 這一頁正在內容區（外殼依 Loader 的頁面決定）

    readonly property color backgroundColor: down ? Theme.navItemPressed
                                           : hovered ? Theme.navItemHover : Theme.navItem
    readonly property color foregroundColor: current ? Theme.navItemCurrentText : Theme.textOnDark

    implicitHeight: Theme.controlHeightL
    padding: Theme.spaceS
    spacing: Theme.spaceS
    hoverEnabled: true                  // 以前的 MouseArea 沒開，hover 的顏色從來不會出現（UX-44）
    focusPolicy: Qt.StrongFocus         // 全部能用鍵盤操作（DS-05）
    font.pixelSize: Theme.fontTitleSmall
    font.bold: true

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

    contentItem: Row {
        spacing: control.spacing

        Image {
            objectName: "navIcon"
            anchors.verticalCenter: parent.verticalCenter
            width: Theme.iconNav
            height: Theme.iconNav
            sourceSize: Qt.size(Theme.iconNav, Theme.iconNav)
            source: control.iconName === "" ? ""
                : "icons/nav/%1_%2.svg".arg(control.iconName)
                                       .arg(control.current ? "navItemCurrentText" : "chromeIcon")
            Accessible.ignored: true
        }

        Text {
            objectName: "navLabel"
            anchors.verticalCenter: parent.verticalCenter
            width: control.availableWidth - Theme.iconNav - control.spacing
            text: control.text
            font: control.font
            color: control.foregroundColor
            elide: Text.ElideRight
            Accessible.ignored: true    // 按鈕自己就叫這個名字，不再唸一次
        }
    }

    background: Rectangle {
        objectName: "navBackground"
        implicitWidth: 200
        implicitHeight: Theme.controlHeightL
        radius: Theme.radiusM
        color: control.backgroundColor

        // M-1：hover、按下的顏色 100 ms；減少動態（durFast 是 0）時直接換
        Behavior on color {
            enabled: Theme.durFast > 0
            ColorAnimation { duration: Theme.durFast }
        }

        Rectangle {     // 目前頁面：亮底（DS-32），和登入按鈕同一組顏色，立刻出現
            objectName: "currentBackground"
            anchors.fill: parent
            radius: parent.radius
            visible: control.current
            color: Theme.navItemCurrent
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
}
