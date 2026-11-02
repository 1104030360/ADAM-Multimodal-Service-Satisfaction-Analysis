pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import "../components"

// 一項目標的進度：四個步驟（設計稿「Coaching, redesigned」，T47）。
// 管理員 Goal set → Mia replied → Follow-up → Your review；服務人員 Goal set → You replied → Later service → Review。
// 做完的是亮底打勾，進行中的第一個是藍框（字也粗），還沒到的是灰框加編號。調整目標之後從「回應」重新算。
// 步驟和狀態都在 Python（coaching.progress_steps）；顏色只是輔助，螢幕閱讀器唸「Mia replied, current step」。
RowLayout {
    id: root

    property var steps: []

    spacing: 0
    Accessible.role: Accessible.List
    Accessible.name: qsTr("Progress")

    Repeater {
        model: root.steps
        delegate: ColumnLayout {
            id: step

            required property var modelData
            required property int index
            readonly property bool done: modelData.state === "done"
            readonly property bool current: modelData.state === "current"
            readonly property bool last: index === root.steps.length - 1

            objectName: "progressStep"
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            Layout.alignment: Qt.AlignTop
            spacing: 8
            Accessible.role: Accessible.ListItem
            Accessible.name: done ? qsTr("%1, done").arg(modelData.label)
                           : current ? qsTr("%1, current step").arg(modelData.label)
                           : qsTr("%1, not yet").arg(modelData.label)

            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Rectangle {
                    objectName: "stepDot"
                    implicitWidth: 28
                    implicitHeight: 28
                    radius: 14
                    color: step.done ? Theme.actionPrimary : "transparent"
                    border.width: 2
                    border.color: step.done ? Theme.actionPrimary : step.current ? Theme.infoOnDark
                                                                                 : Theme.actionSecondaryBorder
                    Accessible.ignored: true

                    Image {
                        anchors.centerIn: parent
                        visible: step.done
                        width: 14
                        height: 14
                        sourceSize: Qt.size(14, 14)
                        source: "../components/icons/check_actionPrimaryText.svg"
                    }
                    Text {
                        anchors.centerIn: parent
                        visible: !step.done
                        text: step.index + 1
                        color: step.current ? Theme.infoOnDark : Theme.textFaint
                        font.pixelSize: Theme.fontSmall
                        font.weight: Font.Bold
                    }
                }
                Rectangle {     // 到下一步的線：做完的那一段亮一點
                    Layout.fillWidth: true
                    Layout.rightMargin: 8
                    implicitHeight: 2
                    color: step.last ? "transparent" : step.done ? Theme.stepLineDone : Theme.tileBorder
                }
            }

            Text {
                objectName: "stepLabel"
                Layout.fillWidth: true
                Layout.rightMargin: 8
                text: step.modelData.label
                color: step.done || step.current ? Theme.textPrimary : Theme.textFaint
                font.pixelSize: Theme.fontSmall
                font.weight: step.current ? Font.Bold : Font.Medium
                wrapMode: Text.Wrap
                Accessible.ignored: true
            }
        }
    }
}
