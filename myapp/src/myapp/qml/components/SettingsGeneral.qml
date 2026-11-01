import QtQuick
import QtQuick.Controls.Basic

// 設定頁的 General 區塊（new-feature PLAN 13.1～13.3；新版介面 PLAN 6-7；T39）：視窗、動態效果、播放、通知。
// 每一組都是一排整塊可以點的方塊；同一組只會選一個，已經選的那個再按也不會被取消（ButtonGroup）。改了立刻生效。
// - Window：Windowed／Full Screen。選取的狀態直接看主視窗是不是全螢幕，視窗模式一變就跟著更新。
//   uiPrefs 記住每個帳號上次的模式（restoreWindow），下次登入照它打開；這裡只切換視窗。
// - Reduce Motion（DS-43、ENG-20）：Follow System／On／Off，就是 uiPrefs.motionMode 的 0／1／2。這台電腦共用，不分帳號。
//   外殼的 Binding 把它寫進 Theme，所有動畫時間立刻變成 0（或恢復）。
// - Start Playback Muted（S1）：每次打開服務明細時先靜音。音量在播放器上調、自動記住，這裡不放（PLAN 13.1：不加「是否記住」）。
// - Show Analysis Completion Alerts、Bounce Dock Icon（S2）：只有服務人員端有（showNotifications），
//   管理員端沒有分析完成的通知，不顯示用不到的選項（PLAN 13.1）。失敗的通知不受影響。
// 播放、通知的值存在每個帳號自己的偏好（uiPrefs，account_id 當 key）。
SettingsSection {
    id: general

    property var window: null       // 外殼的視窗（頁面的 setWindow）
    property var prefs: null        // UiPrefs（context property uiPrefs）；程式結束時可能先變成 null
    property bool showNotifications: false  // 服務人員端：有分析完成的通知、Dock 的提醒

    readonly property bool fullScreen: window ? window.visibility === Window.FullScreen : true // 主視窗一打開就是全螢幕
    readonly property int motionMode: prefs ? prefs.motionMode : 0
    readonly property bool startMuted: prefs ? prefs.startPlaybackMuted : false
    readonly property bool completionAlerts: prefs ? prefs.showCompletionAlerts : true
    readonly property bool dockBounce: prefs ? prefs.bounceDockIcon : true

    function setMotionMode(mode) {
        if (prefs)
            prefs.motionMode = mode
    }

    // name 是 uiPrefs 的屬性：startPlaybackMuted、showCompletionAlerts、bounceDockIcon
    function setPref(name, value) {
        if (prefs)
            prefs[name] = value
    }

    title: qsTr("General")

    ButtonGroup { id: windowGroup }
    ButtonGroup { id: motionGroup }
    ButtonGroup { id: mutedGroup }
    ButtonGroup { id: alertsGroup }
    ButtonGroup { id: dockGroup }

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

    SettingsDivider {}

    // 播放（S1）：只影響服務明細的播放，不影響錄影和麥克風
    SettingsGroup {
        id: mutedSection
        objectName: "startMutedSection"
        title: qsTr("Start Playback Muted")
        hint: qsTr("Each service opens with the sound off. Unmute in the player to hear it at your last volume.")

        SettingsOption {
            objectName: "startMutedOn"
            width: (mutedSection.width - Theme.spaceS) / 2
            text: qsTr("On")
            ButtonGroup.group: mutedGroup
            checked: general.startMuted
            onClicked: general.setPref("startPlaybackMuted", true)
        }

        SettingsOption {
            objectName: "startMutedOff"
            width: (mutedSection.width - Theme.spaceS) / 2
            text: qsTr("Off")
            ButtonGroup.group: mutedGroup
            checked: !general.startMuted
            onClicked: general.setPref("startPlaybackMuted", false)
        }
    }

    // 通知（S2）：只控制分析完成的 toast、Dock 圖示跳動；分析、清單的更新、Dock 的徽章、失敗的通知都照舊
    SettingsDivider { visible: general.showNotifications }

    SettingsGroup {
        id: alertsSection
        objectName: "completionAlertsSection"
        visible: general.showNotifications
        title: qsTr("Show Analysis Completion Alerts")
        hint: qsTr("Results still appear on the Analysis page. Failed analyses and upload errors are always shown.")

        SettingsOption {
            objectName: "completionAlertsOn"
            width: (alertsSection.width - Theme.spaceS) / 2
            text: qsTr("On")
            ButtonGroup.group: alertsGroup
            checked: general.completionAlerts
            onClicked: general.setPref("showCompletionAlerts", true)
        }

        SettingsOption {
            objectName: "completionAlertsOff"
            width: (alertsSection.width - Theme.spaceS) / 2
            text: qsTr("Off")
            ButtonGroup.group: alertsGroup
            checked: !general.completionAlerts
            onClicked: general.setPref("showCompletionAlerts", false)
        }
    }

    SettingsDivider { visible: general.showNotifications }

    SettingsGroup {
        id: dockSection
        objectName: "dockBounceSection"
        visible: general.showNotifications
        title: qsTr("Bounce Dock Icon")
        hint: qsTr("When ADAM is in the background, the Dock icon bounces when an analysis finishes or fails. The number on the icon still updates.")

        SettingsOption {
            objectName: "dockBounceOn"
            width: (dockSection.width - Theme.spaceS) / 2
            text: qsTr("On")
            ButtonGroup.group: dockGroup
            checked: general.dockBounce
            onClicked: general.setPref("bounceDockIcon", true)
        }

        SettingsOption {
            objectName: "dockBounceOff"
            width: (dockSection.width - Theme.spaceS) / 2
            text: qsTr("Off")
            ButtonGroup.group: dockGroup
            checked: !general.dockBounce
            onClicked: general.setPref("bounceDockIcon", false)
        }
    }
}
