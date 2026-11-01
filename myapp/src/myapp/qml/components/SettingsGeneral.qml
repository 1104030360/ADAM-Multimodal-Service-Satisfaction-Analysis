import QtQuick
import QtQuick.Controls.Basic

// 設定頁的 General 區塊（new-feature PLAN 13.1、13.2；新版介面 PLAN 6-7）：視窗、動態效果，之後加播放、通知（S1、S2）。
// 用詞照 DS-51：組名「Window」「Reduce Motion」。
// - Window：Windowed／Full Screen。選取的狀態直接看主視窗是不是全螢幕，視窗模式一變就跟著更新；
//   同一組只會選一個，已經選的那個再按也不會被取消（ButtonGroup）。
// - Reduce Motion（DS-43、ENG-20）：Follow System／On／Off，就是 uiPrefs.motionMode 的 0／1／2。
//   UiPrefs 用 QSettings 存起來，重開 app 還在；外殼的 Binding 把它寫進 Theme，所有動畫時間立刻變成 0（或恢復）。
SettingsSection {
    id: general

    property var window: null       // 外殼的視窗（頁面的 setWindow）
    property var prefs: null        // UiPrefs（context property uiPrefs）；程式結束時可能先變成 null

    readonly property bool fullScreen: window ? window.visibility === Window.FullScreen : true // 主視窗一打開就是全螢幕
    readonly property int motionMode: prefs ? prefs.motionMode : 0

    function setMotionMode(mode) {
        if (prefs)
            prefs.motionMode = mode
    }

    title: qsTr("General")

    ButtonGroup { id: windowGroup }
    ButtonGroup { id: motionGroup }

    SettingsGroup {
        id: windowSection
        objectName: "windowSection"
        title: qsTr("Window")

        SettingsOption {
            objectName: "windowedOption"
            width: (windowSection.width - Theme.spaceS) / 2
            text: qsTr("Windowed")
            ButtonGroup.group: windowGroup
            checked: !general.fullScreen
            onClicked: {
                if (general.window)
                    general.window.showNormal()
            }
        }

        SettingsOption {
            objectName: "fullScreenOption"
            width: (windowSection.width - Theme.spaceS) / 2
            text: qsTr("Full Screen")
            ButtonGroup.group: windowGroup
            checked: general.fullScreen
            onClicked: {
                if (general.window)
                    general.window.showFullScreen()
            }
        }
    }

    SettingsDivider {}

    SettingsGroup {
        id: motionSection
        objectName: "motionSection"
        title: qsTr("Reduce Motion")
        hint: qsTr("Follow System uses the Reduce Motion setting on this Mac. On turns off page fades and other animations.")

        SettingsOption {
            objectName: "motionFollowSystem"
            width: (motionSection.width - 2 * Theme.spaceS) / 3
            text: qsTr("Follow System")
            ButtonGroup.group: motionGroup
            checked: general.motionMode === 0
            onClicked: general.setMotionMode(0)
        }

        SettingsOption {
            objectName: "motionOn"
            width: (motionSection.width - 2 * Theme.spaceS) / 3
            text: qsTr("On")
            ButtonGroup.group: motionGroup
            checked: general.motionMode === 1
            onClicked: general.setMotionMode(1)
        }

        SettingsOption {
            objectName: "motionOff"
            width: (motionSection.width - 2 * Theme.spaceS) / 3
            text: qsTr("Off")
            ButtonGroup.group: motionGroup
            checked: general.motionMode === 2
            onClicked: general.setMotionMode(2)
        }
    }
}
