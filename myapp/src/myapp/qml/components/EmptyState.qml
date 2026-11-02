pragma ComponentBehavior: Bound
import QtQuick

// 空狀態（DS-26）。只在載入成功、真的沒有資料時顯示；載入中和失敗不能用它。
// 結構：圖示 40 px（裝飾）、標題 18 px、一行說明 14 px、最多一個次要按鈕；置中，最寬 360。
// 出現時用 Polite 唸一次標題。
Item {
    id: root

    property string title: ""
    property string description: ""
    property string actionText: ""          // 空字串＝沒有按鈕
    property url iconSource: Qt.resolvedUrl("icons/inbox_textDisabled.svg")   // 換圖示時請傳 Qt.resolvedUrl(…)
    property var announced: []              // 唸過的內容，測試和除錯用

    signal actionTriggered()

    implicitWidth: 360
    implicitHeight: column.implicitHeight

    Column {
        id: column
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(root.width, 360)
        spacing: Theme.spaceS

        Image {
            anchors.horizontalCenter: parent.horizontalCenter
            width: 40
            height: 40
            sourceSize: Qt.size(40, 40)
            source: root.iconSource
            Accessible.ignored: true
        }

        Text {
            objectName: "emptyTitle"
            width: parent.width
            text: root.title
            color: Theme.textPrimary
            font.pixelSize: Theme.fontBodyLarge
            font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
        }

        Text {
            objectName: "emptyDescription"
            width: parent.width
            visible: text !== ""
            text: root.description
            color: Theme.textSecondary
            font.pixelSize: Theme.fontCaption
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
        }

        Item {
            visible: root.actionText !== ""
            anchors.horizontalCenter: parent.horizontalCenter
            width: actionButton.implicitWidth
            height: 6 + actionButton.implicitHeight

            BusyButton {
                id: actionButton
                objectName: "emptyAction"
                y: 6
                text: root.actionText
                onClicked: root.actionTriggered()
            }
        }
    }

    QtObject {
        id: internal
        property bool ready: false     // 建立時設定屬性也會觸發 changed，那時只在 onCompleted 唸一次
    }

    function announce() {
        if (!internal.ready || !visible || title === "")
            return
        announced = announced.concat([title])
        Accessible.announce(title, Accessible.Polite)
    }

    onVisibleChanged: announce()
    onTitleChanged: announce()
    Component.onCompleted: {
        internal.ready = true
        announce()
    }
}
