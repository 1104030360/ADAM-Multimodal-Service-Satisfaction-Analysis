pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 RadioButton 的 indicator、contentItem、background，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic

// 設定頁的內容，服務人員端和管理員端共用（UX-46；新版介面 PLAN 6-7，設計稿 StaffSettings、AdminSettings）。
// 頁面標題和「改了立刻生效」的說明在外殼的頁首，這裡只有一張卡片、兩組選項；選項是整塊可以點的方塊（52 高）。
// 用詞照 DS-51：區塊「Window」「Reduce Motion」。
// - Window：Windowed／Full Screen。選取的狀態直接看主視窗是不是全螢幕，視窗模式一變就跟著更新；
//   同一組只會選一個，已經選的那個再按也不會被取消（ButtonGroup）。
// - Reduce Motion（DS-43、ENG-20）：Follow System／On／Off，就是 uiPrefs.motionMode 的 0／1／2。
//   UiPrefs 用 QSettings 存起來，重開 app 還在；外殼的 Binding 把它寫進 Theme，所有動畫時間立刻變成 0（或恢復）。
Item {
    id: root

    property var window: null       // 外殼的視窗（頁面的 setWindow）
    property var prefs: null        // UiPrefs（context property uiPrefs）；程式結束時可能先變成 null

    readonly property bool fullScreen: window ? window.visibility === Window.FullScreen : true // 主視窗一打開就是全螢幕
    readonly property int motionMode: prefs ? prefs.motionMode : 0

    function setMotionMode(mode) {
        if (prefs)
            prefs.motionMode = mode
    }

    // 一個選項：整塊方塊都可以點，左邊是單選鈕的圓圈。選到的方塊底色亮一階、框線變亮，圓圈裡有點（不只靠顏色）
    component Option: RadioButton {
        id: option

        implicitHeight: 52
        leftPadding: Theme.spaceM
        rightPadding: Theme.spaceM
        spacing: 12
        focusPolicy: Qt.StrongFocus
        font.pixelSize: Theme.fontInput
        font.weight: Font.DemiBold

        Accessible.role: Accessible.RadioButton
        Accessible.name: text
        Keys.onReturnPressed: click()
        Keys.onEnterPressed: click()

        indicator: Rectangle {
            x: option.leftPadding
            y: (option.height - height) / 2
            implicitWidth: 18
            implicitHeight: 18
            radius: width / 2
            color: Theme.field
            border.width: 2
            border.color: option.checked ? Theme.actionPrimary : Theme.fieldBorder     // 沒選的圓圈和方塊 3:1 以上

            Rectangle {
                anchors.centerIn: parent
                width: 8
                height: 8
                radius: 4
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
            elide: Text.ElideRight
            Accessible.ignored: true    // 選項自己就叫這個名字
        }

        background: Rectangle {
            radius: 12
            color: option.checked ? Theme.tileSelected : Theme.tile
            border.width: 1
            border.color: option.checked ? Theme.textFaint : option.hovered ? Theme.fieldBorder : Theme.tileBorder

            Rectangle {     // 鍵盤焦點框（DS-30）：2 px、往外 2 px。測試用 background.children[0] 找它
                visible: option.visualFocus
                anchors.fill: parent
                anchors.margins: -4
                radius: parent.radius + 4
                color: "transparent"
                border.color: Theme.focusOnDark
                border.width: 2
            }
        }
    }

    // 一組選項：標題、一排方塊（平分寬度）、下面可以有一句說明
    component Group: Column {
        id: group

        property string title: ""
        property string hint: ""
        default property alias options: optionRow.data

        width: parent ? parent.width : 0
        topPadding: Theme.spaceL
        bottomPadding: Theme.spaceL
        spacing: 12
        Accessible.role: Accessible.Grouping
        Accessible.name: title

        Text {
            bottomPadding: 4
            text: group.title
            color: Theme.textTitle
            font.pixelSize: Theme.fontBodyLarge
            font.weight: Font.Bold
            Accessible.ignored: true    // 區塊自己就叫這個名字
        }

        Row {
            id: optionRow
            width: group.width
            spacing: Theme.spaceS
        }

        Text {
            objectName: "groupHint"
            width: group.width
            visible: text !== ""
            text: group.hint
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
            lineHeight: 18
            lineHeightMode: Text.FixedHeight
            wrapMode: Text.Wrap
        }
    }

    ButtonGroup { id: windowGroup }
    ButtonGroup { id: motionGroup }

    Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: card.height
        boundsBehavior: Flickable.StopAtBounds
        clip: true

        Card {
            id: card
            objectName: "settingsCard"
            width: Math.min(760, root.width)
            topPadding: 8
            bottomPadding: 8

            Column {
                width: parent.width

                Group {
                    id: windowSection
                    objectName: "windowSection"
                    title: qsTr("Window")

                    Option {
                        objectName: "windowedOption"
                        width: (windowSection.width - Theme.spaceS) / 2
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
                        width: (windowSection.width - Theme.spaceS) / 2
                        text: qsTr("Full Screen")
                        ButtonGroup.group: windowGroup
                        checked: root.fullScreen
                        onClicked: {
                            if (root.window)
                                root.window.showFullScreen()
                        }
                    }
                }

                Rectangle {     // 兩組之間的分隔線
                    width: parent.width
                    height: 1
                    color: Theme.cardBorder
                }

                Group {
                    id: motionSection
                    objectName: "motionSection"
                    title: qsTr("Reduce Motion")
                    hint: qsTr("Follow System uses the Reduce Motion setting on this Mac. On turns off page fades and other animations.")

                    Option {
                        objectName: "motionFollowSystem"
                        width: (motionSection.width - 2 * Theme.spaceS) / 3
                        text: qsTr("Follow System")
                        ButtonGroup.group: motionGroup
                        checked: root.motionMode === 0
                        onClicked: root.setMotionMode(0)
                    }

                    Option {
                        objectName: "motionOn"
                        width: (motionSection.width - 2 * Theme.spaceS) / 3
                        text: qsTr("On")
                        ButtonGroup.group: motionGroup
                        checked: root.motionMode === 1
                        onClicked: root.setMotionMode(1)
                    }

                    Option {
                        objectName: "motionOff"
                        width: (motionSection.width - 2 * Theme.spaceS) / 3
                        text: qsTr("Off")
                        ButtonGroup.group: motionGroup
                        checked: root.motionMode === 2
                        onClicked: root.setMotionMode(2)
                    }
                }
            }
        }
    }
}
