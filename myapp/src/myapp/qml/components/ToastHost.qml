pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 Popup 的 background，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic

// 通知堆疊（DS-21、ENG-15）。兩個外殼各放一個，位置由外殼決定（內容區右下角，距邊 24）：
//     ToastHost { center: notifier; x: window.width - width - 24; y: window.height - height - 24 }
// - 是 Popup，才能蓋過對話框（DS-16）；不搶焦點、不自動關閉。
// - 一直開著：沒有通知時高度是 0，不擋點擊。不要把 visible 綁到數量：
//   關著的 Popup 裡，ListView 不會建立 delegate，計時器也不會開始（ENG-15 的坑）。
// - 清單由下往上排（BottomToTop）：模型的第 0 列在最下面。Notifier 把新的一則插在第 0 列，
//   新的就在最下面、舊的往上推（DS-21）。
Popup {
    id: host

    property var center: null       // Notifier（context property notifier）；測試可以給替身
    readonly property alias count: list.count
    readonly property int overflowCount: center ? center.overflowCount : 0
    // 給內容區最下面的清單設 bottomMargin：堆疊的高度加上和視窗下緣的 24
    readonly property int occupiedHeight: height > 0 ? height + Theme.spaceXL : 0

    modal: false
    focus: false
    closePolicy: Popup.NoAutoClose
    padding: 0
    background: null
    z: Theme.zToast
    width: Theme.toastWidth
    height: stack.implicitHeight

    Component.onCompleted: open()

    contentItem: Column {
        id: stack
        spacing: 8

        Accessible.role: Accessible.Grouping
        Accessible.name: qsTr("Notifications")

        // 3 則都是常駐時，新的排隊（DS-21）。做成和 toast 同色的小標籤，蓋在影片上也看得清楚
        Rectangle {
            id: overflowBadge
            objectName: "toastOverflow"
            anchors.right: parent.right
            visible: host.overflowCount > 0
            width: overflowText.implicitWidth + 2 * 12
            height: 28
            radius: height / 2
            color: Theme.toastBg
            border.color: Theme.toastBorder
            border.width: 1

            Text {
                id: overflowText
                objectName: "toastOverflowText"
                anchors.centerIn: parent
                text: host.overflowCount === 1 ? qsTr("1 more notification")
                                               : qsTr("%1 more notifications").arg(host.overflowCount)
                color: Theme.toastText
                font.pixelSize: Theme.fontCaption
            }
        }

        ListView {
            id: list
            objectName: "toastList"
            width: parent.width
            height: contentHeight
            interactive: false
            spacing: 8
            verticalLayoutDirection: ListView.BottomToTop
            model: host.center ? host.center.model : null

            delegate: Toast {
                onDismissRequested: (toastId) => host.dismiss(toastId)
                onActionRequested: (toastId) => host.trigger(toastId)
            }

            // M-2 進場：opacity 0→1＋往上 8 px
            add: Transition {
                id: addTransition
                ParallelAnimation {
                    OpacityAnimator { from: 0; to: 1; duration: Theme.durEnter; easing.type: Theme.easeEnter }
                    YAnimator {
                        from: addTransition.ViewTransition.destination.y + 8
                        to: addTransition.ViewTransition.destination.y
                        duration: Theme.durEnter
                        easing.type: Theme.easeEnter
                    }
                }
            }
            // M-3 退場：opacity 1→0
            remove: Transition {
                OpacityAnimator { to: 0; duration: Theme.durBase; easing.type: Theme.easeExit }
            }
            // M-4 其餘的補位
            displaced: Transition {
                YAnimator { duration: Theme.durBase; easing.type: Theme.easeMove }
            }
        }
    }

    function dismiss(toastId) {
        if (center)
            center.dismiss(toastId)
    }

    function trigger(toastId) {
        if (center)
            center.trigger(toastId)
    }
}
