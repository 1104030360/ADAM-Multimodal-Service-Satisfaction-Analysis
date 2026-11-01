pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 ProgressBar 的 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic

// 上傳、下載的進度（DS-23）。done、total 是 bytes。
// - total > 0：有總量的條，右邊是「58.6 / 124.0 MB · 47%」。
// - total 不大於 0：不定進度條，旁邊一定有 label。減少動態時是靜止的軌道加文字。
// 數值直接設定，不做補間；更新頻率由 Python 端節流（每秒最多 4 次）。
Item {
    id: root

    property real done: 0
    property real total: 0
    property string label: ""
    property bool onDark: false     // toast 裡用：高 4、深色軌道，不顯示文字列（數字在 toast 的內文裡）

    readonly property bool determinate: total > 0
    readonly property string amountText: Theme.transferText(done, total)

    implicitWidth: 240
    implicitHeight: onDark ? bar.height : textRow.height + 6 + bar.height

    Item {
        id: textRow
        visible: !root.onDark
        width: parent.width
        height: visible ? Math.max(labelText.implicitHeight, amountLabel.implicitHeight) : 0

        Text {
            id: labelText
            objectName: "progressLabel"
            width: parent.width - (amountLabel.text !== "" ? amountLabel.implicitWidth + Theme.spaceM : 0)
            text: root.label
            elide: Text.ElideRight
            color: Theme.textPrimary
            font.pixelSize: Theme.fontCaption
            font.weight: Font.DemiBold
        }

        Text {
            id: amountLabel
            objectName: "progressAmount"
            anchors.right: parent.right
            text: root.determinate ? root.amountText : ""
            color: Theme.textSecondary
            font.pixelSize: Theme.fontCaption
            font.features: { "tnum": 1 }    // 等寬數字，數字變了寬度不變（DS-14）
        }
    }

    ProgressBar {
        id: bar
        objectName: "progressBar"
        y: root.onDark ? 0 : textRow.height + 6
        width: parent.width
        height: root.onDark ? 4 : 6
        from: 0
        to: root.determinate ? root.total : 1
        value: root.determinate ? Math.min(root.done, root.total) : 0
        indeterminate: !root.determinate

        Accessible.name: root.label

        background: Rectangle {
            implicitHeight: bar.height
            radius: height / 2
            color: root.onDark ? Theme.toastTrack : Theme.track
        }

        contentItem: Item {
            implicitHeight: bar.height
            clip: true      // 不定進度的色塊從左邊滑進、右邊滑出；clip 不會開 layer

            Rectangle {
                visible: !bar.indeterminate
                width: parent.width * bar.position
                height: parent.height
                radius: height / 2
                color: root.onDark ? Theme.toastFill : Theme.progressFill
            }

            // M-8：只動 x（XAnimator）。時間是 0（減少動態、測試）時不顯示，只剩靜止的軌道
            Rectangle {
                id: segment
                objectName: "indeterminateSegment"
                visible: bar.indeterminate && Theme.durIndeterminate > 0
                width: parent.width * 0.28
                height: parent.height
                radius: height / 2
                color: root.onDark ? Theme.toastFill : Theme.progressFill

                XAnimator on x {
                    from: -segment.width
                    to: segment.parent ? segment.parent.width : 0
                    duration: Theme.durIndeterminate
                    loops: Animation.Infinite
                    running: segment.visible
                }
            }
        }
    }
}
