pragma ComponentBehavior: Bound
import QtQuick
// 自訂了 Details 按鈕的 background、contentItem，要用 Basic 樣式（T13）
import QtQuick.Controls.Basic

// 區塊錯誤與重試（DS-27）：放在載不出來的內容原本的位置（影片、圖表、分數、清單）。
// - 技術細節（NoSuchKey、timeout、1062）收在 "Details" 裡，預設收起。
// - 同一個區塊連續失敗 3 次（failureCount ≥ 3），多一行建議。
// - 出現、內容改變、又失敗一次時，用 Assertive 唸出來。
// - 重試中把 busy 設成 true：Retry 按鈕進入忙碌（DS-24）。
Item {
    id: root

    property string title: ""
    property string detail: ""
    property string technicalDetail: ""
    property string retryText: qsTr("Retry")
    property int failureCount: 0
    property bool busy: false
    property bool detailsExpanded: false
    property var announced: []              // 唸過的內容，測試和除錯用

    readonly property bool showsHint: failureCount >= 3

    signal retry()

    implicitWidth: 360
    implicitHeight: column.implicitHeight

    Accessible.role: Accessible.AlertMessage
    Accessible.name: title
    Accessible.description: detail

    Column {
        id: column
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(root.width, 480)
        spacing: Theme.spaceS

        Image {
            anchors.horizontalCenter: parent.horizontalCenter
            width: Theme.iconM
            height: Theme.iconM
            sourceSize: Qt.size(Theme.iconM, Theme.iconM)
            source: "icons/circle-alert_errorOnLight.svg"
            Accessible.ignored: true
        }

        Text {
            objectName: "errorTitle"
            width: parent.width
            text: root.title
            color: Theme.textPrimary
            font.pixelSize: Theme.fontBody
            font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
        }

        Text {
            objectName: "errorDetail"
            width: parent.width
            visible: text !== ""
            text: root.detail
            color: Theme.textSecondary
            font.pixelSize: Theme.fontCaption
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
        }

        Text {
            objectName: "errorHint"
            width: parent.width
            visible: root.showsHint
            text: qsTr("If it still fails, check your network or contact your administrator.")   // DS-52 的原句
            color: Theme.textSecondary
            font.pixelSize: Theme.fontCaption
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
        }

        Item {
            anchors.horizontalCenter: parent.horizontalCenter
            width: buttons.implicitWidth
            height: 6 + buttons.implicitHeight

            Row {
                id: buttons
                y: 6
                spacing: Theme.spaceM

                BusyButton {
                    id: retryButton
                    objectName: "retryButton"
                    visible: root.retryText !== ""
                    text: root.retryText
                    busy: root.busy
                    busyText: qsTr("Retrying…")
                    onClicked: root.retry()
                }

                Button {
                    id: detailsButton
                    objectName: "detailsButton"
                    visible: root.technicalDetail !== ""
                    height: Theme.controlHeight
                    leftPadding: 4
                    rightPadding: 4
                    focusPolicy: Qt.StrongFocus
                    text: qsTr("Details")
                    font.pixelSize: Theme.fontCaption
                    onClicked: root.detailsExpanded = !root.detailsExpanded

                    Accessible.expandable: true
                    Accessible.expanded: root.detailsExpanded

                    contentItem: Text {
                        text: detailsButton.text
                        font.pixelSize: detailsButton.font.pixelSize
                        font.underline: true
                        color: Theme.textSecondary
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Item {
                        Rectangle {     // 鍵盤焦點框（DS-30）
                            visible: detailsButton.visualFocus
                            anchors.fill: parent
                            anchors.margins: -2
                            radius: Theme.radiusS + 2
                            color: "transparent"
                            border.color: Theme.focusOnLight
                            border.width: 2
                        }
                    }
                }
            }
        }

        Text {
            objectName: "errorTechnical"
            width: parent.width
            visible: root.detailsExpanded && root.technicalDetail !== ""
            text: root.technicalDetail
            textFormat: Text.PlainText
            color: Theme.textSecondary
            font.pixelSize: Theme.fontCaption
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WrapAnywhere
        }
    }

    QtObject {
        id: internal
        property bool ready: false     // 建立時設定屬性也會觸發 changed，那時只在 onCompleted 唸一次
    }

    function announce() {
        if (!internal.ready || !visible || title === "")
            return
        const text = detail === "" ? title : title + ". " + detail
        announced = announced.concat([text])
        Accessible.announce(text, Accessible.Assertive)
    }

    onVisibleChanged: announce()
    onTitleChanged: announce()
    onDetailChanged: announce()
    onFailureCountChanged: announce()
    Component.onCompleted: {
        internal.ready = true
        announce()
    }
}
