pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 RadioButton 的 indicator、contentItem、background，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic

// 設定頁的內容，服務人員端和管理員端共用（UX-46）。用詞照 DS-51：區塊「Window」「Reduce Motion」。
// - Window：Windowed／Full Screen。選取的狀態直接看主視窗是不是全螢幕，視窗模式一變就跟著更新；
//   同一組只會選一個，已經選的那個再按也不會被取消（ButtonGroup）。
// - Reduce Motion（DS-43、ENG-20）：Follow System／On／Off，就是 uiPrefs.motionMode 的 0／1／2。
//   UiPrefs 用 QSettings 存起來，重開 app 還在；外殼的 Binding 把它寫進 Theme，所有動畫時間立刻變成 0（或恢復）。
Rectangle {
    id: root

    property var window: null       // 外殼的視窗（頁面的 setWindow）
    property var prefs: null        // UiPrefs（context property uiPrefs）；程式結束時可能先變成 null

    readonly property bool fullScreen: window ? window.visibility === Window.FullScreen : true // 主視窗一打開就是全螢幕
    readonly property int motionMode: prefs ? prefs.motionMode : 0

    function setMotionMode(mode) {
        if (prefs)
            prefs.motionMode = mode
    }

    color: Theme.contentBg

    // 單選的選項：Basic RadioButton，顏色取自 Theme（淺色底）；鍵盤焦點框照 DS-30
    component Option: RadioButton {
        id: option

        focusPolicy: Qt.StrongFocus
        spacing: Theme.spaceS
        font.pixelSize: Theme.fontBody

        Accessible.role: Accessible.RadioButton
        Accessible.name: text

        indicator: Rectangle {
            x: option.leftPadding
            y: option.topPadding + (option.availableHeight - height) / 2
            implicitWidth: Theme.iconM
            implicitHeight: Theme.iconM
            radius: width / 2
            color: option.down ? Theme.tile : Theme.contentBg
            border.width: 2
            border.color: option.checked || option.hovered ? Theme.textPrimary : Theme.textSecondary

            Rectangle {
                anchors.centerIn: parent
                width: parent.width / 2
                height: width
                radius: width / 2
                visible: option.checked
                color: Theme.actionPrimary
            }
        }

        contentItem: Text {
            leftPadding: option.indicator.width + option.spacing
            verticalAlignment: Text.AlignVCenter
            text: option.text
            font: option.font
            color: Theme.textPrimary
            Accessible.ignored: true    // 選項自己就叫這個名字
        }

        background: Item {
            implicitHeight: Theme.hitMin

            Rectangle {     // 鍵盤焦點框（DS-30）：2 px、往外 2 px，淺色底上用 focusOnLight
                visible: option.visualFocus
                anchors.fill: parent
                anchors.margins: -4
                radius: Theme.radiusS + 4
                color: "transparent"
                border.color: Theme.focusOnDark
                border.width: 2
            }
        }
    }

    ButtonGroup { id: windowGroup }
    ButtonGroup { id: motionGroup }

    Column {
        id: content
        anchors.horizontalCenter: parent.horizontalCenter
        y: Theme.space3XL
        width: Math.min(640, root.width - 2 * Theme.space3XL)
        spacing: Theme.space2XL

        Text {
            objectName: "settingsTitle"
            text: qsTr("Settings")
            color: Theme.textPrimary
            font.pixelSize: Theme.fontHeadline
            font.bold: true
        }

        Column {
            objectName: "windowSection"
            width: parent.width
            spacing: Theme.spaceS
            Accessible.role: Accessible.Grouping
            Accessible.name: qsTr("Window")

            Text {
                text: qsTr("Window")
                color: Theme.textPrimary
                font.pixelSize: Theme.fontBody
                font.weight: Font.DemiBold
                Accessible.ignored: true    // 區塊自己就叫這個名字
            }

            Row {
                spacing: Theme.spaceXL

                Option {
                    objectName: "windowedOption"
                    text: qsTr("Windowed")
                    ButtonGroup.group: windowGroup
                    checked: !root.fullScreen
                    onClicked: {
                        if (root.window)
                            root.window.showNormal()
                    }
                }

                Option {
                    objectName: "fullScreenOption"
                    text: qsTr("Full Screen")
                    ButtonGroup.group: windowGroup
                    checked: root.fullScreen
                    onClicked: {
                        if (root.window)
                            root.window.showFullScreen()
                    }
                }
            }
        }

        Column {
            objectName: "motionSection"
            width: parent.width
            spacing: Theme.spaceS
            Accessible.role: Accessible.Grouping
            Accessible.name: qsTr("Reduce Motion")

            Text {
                text: qsTr("Reduce Motion")
                color: Theme.textPrimary
                font.pixelSize: Theme.fontBody
                font.weight: Font.DemiBold
                Accessible.ignored: true
            }

            Row {
                spacing: Theme.spaceXL

                Option {
                    objectName: "motionFollowSystem"
                    text: qsTr("Follow System")
                    ButtonGroup.group: motionGroup
                    checked: root.motionMode === 0
                    onClicked: root.setMotionMode(0)
                }

                Option {
                    objectName: "motionOn"
                    text: qsTr("On")
                    ButtonGroup.group: motionGroup
                    checked: root.motionMode === 1
                    onClicked: root.setMotionMode(1)
                }

                Option {
                    objectName: "motionOff"
                    text: qsTr("Off")
                    ButtonGroup.group: motionGroup
                    checked: root.motionMode === 2
                    onClicked: root.setMotionMode(2)
                }
            }

            Text {
                objectName: "motionHint"
                width: parent.width
                text: qsTr("Follow System uses the Reduce Motion setting on this Mac.")
                color: Theme.textSecondary
                font.pixelSize: Theme.fontCaption
                wrapMode: Text.Wrap
            }
        }
    }
}
