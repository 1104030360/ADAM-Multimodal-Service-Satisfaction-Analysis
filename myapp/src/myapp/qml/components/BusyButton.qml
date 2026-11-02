pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic

// 忙碌按鈕（DS-24）。用法：onClicked 裡把 busy 設成 true，工作結束（成功或失敗）再設回 false。
// 1. busy 一變成 true 就擋住點擊（滑鼠、Space、Enter），外觀只換成按下的底色。
//    不設 enabled: false：停用會讓按鈕失去焦點；焦點留在按鈕上。
// 2. 過了 busyDelay 還沒結束，才換成 busyText 和旋轉圖示；一旦顯示，至少維持 busyMinShow，避免閃一下。
// 3. 寬度取兩種文字中較寬的那個，換字時按鈕不變寬、旁邊不位移。
Button {
    id: control

    property bool busy: false
    property string busyText: ""            // 進行式，以「…」結尾，例如 "Sending…"
    property string variant: "secondary"    // primary（淺色膠囊）、secondary、danger、chrome（登入頁的亮色按鈕）
    property bool large: false              // 頁面主要按鈕：高 50、字 18
    property url iconSource: ""             // 字前面的圖示（預先上色的 SVG，例如首頁的 New Recording）；忙碌時換成旋轉圖示
    property int busyDelay: Theme.busyDelay         // 測試可以改短
    property int busyMinShow: Theme.busyMinShow

    readonly property bool busyShown: internal.shown   // 外觀已經換成忙碌

    // 各種狀態的顏色（DS-12、DS-24）：按下、等待中（busy 但還沒顯示）用同一個底色。
    // 深色介面（new-UIUX PLAN 第 3 節）：主要按鈕是淺色膠囊配深色字，次要按鈕是深色底加框線
    readonly property var colors: {
        if (!enabled)
            return { bg: Theme.actionDisabledBg, border: Theme.actionDisabledBg, text: Theme.actionDisabledText,
                     spinner: "icons/loader-circle_textSecondary.svg" }
        const pressed = down || busy
        switch (variant) {
        case "primary":
            return { bg: pressed ? Theme.actionPrimaryPressed : hovered ? Theme.actionPrimaryHover : Theme.actionPrimary,
                     border: "transparent", text: Theme.actionPrimaryText, spinner: "icons/loader-circle_actionPrimaryText.svg" }
        case "chrome":
            return { bg: pressed ? Theme.chromeButtonPressed : hovered ? Theme.chromeButtonHover : Theme.chromeIcon,
                     border: "transparent", text: Theme.chromeOnLight, spinner: "icons/loader-circle_chromeOnLight.svg" }
        case "danger":
            if (internal.shown)
                return { bg: Theme.tile, border: Theme.cardBorder, text: Theme.textSecondary,
                         spinner: "icons/loader-circle_textSecondary.svg" }
            return { bg: pressed || hovered ? Theme.errorTint : Theme.actionSecondaryBg,
                     border: Theme.errorOnDark, text: Theme.errorOnDark, spinner: "icons/loader-circle_textSecondary.svg" }
        default:    // secondary
            if (internal.shown)
                return { bg: Theme.tile, border: Theme.cardBorder, text: Theme.textSecondary,
                         spinner: "icons/loader-circle_textSecondary.svg" }
            return { bg: pressed ? Theme.actionSecondaryPressed : hovered ? Theme.actionSecondaryHover : Theme.actionSecondaryBg,
                     border: Theme.actionSecondaryBorder, text: Theme.actionSecondaryText,
                     spinner: "icons/loader-circle_textSecondary.svg" }
        }
    }

    focusPolicy: Qt.StrongFocus     // 全部能用鍵盤操作（DS-05）
    horizontalPadding: large ? Theme.spaceXL : Theme.spaceM
    verticalPadding: 0
    font.pixelSize: large ? Theme.fontBodyLarge : Theme.fontBody
    font.weight: Font.DemiBold

    Accessible.name: internal.shown && busyText !== "" ? busyText : text
    Accessible.description: internal.shown ? qsTr("Busy") : ""     // DS-24、DS-51 的用詞

    QtObject {
        id: internal
        property bool shown: false
    }

    Timer {
        id: showTimer
        objectName: "busyShowTimer"
        interval: Math.max(0, control.busyDelay)
        onTriggered: {
            internal.shown = true
            minShowTimer.restart()
        }
    }

    Timer {
        id: minShowTimer
        objectName: "busyMinShowTimer"
        interval: Math.max(0, control.busyMinShow)
        onTriggered: {
            if (!control.busy)
                internal.shown = false
        }
    }

    onBusyChanged: {
        if (busy) {
            if (!internal.shown)
                showTimer.restart()
        } else {
            showTimer.stop()
            if (internal.shown && !minShowTimer.running)
                internal.shown = false
        }
    }
    Component.onCompleted: {
        if (busy)
            showTimer.restart()
    }

    // 忙碌時吃掉觸發按鈕的按鍵（按下和放開都要吃，否則放開時還是會觸發）
    function isTriggerKey(key) {
        return key === Qt.Key_Space || key === Qt.Key_Return || key === Qt.Key_Enter || key === Qt.Key_Select
    }
    Keys.onPressed: (event) => {
        if (busy && isTriggerKey(event.key))
            event.accepted = true
    }
    Keys.onReleased: (event) => {
        if (busy && isTriggerKey(event.key))
            event.accepted = true
    }

    // 忙碌時吃掉滑鼠的按下（蓋在按鈕上面；不忙碌時停用，點擊照常傳給按鈕）
    MouseArea {
        objectName: "busyClickBlocker"
        anchors.fill: parent
        enabled: control.busy
        acceptedButtons: Qt.AllButtons
    }

    TextMetrics { id: textMetrics; font: control.font; text: control.text }
    TextMetrics { id: busyMetrics; font: control.font; text: control.busyText !== "" ? control.busyText : control.text }

    contentItem: Item {
        // 圖示和旋轉圖示一樣大、在同一個位置：換成忙碌時按鈕不變寬
        readonly property real leading: control.iconSource.toString() !== "" ? Theme.iconS + 8 : 0
        implicitWidth: Math.ceil(Math.max(leading + textMetrics.advanceWidth, Theme.iconS + 8 + busyMetrics.advanceWidth))
        implicitHeight: label.implicitHeight

        Row {
            anchors.centerIn: parent
            spacing: 8

            Image {
                id: spinner
                objectName: "busySpinner"
                anchors.verticalCenter: parent.verticalCenter
                visible: internal.shown
                width: Theme.iconS
                height: Theme.iconS
                sourceSize: Qt.size(Theme.iconS, Theme.iconS)
                source: control.colors.spinner
                Accessible.ignored: true

                // M-7：一圈 1000 ms、等速。減少動態（durSpin 是 0）時是靜止的 loader-circle（DS-43）
                RotationAnimator on rotation {
                    from: 0
                    to: 360
                    duration: Theme.durSpin
                    loops: Animation.Infinite
                    running: spinner.visible && Theme.durSpin > 0
                }
            }

            Image {
                objectName: "buttonIcon"
                anchors.verticalCenter: parent.verticalCenter
                visible: !internal.shown && control.iconSource.toString() !== ""
                width: Theme.iconS
                height: Theme.iconS
                sourceSize: Qt.size(Theme.iconS, Theme.iconS)
                source: control.iconSource
                Accessible.ignored: true
            }

            Text {
                id: label
                objectName: "busyLabel"
                anchors.verticalCenter: parent.verticalCenter
                text: internal.shown && control.busyText !== "" ? control.busyText : control.text
                font: control.font
                color: control.colors.text
            }
        }
    }

    background: Rectangle {
        implicitWidth: 64
        implicitHeight: control.large ? Theme.controlHeightL : Theme.controlHeight
        radius: height / 2      // 膠囊（設計稿的按鈕都是高 44、圓角 22）
        color: control.colors.bg
        border.color: control.colors.border
        border.width: control.variant === "secondary" || control.variant === "danger" ? 1 : 0

        // M-1：hover、按下的顏色 100 ms
        Behavior on color {
            enabled: Theme.durFast > 0
            ColorAnimation { duration: Theme.durFast }
        }

        Rectangle {     // 鍵盤焦點框（DS-30）：2 px、往外 2 px；只在鍵盤造成焦點時出現
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
