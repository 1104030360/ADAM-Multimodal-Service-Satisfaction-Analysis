pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 background，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic

// 確認對話框（DS-28）：只用在會丟資料的時候，例如上傳中要離開。
// - title 用問句，body 說後果；兩個按鈕都用動詞，不用 OK、Cancel。
// - 安全的選項（cancelText）是預設：實心、放右邊、開啟時有焦點。Esc 和 Enter 都等於安全的選項。
// - 危險的選項（confirmText）用紅字紅框。
// - Dialog 自己就是 Accessible.Dialog，名稱是 title；關閉後焦點回到原本的控制項（Qt 會還原）。
Dialog {
    id: root

    property string body: ""
    property string confirmText: ""     // 危險的選項，例如 "Quit Anyway"
    property string cancelText: ""      // 安全的選項，例如 "Keep Uploading"

    signal confirmed()
    signal cancelled()

    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    z: Theme.zDialog
    anchors.centerIn: Overlay.overlay
    width: Math.min(460, Overlay.overlay ? Overlay.overlay.width - 2 * Theme.spaceXL : 460)
    padding: Theme.spaceXL
    header: null
    footer: null

    onAccepted: confirmed()
    onRejected: cancelled()     // Esc 也會走到這裡

    background: Rectangle {
        color: Theme.card
        radius: Theme.radiusCard
        border.color: Theme.cardBorder
        border.width: 1
    }

    Overlay.modal: Rectangle {
        color: Theme.scrim
        Behavior on opacity {
            enabled: Theme.durBase > 0
            OpacityAnimator { duration: Theme.durBase }
        }
    }

    // M-6：opacity＋scale 0.96→1，150 ms；關閉 opacity 100 ms
    enter: Transition {
        ParallelAnimation {
            OpacityAnimator { from: 0; to: 1; duration: Theme.durBase; easing.type: Theme.easeEnter }
            ScaleAnimator { from: 0.96; to: 1; duration: Theme.durBase; easing.type: Theme.easeEnter }
        }
    }
    exit: Transition {
        OpacityAnimator { from: 1; to: 0; duration: Theme.durFast; easing.type: Theme.easeExit }
    }

    contentItem: Column {
        spacing: 12

        // Enter／Return 等於安全的選項；Space 照常按下有焦點的按鈕
        Keys.onReturnPressed: root.reject()
        Keys.onEnterPressed: root.reject()

        Text {
            objectName: "dialogTitle"
            width: parent.width
            text: root.title
            color: Theme.textPrimary
            font.pixelSize: Theme.fontTitleSmall
            font.weight: Font.Bold
            wrapMode: Text.Wrap
        }

        Text {
            objectName: "dialogBody"
            width: parent.width
            visible: text !== ""
            text: root.body
            color: Theme.textPrimary
            font.pixelSize: Theme.fontBody
            wrapMode: Text.Wrap
        }

        Item {
            width: parent.width
            height: 8 + buttons.implicitHeight

            Row {
                id: buttons
                anchors.right: parent.right
                y: 8
                spacing: 12

                BusyButton {
                    objectName: "confirmButton"
                    variant: "danger"
                    text: root.confirmText
                    onClicked: root.accept()
                }

                BusyButton {
                    objectName: "cancelButton"
                    variant: "primary"
                    focus: true
                    text: root.cancelText
                    onClicked: root.reject()
                }
            }
        }
    }
}
